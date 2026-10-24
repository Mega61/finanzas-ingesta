"""Los debitos automaticos que manda `@bancolombia.com.co`.

Hasta octubre de 2026 ese remitente no se bajaba: EPM y el arriendo de
Construbienes entraban al libro solo al conciliar el extracto, semanas
despues. Del mismo remitente llegan los codigos de un solo uso, que no se
pueden guardar.
"""

from __future__ import annotations

from datetime import date

import pytest

from finanzas.adaptadores import graph
from finanzas.dominio import texto
from finanzas.parsers import bancolombia_alertas as alertas

EPM = (
    'Bancolombia informa pago Factura Programada EPM SERVICIOS P Ref 12901883 '
    'por $429.350,00 desde TC*5868. 23/09/2026. Inquietudes '
    '6045109095/018000931987. Este es una notificación automática'
)
ARRIENDO = (
    'Bancolombia informa pago Factura Programada CONSTRUBIENES S Ref 1000366000 '
    'por $2.311.652,00 desde TC*5868. 27/01/2026. Inquietudes 6045109095'
)
CLAVE = (
    'Apreciado Cliente, De acuerdo con la solicitud realizada por usted, le '
    'enviamos su Clave Dinámica para que continúe con la transacción. '
    'Bancolombia te informa la Clave Dinámica 503167. Inquietudes al 0345109000'
)


@pytest.mark.parametrize(
    ('correo', 'quien', 'valor', 'fecha'),
    [
        (EPM, 'EPM SERVICIOS P', -429350.0, date(2026, 9, 23)),
        (ARRIENDO, 'CONSTRUBIENES S', -2311652.0, date(2026, 1, 27)),
    ],
)
def test_el_pago_programado_es_un_movimiento(correo, quien, valor, fecha):
    ev = alertas.parse_texto(correo, asunto='Alertas y Notificaciones')
    assert ev.plantilla == 'pago_programado'
    assert (ev.contraparte, ev.valor, ev.fecha) == (quien, valor, fecha)
    assert (ev.instrumento, ev.clase_instrumento) == ('5868', 'tarjeta')


def test_desde_la_cuenta_es_de_la_cuenta():
    ev = alertas.parse_texto(EPM.replace('desde TC*5868', 'desde cta *4762'))
    assert (ev.instrumento, ev.clase_instrumento) == ('4762', 'cuenta')


def test_la_clave_dinamica_no_se_guarda_ni_se_parsea():
    assert texto.es_codigo_de_seguridad(CLAVE)
    assert not texto.es_codigo_de_seguridad(EPM)
    with pytest.raises(alertas.Descartado):
        alertas.parse_texto(CLAVE)


def _msg(remitente, cuerpo):
    return {
        'from': {'emailAddress': {'address': remitente}},
        'subject': 'Servicio de Alertas y Notificaciones Bancolombia',
        'body': {'contentType': 'text', 'content': cuerpo},
    }


def test_graph_baja_el_pago_y_salta_la_clave():
    assert graph._nos_interesa(_msg('alertasynotificaciones@bancolombia.com.co', EPM))
    assert not graph._nos_interesa(
        _msg('alertasynotificaciones@bancolombia.com.co', CLAVE)
    )


def test_la_publicidad_del_grupo_no_entra():
    """`solicitudesgrupobancolombia.com.co` termina en lo mismo, sin la arroba."""
    assert not graph._nos_interesa(
        _msg('informacion@solicitudesgrupobancolombia.com.co', 'Bintec 2026')
    )
