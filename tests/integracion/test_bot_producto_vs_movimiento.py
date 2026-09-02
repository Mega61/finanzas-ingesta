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
        self.botones: list[list] = []
        self._id = 5000

    def enviar(self, chat, texto, botones=None, modo='HTML'):
        self.enviados.append(texto)
        self.botones += list(botones or [])
        self._id += 1
        return {'message_id': self._id}

    def editar(self, chat, message_id, texto, botones=None, modo='HTML'):
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


def _cb(dato):
    return {
        'callback_query': {
            'id': 'q',
            'data': dato,
            'message': {'message_id': 5, 'chat': {'id': '555'}},
        }
    }


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


class TestLaFronteraEntreLosDosMundos:
    """Con productos de factura esperando, una respuesta que no nombra ninguna
    transaccion no se puede adivinar.

    Adivinando, «es shampoo» acababa escrito en la compra de Google Workspace Y
    aprendido como REGLA permanente: «GOOGLE WORKSPACE GO -> Cuidado personal».
    O sea que el bot aprendia para siempre que Google Workspace es shampoo.
    Preguntar cuesta un mensaje; equivocarse cuesta una regla que envenena todo
    lo que entre despues.
    """

    def _con_pregunta_abierta(self, alm, uid):
        bid = alm.guardar_buzon(uid, 'graph', 'j@e.com')
        cid, _ = alm.guardar_correo(bid, '<m>', 'banco', 'Alerta', '2026-09-02', 'x')
        pid, _ = alm.crear_pendiente(
            correo_id=cid,
            usuario_id=uid,
            tipo='compra_tarjeta',
            fecha='2026-09-02',
            valor=-63000.0,
            moneda='COP',
            contraparte='GOOGLE *Workspace_go',
            descripcion='GOOGLE *Workspace_go',
            categoria='GBS Infra',
            estado='publicado',
            pregunta='categoria',
            external_id='bc-google',
        )
        alm.actualizar_pendiente(pid, firefly_id='1459')
        alm.marcar_preguntado(pid)
        alm.cx.commit()
        return pid

    def test_no_escribe_en_una_transaccion_ni_aprende_una_regla(self, entorno):
        alm, tg, _c, tocadas = entorno
        uid = alm.usuario_por_nombre('Juan')['id']
        pid = self._con_pregunta_abierta(alm, uid)
        bot.preguntar_productos(alm.cx, limite=1)
        tg.enviados.clear()

        bot.manejar_update(alm.cx, _msg('es shampoo'))

        assert tocadas == [], 'no puede tocar ninguna transaccion'
        assert alm.cx.execute('SELECT count(*) FROM reglas').fetchone()[0] == 0, (
            'ni aprender una regla'
        )
        fila = alm.cx.execute(
            'SELECT pregunta FROM pendientes WHERE id = ?', (pid,)
        ).fetchone()
        assert fila['pregunta'] == 'categoria', 'ni cerrar la pregunta equivocada'

    def test_pregunta_de_cual_de_los_dos_mundos_habla(self, entorno):
        alm, tg, _c, _t = entorno
        uid = alm.usuario_por_nombre('Juan')['id']
        self._con_pregunta_abierta(alm, uid)
        bot.preguntar_productos(alm.cx, limite=1)
        tg.enviados.clear()

        bot.manejar_update(alm.cx, _msg('es shampoo'))

        assert 'producto del mercado' in tg.todo
        datos = [d for fila in tg.botones for _, d in fila]
        assert any(d.startswith('fp:') for d in datos), 'un boton por producto'
        assert any(d.startswith('m:') for d in datos), 'y uno por la transaccion'

    def test_el_boton_del_producto_aplica_lo_que_escribio(self, entorno):
        """El texto no cabe en el callback -- Telegram admite 64 bytes -- asi
        que queda guardado por chat y el boton solo dice a cual de los dos iba.
        """
        alm, _tg, cat_id, tocadas = entorno
        uid = alm.usuario_por_nombre('Juan')['id']
        self._con_pregunta_abierta(alm, uid)
        bot.preguntar_productos(alm.cx, limite=1)
        bot.manejar_update(alm.cx, _msg('es el costo del domicilio'))

        bot.manejar_update(alm.cx, _cb(f'fp:{cat_id}:0'))

        p = alm.catalogo_por_id(cat_id)
        assert (p['grupo'], p['categoria']) == ('Servicios', 'Domicilio')
        assert tocadas == []

    def test_sin_productos_esperando_sigue_el_camino_de_siempre(self, entorno):
        """La frontera solo aplica cuando de verdad hay dos mundos abiertos."""
        alm, tg, _c, _t = entorno
        uid = alm.usuario_por_nombre('Juan')['id']
        self._con_pregunta_abierta(alm, uid)
        tg.enviados.clear()

        bot.manejar_update(alm.cx, _msg('es shampoo'))

        assert 'producto del mercado' not in tg.todo


