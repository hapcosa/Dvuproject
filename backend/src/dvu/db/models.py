"""Modelos SQLAlchemy — Fases 0 y 1.

El detalle y la justificación de cada tabla están en docs/02-modelo-datos.md.
"""

from __future__ import annotations

import uuid as uuid_lib
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Sequence,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from dvu.db.base import Base, TimestampMixin, UUIDMixin, pk

# =============================================================================
# Fase 0 — catálogo
# =============================================================================


class CatalogoFuente(Base, TimestampMixin):
    """Una corrida de extracción. Permite comparar ediciones del catálogo."""

    __tablename__ = "catalogo_fuente"

    id: Mapped[pk]
    archivo: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    edicion: Mapped[str | None] = mapped_column(String(64))
    paginas: Mapped[int] = mapped_column(Integer, nullable=False)
    filas_detectadas: Mapped[int] = mapped_column(Integer, default=0)
    filas_cargables: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (UniqueConstraint("sha256", name="uq_catalogo_fuente_sha256"),)


class CatalogoPagina(Base, TimestampMixin):
    """Una página del catálogo que es diseño, no tabla: portada, oferta, contraportada.

    No se redibujan: se guarda la página original recortada del PDF (`key_pdf`) y una
    vista previa en PNG para la web (`key_png`). Al emitir el catálogo se reinsertan en
    su lugar, así que salen idénticas a la imprenta y no una imitación.
    """

    __tablename__ = "catalogo_pagina"

    id: Mapped[pk]
    #: De qué PDF salió y en qué página venía. Es la procedencia, no el orden: dos
    #: portadas pueden venir las dos de la página 1 de archivos distintos.
    archivo: Mapped[str] = mapped_column(String(255), nullable=False)
    pagina: Mapped[int] = mapped_column(Integer, nullable=False)
    #: `portada`, `promocion` o `contraportada`.
    tipo: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Posición dentro de su sección, la que el administrador arrastra. El orden entre
    #: secciones no se guarda: lo fija el tipo (portada al principio, contraportada al
    #: final), que es lo que el lector reconoce y no algo que convenga poder invertir.
    orden: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    key_pdf: Mapped[str] = mapped_column(String(255), nullable=False)
    key_png: Mapped[str] = mapped_column(String(255), nullable=False)
    #: El administrador puede sacar una oferta vencida sin borrar el registro.
    activa: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        UniqueConstraint("archivo", "pagina", name="uq_catalogo_pagina_archivo_pagina"),
        CheckConstraint(
            "tipo IN ('portada','promocion','contraportada')", name="ck_catalogo_pagina_tipo"
        ),
    )


class CatalogoActivo(Base, TimestampMixin):
    """Activos gráficos sueltos de la plantilla, por clave: `banner_par`, `banner_impar`.

    Es una tabla clave-valor a propósito. Son tres o cuatro imágenes de maqueta y darles
    una columna a cada una obligaría a migrar el esquema cada vez que el diseño cambia.
    """

    __tablename__ = "catalogo_activo"

    id: Mapped[pk]
    clave: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    key_objeto: Mapped[str] = mapped_column(String(255), nullable=False)


class CatalogoFilaCruda(Base, TimestampMixin):
    """Salida literal del extractor, antes de normalizar.

    Es la red de seguridad: si el parseo estuvo mal, se re-normaliza desde aquí sin
    volver a procesar 380 MB de PDF.
    """

    __tablename__ = "catalogo_fila_cruda"

    id: Mapped[pk]
    fuente_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("catalogo_fuente.id", ondelete="CASCADE"), index=True
    )
    pagina: Mapped[int] = mapped_column(Integer, nullable=False)
    orden: Mapped[int] = mapped_column(Integer, nullable=False)

    # Todo `Text`: esto es la salida literal del PDF y no puede rechazar nada. Una celda
    # mal fusionada produce cadenas largas — son justamente las que hay que conservar
    # para diagnosticar. Los largos acotados van en `producto`, que sí es dato validado.
    codigo_raw: Mapped[str | None] = mapped_column(Text)
    descripcion_raw: Mapped[str | None] = mapped_column(Text)
    venta_min_raw: Mapped[str | None] = mapped_column(Text)
    marca_raw: Mapped[str | None] = mapped_column(Text)
    medida_raw: Mapped[str | None] = mapped_column(Text)
    precio_raw: Mapped[str | None] = mapped_column(Text)

    confianza: Mapped[Decimal] = mapped_column(Numeric(3, 2), default=Decimal("0"))
    problemas: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    posicion: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    __table_args__ = (Index("ix_fila_cruda_fuente_pagina", "fuente_id", "pagina"),)


