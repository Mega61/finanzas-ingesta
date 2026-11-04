"""Quien fue el comercio de un cargo raro, mirando el correo de alrededor.
Los casos salen de buzones reales de octubre y noviembre de 2026, sin los
datos de la persona."""

from __future__ import annotations

from datetime import datetime, timedelta

from finanzas.dominio.fechas import BOGOTA
from finanzas.dominio.sospechosos import (
    Correo,
    FacturaDelMonto,
    menciona_el_monto,
    nombre_del_comercio,
    puntuar,
)

CARGO = datetime(2026, 10, 31, 18, 13, tzinfo=BOGOTA)


def _c(remitente, asunto, minutos, cuerpo='', nombre=''):
    return Correo(remitente, nombre, asunto, CARGO + timedelta(minutes=minutos), cuerpo)


BANCO = _c(
    'alertasynotificaciones@ayn.notificacionesbancolombia.com',
    'Alertas y Notificaciones',
    1,
    'Compraste COP78.000,00 en RAPYD*MC con tu T.Cred *0000',
)
CONFIRMACION = _c(
    'boleteria@corferias.com',
    'Confirmación de Compra - Orden #293088',
    1,
    'Confirmación de compra COMIC CON MEDELLIN ... Total $78.000',
)
FACTURA = _c(
    'notificaciones@thefactoryhka.com.co',
    '900525906;CORFERIAS INVERSIONES S A S;BOM18970;01;CORFERIAS INVERSIONES S A S;',
    23,
    'Factura Electrónica de Venta ... Total: 78.000,00',
)
VERIFICA = _c(
    'boleteria@corferias.com',
    'Verifica tu cuenta en Corferias',
    -4,
    'Activación de cuenta. Gracias por registrarte.',
)


def test_rapyd_era_comic_con():
    r = puntuar(-78000, CARGO, [VERIFICA, BANCO, CONFIRMACION, FACTURA])
    assert len(r) == 1
    assert r[0].comercio == 'Corferias'
    assert 'Confirmación de Compra' in r[0].motivo or 'BOM18970' in r[0].motivo


def test_la_alerta_del_banco_no_es_sospechosa():
    assert puntuar(-78000, CARGO, [BANCO]) == []


def test_llegar_cerca_no_alcanza():
    """Un boletin que cae a la misma hora es ruido, no pista."""
    ruido = _c('news@tienda.com', 'Novedades de la semana', 2, 'Mira lo nuevo')
    assert puntuar(-78000, CARGO, [ruido]) == []


def test_un_correo_de_compra_inmediato_si_alcanza_sin_monto():
    c = _c('pedidos@tienda.com', 'Tu pedido fue recibido', 3, nombre='Tienda X')
    r = puntuar(-78000, CARGO, [c])
    assert [s.comercio for s in r] == ['Tienda X']


def test_fuera_de_la_ventana_no_cuenta():
    tarde = _c('boleteria@corferias.com', 'Confirmación de Compra', 60 * 5, '78.000')
    assert puntuar(-78000, CARGO, [tarde]) == []


def test_la_factura_guardada_gana():
    f = FacturaDelMonto(
        'FARMATODO COLOMBIA S.A', '830129327', 'SB0637194', 'Ibuprofeno'
    )
    r = puntuar(-46600, CARGO, [CONFIRMACION], [f])
    assert r[0].comercio.startswith('Farmatodo')
    assert 'SB0637194' in r[0].motivo


def test_el_mismo_comercio_con_dos_nombres_es_uno():
    """La confirmacion dice «Corferias» y la factura «Corferias Inversiones»."""
    r = puntuar(-78000, CARGO, [CONFIRMACION, FACTURA])
    assert len(r) == 1


def test_la_pasarela_no_es_el_comercio():
    c = _c(
        'no-reply@wompi.co',
        'Transacción APROBADA en Farmatodo Colombia SA - ref. 24ac6265',
        2,
    )
    assert nombre_del_comercio(c) == 'Farmatodo Colombia SA'


def test_el_monto_en_todos_los_formatos():
    for texto in ('$78.000', 'COP78.000,00', '78,000.00', 'Total 78000 pesos'):
        assert menciona_el_monto(texto, -78000), texto


def test_el_monto_no_es_parte_de_otro():
    assert not menciona_el_monto('Total 178.000', 78000)
    assert not menciona_el_monto('ref 78.0001', 78000)


def test_un_monto_chico_sin_separador_no_es_prueba():
    """900 aparece en cualquier codigo o NIT."""
    assert not menciona_el_monto('NIT 900525906, a las 9:00', 900)
