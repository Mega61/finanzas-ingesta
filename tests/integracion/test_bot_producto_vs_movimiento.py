"""Un producto de supermercado no es un movimiento del banco.

El reclamo: «me esta diciendo el bot que que es esto, fletes gravado comprado
una vez 9900, esto es meramente de mercado, y cuando le dije que es el costo de
domicilio me dice que si quiero cambiar la categoria de una compra».

Dos features en el mismo chat y sin frontera:

- «FLETES GRAVADO» es una LINEA DE FACTURA del super. La pregunta mandaba
  botones y NO registraba su mensaje, asi que contestar por escrito no tenia a
  donde llegar.
- El ruteo por el modelo agarraba ese texto y lo llevaba al camino de editar
  TRANSACCIONES, que le ofrecio cambiarle la categoria a una compra del banco.
"""

from __future__ import annotations

import sqlite3
import types

import pytest

from finanzas.adaptadores import db
from finanzas.adaptadores.almacen import Almacen
from finanzas.aplicacion import catalogo, movimientos
from finanzas.entrada import bot


class TelegramFalso:
    class TelegramError(Exception):
        pass

    def __init__(self):
        self.enviados: list[str] = []
        self.editados: list[str] = []
        self.avisos: list[str] = []
        self._id = 5000

    def enviar(self, chat, texto, botones=None, modo='HTML'):
        self.enviados.append(texto)
        self._id += 1
        return {'message_id': self._id}

    def editar(self, chat, message_id, texto, modo='HTML'):
        self.editados.append(texto)
        return {'message_id': message_id}

    def responder_callback(self, cq_id, texto=None, alerta=False):
        self.avisos.append(texto or '')

    @property
    def todo(self):
        return ' '.join(self.enviados + self.editados)


@pytest.fixture
def entorno(monkeypatch):
    cx = sqlite3.connect(':memory:')
    cx.row_factory = sqlite3.Row
    cx.execute('PRAGMA foreign_keys = ON')
    alm = Almacen(cx)
    alm.inicializar(db.ESQUEMA)
    alm.guardar_usuario('Juan', 'u', 't', '555')
    cx.execute(
        'INSERT INTO catalogo (nit, codigo, descripcion, grupo, categoria, tipo)'
        " VALUES ('890900608', '9900001', 'FLETES GRAVADO', 'Sin clasificar',"
        " 'Sin clasificar', 'Sin clasificar')"
    )
    cx.commit()
    cat_id = cx.execute('SELECT rowid FROM catalogo').fetchone()[0]

    tg = TelegramFalso()
    monkeypatch.setattr(bot, 'telegram', tg)
    monkeypatch.setattr(
        bot,
        'config',
        types.SimpleNamespace(get=lambda k, d=None: '555' if 'CHAT' in k else d),
    )

    movs = {
        '1459': {
            'id': '1459',
            'fecha': '2026-09-02',
            'valor': -185500.0,
            'moneda': 'COP',
            'descripcion': 'KAYBU SAS',
            'categoria': 'Regalos',
            'presupuesto': 'Vivir',
            'origen': 'VISA',
            'destino': 'KAYBU SAS',
            'etiquetas': [],
            'tipo': 'withdrawal',
            'notas': '',
            'partes': 1,
        }
    }
    tocadas = []
    monkeypatch.setattr(
        movimientos, 'ultimos', lambda limite=15, dias=35: list(movs.values())
    )
    monkeypatch.setattr(movimientos, 'uno', lambda t: movs.get(str(t)))
    monkeypatch.setattr(
        movimientos, 'categorias', lambda d=None: ['Compras', 'Mercado', 'Regalos']
    )
    monkeypatch.setattr(movimientos, 'etiquetas_mas_usadas', lambda limite=24: ['Uber'])
    monkeypatch.setattr(
        bot.presupuestos, 'nombres_activos', lambda: ['Esencial', 'Vivir', 'Antojos']
    )
    monkeypatch.setattr(
        movimientos,
        'editar',
        lambda tid, **c: tocadas.append((tid, c)) or movs[str(tid)],
    )
    monkeypatch.setattr(
        movimientos,
        'editar_varios',
        lambda ids, **c: [
            {'id': i, 'movimiento': tocadas.append((i, c)) or movs[str(i)]} for i in ids
        ],
    )
    # Sin IA: se prueba el respaldo por reglas de palabras, que para «es el
    # costo de domicilio» acierta. El camino con modelo se prueba aparte.
    monkeypatch.setattr(bot.ia, 'disponible', lambda: False)
    return alm, tg, cat_id, tocadas


