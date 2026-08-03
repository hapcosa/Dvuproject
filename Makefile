.DEFAULT_GOAL := help
SHELL := /bin/bash
COMPOSE := docker compose
DC_PROD := docker compose -f docker-compose.yml

.PHONY: help
help: ## Muestra esta ayuda
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	 | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- entorno -----------------------------------------------------------------
.env: ## Crea .env desde .env.example si no existe
	@test -f .env || (cp .env.example .env && \
	  echo "Creado .env desde .env.example — revisa DVU_SECRET_KEY antes de producción")

.PHONY: init
init: .env ## Prepara el entorno local por primera vez
	@mkdir -p data/extraccion
	@echo "Listo. Ahora: make up"

# --- ciclo de vida -----------------------------------------------------------
.PHONY: up
up: .env ## Levanta el stack completo
	$(COMPOSE) up -d --build
	@echo "API:            http://localhost:$${DVU_API_PORT:-8000}/docs"
	@echo "MinIO console:  http://localhost:9001"

.PHONY: down
down: ## Detiene el stack (conserva los volúmenes)
	$(COMPOSE) down

.PHONY: clean
clean: ## Detiene y BORRA los volúmenes (destruye la BD local)
	$(COMPOSE) down -v

.PHONY: logs
logs: ## Sigue los logs (make logs s=api)
	$(COMPOSE) logs -f $(s)

.PHONY: ps
ps: ## Estado de los servicios
	$(COMPOSE) ps

.PHONY: shell
shell: ## Shell dentro del contenedor api
	$(COMPOSE) exec api bash

.PHONY: psql
psql: ## Consola psql
	$(COMPOSE) exec db psql -U $${POSTGRES_USER:-dvu} -d $${POSTGRES_DB:-dvu}

# --- calidad -----------------------------------------------------------------
.PHONY: lint
lint: ## ruff check + mypy
	$(COMPOSE) run --rm --no-deps api ruff check src tests
	$(COMPOSE) run --rm --no-deps api ruff format --check src tests
	$(COMPOSE) run --rm --no-deps api mypy src

.PHONY: fmt
fmt: ## Formatea y autocorrige
	$(COMPOSE) run --rm --no-deps api ruff check --fix src tests
	$(COMPOSE) run --rm --no-deps api ruff format src tests

.PHONY: test
test: ## Tests (sin los marcados integration)
	$(COMPOSE) run --rm api pytest -m "not integration"

.PHONY: test-all
test-all: ## Todos los tests, incluidos los de integración
	$(COMPOSE) run --rm api pytest

.PHONY: check
check: lint test ## Lo mismo que corre CI

# --- base de datos -----------------------------------------------------------
.PHONY: migrate
migrate: ## Aplica migraciones pendientes
	$(COMPOSE) run --rm api alembic upgrade head

.PHONY: downgrade
downgrade: ## Revierte una migración
	$(COMPOSE) run --rm api alembic downgrade -1

.PHONY: revision
revision: ## Nueva migración autogenerada (make revision m="agrega tabla x")
	@test -n "$(m)" || (echo 'Falta el mensaje: make revision m="..."'; exit 1)
	$(COMPOSE) run --rm api alembic revision --autogenerate -m "$(m)"

.PHONY: seed
seed: ## Carga datos de ejemplo para desarrollo
	$(COMPOSE) run --rm api python -m dvu.cli seed

# --- Fase 0: catálogo --------------------------------------------------------
.PHONY: extract
extract: ## Extrae los PDF del catálogo a JSONL + reporte de calidad
	$(COMPOSE) run --rm api python -m dvu.cli extraer --con-imagenes

.PHONY: extract-dry
extract-dry: ## Extrae solo las 6 primeras páginas, sin imágenes (iteración rápida)
	$(COMPOSE) run --rm api python -m dvu.cli extraer --hasta-pagina 6

.PHONY: cargar-catalogo
cargar-catalogo: ## Carga el resultado de la extracción a la BD
	$(COMPOSE) run --rm api python -m dvu.cli cargar-catalogo

# --- Fase 2: conciliación y DTE ----------------------------------------------
.PHONY: conciliar
conciliar: ## Trae la cartola del banco y concilia los pagos declarados
	$(COMPOSE) run --rm api python -m dvu.cli conciliar

.PHONY: cartola-demo
cartola-demo: ## Cartola de prueba para ensayar la conciliación sin agregador
	$(COMPOSE) run --rm api python -m dvu.cli cartola-demo

# --- operación ---------------------------------------------------------------
.PHONY: exportar
exportar: ## Excel de ventas, detalle y pagos en ./data/exports
	@mkdir -p data/exports
	$(COMPOSE) run --rm api python -m dvu.cli exportar \
	  --salida data/exports/dvu-ventas-$$(date +%Y%m%d).xlsx

.PHONY: backup
backup: ## Backup de la BD a ./data/backups
	@mkdir -p data/backups
	$(COMPOSE) exec -T db pg_dump -U $${POSTGRES_USER:-dvu} -Fc $${POSTGRES_DB:-dvu} \
	  > data/backups/dvu-$$(date +%Y%m%d-%H%M%S).dump
	@echo "Backup en data/backups/"

.PHONY: deploy
deploy: ## Despliegue en producción (sin el override de desarrollo)
	$(DC_PROD) up -d --build
	$(DC_PROD) run --rm api alembic upgrade head
