"""Integraciones con terceros: banco, SII, pagos en línea, WhatsApp.

Todas viven detrás de un `Protocol` con una implementación `fake` por defecto, para que
el stack levante y los tests corran sin credenciales de nadie. El proveedor real se
elige por configuración (`DVU_BANCO_PROVEEDOR`, `DVU_DTE_PROVEEDOR`, …), y
`Settings.validar_para_produccion()` aborta el arranque si producción quedó en `fake`.
"""
