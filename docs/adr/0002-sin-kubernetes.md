# ADR-0002 — Docker Compose, sin Kubernetes

- **Estado:** aceptado
- **Fecha:** 2026-08-03

## Contexto

Se pidió explícitamente cultura DevOps sólida pero **sin Kubernetes por ahora**. El
volumen esperado de DVU es de decenas de vendedores y miles de pedidos al mes: una carga
que un solo servidor absorbe sin esfuerzo.

## Decisión

**Docker + Docker Compose** para desarrollo y producción. Producción en un VPS con Caddy
como reverse proxy (TLS automático) y backups de PostgreSQL a almacenamiento externo.

## Razones

- Kubernetes agrega una superficie operacional (control plane, ingress, secrets,
  observabilidad, upgrades) que en este volumen no compra nada y hay que mantener.
- El mismo `docker-compose.yml` sirve en el laptop y en el servidor: menos deriva entre
  entornos, menos "en mi máquina funciona".
- Escalar verticalmente un VPS cubre varios órdenes de magnitud de crecimiento antes de
  necesitar orquestación.

## Consecuencias y mitigaciones

- **Sin auto-healing multi-nodo.** Mitigación: `restart: unless-stopped` y healthchecks
  en todos los servicios; monitoreo externo (uptime check) que avisa si el host cae.
- **Deploy con downtime breve.** Aceptable: DVU no opera 24/7. Si más adelante molesta,
  se resuelve con dos réplicas de `api` detrás de Caddy y recarga escalonada, sin migrar
  a Kubernetes.
- **Backups son responsabilidad explícita nuestra.** `scripts/backup_db.sh` + cron, con
  restore probado periódicamente. Un backup que nunca se restauró no es un backup.

## Cuándo revisar esta decisión

Si se cumple alguna: más de un servidor de aplicación necesario de forma sostenida,
requerimiento real de despliegue sin downtime, o más de ~5 servicios desplegables
independientes. Ninguna es previsible en el horizonte de las Fases 0–4.
