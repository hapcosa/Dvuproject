"""Configuración central. Toda variable de entorno se declara aquí y en .env.example."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Entorno = Literal["development", "test", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DVU_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- general ---
    env: Entorno = "development"
    debug: bool = False
    log_level: str = "INFO"
    tz: str = "America/Santiago"

    # --- api ---
    api_prefix: str = "/api/v1"
    #: `NoDecode` desactiva el parseo JSON que pydantic-settings aplica por defecto a
    #: los tipos complejos. Sin esto, el valor separado por comas del .env explota en
    #: el source (SettingsError) antes de que corra `_split_csv`.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- seguridad ---
    secret_key: str = "cambiame-openssl-rand-hex-32"  # noqa: S105 — placeholder, no un secreto
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 60
    refresh_token_days: int = 30
    #: Vida del token de descarga que viaja en la URL. Es corto a propósito: la URL queda
    #: en el historial del navegador y en el log del proxy, así que sirve para bajar el
    #: archivo y nada más. Dos minutos alcanzan de sobra — el catálogo entero tarda ~10 s.
    descarga_token_minutes: int = 2

    # --- infraestructura ---
    database_url: str = "postgresql+psycopg://dvu:dvu@localhost:5432/dvu"
    redis_url: str = "redis://localhost:6379/0"

    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "dvu"
    s3_region: str = "us-east-1"
    s3_secure: bool = False

    # --- extractor (Fase 0) ---
    catalogo_dir: Path = Path("/app/catalago")
    extractor_output_dir: Path = Path("/app/data/extraccion")
    extractor_confianza_minima: float = 0.60

    # --- reglas de negocio ---
    iva: float = 0.19
    precios_incluyen_iva: bool = False
    moneda: str = "CLP"

    # --- integraciones (fake por defecto: el stack levanta sin credenciales) ---
    banco_proveedor: str = "fake"
    pagos_proveedor: str = "fake"
    dte_proveedor: str = "fake"
    whatsapp_proveedor: str = "fake"

    # --- conciliación bancaria (Fase 2) ---
    banco_api_key: str = ""
    banco_cuenta_id: str = ""
    banco_link_token: str = ""
    #: Cartola de prueba que lee el proveedor `fake`. Ver `dvu cartola-demo`.
    cartola_fake_path: Path = Path("/app/data/cartola.jsonl")
    #: Días hacia atrás que sincroniza una corrida sin rango explícito.
    conciliacion_dias_atras: int = 7

    # --- facturación electrónica (Fase 2) ---
    dte_api_key: str = ""
    dte_ambiente: Literal["certificacion", "produccion"] = "certificacion"
    #: Emisor. Va en cada DTE; sin esto el SII rechaza el documento.
    emisor_rut: str = "76000000-0"
    emisor_razon_social: str = "COMERCIAL DVU SPA"
    emisor_giro: str = "DISTRIBUCION DE PRODUCTOS FERRETEROS Y DE CONSTRUCCION"
    emisor_direccion: str = ""
    emisor_comuna: str = ""

    sentry_dsn: str = ""

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @property
    def es_produccion(self) -> bool:
        return self.env == "production"

    def validar_para_produccion(self) -> None:
        """Falla temprano si se intenta arrancar producción con valores de ejemplo."""
        if not self.es_produccion:
            return
        problemas: list[str] = []
        if "cambiame" in self.secret_key or len(self.secret_key) < 32:
            problemas.append("DVU_SECRET_KEY es de ejemplo o muy corta")
        if self.debug:
            problemas.append("DVU_DEBUG=true en producción")
        if "fake" in {self.dte_proveedor, self.pagos_proveedor}:
            problemas.append("proveedores fake activos en producción")
        if problemas:
            raise RuntimeError("Configuración inválida para producción: " + "; ".join(problemas))


@lru_cache
def get_settings() -> Settings:
    return Settings()