class Categoria(Base, TimestampMixin):
    __tablename__ = "categoria"

    id: Mapped[pk]
    nombre: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    parent_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("categoria.id"))
    orden: Mapped[int] = mapped_column(Integer, default=0)

    hijos: Mapped[list[Categoria]] = relationship(back_populates="padre")
    # `remote_side` va como string: `id` sólo está anotado (Mapped[pk], sin asignación),
    # así que en el cuerpo de la clase el nombre resuelve al builtin `id`.
    padre: Mapped[Categoria | None] = relationship(
        back_populates="hijos", remote_side="Categoria.id"
    )


class Producto(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "producto"

    id: Mapped[pk]
    sku: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    descripcion: Mapped[str] = mapped_column(Text, nullable=False)
    categoria_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("categoria.id"))
    marca: Mapped[str | None] = mapped_column(String(128))

    medida: Mapped[str | None] = mapped_column(String(128))
    medida_valor: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    medida_unidad: Mapped[str | None] = mapped_column(String(16))

    # Regla de negocio central: DVU vende por caja, no por unidad suelta.
    unidad_venta: Mapped[str] = mapped_column(String(16), default="UNID", nullable=False)
    multiplo_venta: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    envase: Mapped[str | None] = mapped_column(String(32))

    precio_lista_clp: Mapped[Decimal] = mapped_column(Numeric(12, 0), nullable=False)
    imagen_key: Mapped[str | None] = mapped_column(String(255))
    #: En el catálogo impreso la marca es el logo del proveedor, no su nombre escrito.
    #: Por eso `marca` está casi siempre vacía: el dato estaba en un PNG.
    marca_logo_key: Mapped[str | None] = mapped_column(String(255))

    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    alias: Mapped[list[ProductoAlias]] = relationship(
        back_populates="producto", cascade="all, delete-orphan"
    )
    # `selectin` y no `select`: el catálogo se lista de a 50 filas y cada una muestra su
    # categoría. La tabla tiene diez filas, así que es una consulta más, no cincuenta.
    categoria: Mapped[Categoria | None] = relationship(lazy="selectin")

    __table_args__ = (
        CheckConstraint("multiplo_venta >= 1", name="ck_producto_multiplo_positivo"),
        CheckConstraint("precio_lista_clp >= 0", name="ck_producto_precio_no_negativo"),
        Index(
            "ix_producto_descripcion_trgm",
            "descripcion",
            postgresql_using="gin",
            postgresql_ops={"descripcion": "gin_trgm_ops"},
        ),
    )


class ProductoAlias(Base, TimestampMixin):
    """Códigos de proveedor. El vendedor busca por el que tenga a mano."""

    __tablename__ = "producto_alias"

    id: Mapped[pk]
    producto_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("producto.id", ondelete="CASCADE"), index=True
    )
    codigo: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    origen: Mapped[str] = mapped_column(String(64), nullable=False)

    producto: Mapped[Producto] = relationship(back_populates="alias")

    __table_args__ = (UniqueConstraint("codigo", "origen", name="uq_alias_codigo_origen"),)


# =============================================================================
# Fase 1 — operación
# =============================================================================