def _msg(texto, responde_a=None):
    m = {'message_id': 5, 'chat': {'id': '555'}, 'text': texto}
    if responde_a:
        m['reply_to_message'] = {'message_id': responde_a}
    return {'message': m}


def _mensaje_del_producto(alm):
    fila = alm.cx.execute('SELECT mensaje_id FROM preguntas_producto').fetchone()
    return fila['mensaje_id'] if fila else None


class TestLaPreguntaSeAta:
    def test_la_pregunta_registra_su_mensaje(self, entorno):
        """Sin esto no habia forma de saber que una respuesta era del producto."""
        alm, _tg, cat_id, _t = entorno
        bot.preguntar_productos(alm.cx, limite=1)
        assert alm.producto_de_mensaje('555', _mensaje_del_producto(alm)) == cat_id

    def test_la_pregunta_dice_que_se_puede_escribir(self, entorno):
        """Los botones son los diez grupos; el detalle no cabe en botones."""
        alm, tg, _c, _t = entorno
        bot.preguntar_productos(alm.cx, limite=1)
        assert 'respóndeme' in tg.todo


class TestResponderPorEscrito:
    def test_contestar_al_mensaje_clasifica_el_producto(self, entorno):
        alm, _tg, cat_id, tocadas = entorno
        bot.preguntar_productos(alm.cx, limite=1)
        bot.manejar_update(
            alm.cx,
            _msg('es el costo de domicilio', responde_a=_mensaje_del_producto(alm)),
        )
        p = alm.catalogo_por_id(cat_id)
        assert (p['grupo'], p['categoria']) == ('Servicios', 'Domicilio')
        assert tocadas == [], 'NO puede tocar ninguna transaccion'

    def test_no_ofrece_cambiar_la_categoria_de_una_compra(self, entorno):
        """El sintoma exacto del reclamo."""
        alm, tg, _c, _t = entorno
        bot.preguntar_productos(alm.cx, limite=1)
        tg.enviados.clear()
        bot.manejar_update(
            alm.cx,
            _msg('es el costo de domicilio', responde_a=_mensaje_del_producto(alm)),
        )
        assert 'KAYBU' not in tg.todo, 'no puede hablar de un movimiento del banco'
        assert 'Regalos' not in tg.todo

    def test_lo_guardado_queda_como_del_usuario(self, entorno):
        """`origen='usuario'` es lo que evita que una regla lo vuelva a pisar."""
        alm, _tg, cat_id, _t = entorno
        bot.preguntar_productos(alm.cx, limite=1)
        bot.manejar_update(
            alm.cx,
            _msg('es el costo de domicilio', responde_a=_mensaje_del_producto(alm)),
        )
        assert alm.catalogo_por_id(cat_id)['origen'] == 'usuario'

    def test_algo_que_no_se_entiende_no_inventa(self, entorno):
        alm, tg, cat_id, _t = entorno
        bot.preguntar_productos(alm.cx, limite=1)
        bot.manejar_update(
            alm.cx, _msg('ehhh no sé', responde_a=_mensaje_del_producto(alm))
        )
        assert alm.catalogo_por_id(cat_id)['grupo'] == 'Sin clasificar'
        assert 'botones' in tg.todo

    def test_un_producto_que_ya_no_esta_avisa(self, entorno):
        alm, tg, _c, _t = entorno
        alm.guardar_mensaje_producto('555', 4242, 9999)
        bot.manejar_update(alm.cx, _msg('es domicilio', responde_a=4242))
        assert 'ya no está' in tg.todo


