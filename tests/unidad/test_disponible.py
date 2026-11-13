"""Cuanto se puede gastar hoy. El caso es el del 13 de noviembre de 2026: el
tablero decia que en Esencial quedaban 2,8M y faltaba pagar el arriendo."""

from datetime import date

import pytest

from finanzas.aplicacion import disponible as d
from finanzas.entrada import bot

HOY = date(2026, 11, 13)
ESTADO = [
    {'nombre': 'Esencial', 'limite': 3_970_000, 'gastado': 1_136_654},
    {'nombre': 'Vivir', 'limite': 1_200_000, 'gastado': 321_712},
    {'nombre': 'Antojos', 'limite': 1_000_000, 'gastado': 406_310},
    {'nombre': 'Imprevistos Esenciales', 'limite': 250_000, 'gastado': 24_000},
    {'nombre': 'Sin tope', 'limite': None, 'gastado': 5_000},
]
CUENTAS = [
    d.Cuenta('Arriendo nuevo', 2_750_000, date(2026, 11, 23), 'Esencial'),
    d.Cuenta('Factura EPM', 350_000, date(2026, 11, 23), 'Esencial'),
]


def _nov(**kw):
    return d.calcular(HOY, ESTADO, CUENTAS, 328_766, meta_mercado=1_200_000, **kw)


def test_aparta_las_cuentas_y_el_mercado_antes_de_decir_libre():
    x = _nov()
    assert x.dias == 18
    # 3,97M - 1,14M gastado - 3,1M de cuentas - 0,87M de mercado que falta
    assert x.hueco == pytest.approx(1_137_888)


def test_el_hueco_sale_de_antojos_y_despues_de_vivir():
    x = _nov()
    assert x.libre('Antojos') == 0
    assert x.libre('Vivir') == pytest.approx(
        1_200_000 - 321_712 - (1_137_888 - 593_690)
    )
    assert x.libre('Imprevistos Esenciales') == 226_000, 'lo de emergencias no se toca'
    assert x.sin_cubrir == 0


def test_sin_cuentas_por_pagar_no_hay_hueco():
    x = d.calcular(HOY, ESTADO, [], 1_200_000, meta_mercado=1_200_000)
    assert x.hueco == 0
    assert x.libre('Antojos') == 1_000_000 - 406_310


def test_lo_que_no_alcanzan_a_cubrir_se_dice():
    x = d.calcular(
        HOY, ESTADO, [*CUENTAS, d.Cuenta('X', 3_000_000, HOY, 'Esencial')], 0
    )
    assert x.sin_cubrir > 0
    assert 'ahorro' in d.texto_hoy(x)


@pytest.mark.parametrize(
    ('texto', 'esperado'),
    [
        ('restaurante', 'Vivir'),
        ('un Almuerzo', 'Vivir'),
        ('juego de Steam', 'Antojos'),
        ('el sofá', 'Antojos'),
        ('mercado', 'Esencial'),
        ('algo', None),
    ],
)
def test_en_que_presupuesto_cae(texto, esperado):
    assert d.presupuesto_de(texto) == esperado


def test_puedo_dice_si_no_y_cabe_pero():
    x = _nov()
    libre = x.libre('Vivir')
    assert d.texto_puedo(x, d.puedo(x, 15_000, 'Vivir'), 15_000).startswith('✅')
    assert 'Cabe, pero' in d.texto_puedo(
        x, d.puedo(x, libre * 0.8, 'Vivir'), libre * 0.8
    )
    no = d.texto_puedo(x, d.puedo(x, 60_000, 'Antojos'), 60_000)
    assert no.startswith('❌') and 'Vivir' in no


def test_las_cuentas_por_pagar_reconocen_la_categoria():
    cats = ['Arriendo', 'Facturas', 'Mercado']
    assert d._categoria_de_cuenta('Arriendo nuevo', cats) == 'Arriendo'
    assert d._categoria_de_cuenta('Factura EPM', cats) == 'Facturas'
    assert d._categoria_de_cuenta('Al fondo de emergencia', cats) is None


def test_una_cuenta_ya_pagada_no_se_aparta():
    gastos = [
        {'categoria': 'Arriendo', 'monto': 2_750_000, 'fecha': date(2026, 11, 20)}
    ]
    assert d._ya_pagada(gastos, 'Arriendo', 2_750_000, 2_750_000, date(2026, 11, 23))
    assert not d._ya_pagada(
        gastos, 'Arriendo', 2_750_000, 2_750_000, date(2026, 12, 23)
    )


@pytest.mark.parametrize(
    ('texto', 'monto'),
    [
        ('180000 restaurante', 180_000),
        ('180.000 restaurante', 180_000),
        ('180k comida', 180_000),
        ('60 mil steam', 60_000),
        ('1,2M sofa', 1_200_000),
        ('nada', None),
    ],
)
def test_lee_el_monto(texto, monto):
    assert bot._leer_monto(texto) == monto
