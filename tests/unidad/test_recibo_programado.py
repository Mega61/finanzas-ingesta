"""El recibo que Firefly ya crea solo no se publica otra vez.

Firefly tiene siete recurrentes activos —Tigo, el arriendo, el gimnasio, tres
cuotas de manejo y Prime— y los crea por su cuenta cada mes. Ademas llega la
alerta del banco del mismo cobro, y la ingesta la publicaba: en septiembre de
2026 la factura de Tigo quedo dos veces.

    2026-09-04  UNE TELCO UNE PAGO E   129.721   la alerta del banco
    2026-09-06  Factura Tigo           119.900   el recurrente de Firefly

El anti-duplicado por monto no los cruza y nunca podria: el recurrente lleva
el monto de siempre y la alerta el del mes. Son 9.821 de diferencia, muy por
encima del peso de tolerancia.
"""

from __future__ import annotations

from datetime import date

import pytest

from finanzas.aplicacion import publicador

RECURRENTES = {'Tigo': 119900.0, 'Bancolombia': 25480.0, 'Athletic': 70000.0}


def _indice(registrados):
    """Un IndiceFirefly sin red: se le siembra `por_mes` a mano."""
    idx = publicador.IndiceFirefly.__new__(publicador.IndiceFirefly)
    idx.por_monto = {}
    idx.external = {}
    idx.n = 0
    idx.recurrentes = RECURRENTES
    idx.por_mes = {}
    for cuenta, f, monto in registrados:
        idx.por_mes.setdefault((cuenta, f), []).append(monto)
    return idx


def test_el_caso_de_tigo():
    """119.900 ya puesto el dia 6; la alerta de 129.721 no debe entrar."""
    idx = _indice([('Tigo', '2026-09', 119900)])
    assert idx.recibo_del_mes('Tigo', date(2026, 9, 4), -129721, RECURRENTES)


def test_si_el_mes_no_tiene_recibo_si_entra():
    idx = _indice([('Tigo', '2026-08', 119900)])
    assert idx.recibo_del_mes('Tigo', date(2026, 9, 4), -129721, RECURRENTES) is None


def test_una_cuenta_que_no_es_recurrente_no_se_toca():
    idx = _indice([('Grupo Éxito', '2026-09', 119900)])
    assert (
        idx.recibo_del_mes('Grupo Éxito', date(2026, 9, 4), -119900, RECURRENTES)
        is None
    )


@pytest.mark.parametrize('monto', [-300000, -5000])
def test_un_monto_que_no_se_parece_al_recibo_pasa(monto):
    """A 'Bancolombia' le llegan cuotas de manejo Y traslados cualquiera.

    Tapar todo lo que le entre seria peor que el duplicado: por eso el monto
    tiene que parecerse al del recurrente, no basta con la cuenta.
    """
    idx = _indice([('Bancolombia', '2026-09', 25480)])
    assert (
        idx.recibo_del_mes('Bancolombia', date(2026, 9, 4), monto, RECURRENTES) is None
    )


def test_el_margen_es_del_20_por_ciento():
    idx = _indice([('Athletic', '2026-09', 70000)])
    # 78.000 es un 11% mas: sigue siendo el mismo recibo
    assert idx.recibo_del_mes('Athletic', date(2026, 9, 9), -78000, RECURRENTES)
    # 95.000 es un 36% mas: eso ya es otra cosa
    assert idx.recibo_del_mes('Athletic', date(2026, 9, 9), -95000, RECURRENTES) is None
