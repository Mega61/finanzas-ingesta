"""El plan de caja: que la proyeccion de la cuenta salga bien contada.

Lo que se protege, con los casos de octubre-noviembre de 2026 que lo motivaron:
lo pasado por tarjeta sale de la cuenta el dia de PAGO, no el de la compra; el
celular a 0% no se cobra entero; la quincena de un domingo llega el viernes; y
lo que ya se pago antes de tiempo no se resta dos veces.
"""

from __future__ import annotations

from datetime import date

import pytest

from finanzas.aplicacion import plan as P

HOY = date(2026, 10, 2)


def _plan(**extra):
    datos = {'cuenta': 'Banco', 'reserva': 'Nu', 'colchon': 0, 'horizonte_dias': 60}
    datos.update(extra)
    return P.interpretar(datos)


def _saldo(proy, d):
    return dict(proy.serie)[d]


def test_un_compromiso_baja_la_cuenta_en_su_fecha():
    pl = _plan(
        compromiso=[{'concepto': 'Arriendo', 'fecha': '2026-10-23', 'monto': 2_300_000}]
    )
    proy = P.proyectar(pl, HOY, {'Banco': 3_000_000})
    assert _saldo(proy, date(2026, 10, 22)) == 3_000_000
    assert _saldo(proy, date(2026, 10, 23)) == 700_000


def test_el_faltante_dice_cuanto_y_antes_de_cuando():
    pl = _plan(
        colchon=500_000,
        compromiso=[
            {'concepto': 'Contrato', 'fecha': '2026-10-20', 'monto': 3_400_000}
        ],
    )
    proy = P.proyectar(pl, HOY, {'Banco': 1_800_000})
    assert proy.minimo == (date(2026, 10, 20), -1_600_000)
    assert proy.faltante == 2_100_000
    assert proy.faltante_antes_de == date(2026, 10, 20)


def test_sin_faltante_no_hay_aviso():
    proy = P.proyectar(_plan(colchon=500_000), HOY, {'Banco': 1_000_000})
    assert proy.faltante == 0
    assert proy.faltante_antes_de is None


def test_la_quincena_de_un_domingo_llega_el_viernes():
    pl = _plan(ingreso=[{'concepto': 'Quincena', 'dia': 15, 'monto': 4_000_000}])
    proy = P.proyectar(pl, HOY, {'Banco': 0})
    fechas = [m.fecha for m in proy.eventos]
    assert date(2026, 10, 15) in fechas  # jueves
    assert date(2026, 11, 13) in fechas  # el 15 de noviembre es domingo


def test_el_dia_31_en_noviembre_es_el_30():
    r = P.Regla('x', 1, 'Banco', dia=31)
    assert r.ocurrencias(date(2026, 11, 1), date(2026, 11, 30)) == [date(2026, 11, 30)]


def test_lo_pasado_por_tarjeta_sale_el_dia_de_pago():
    """La renta en la Mastercard el 26-oct cae en el corte del 30 y se paga el 19-nov."""
    pl = _plan(
        compromiso=[
            {
                'concepto': 'DIAN',
                'fecha': '2026-10-26',
                'monto': 6_640_000,
                'medio': 'MASTER',
            }
        ],
        tarjeta=[{'nombre': 'MASTER', 'corte': 30, 'pago': 19}],
    )
    proy = P.proyectar(pl, HOY, {'Banco': 10_000_000, 'MASTER': -929_045})
    assert _saldo(proy, date(2026, 10, 26)) == 10_000_000
    assert proy.pagos_de_tarjeta['MASTER'] == [(date(2026, 11, 19), 7_569_045)]
    assert _saldo(proy, date(2026, 11, 19)) == 10_000_000 - 7_569_045


def test_el_gasto_diario_de_la_tarjeta_entra_al_pago():
    pl = _plan(
        tarjeta=[{'nombre': 'MASTER', 'corte': 30, 'pago': 19, 'gasto_diario': 100_000}]
    )
    proy = P.proyectar(pl, HOY, {'Banco': 0, 'MASTER': 0})
    # del 2 al 30 de octubre: 28 dias de gasto
    assert proy.pagos_de_tarjeta['MASTER'][0] == (date(2026, 11, 19), 2_800_000)


