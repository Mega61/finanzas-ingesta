"""/mercado y el bloque de sobres de /hoy, contra una base de verdad: las
lineas salen de factura_lineas, el grupo del catalogo y el signo de la
factura."""

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from finanzas.adaptadores.almacen import Almacen
from finanzas.aplicacion import disponible, sobres
from finanzas.entrada import bot

ESQUEMA = Path(__file__).resolve().parents[2] / 'src' / 'finanzas' / 'esquema.sql'
HOY = date(2026, 11, 13)
EXITO = '890900608'


@pytest.fixture
def cx():
    cn = sqlite3.connect(':memory:')
    cn.row_factory = sqlite3.Row
    cn.executescript(ESQUEMA.read_text(encoding='utf-8'))
    yield cn
    cn.close()


def _factura(cx, cufe, fecha, lineas, nit=EXITO, signo=1):
    cx.execute(
        """INSERT INTO facturas (cufe, nit, proveedor, numero, tipo, signo,
              fecha, total) VALUES (?, ?, 'EXITO', ?, ?, ?, ?, 0)""",
        (cufe, nit, cufe, 'factura' if signo == 1 else 'nota_credito', signo, fecha),
    )
    for n, (codigo, desc, total, grupo, categoria) in enumerate(lineas, 1):
        cx.execute(
            """INSERT INTO factura_lineas (cufe, n, nit, codigo, descripcion,
                  cantidad, precio_unitario, total, fecha)
               VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)""",
            (cufe, n, nit, codigo, desc, total, total, fecha),
        )
        if grupo:
            cx.execute(
                """INSERT OR REPLACE INTO catalogo (nit, codigo, descripcion,
                      grupo, categoria) VALUES (?, ?, ?, ?, ?)""",
                (nit, codigo, desc, grupo, categoria),
            )
    cx.commit()


def _sembrar(cx):
    a, c = 'Alimentacion', 'Carnes y pollo'
    _factura(cx, 'OCT', '2026-10-20', [('1', 'Lomo Fino', 130_000, a, c)])
    _factura(
        cx,
        'NOV1',
        '2026-11-03',
        [
            ('2', 'Pechuga & Muslo', 152_560, a, c),
            ('3', 'Aguacate Hass', 48_000, a, 'Frutas y verduras'),
            ('4', 'Queso Campesino', 210_000, a, 'Lacteos y huevos'),
            ('5', 'Detergente', 35_000, 'Aseo y hogar', 'Limpieza'),
            ('6', 'Licuadora', 400_000, 'Electrodomesticos', 'Electrodomesticos'),
            ('7', 'Cosa Rarisima', 8_000, None, None),
        ],
    )
    # devolvieron parte del queso
    _factura(
        cx,
        'NC1',
        '2026-11-11',
        [('4', 'Queso Campesino', 5_000, None, None)],
        signo=-1,
    )
    # el gimnasio factura al mismo buzon: no es del super
    _factura(
        cx,
        'GYM',
        '2026-11-12',
        [('8', 'Plan de salud', 70_000, a, c)],
        nit='901234567',
    )
    Almacen(cx).guardar_usuario('Juan', 'https://f', 'tok', '555')


@pytest.fixture
def enviados(monkeypatch):
    lista = []
    monkeypatch.setattr(
        bot.telegram, 'enviar', lambda chat, texto, *a, **k: lista.append(texto)
    )
    monkeypatch.setattr(bot.sobres._fechas, 'hoy', lambda tz=None: HOY)
    return lista


def test_las_lineas_salen_del_almacen_solo_de_los_supermercados(cx):
    _sembrar(cx)
    x = sobres.leer(cx, HOY)
    carne = next(o for o in x.sobres if o.definicion is sobres.CARNE)
    assert carne.gastado == 152_560, 'la factura del gimnasio no es carne'
    assert carne.anterior == 130_000
    alacena = next(o for o in x.sobres if o.definicion is sobres.ALACENA)
    assert alacena.gastado == 210_000 - 5_000 + 8_000
    assert alacena.sin_clasificar == 1
    assert x.ultima_factura == date(2026, 11, 11)


def test_mercado(cx, enviados):
    _sembrar(cx)
    bot.COMANDOS['/mercado'](cx, '555', '/mercado')
    (t,) = enviados
    assert '🥩 Carne 152.560 / 200.000 · quedan 47.440' in t
    assert '🥫 Alacena 213.000 / 200.000 ⚠️ · te pasaste por 13.000' in t
    assert 'Pechuga &amp; Muslo 152.560' in t, 'lo del super va escapado'
    assert 'Licuadora' not in t
    assert 'facturas hasta el 11-nov' in t
    assert '🥩 Carne 130.000 / 200.000' in t.split('Quincena pasada')[1]


def test_mercado_es_de_juan(cx, enviados):
    Almacen(cx).guardar_usuario('Mariana', 'https://f', 'tok', '777')
    bot.COMANDOS['/mercado'](cx, '777', '/mercado')
    assert enviados == ['Esto todavía es solo de Juan.']


def _sin_firefly(monkeypatch):
    x = disponible.calcular(HOY, [], [], 0)
    monkeypatch.setattr(bot.disponible, 'leer', lambda cx=None: x)
    monkeypatch.setattr(bot.db, 'resumen', lambda cx, uid: [])


def test_hoy_trae_los_sobres(cx, enviados, monkeypatch):
    _sembrar(cx)
    _sin_firefly(monkeypatch)
    bot.cmd_hoy(cx, '555')
    (t,) = enviados
    assert t.startswith('💸')
    assert '🧺 <b>Sobres</b> · 1–15 nov' in t  # noqa: RUF001
    assert '🥩 Carne 152.560 / 200.000\n' in t
    assert '🥫 Alacena 213.000 / 200.000 ⚠️' in t
    assert '🧴 Aseo y casa 35.000 / 100.000 al mes' in t
    assert 'facturas hasta el 11-nov · 1 sin clasificar en Alacena' in t


def test_si_los_sobres_fallan_hoy_llega_igual(cx, enviados, monkeypatch):
    Almacen(cx).guardar_usuario('Juan', 'https://f', 'tok', '555')
    _sin_firefly(monkeypatch)
    monkeypatch.setattr(bot.sobres, 'leer', lambda cx: 1 / 0)
    bot.cmd_hoy(cx, '555')
    (t,) = enviados
    assert 'Sobres' not in t and t.startswith('💸')
