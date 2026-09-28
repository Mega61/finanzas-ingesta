"""Correos del banco que traen numeros y no son movimientos."""

import pytest

from finanzas.parsers import bancolombia_alertas as alertas

CODIGO = (
    'Por tu seguridad Este es tu código Bancolombia: 837099 es el codigo que '
    'necesitas para actualizar tus datos. Es solo para ti, no lo compartas. '
    'Este código es de un sólo uso.'
)


def test_el_codigo_de_seguridad_se_descarta():
    """Llego el dia que la segunda persona cambio el correo de sus alertas. Sin
    esta regla quedaba como «sin reconocer», que es lo que se revisa a mano."""
    with pytest.raises(alertas.Descartado) as ex:
        alertas.parse_texto(CODIGO, asunto='Alertas y Notificaciones')
    assert ex.value.motivo == 'codigo_de_seguridad'