class TestElGrupoTieneQueExistir:
    """Se validaba la CATEGORIA contra el grupo pero nunca el GRUPO contra el
    catalogo. Con uno inventado -- «Mercado», que es el nombre de una categoria
    de MOVIMIENTOS y por eso el error mas probable -- la lista de categorias
    validas quedaba vacia y se guardaba grupo=categoria=Mercado. Y como queda
    con origen='usuario', ninguna regla lo vuelve a pisar: el dashboard se
    rompia en silencio y para siempre."""

    def test_un_grupo_inventado_no_se_guarda(self, entorno):
        alm, _tg, cat_id, _t = entorno
        bot._clasificar_producto_del_plan(
            alm.cx,
            '555',
            {
                'producto_id': str(cat_id),
                'producto_grupo': 'Mercado',
                'producto_categoria': 'Mercado',
                'explicacion': 'a proposito mal',
            },
        )
        assert alm.catalogo_por_id(cat_id)['grupo'] == 'Sin clasificar'

    def test_y_se_vuelve_a_preguntar_diciendo_por_que(self, entorno):
        alm, tg, cat_id, _t = entorno
        bot._clasificar_producto_del_plan(
            alm.cx,
            '555',
            {
                'producto_id': str(cat_id),
                'producto_grupo': 'Mercado',
                'producto_categoria': 'Mercado',
            },
        )
        assert 'No tengo un grupo que se llame' in tg.todo

    def test_una_categoria_cruzada_se_corrige_y_se_dice(self, entorno):
        """Guardar otra categoria callado es lo mismo que mentir."""
        alm, tg, cat_id, _t = entorno
        bot._clasificar_producto_del_plan(
            alm.cx,
            '555',
            {
                'producto_id': str(cat_id),
                'producto_grupo': 'Mascotas',
                'producto_categoria': 'Panaderia',
            },
        )
        p = alm.catalogo_por_id(cat_id)
        assert p['grupo'] == 'Mascotas'
        assert p['categoria'] in catalogo.CATEGORIAS['Mascotas']
        assert 'no es una categoría de Mascotas' in tg.todo


class TestLaConfianzaTambienCuentaParaLosProductos:
    """Era asimetrico al reves de como deberia: editar una transaccion pedia
    0.75 y se puede deshacer; un producto no pedia nada y no lo vuelve a tocar
    ninguna regla."""

    def test_con_confianza_baja_propone_en_vez_de_guardar(self, entorno):
        alm, tg, cat_id, _t = entorno
        bot._clasificar_producto_del_plan(
            alm.cx,
            '555',
            {
                'producto_id': str(cat_id),
                'producto_grupo': 'Servicios',
                'producto_categoria': 'Domicilio',
            },
            confianza=0.6,
        )
        assert alm.catalogo_por_id(cat_id)['grupo'] == 'Sin clasificar'
        assert '¿' in tg.todo
        datos = [d for fila in tg.botones for _, d in fila]
        assert any(d.startswith('fc:') for d in datos), 'con un boton para decir si'

    def test_el_boton_de_confirmar_apunta_al_par_correcto(self, entorno):
        alm, tg, cat_id, _t = entorno
        bot._clasificar_producto_del_plan(
            alm.cx,
            '555',
            {
                'producto_id': str(cat_id),
                'producto_grupo': 'Servicios',
                'producto_categoria': 'Domicilio',
            },
            confianza=0.6,
        )
        dato = next(
            d for fila in tg.botones for _, d in fila if d.startswith('fc:')
        )
        bot.manejar_update(alm.cx, _cb(dato))
        p = alm.catalogo_por_id(cat_id)
        assert (p['grupo'], p['categoria']) == ('Servicios', 'Domicilio')

    def test_con_confianza_alta_guarda_directo(self, entorno):
        alm, _tg, cat_id, _t = entorno
        bot._clasificar_producto_del_plan(
            alm.cx,
            '555',
            {
                'producto_id': str(cat_id),
                'producto_grupo': 'Servicios',
                'producto_categoria': 'Domicilio',
            },
            confianza=0.95,
        )
        assert alm.catalogo_por_id(cat_id)['grupo'] == 'Servicios'


class TestElTextoDelUsuarioManda:
    """El respaldo por palabras mezclaba el TEXTO del usuario con el CODIGO del
    producto, asi que los OVERRIDES ganaban antes de mirar una sola palabra: se
    contestaba «eso es una cerveza» sobre un producto que esta en OVERRIDES
    como reloj y guardaba «Tecnologia» diciendo «por lo que escribiste: eso es
    una cerveza». Decir que hizo lo que le pidieron y guardar lo contrario es
    peor que no entender."""

    def test_el_override_no_pisa_lo_que_escribio(self):
        _t, grupo, _c, _f = catalogo.clasificar_texto('eso es una cerveza')
        assert grupo != 'Tecnologia'

    def test_y_el_codigo_sigue_mandando_cuando_es_la_factura(self):
        """Sobre la LINEA de la factura si tiene que ganar el override."""
        _t, _g, _c, fuente = catalogo.clasificar(
            '890900608', '3730739', 'GALAXY WATCH', 19
        )
        assert fuente == 'override'
