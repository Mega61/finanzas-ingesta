"""La cuenta de gasto de Firefly no la decide el datafono.

El banco manda el nombre del PUNTO DE VENTA, no el de la cadena:

    TIENDA D1 SABANETA P    TIENDA D1 SABANETA S    KOBA COLOMBIA
    EXITO SABANETA          ALMACENES EXITO

`normalizar` los deja distintos, que es correcto —son textos distintos—, pero
el efecto en cadena no lo era: clave de regla nueva, sin cuenta aprendida, el
publicador cae en el texto crudo y Firefly CREA una cuenta de gasto con ese
nombre. Asi salieron cuatro cuentas nuevas en septiembre de 2026 al lado de
'Grupo Éxito' (128 movimientos desde julio de 2025) y 'D1' (22).
"""

from __future__ import annotations

import pytest

from finanzas.aplicacion import publicador
from finanzas.dominio.texto import cuenta_de_cadena

BASE = {
    'valor': -35600.0,
    'cuenta_firefly': 'MASTERCARD BLACK',
    'cuenta_destino': None,
    'contraparte': 'KOBA COLOMBIA',
    'traslado_a': None,
    'tipo': 'compra',
    'fecha': '2026-09-10',
    'moneda': 'COP',
    'descripcion': 'KOBA COLOMBIA',
    'external_id': 'bc-x',
    'plantilla': 'compra',
    'instrumento': '1234',
    'confianza': 0.9,
    'categoria': None,
    'presupuesto': None,
    'hora': None,
}


@pytest.mark.parametrize(
    'crudo,esperada',
    [
        ('TIENDA D1 SABANETA P', 'D1'),
        ('TIENDA D1 SABANETA S', 'D1'),
        ('KOBA COLOMBIA', 'D1'),
        ('ALMACENES EXITO', 'Grupo Éxito'),
        ('EXITO SABANETA', 'Grupo Éxito'),
        ('CARULLA POBLADO', 'Grupo Éxito'),
        ('SUPERMU', 'Supermu'),
        ('La vaquita', 'Supermu'),
    ],
)
def test_cada_datafono_cae_en_la_cuenta_de_su_cadena(crudo, esperada):
    assert cuenta_de_cadena(crudo) == esperada


@pytest.mark.parametrize(
    'ajeno', ['PANADERIA LA 33', 'FARMATODO', 'EMPRESAS PUBLICAS DE MEDELLIN', '']
)
def test_no_se_inventa_cadena_donde_no_la_hay(ajeno):
    """Mejor el nombre crudo que meterlo en la cadena equivocada."""
    assert cuenta_de_cadena(ajeno) is None


def test_el_publicador_usa_la_cadena_cuando_no_hay_regla():
    """El caso de KOBA COLOMBIA del 10 de septiembre."""
    payload = publicador.armar_payload(dict(BASE))
    assert payload['transactions'][0]['destination_name'] == 'D1'


def test_la_regla_le_gana_a_la_cadena():
    """Lo aprendido del historico o contestado por Telegram sigue mandando."""
    p = dict(BASE, cuenta_destino='Tienda de barrio')
    payload = publicador.armar_payload(p)
    assert payload['transactions'][0]['destination_name'] == 'Tienda de barrio'


def test_un_comercio_desconocido_conserva_su_nombre():
    """Sin cadena reconocida no se toca nada: el crudo sigue siendo el destino."""
    p = dict(BASE, contraparte='PANADERIA LA 33', cuenta_destino=None)
    payload = publicador.armar_payload(p)
    assert payload['transactions'][0]['destination_name'] == 'PANADERIA LA 33'
