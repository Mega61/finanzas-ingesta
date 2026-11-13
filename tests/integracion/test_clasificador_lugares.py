"""El centro comercial no es el comercio.

Noviembre de 2026, todo de la Mastercard, todo publicado en Firefly:

    MINISO VIVA ENVIGADO   39.900   -> 'Viva Envigado', Transporte Moto
    TIENDA D1 SAB AVES M    7.800   -> 'Aves Maria',    Transporte Moto
    MCDONAL D NVIGA O      29.800   -> sin categoria
    IKEA ENVIGADO         209.910   -> sin categoria
    HOMECENTER ENVIGADO   126.500   -> sin categoria

No fue la IA: Gemini no toca la clasificacion de una alerta. Fue el
emparejador de palabras del clasificador contra los nombres de las cuentas de
gasto de Firefly. 'Viva Envigado' y 'Aves Maria' son los parqueaderos de la
moto, y el banco pega el centro comercial detras de la marca:

  - 'VIVA ENVIGADO' esta DENTRO de 'MINISO VIVA ENVIGADO' (subcadena, 0.88).
  - 'TIENDA D1 SAB AVES M' comparte AVES con 'AVES MARIA' (palabra, 0.80), y
    la cuenta 'D1' no tiene regla propia: el sembrador no guarda claves de 2
    letras.
  - 'HOMECENTER ENVIGADO' comparte ENVIGADO con 'VIVA ENVIGADO'.

Y el pago del parqueadero ('THE PARKING VIVA ENV') SI es Transporte Moto: el
unico caso donde el lugar es el comercio.
"""

from __future__ import annotations

import sqlite3

import pytest

from finanzas.adaptadores import db
from finanzas.adaptadores.almacen import Almacen
from finanzas.aplicacion import clasificador, presupuestos
from finanzas.dominio import texto

# (cuenta de gasto de Firefly, categoria, aciertos), como las deja el sembrador
CUENTAS = [
    ('Viva Envigado', 'Transporte Moto', 18),
    ('Aves Maria', 'Transporte Moto', 15),
    ('D1', 'Mercado', 5),
    ('Grupo Éxito', 'Mercado', 9),
    ('Amare Home', 'Compras Casa', 3),
    ('Crepes & Waffles', 'Restaurante', 4),
    ('Viva Aerobus', 'Viajes', 1),
]


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


@pytest.fixture(autouse=True)
def _sin_red(monkeypatch):
    monkeypatch.setattr(
        clasificador, 'cuenta_de_instrumento', lambda inst, fecha: 'MASTERCARD BLACK'
    )
    monkeypatch.setattr(
        presupuestos,
        'mapa_categoria',
        lambda **k: {
            c: {'presupuesto': 'Esencial', 'seguro': True, 'reparto': {}}
            for c in ('Mercado', 'Transporte Moto', 'Compras Casa', 'Restaurante')
        },
    )
    monkeypatch.setattr(presupuestos, 'nombres_activos', lambda: ['Esencial'])


def _sembrar(cx, uid, cuentas=CUENTAS):
    for cuenta, categoria, aciertos in cuentas:
        db.regla_guardar(
            cx,
            uid,
            clasificador.normalizar(cuenta),
            cuenta_firefly=cuenta,
            categoria=categoria,
            origen='comercio',
            direccion='gasto',
            aciertos=aciertos,
        )
    cx.commit()


def _clasificar(cx, uid, alerta, valor=-39900.0):
    return clasificador.clasificar(
        cx,
        uid,
        {
            'tipo': 'compra',
            'fecha': '2026-11-10',
            'instrumento': '1234',
            'clase_instrumento': 'tarjeta',
            'traslado_a': None,
            'contraparte': alerta,
            'descripcion': alerta,
            'valor': valor,
        },
    )


