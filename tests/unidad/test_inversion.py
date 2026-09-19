"""«Es una inversion» saca el movimiento de los presupuestos del mes.

Una inversion no se come ningun bucket. Dejarla en uno —aunque sea el que mas
le cuadre— descuadra el presupuesto del mes, y por eso hay que QUITAR el
presupuesto, no solo no ponerlo. Esa distincion no existia: `editar` miraba
`if cambios.get('presupuesto')`, asi que mandar vacio era lo mismo que no
mandar nada.

Los nombres son los que ya usa Firefly: categoria Inversión (27 movimientos)
y etiqueta Inversión (18). Presupuesto, ninguno.
"""

from __future__ import annotations

import contextlib
import types

import pytest

from finanzas.aplicacion import movimientos
from finanzas.entrada import bot

SIN_ED = types.SimpleNamespace(etiqueta_agregar=None, etiqueta_quitar=None)


@pytest.mark.parametrize(
    'frase',
    [
        'es una inversión',
        'esto es inversion',
        'marcala como inversión',
        'INVERSIÓN',
    ],
)
def test_la_frase_saca_el_presupuesto_y_pone_la_etiqueta(frase):
    c = bot._cambios_de_etiquetas_y_presupuesto(SIN_ED, frase)
    assert c['categoria'] == 'Inversión'
    assert c['etiquetas'] == ['Inversión']
    assert c['presupuesto'] == movimientos.SIN_PRESUPUESTO


def test_un_presupuesto_nombrado_de_paso_no_revive_el_bucket():
    """«la inversion de la casa» no puede acabar metida en un presupuesto."""
    c = bot._cambios_de_etiquetas_y_presupuesto(SIN_ED, 'la inversión de Vivir')
    assert c['presupuesto'] == movimientos.SIN_PRESUPUESTO


def test_sin_la_palabra_no_se_mete_nada():
    c = bot._cambios_de_etiquetas_y_presupuesto(SIN_ED, 'ponla en mercado')
    assert 'categoria' not in c
    assert c.get('presupuesto') != movimientos.SIN_PRESUPUESTO


def test_quitar_el_presupuesto_manda_budget_id_nulo(monkeypatch):
    """Firefly ignora `budget_name: ''`. Lo que quita de verdad es budget_id."""
    visto = {}
    monkeypatch.setattr(
        movimientos, 'uno', lambda t: {'partes': 1, 'valor': -100.0, 'id': t}
    )
    monkeypatch.setattr(
        movimientos.firefly,
        'actualizar_split',
        lambda tx, **campos: visto.update(campos) or True,
    )
    monkeypatch.setattr(movimientos, '_releer', lambda t: {}, raising=False)
    # Falla al releer el movimiento y da igual: lo que se comprueba es el
    # payload que se armo antes de salir a la red.
    with contextlib.suppress(Exception):
        movimientos.editar('1', presupuesto=movimientos.SIN_PRESUPUESTO)
    assert 'budget_id' in visto
    assert visto['budget_id'] is None
    assert 'budget_name' not in visto


def test_poner_un_presupuesto_sigue_mandando_el_nombre(monkeypatch):
    visto = {}
    monkeypatch.setattr(
        movimientos, 'uno', lambda t: {'partes': 1, 'valor': -100.0, 'id': t}
    )
    monkeypatch.setattr(
        movimientos.firefly,
        'actualizar_split',
        lambda tx, **campos: visto.update(campos) or True,
    )
    with contextlib.suppress(Exception):
        movimientos.editar('1', presupuesto='Esencial')
    assert visto.get('budget_name') == 'Esencial'
    assert 'budget_id' not in visto
