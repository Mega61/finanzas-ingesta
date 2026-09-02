"""El bot preguntando por productos de supermercado.

Lo que se prueba aqui no es que se manden mensajes, sino las dos propiedades
que hacen que el flujo sirva:

1. Se pregunta por PRODUCTO, no por linea de factura, y la respuesta se aplica
   hacia atras: la compra de hace seis meses cambia de categoria igual que la
   de ayer. Eso solo funciona si la categoria vive en el catalogo y las lineas
   la leen con un join.
2. Una respuesta del usuario no la pisa ninguna regla automatica, ni siquiera
   cuando se manda a reclasificar todo a proposito.
"""

import sqlite3
from pathlib import Path

import pytest

from finanzas.adaptadores import db
from finanzas.adaptadores.almacen import Almacen
from finanzas.aplicacion import catalogo, facturas
from finanzas.entrada import bot

ESQUEMA = Path(__file__).resolve().parents[2] / 'src' / 'finanzas' / 'esquema.sql'


@pytest.fixture
def cx():
    cn = sqlite3.connect(':memory:')
    cn.row_factory = sqlite3.Row
    cn.executescript(ESQUEMA.read_text(encoding='utf-8'))
    yield cn
    cn.close()


def _sembrar(cx, veces=3):
    """Una factura con el mismo producto comprado `veces` meses seguidos."""
    for i in range(veces):
        cufe = f'CUFE{i}'
        cx.execute(
            """INSERT INTO facturas (cufe, nit, proveedor, numero, tipo, signo,
                  fecha, total) VALUES (?, '890900608', 'EXITO', ?, 'factura',
                  1, ?, 10000)""",
            (cufe, f'F{i}', f'2026-0{i + 1}-15'),
        )
        cx.execute(
            """INSERT INTO factura_lineas (cufe, n, nit, codigo, descripcion,
                  cantidad, precio_unitario, total, fecha)
               VALUES (?, 1, '890900608', 'XYZ999', 'Cosa Rarisima', 1, 10000,
                       10000, ?)""",
            (cufe, f'2026-0{i + 1}-15'),
        )
    cx.commit()


class ChatFalso:
    """Telegram de mentiras: guarda lo que se mandaria."""

    def __init__(self):
        self.enviados = []
        self.editados = []
        self.avisos = []

    def enviar(self, chat, texto, botones=None, modo='HTML'):
        self.enviados.append((texto, botones))
        return {'message_id': len(self.enviados)}

    def editar(self, chat, mid, texto, botones=None, modo='HTML'):
        self.editados.append((texto, botones))
        return {'message_id': mid}

    def responder_callback(self, cq_id, texto=''):
        self.avisos.append(texto)


@pytest.fixture
def falso(monkeypatch):
    f = ChatFalso()
    monkeypatch.setattr(bot.telegram, 'enviar', f.enviar)
    monkeypatch.setattr(bot.telegram, 'editar', f.editar)
    monkeypatch.setattr(bot.telegram, 'responder_callback', f.responder_callback)
    monkeypatch.setattr(
        bot.config, 'get', lambda k, d=None: '123' if 'CHAT' in k else d
    )
    return f


def _toque(cx, data):
    return bot.Toque(
        cx,
        {
            'id': 'cq1',
            'message': {'chat': {'id': '123'}, 'message_id': 1},
            'data': data,
        },
    )


def test_pregunta_por_producto_y_ofrece_los_grupos(cx, falso):
    _sembrar(cx)
    facturas.clasificar(cx)
    assert bot.preguntar_productos(cx) == 1

    texto, botones = falso.enviados[0]
    assert 'Cosa Rarisima' in texto
    # 3 compras, no 3 preguntas: se pregunta por producto.
    assert '3 veces' in texto
    planos = [b for fila in botones for b in fila]
    assert any(t == 'Alimentacion' for t, _ in planos)
    assert any(t.startswith('🤷') for t, _ in planos)


def test_la_respuesta_se_aplica_a_las_compras_viejas(cx, falso):
    _sembrar(cx)
    facturas.clasificar(cx)
    bot.preguntar_productos(cx)
    fila = Almacen(cx).catalogo_sin_clasificar(1)[0]

    # elige "Aseo y hogar" y despues "Limpieza"
    gidx = catalogo.GRUPOS.index('Aseo y hogar')
    bot.TOQUES['fg'](_toque(cx, f'fg:{fila["id"]}:{gidx}'))
    cidx = catalogo.CATEGORIAS['Aseo y hogar'].index('Limpieza')
    bot.TOQUES['fc'](_toque(cx, f'fc:{fila["id"]}:{gidx * 100 + cidx}'))

    p = db.catalogo_por_id(cx, fila['id'])
    assert (p['tipo'], p['grupo'], p['categoria']) == (
        'Consumible',
        'Aseo y hogar',
        'Limpieza',
    )
    assert p['origen'] == 'usuario'

    # Las TRES lineas quedan clasificadas, no solo la ultima: la categoria se
    # lee del catalogo con un join, nunca copiada en la linea.
    n = cx.execute(
        """SELECT COUNT(*) FROM factura_lineas l
           JOIN catalogo c ON c.nit = l.nit AND c.codigo = l.codigo
           WHERE c.categoria = 'Limpieza'"""
    ).fetchone()[0]
    assert n == 3


def test_reclasificar_no_pisa_lo_que_respondio_el_usuario(cx, falso):
    _sembrar(cx)
    facturas.clasificar(cx)
    fila = Almacen(cx).catalogo_sin_clasificar(1)[0]
    gidx = catalogo.GRUPOS.index('Licores')
    bot.TOQUES['fg'](_toque(cx, f'fg:{fila["id"]}:{gidx}'))

    # una categoria sola: se cierra sin segunda pregunta
    p = db.catalogo_por_id(cx, fila['id'])
    assert p['grupo'] == 'Licores'

    facturas.clasificar(cx, solo_nuevos=False)
    p = db.catalogo_por_id(cx, fila['id'])
    assert p['grupo'] == 'Licores', 'una regla automatica piso al usuario'


def test_saltar_lo_devuelve_a_la_cola_despues(cx, falso):
    _sembrar(cx)
    facturas.clasificar(cx)
    fila = Almacen(cx).catalogo_sin_clasificar(1)[0]
    bot.TOQUES['fx'](_toque(cx, f'fx:{fila["id"]}:0'))

    p = db.catalogo_por_id(cx, fila['id'])
    assert p['grupo'] == 'Sin clasificar'
    assert p['preguntado_en'] is not None
    # recien preguntado: no vuelve a salir de una
    assert Almacen(cx).catalogo_por_preguntar(5) == []


def test_no_se_repite_la_pregunta_en_la_siguiente_pasada(cx, falso):
    _sembrar(cx)
    facturas.clasificar(cx)
    assert bot.preguntar_productos(cx) == 1
    assert bot.preguntar_productos(cx) == 0