class TestElLugarNoHaceElComercio:
    def test_miniso_en_viva_no_es_parqueadero(self, cx, uid):
        _sembrar(cx, uid)
        d = _clasificar(cx, uid, 'MINISO VIVA ENVIGADO')
        assert d['cuenta_destino'] != 'Viva Envigado'
        assert d['categoria'] != 'Transporte Moto'
        assert d['pregunta'] == 'categoria', 'sin saber que es, se pregunta'
        assert d['confianza'] < clasificador.UMBRAL

    def test_d1_en_aves_maria_es_d1(self, cx, uid):
        _sembrar(cx, uid)
        d = _clasificar(cx, uid, 'TIENDA D1 SAB AVES M', valor=-7800.0)
        assert d['cuenta_destino'] == 'D1'
        assert d['categoria'] == 'Mercado'
        assert d['presupuesto'] == 'Esencial'

    def test_homecenter_en_envigado_no_es_parqueadero(self, cx, uid):
        _sembrar(cx, uid)
        d = _clasificar(cx, uid, 'HOMECENTER ENVIGADO', valor=-126500.0)
        assert d['cuenta_destino'] != 'Viva Envigado'
        assert d['categoria'] == 'Compras Casa'
        # la tabla de marcas no es una respuesta tuya: igual se pregunta
        assert d['pregunta'] == 'categoria'

    def test_el_parqueadero_del_viva_si_es_transporte_moto(self, cx, uid):
        _sembrar(cx, uid)
        d = _clasificar(cx, uid, 'THE PARKING VIVA ENV', valor=-6000.0)
        assert d['cuenta_destino'] == 'Viva Envigado'
        assert d['categoria'] == 'Transporte Moto'

    def test_el_parqueadero_de_aves_maria(self, cx, uid):
        _sembrar(cx, uid)
        d = _clasificar(cx, uid, 'PARQUEADERO AVES MARI', valor=-4000.0)
        assert d['cuenta_destino'] == 'Aves Maria'

    def test_la_regla_exacta_del_sitio_sigue_mandando(self, cx, uid):
        _sembrar(cx, uid)
        d = _clasificar(cx, uid, 'VIVA ENVIGADO', valor=-6000.0)
        assert d['cuenta_destino'] == 'Viva Envigado'
        assert d['confianza'] >= 0.9

    def test_una_marca_que_empieza_con_un_lugar_sigue_siendo_marca(self, cx, uid):
        _sembrar(cx, uid)
        d = _clasificar(cx, uid, 'VIVA AEROBUS COM', valor=-300000.0)
        assert d['cuenta_destino'] == 'Viva Aerobus'


class TestMarcasConocidas:
    def test_mcdonalds_mutilado_por_el_banco(self, cx, uid):
        _sembrar(cx, uid)
        d = _clasificar(cx, uid, 'MCDONAL D NVIGA O', valor=-29800.0)
        assert d['categoria'] == 'Restaurante'
        assert d['cuenta_destino'] == 'McDonalds'
        assert d['decidido_por'] == 'marca'
        assert d['pregunta'] == 'categoria'

    def test_ikea_sin_regla(self, cx, uid):
        _sembrar(cx, uid)
        d = _clasificar(cx, uid, 'IKEA ENVIGADO', valor=-209910.0)
        assert d['categoria'] == 'Compras Casa'
        assert d['cuenta_destino'] != 'Viva Envigado'

    def test_ikea_con_regla_de_categoria_retirada(self, cx, uid):
        """La cuenta 'Ikea' quedo aprendida con 'Compras', que se retiro: la
        regla da la cuenta y la tabla la categoria probable."""
        _sembrar(cx, uid, [*CUENTAS, ('Ikea', 'Compras', 0)])
        d = _clasificar(cx, uid, 'IKEA ENVIGADO', valor=-209910.0)
        assert d['cuenta_destino'] == 'Ikea'
        assert d['categoria'] == 'Compras Casa'
        assert d['pregunta'] == 'categoria'

    def test_la_regla_aprendida_le_gana_a_la_tabla(self, cx, uid):
        _sembrar(cx, uid, [*CUENTAS, ('Macdonalds', 'Mecato', 2)])
        d = _clasificar(cx, uid, 'MCDONAL D NVIGA O', valor=-29800.0)
        assert d['cuenta_destino'] == 'Macdonalds'
        assert d['categoria'] == 'Mecato'

    def test_home_center_con_espacio_es_el_mismo(self, cx, uid):
        _sembrar(cx, uid, [*CUENTAS, ('Home Center', 'Compras Casa', 2)])
        d = _clasificar(cx, uid, 'HOMECENTER ENVIGADO', valor=-126500.0)
        assert d['cuenta_destino'] == 'Home Center'

    def test_no_inventa_una_categoria_que_no_usas(self, cx, uid):
        _sembrar(cx, uid, [c for c in CUENTAS if c[1] != 'Restaurante'])
        d = _clasificar(cx, uid, 'MCDONAL D NVIGA O', valor=-29800.0)
        assert d['categoria'] is None
        assert d['pregunta'] == 'categoria'


class TestMarcaYLugar:
    @pytest.mark.parametrize(
        ('alerta', 'esperada'),
        [
            ('MINISO VIVA ENVIGADO', ['MINISO']),
            ('TIENDA D1 SAB AVES M', ['TIENDA', 'D1']),
            ('THE PARKING VIVA ENV', ['THE', 'PARKING']),
            ('HOMECENTER ENVIGADO', ['HOMECENTER']),
            ('STARBUCKS VIVA ENVIG', ['STARBUCKS']),
            ('VIVA AEROBUS', ['VIVA', 'AEROBUS']),
            ('DROGUERIA ALEMANA', ['DROGUERIA', 'ALEMANA']),
        ],
    )
    def test_marca(self, alerta, esperada):
        assert texto.marca(texto.normalizar(alerta)) == esperada

    def test_una_cuenta_que_es_solo_lugar_no_tiene_nucleo(self):
        assert texto.nucleo('VIVA ENVIGADO') == set()
        assert texto.nucleo('AVES MARIA') == set()
        assert texto.nucleo('GRUPO EXITO') == {'GRUPO', 'EXITO'}
