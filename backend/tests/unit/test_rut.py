from __future__ import annotations

import pytest

from dvu.domain.rut import RutInvalido, digito_verificador, es_valido, formatear, normalizar


class TestDigitoVerificador:
    @pytest.mark.parametrize(
        ("numero", "dv"),
        [(76123456, "0"), (11111111, "1"), (12345678, "5"), (6, "K"), (30686957, "4")],
    )
    def test_modulo_11(self, numero: int, dv: str) -> None:
        assert digito_verificador(numero) == dv


class TestNormalizar:
    @pytest.mark.parametrize(
        "entrada", ["76.123.456-0", "761234560", "76123456-0", " 76.123.456 - 0 "]
    )
    def test_acepta_los_formatos_que_escribe_la_gente(self, entrada: str) -> None:
        assert normalizar(entrada) == "76123456-0"

    def test_dv_k_minuscula(self) -> None:
        assert normalizar("6-k") == "6-K"

    def test_dv_incorrecto_falla(self) -> None:
        """Un RUT mal guardado es un DTE rechazado por el SII más adelante."""
        with pytest.raises(RutInvalido, match="Dígito verificador"):
            normalizar("76123456-2")

    @pytest.mark.parametrize("entrada", ["", "abc", "76.123.456", "999999999999-1"])
    def test_formato_no_reconocido(self, entrada: str) -> None:
        with pytest.raises(RutInvalido):
            normalizar(entrada)


def test_es_valido_no_lanza() -> None:
    assert es_valido("76.123.456-0")
    assert not es_valido("76123456-2")


def test_formatear_para_mostrar() -> None:
    assert formatear("761234560") == "76.123.456-0"
