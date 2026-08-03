-- Extensiones requeridas por el modelo de datos.
-- Se ejecuta una sola vez, al crear el cluster (docker-entrypoint-initdb.d).

-- Búsqueda difusa de productos: el vendedor escribe "codo media", no el SKU.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Comparación sin distinción de mayúsculas para códigos y emails.
CREATE EXTENSION IF NOT EXISTS citext;