def test_el_celular_a_cero_no_se_cobra_entero():
    """La VISA debe 4,97M, de los que 4,72M son el celular a 0%: el 5-nov se
    paga lo demas mas la cuota del celular, no los 4,97M."""
    pl = _plan(
        horizonte_dias=70,
        tarjeta=[
            {
                'nombre': 'VISA',
                'corte': 15,
                'pago': 5,
                'sin_interes': {
                    'saldo': 4_715_181.68,
                    'al': '2026-10-02',
                    'cuota': 261_954.58,
                },
            }
        ],
    )
    proy = P.proyectar(pl, HOY, {'Banco': 0, 'VISA': -4_970_004})
    oct_, nov_ = proy.pagos_de_tarjeta['VISA']
    assert oct_ == (
        date(2026, 11, 5),
        pytest.approx(4_970_004 - (4_715_181.68 - 261_954.58)),
    )
    # el segundo corte (15-nov) solo trae la cuota siguiente
    assert nov_[1] == pytest.approx(261_954.58)


def test_lo_ya_pagado_antes_de_tiempo_no_se_resta_dos_veces():
    pl = _plan(
        compromiso=[{'concepto': 'Arriendo', 'fecha': '2026-10-20', 'monto': 2_300_000}]
    )
    ya = [P.Movimiento(HOY, 'PSE Construbienes', -2_306_000, 'Banco')]
    proy = P.proyectar(pl, HOY, {'Banco': 700_000}, ya)
    assert proy.eventos == []
    assert proy.minimo[1] == 700_000


def test_un_movimiento_de_firefly_cubre_una_sola_ocurrencia():
    pl = _plan(
        ingreso=[
            {'concepto': 'Q1', 'fecha': '2026-10-15', 'monto': 4_000_000},
            {'concepto': 'Q1 bis', 'fecha': '2026-10-16', 'monto': 4_000_000},
        ]
    )
    ya = [P.Movimiento(date(2026, 10, 2), 'Nomina', 4_000_000, 'Banco')]
    proy = P.proyectar(pl, HOY, {'Banco': 0}, ya)
    assert len(proy.eventos) == 1


def test_lo_vencido_sin_pagar_se_cuenta_hoy():
    pl = _plan(
        compromiso=[{'concepto': 'Olvidado', 'fecha': '2026-09-28', 'monto': 100}]
    )
    # fuera del rango: no se ve
    assert P.proyectar(pl, HOY, {'Banco': 0}).eventos == []
    pl = _plan(compromiso=[{'concepto': 'Hoy', 'fecha': '2026-10-02', 'monto': 100}])
    assert P.proyectar(pl, HOY, {'Banco': 0}).eventos[0].fecha == HOY


@pytest.mark.parametrize(
    'datos,dice',
    [
        ({}, 'cuenta'),
        ({'cuenta': 'B', 'compromiso': [{'concepto': 'x', 'monto': 1}]}, 'fecha'),
        (
            {
                'cuenta': 'B',
                'compromiso': [
                    {'concepto': 'x', 'monto': 1, 'dia': 3, 'fecha': '2026-10-01'}
                ],
            },
            'fecha',
        ),
        (
            {
                'cuenta': 'B',
                'compromiso': [
                    {'concepto': 'x', 'monto': 1, 'dia': 3, 'medio': 'AMEX'}
                ],
            },
            'AMEX',
        ),
        (
            {
                'cuenta': 'B',
                'tarjeta': [
                    {'nombre': 'V', 'corte': 15, 'pago': 5, 'sin_interes': {'saldo': 1}}
                ],
            },
            'al',
        ),
        (
            {'cuenta': 'B', 'ingreso': [{'concepto': 'x', 'monto': 'mucho', 'dia': 3}]},
            'monto',
        ),
    ],
)
def test_un_plan_mal_escrito_dice_que_falla(datos, dice):
    with pytest.raises(P.PlanInvalido, match=dice):
        P.interpretar(datos)


def test_se_lee_de_plan_json(monkeypatch):
    monkeypatch.setattr(
        P.config,
        'get',
        lambda k, d=None: (
            '{"cuenta":"Banco","ingreso":[{"concepto":"Q","dia":15,"monto":1}]}'
            if k == 'PLAN_JSON'
            else d
        ),
    )
    pl = P.leer()
    assert pl.cuenta == 'Banco'
    assert pl.ingresos[0].dia == 15