class TestElFleteNoEsUnEmpaque:
    """Un flete de 9.900 es el costo de que traigan el mercado. Metido en
    «Bolsas y empaques» el dashboard cuenta empaques que nadie compro."""

    def test_servicios_tiene_domicilio(self):
        assert 'Domicilio' in catalogo.CATEGORIAS['Servicios']

    @pytest.mark.parametrize(
        'desc',
        [
            'FLETES GRAVADO',
            'FLETE',
            'DOMICILIO EXITO',
            'domcilio',
        ],
    )
    def test_un_flete_va_a_domicilio(self, desc):
        _tipo, grupo, cat, _f = catalogo.clasificar('890900608', 'x', desc, 19)
        assert (grupo, cat) == ('Servicios', 'Domicilio')

    @pytest.mark.parametrize(
        'desc',
        [
            'BOLSA RECICLADA',
            'BOLSA REUTILIZABLE',
            'BOLSA PAPEL',
        ],
    )
    def test_una_bolsa_sigue_siendo_un_empaque(self, desc):
        _tipo, grupo, cat, _f = catalogo.clasificar('890900608', 'x', desc, 19)
        assert (grupo, cat) == ('Servicios', 'Bolsas y empaques')


class TestElParSiempreEsValido:
    def test_una_categoria_que_no_es_del_grupo_se_corrige(self, entorno):
        """El modelo puede cruzar grupo y categoria. Guardar un par invalido
        rompe el dashboard en silencio."""
        alm, _tg, cat_id, _t = entorno
        bot._clasificar_producto_del_plan(
            alm.cx,
            '555',
            {
                'producto_id': str(cat_id),
                'producto_grupo': 'Mascotas',
                'producto_categoria': 'Panaderia',
                'explicacion': 'a proposito mal',
            },
        )
        p = alm.catalogo_por_id(cat_id)
        assert p['grupo'] == 'Mascotas'
        assert p['categoria'] in catalogo.CATEGORIAS['Mascotas']

    def test_sin_grupo_no_guarda_nada(self, entorno):
        alm, _tg, cat_id, _t = entorno
        atendido = bot._clasificar_producto_del_plan(
            alm.cx, '555', {'producto_id': str(cat_id), 'producto_grupo': None}
        )
        assert atendido is False
        assert alm.catalogo_por_id(cat_id)['grupo'] == 'Sin clasificar'


class TestConElModelo:
    def test_el_plan_de_producto_se_ejecuta(self, entorno, monkeypatch):
        """Con IA, «es el costo de domicilio» suelto —sin responder a ningun
        mensaje— tambien tiene que llegar al producto, porque el modelo recibe
        los productos pendientes."""
        alm, _tg, cat_id, tocadas = entorno
        bot.preguntar_productos(alm.cx, limite=1)
        monkeypatch.setattr(bot.ia, 'disponible', lambda: True)
        monkeypatch.setattr(
            bot.ia,
            'entender_orden',
            lambda *a, **k: {
                'accion': 'clasificar_producto',
                'producto_id': str(cat_id),
                'producto_grupo': 'Servicios',
                'producto_categoria': 'Domicilio',
                'movimientos': [],
                'confianza': 0.95,
                'explicacion': 'es un domicilio',
            },
        )
        bot.manejar_update(alm.cx, _msg('eso es el costo del domicilio'))
        p = alm.catalogo_por_id(cat_id)
        assert (p['grupo'], p['categoria']) == ('Servicios', 'Domicilio')
        assert tocadas == []

    def test_el_modelo_recibe_los_productos_pendientes(self, entorno, monkeypatch):
        """Si no los recibe, no puede distinguir un producto de un movimiento."""
        alm, _tg, _c, _t = entorno
        bot.preguntar_productos(alm.cx, limite=1)
        visto = {}
        monkeypatch.setattr(bot.ia, 'disponible', lambda: True)

        def espia(texto, movs, cats, pres, etqs=(), **kw):
            visto.update(kw)
            return {'accion': 'nada', 'confianza': 0.1, 'explicacion': ''}

        monkeypatch.setattr(bot.ia, 'entender_orden', espia)
        bot.manejar_update(alm.cx, _msg('algo'))
        assert visto.get('productos'), 'tiene que ver los productos pendientes'
        assert visto['productos'][0]['descripcion'] == 'FLETES GRAVADO'
        assert visto.get('grupos_producto'), 'y los grupos validos'
