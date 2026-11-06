"""El avance y su «recibiste» son un solo movimiento. Los textos son las
alertas reales del 3 de noviembre de 2026, con el nombre cambiado."""

from __future__ import annotations

import pytest

from finanzas.adaptadores import almacen, db
from finanzas.dominio.espejos import es_espejo
from finanzas.entrada import demonio
from finanzas.parsers import bancolombia_alertas as alertas

AVANCE = (
    'Bancolombia: Hiciste un avance de $600,000 en tu SUC VIRTUAL el 20:33 '
    '03/11/2026 desde tu T.Credito *9919 a la cuenta *5788.'
)
RECIBISTE = (
    'Bancolombia: Recibiste una transferencia por $600,000 de ANA PEREZ en tu '
    'cuenta **5788, el 03/11/2026 a las 20:33. Si tienes dudas, hablemos.'
)
OTRO_MONTO = RECIBISTE.replace('$600,000', '$150,000')


def _fila(texto):
    ev = alertas.parse_texto(texto)
    d = ev.dict()
    return d


def test_el_recibiste_es_la_otra_pata_del_avance():
    assert es_espejo(_fila(AVANCE), _fila(RECIBISTE))


def test_otro_monto_no_es_espejo():
    assert not es_espejo(_fila(AVANCE), _fila(OTRO_MONTO))


def test_otra_hora_no_es_espejo():
    tarde = RECIBISTE.replace('a las 20:33', 'a las 21:40')
    assert not es_espejo(_fila(AVANCE), _fila(tarde))


def test_una_transferencia_de_verdad_no_se_toca():
    """Sin avance ese dia, un «recibiste» es plata que entro."""
    assert not es_espejo(_fila(RECIBISTE), _fila(RECIBISTE))


@pytest.fixture
def cx(tmp_path):
    a = almacen.Almacen.abrir(str(tmp_path / 'x.db'), db.ESQUEMA)
    uid = a.guardar_usuario('Ana', 'x', 'x', '1')
    a.guardar_buzon(uid, 'imap', 'ana@x.com')
    return a.cx


def _crear(cx, texto, n):
    ev = alertas.parse_texto(texto)
    cid, _ = db.correo_guardar(cx, 1, f'm{n}', 'x', 'x', '2026-11-03', texto)
    d = ev.dict()
    db.pendiente_crear(cx, correo_id=cid, usuario_id=1, estado='nuevo', **d)
    return ev


@pytest.mark.parametrize('primero', ['avance', 'recibiste'])
def test_en_cualquier_orden_queda_un_solo_movimiento(cx, primero):
    """Ese dia el «recibiste» de los 600.000 llego un minuto antes que su
    avance: hay que poder cazarlo en las dos direcciones."""
    c = {'usuario_id': 1}
    if primero == 'avance':
        _crear(cx, AVANCE, 1)
        ev = alertas.parse_texto(RECIBISTE)
        assert demonio._es_espejo_de_avance(cx, c, ev, '2026-11-03')
    else:
        _crear(cx, RECIBISTE, 1)
        ev = alertas.parse_texto(AVANCE)
        assert not demonio._es_espejo_de_avance(cx, c, ev, '2026-11-03')
        fila = cx.execute(
            "SELECT estado, decidido_por FROM pendientes WHERE tipo = 'transferencia_entrada'"
        ).fetchone()
        assert (fila['estado'], fila['decidido_por']) == (
            'descartado',
            'espejo_de_avance',
        )


def test_la_recarga_de_la_civica_es_un_movimiento():
    ev = alertas.parse_texto(
        'Bancolombia: Recargaste 50,000.00 a Tarjeta Civica desde tu cuenta '
        '*5788 el 02/11/2026 16:11 ¿Dudas? Llama al 018000945555.'
    )
    assert (ev.valor, ev.instrumento, ev.plantilla) == (
        -50000.0,
        '5788',
        'recarga_civica',
    )
    assert str(ev.fecha) == '2026-11-02' and ev.hora == '16:11'


def test_identificarse_por_telefono_no_es_un_movimiento():
    with pytest.raises(alertas.Descartado) as ex:
        alertas.parse_texto(
            'Bancolombia: Listo. Te identificaste con tu clave principal en '
            'nuestra Sucursal Telefonica o en nuestro chat el 03/11/2026 a las 16:25.'
        )
    assert ex.value.motivo == 'identificacion'