class Usuario(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "usuario"

    id: Mapped[pk]
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    nombre: Mapped[str] = mapped_column(String(160), nullable=False)
    rol: Mapped[str] = mapped_column(String(16), nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        CheckConstraint("rol IN ('vendedor','cliente','bodega','admin')", name="ck_usuario_rol"),
    )


class Cliente(Base, UUIDMixin, TimestampMixin):
    """La ferretería que compra."""

    __tablename__ = "cliente"

    id: Mapped[pk]
    rut: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    razon_social: Mapped[str] = mapped_column(String(255), nullable=False)
    nombre_fantasia: Mapped[str | None] = mapped_column(String(255))
    giro: Mapped[str | None] = mapped_column(String(255))

    direccion: Mapped[str | None] = mapped_column(String(255))
    comuna: Mapped[str | None] = mapped_column(String(128))
    ciudad: Mapped[str | None] = mapped_column(String(128))
    email_dte: Mapped[str | None] = mapped_column(String(255))
    telefono: Mapped[str | None] = mapped_column(String(32))

    vendedor_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("usuario.id"))
    condicion_pago: Mapped[str] = mapped_column(String(32), default="contado", nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


#: Folios de pedido. Declarada en el metadata para que `create_all` (tests) y Alembic
#: (migración f842a1b09129) coincidan. Se consume desde dvu.db.numeracion.
pedido_numero_seq = Sequence("pedido_numero_seq", metadata=Base.metadata)


class Pedido(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "pedido"

    id: Mapped[pk]
    #: Generado por la app offline. Clave de idempotencia: el mismo pedido reenviado
    #: tras recuperar señal no se duplica.
    client_uuid: Mapped[uuid_lib.UUID] = mapped_column(
        PgUUID(as_uuid=True), unique=True, nullable=False, index=True
    )
    #: El folio comercial, `P-2026-000123`. Es **nulo mientras el pedido es un borrador**:
    #: una lista que el vendedor todavía está armando no es un documento y no debe
    #: quemar un número de la secuencia, que después se ve como un hueco en la
    #: correlatividad. Se asigna al enviarlo.
    numero: Mapped[str | None] = mapped_column(String(24), unique=True)

    cliente_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cliente.id"), index=True)
    vendedor_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("usuario.id"))
    origen: Mapped[str] = mapped_column(String(24), nullable=False)
    estado: Mapped[str] = mapped_column(String(24), nullable=False, index=True)

    neto_clp: Mapped[Decimal] = mapped_column(Numeric(12, 0), default=0)
    iva_clp: Mapped[Decimal] = mapped_column(Numeric(12, 0), default=0)
    total_clp: Mapped[Decimal] = mapped_column(Numeric(12, 0), default=0)

    observaciones: Mapped[str | None] = mapped_column(Text)
    creado_en_dispositivo: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sincronizado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    cliente: Mapped[Cliente] = relationship()
    vendedor: Mapped[Usuario | None] = relationship()
    lineas: Mapped[list[PedidoLinea]] = relationship(
        back_populates="pedido", cascade="all, delete-orphan"
    )
    eventos: Mapped[list[PedidoEvento]] = relationship(
        back_populates="pedido", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "origen IN ('app_vendedor','web_cliente','admin')", name="ck_pedido_origen"
        ),
    )


class PedidoLinea(Base, TimestampMixin):
    """Precio y descripción quedan **congelados**: un pedido de hace seis meses debe
    seguir siendo legible y auditable aunque el catálogo haya cambiado."""

    __tablename__ = "pedido_linea"

    id: Mapped[pk]
    pedido_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pedido.id", ondelete="CASCADE"), index=True
    )
    producto_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("producto.id"))

    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    descripcion: Mapped[str] = mapped_column(Text, nullable=False)
    multiplo_venta: Mapped[int] = mapped_column(Integer, nullable=False)

    cantidad: Mapped[int] = mapped_column(Integer, nullable=False)
    precio_unitario_clp: Mapped[Decimal] = mapped_column(Numeric(12, 0), nullable=False)
    total_linea_clp: Mapped[Decimal] = mapped_column(Numeric(12, 0), nullable=False)

    pedido: Mapped[Pedido] = relationship(back_populates="lineas")

    __table_args__ = (CheckConstraint("cantidad > 0", name="ck_linea_cantidad_positiva"),)


class PedidoEvento(Base):
    """Bitácora de la máquina de estados. Es la fuente del seguimiento que ve el cliente."""

    __tablename__ = "pedido_evento"

    id: Mapped[pk]
    pedido_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pedido.id", ondelete="CASCADE"), index=True
    )
    estado_anterior: Mapped[str | None] = mapped_column(String(24))
    estado_nuevo: Mapped[str] = mapped_column(String(24), nullable=False)
    usuario_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("usuario.id"))
    motivo: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    pedido: Mapped[Pedido] = relationship(back_populates="eventos")


class Pago(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "pago"

    id: Mapped[pk]
    cliente_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cliente.id"), index=True)
    monto_clp: Mapped[Decimal] = mapped_column(Numeric(12, 0), nullable=False)
    fecha_pago: Mapped[date] = mapped_column(Date, nullable=False)
    metodo: Mapped[str] = mapped_column(String(24), nullable=False)
    referencia: Mapped[str | None] = mapped_column(String(128))
    comprobante_key: Mapped[str | None] = mapped_column(String(255))

    #: declarado -> verificado | rechazado | pendiente_revision.
    #: Un pago que no matchea nunca se descarta: queda para la bandeja de excepciones.
    estado: Mapped[str] = mapped_column(String(24), default="declarado", nullable=False, index=True)

    registrado_por: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("usuario.id"))
    verificado_por: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("usuario.id"))

    #: Fase 2: el movimiento de la cartola que respalda este pago.
    movimiento_banco_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("movimiento_banco.id"), unique=True
    )
    #: Puntaje con que se concilió. Sirve para medir el criterio de salida de la fase
    #: (≥85 % sin intervención) y para auditar qué aceptó la máquina sola.
    conciliacion_confianza: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))

    cliente: Mapped[Cliente] = relationship()
    aplicaciones: Mapped[list[PagoAplicacion]] = relationship(
        back_populates="pago", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("monto_clp > 0", name="ck_pago_monto_positivo"),
        CheckConstraint(
            "estado IN ('declarado','verificado','rechazado','pendiente_revision')",
            name="ck_pago_estado",
        ),
    )


