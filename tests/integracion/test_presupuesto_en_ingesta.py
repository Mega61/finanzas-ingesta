"""Un gasto no puede entrar a Firefly sin presupuesto y en silencio.

El reclamo: «las ultimas transacciones que se han agregado no tienen
presupuesto, ¿la ingesta no esta tomando correctamente el budget?».

La ingesta SI lo derivaba, pero solo cuando se podia: del historico cuando la
categoria apunta siempre al mismo presupuesto (>=80%), de la lista fija de
`taxonomia`, o de lo que el usuario haya declarado. Cuando ninguno de los tres
daba respuesta -- una categoria nueva sin historico como «GBS Infra», o una
repartida como «Restaurante» entre Vivir y Antojos -- el movimiento se
publicaba con la categoria buena y el presupuesto EN BLANCO, y no se preguntaba
nunca.

Lo peor es que no se arreglaba con el tiempo: la categoria no acumula historico
porque nunca recibe un presupuesto del cual aprender.

El camino del chat si preguntaba (`interprete.pedir_presupuesto`); al de la
ingesta le faltaba ese paso.
"""

from __future__ import annotations

import sqlite3

import pytest

from finanzas.adaptadores import db
from finanzas.adaptadores.almacen import Almacen
from finanzas.aplicacion import clasificador, presupuestos


@pytest.fixture
def cx():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys = ON')
    Almacen(c).inicializar(db.ESQUEMA)
    return c


@pytest.fixture
def uid(cx):
    return Almacen(cx).guardar_usuario('Juan', 'https://f', 'tok', '555')


@pytest.fixture
def _sin_red(monkeypatch):
    """Ni Firefly, ni el mapa del historico, ni el mapeo de tarjetas.

    `cuenta_de_instrumento` lee `productos.csv`, que tiene los ultimos 4
    digitos de las tarjetas de verdad y por eso NO esta en el repo. Sin
    reemplazarla, estas pruebas pasan en la maquina del dueno -- que si tiene
    el archivo -- y se caen en CI. Aqui no se esta probando la resolucion de
    la tarjeta, sino el presupuesto.
    """
    monkeypatch.setattr(
        clasificador, 'cuenta_de_instrumento', lambda inst, fecha: 'AMEX PLATINO'
    )
    monkeypatch.setattr(presupuestos, 'mapa_categoria', lambda **k: {})
    monkeypatch.setattr(presupuestos, 'nombres_activos', lambda: ['Esencial', 'Vivir'])


def _gasto(cx, uid, categoria, presupuesto=None, valor=-63000.0):
    """Una regla aprendida que resuelve la categoria, como en produccion."""
    db.regla_guardar(
        cx,
        uid,
        clasificador.normalizar('GOOGLE Workspace'),
        categoria=categoria,
        presupuesto=presupuesto,
        cuenta_firefly='Google',
        origen='usuario',
        direccion='gasto',
    )
    cx.commit()
    return clasificador.clasificar(
        cx,
        uid,
        {
            'tipo': 'compra_tarjeta',
            'fecha': '2026-09-02',
            'instrumento': '2567',
            'clase_instrumento': 'tarjeta',
            'traslado_a': None,
            'contraparte': 'GOOGLE Workspace',
            'descripcion': 'GOOGLE Workspace',
            'valor': valor,
        },
    )


@pytest.mark.usefixtures('_sin_red')
class TestUnGastoSinPresupuestoSePregunta:
    def test_sin_presupuesto_derivable_abre_la_pregunta(self, cx, uid):
        d = _gasto(cx, uid, 'GBS Infra')
        assert d['categoria'] == 'GBS Infra', 'la categoria si se resuelve'
        assert not d['presupuesto']
        assert d['pregunta'] == 'categoria', 'y por eso hay que preguntar'

    def test_con_presupuesto_no_molesta(self, cx, uid):
        d = _gasto(cx, uid, 'GBS Infra', presupuesto='Vivir')
        assert d['presupuesto'] == 'Vivir'
        assert d['pregunta'] is None

    def test_del_historico_tampoco_molesta(self, cx, uid, monkeypatch):
        monkeypatch.setattr(
            presupuestos,
            'mapa_categoria',
            lambda **k: {
                'Mercado': {
                    'presupuesto': 'Esencial',
                    'seguro': True,
                    'reparto': {'Esencial': 51},
                }
            },
        )
        d = _gasto(cx, uid, 'Mercado')
        assert d['presupuesto'] == 'Esencial'
        assert d['pregunta'] is None

    def test_un_historico_repartido_si_pregunta(self, cx, uid, monkeypatch):
        """«Restaurante» esta 10 en Vivir y 3 en Antojos: 77%, debajo del 80%.
        Es un juicio de verdad y no se asume la mayoria."""
        monkeypatch.setattr(
            presupuestos,
            'mapa_categoria',
            lambda **k: {
                'Restaurante': {
                    'presupuesto': 'Vivir',
                    'seguro': False,
                    'reparto': {'Vivir': 10, 'Antojos': 3},
                }
            },
        )
        d = _gasto(cx, uid, 'Restaurante')
        assert not d['presupuesto']
        assert d['pregunta'] == 'categoria'

    def test_un_ingreso_no_pregunta_nunca(self, cx, uid):
        """En Firefly un presupuesto es de GASTOS. Ponerselo a un abono de
        tarjeta contaria el gasto dos veces, asi que no se pregunta."""
        d = _gasto(cx, uid, 'Abono', valor=113943.0)
        assert d['pregunta'] is None

    def test_sin_categoria_la_pregunta_sigue_siendo_de_categoria(self, cx, uid):
        """No se puede preguntar el presupuesto de algo que no esta
        clasificado: primero la categoria."""
        d = clasificador.clasificar(
            cx,
            uid,
            {
                'tipo': 'compra_tarjeta',
                'fecha': '2026-09-02',
                'instrumento': '2567',
                'clase_instrumento': 'tarjeta',
                'traslado_a': None,
                'contraparte': 'ALGO QUE NUNCA SE HA VISTO',
                'descripcion': 'ALGO QUE NUNCA SE HA VISTO',
                'valor': -50000.0,
            },
        )
        assert d['categoria'] is None
        assert d['pregunta'] == 'categoria'
