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
	@echo "Catálogo web:   http://localhost:$${DVU_API_PORT:-8000}/"
	@echo "Vendedor:       http://localhost:$${DVU_API_PORT:-8000}/vendedor"
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

# El umbral de cobertura (`fail_under` en pyproject) se mide sobre la suite entera:
# los routers y la carga los cubren los tests de integración. Exigirlo acá, donde
# justamente no corren, es una alarma que siempre suena y que nadie puede apagar.
.PHONY: test
test: ## Tests rápidos, sin los marcados integration (no exige cobertura)
	$(COMPOSE) run --rm api pytest -m "not integration" --cov-fail-under=0

.PHONY: test-all
test-all: ## Todos los tests, incluidos los de integración
	$(COMPOSE) run --rm api pytest

# Los tests del prototipo web corren el `dvu.js` real sobre un DOM (jsdom) contra la API
# levantada: es la única forma de pillar los bugs de estado del carrito, que son de las
# dos vistas desincronizadas y no del objeto. Piden el stack arriba (`make up`) y datos
# de ejemplo (`make seed`); si no los encuentran se saltan solos en vez de fallar.
#
# **Escriben en la base del stack.** Por eso nunca envían un pedido —no se puede borrar—
# y borran los borradores que crean. Ver `backend/tests/navegador/ayuda.mjs`.
.PHONY: test-web
test-web: ## Tests del prototipo web sobre un DOM real (necesita `make up`)
	$(COMPOSE) --profile test run --rm web-test

# `test-all` y no `test`: CI corre `pytest -m "not pdf"` contra Postgres y Redis, o sea
# con los de integración. Acá van además los marcados `pdf`, que se saltan solos si el
# catálogo no está bajado.
#
# `test-web` queda fuera: pide la API sirviendo, que CI no levanta. Se corre a mano al
# tocar el prototipo web.
.PHONY: check
check: lint test-all ## Lo mismo que corre CI

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
cargar-catalogo: ## Carga el resultado de la extracción a la BD, con las fotos
	$(COMPOSE) run --rm api python -m dvu.cli cargar-catalogo --con-imagenes

.PHONY: clasificar
clasificar: ## Arma el árbol de categorías y clasifica el catálogo por descripción
	$(COMPOSE) run --rm api python -m dvu.cli clasificar

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

.PHONY: catalogo-pdf
catalogo-pdf: ## Catálogo en PDF con el diseño del impreso, en ./data/exports
	@mkdir -p data/exports
	$(COMPOSE) run --rm api python -m dvu.cli catalogo-pdf \
	  --salida data/exports/catalogo-dvu-$$(date +%Y%m%d).pdf

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