class PagoAplicacion(Base, TimestampMixin):
    """Un pago puede cubrir varios pedidos: el caso real de "transferí una vez para
    pagar tres facturas"."""

    __tablename__ = "pago_aplicacion"

    id: Mapped[pk]
    pago_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pago.id", ondelete="CASCADE"), index=True
    )
    pedido_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("pedido.id"), index=True)
    monto_clp: Mapped[Decimal] = mapped_column(Numeric(12, 0), nullable=False)

    pago: Mapped[Pago] = relationship(back_populates="aplicaciones")
    pedido: Mapped[Pedido] = relationship()

    __table_args__ = (UniqueConstraint("pago_id", "pedido_id", name="uq_pago_pedido"),)


# =============================================================================
# Fase 2 — conciliación bancaria y documentos tributarios
# =============================================================================


class MovimientoBanco(Base, UUIDMixin, TimestampMixin):
    """Cartola normalizada por el agregador (Fintoc / Floid).

    Es un espejo del banco, no una opinión: se guarda tal como llega, con el
    `id_externo` que da el proveedor como clave de idempotencia. Sincronizar el mismo
    rango de fechas dos veces no duplica movimientos.
    """

    __tablename__ = "movimiento_banco"

    id: Mapped[pk]
    #: Identificador del proveedor. Único: es lo que hace idempotente la sincronización.
    id_externo: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    proveedor: Mapped[str] = mapped_column(String(32), nullable=False)
    cuenta: Mapped[str | None] = mapped_column(String(64))

    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    monto_clp: Mapped[Decimal] = mapped_column(Numeric(12, 0), nullable=False)
    #: Glosa literal del banco. `Text` a propósito: viene con largos impredecibles y es
    #: donde suele estar el RUT o el nº de operación.
    descripcion: Mapped[str | None] = mapped_column(Text)
    referencia: Mapped[str | None] = mapped_column(String(128), index=True)
    rut_contraparte: Mapped[str | None] = mapped_column(String(16))

    #: sin_conciliar -> conciliado | ignorado. `ignorado` es para lo que no es una venta
    #: (cargos del banco, traspasos propios): se saca de la bandeja sin borrarlo.
    estado: Mapped[str] = mapped_column(
        String(24), default="sin_conciliar", nullable=False, index=True
    )

    __table_args__ = (
        CheckConstraint(
            "estado IN ('sin_conciliar','conciliado','ignorado')", name="ck_movimiento_estado"
        ),
        Index("ix_movimiento_fecha_monto", "fecha", "monto_clp"),
    )


class Dte(Base, UUIDMixin, TimestampMixin):
    """Documento tributario electrónico emitido al SII.

    Todo cliente de DVU es empresa y descuenta IVA: el documento de venta es la
    **factura tipo 33**, nunca boleta. El despacho requiere además guía tipo 52.

    El folio lo asigna el proveedor de facturación contra el CAF autorizado por el SII;
    aquí sólo se registra. Un DTE aceptado **no se modifica ni se borra**: se corrige
    emitiendo una nota de crédito (tipo 61) que lo referencia.
    """

    __tablename__ = "dte"

    id: Mapped[pk]
    #: 33 factura afecta, 52 guía de despacho, 61 nota de crédito.
    tipo: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    folio: Mapped[int | None] = mapped_column(BigInteger, index=True)

    pedido_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("pedido.id"), index=True)
    cliente_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cliente.id"), index=True)

    rut_receptor: Mapped[str] = mapped_column(String(16), nullable=False)
    neto_clp: Mapped[Decimal] = mapped_column(Numeric(12, 0), nullable=False)
    iva_clp: Mapped[Decimal] = mapped_column(Numeric(12, 0), nullable=False)
    total_clp: Mapped[Decimal] = mapped_column(Numeric(12, 0), nullable=False)

    #: emitido -> aceptado | rechazado | anulado. `rechazado` por el SII es un problema
    #: operativo, no un error de programa: queda visible con su glosa.
    estado: Mapped[str] = mapped_column(String(24), default="emitido", nullable=False, index=True)
    #: Identificador de seguimiento del envío al SII.
    track_id: Mapped[str | None] = mapped_column(String(64))
    glosa_sii: Mapped[str | None] = mapped_column(Text)

    xml_key: Mapped[str | None] = mapped_column(String(255))
    pdf_key: Mapped[str | None] = mapped_column(String(255))

    #: La nota de crédito referencia al documento que corrige.
    referencia_dte_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("dte.id"))
    emitido_por: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("usuario.id"))

    pedido: Mapped[Pedido] = relationship()
    cliente: Mapped[Cliente] = relationship()

    __table_args__ = (
        CheckConstraint("tipo IN (33,52,61)", name="ck_dte_tipo"),
        CheckConstraint(
            "estado IN ('emitido','aceptado','rechazado','anulado')", name="ck_dte_estado"
        ),
        UniqueConstraint("tipo", "folio", name="uq_dte_tipo_folio"),
    )


# =============================================================================
# Comprobantes declarados por el vendedor — reemplazo del grupo de WhatsApp
# =============================================================================


class Comprobante(Base, UUIDMixin, TimestampMixin):
    """Una transferencia que el vendedor avisa, con su foto.

    Es el registro estructurado de lo que hoy llega como mensaje suelto al grupo
    «COMPROBANTES TRANSF.». Se guarda aunque venga incompleto: perder el aviso es peor
    que registrarlo marcado, y el estado dice exactamente qué falta.

    No es un `Pago`. El `Pago` es el hecho contable que se aplica a pedidos y se cruza
    con la cartola; el comprobante es lo que declaró el vendedor y todavía nadie
    verificó. Se separan a propósito: el comprobante puede estar mal y aun así tiene que
    existir en la base, y un mismo comprobante puede terminar siendo un pago, ninguno o
    parte de uno.
    """

    __tablename__ = "comprobante"

    id: Mapped[pk]

    #: Idempotencia offline-first: la app del vendedor lo genera local y reintenta.
    #: Reenviar el mismo comprobante diez veces desde la bodega crea una sola fila.
    client_uuid: Mapped[uuid_lib.UUID | None] = mapped_column(PgUUID(as_uuid=True), unique=True)

    vendedor_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("usuario.id"), index=True)
    #: Cliente identificado en la base, si se pudo. Nulo no es error: el vendedor puede
    #: no saber el RUT y escribir el nombre a mano.
    cliente_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("cliente.id"), index=True)
    #: Lo que el vendedor escribió como cliente. Se conserva aunque haya `cliente_id`:
    #: es la evidencia de qué dijo, y sirve para corregir el match después.
    cliente_texto: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    #: Números de factura tal como los declaró. Varios: "transferí una vez por tres".
    facturas: Mapped[list[str]] = mapped_column(ARRAY(String(16)), default=list, nullable=False)

    monto_clp: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    banco: Mapped[str | None] = mapped_column(String(64))
    rut_contraparte: Mapped[str | None] = mapped_column(String(16))
    numero_operacion: Mapped[str | None] = mapped_column(String(32), index=True)
    fecha_transferencia: Mapped[date | None] = mapped_column(Date)

    #: El mensaje completo del vendedor. Es la fuente de la que salió todo lo demás y
    #: la única forma de auditar una extracción dudosa.
    detalle: Mapped[str] = mapped_column(Text, default="", nullable=False)
    #: Foto o PDF del comprobante. Trae datos bancarios: nunca se sirve público.
    imagen_key: Mapped[str | None] = mapped_column(String(255))

    #: Ver dvu.domain.comprobante.EstadoComprobante. Lo calcula el sistema al guardar.
    estado: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    observacion: Mapped[str] = mapped_column(Text, default="", nullable=False)

    #: Cobranza ya lo ingresó al sistema contable. No se borra: se marca.
    ingresado: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ingresado_por: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("usuario.id"))

    vendedor: Mapped[Usuario] = relationship(foreign_keys=[vendedor_id])
    cliente: Mapped[Cliente | None] = relationship()

    __table_args__ = (
        CheckConstraint("monto_clp IS NULL OR monto_clp > 0", name="ck_comprobante_monto_positivo"),
        CheckConstraint(
            "estado IN ('listo','falta_monto','falta_factura','falta_cliente',"
            "'falta_dato','duplicado_posible','abono_parcial')",
            name="ck_comprobante_estado",
        ),
        Index("ix_comprobante_fecha_transferencia", "fecha_transferencia"),
        # La bandeja de cobranza siempre ordena y filtra por fecha de registro: la de
        # transferencia puede venir vacía y no sirve para paginar.
        Index("ix_comprobante_creado_en", "creado_en"),
    )
