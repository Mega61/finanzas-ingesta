"""El bot entiende la orden con el modelo, no con expresiones regulares.

«lo de los botones no tiene logica, es mejor yo decirle que es y que el agente
lo infiera. ¿tenemos que mejorar el modelo de gemini? cual es el cuello de
botella».

El cuello de botella no era el modelo: era que no se le estaba preguntando. A
Gemini solo se le pedia «¿que categoria?» sobre UN movimiento, y con el
comercio restringido a un enum de los que ya existian —asi que ni podia
proponer un nombre nuevo—. Todo lo demas lo decidian patrones: a cual te
referias, si era pregunta o respuesta, las etiquetas, el plural, los
presupuestos. Cada fallo del bot salio de ahi.

Aqui se verifica el camino nuevo con el plan del modelo simulado. Que el modelo
DE VERDAD produzca esos planes se comprueba a mano contra Gemini; lo que se
prueba aqui es que el bot ejecute el plan correctamente, que es lo que puede
romperse en silencio.
"""

from __future__ import annotations

import sqlite3

import pytest

from finanzas.adaptadores import db
from finanzas.adaptadores.almacen import Almacen
from finanzas.aplicacion import movimientos
from finanzas.entrada import bot


def _tx(tx_id, monto, destino, categoria, presupuesto=None, etiquetas=None):
    return {
        'id': str(tx_id),
        'attributes': {
            'transactions': [
                {
                    'date': '2026-09-02T00:00:00+02:00',
                    'amount': f'{abs(monto)}.0',
                    'type': 'withdrawal' if monto < 0 else 'deposit',
                    'description': destino,
                    'category_name': categoria,
                    'budget_name': presupuesto,
                    'source_name': 'VISA BLACK',
                    'destination_name': destino,
                    'tags': list(etiquetas or ['sin-confirmar']),
                    'currency_code': 'COP',
                    'notes': '',
                }
            ]
        },
    }


class TelegramFalso:
    class TelegramError(Exception):
        pass

    def __init__(self):
        self.enviados: list[str] = []
        self.editados: list[str] = []
        self.avisos: list[str] = []
        self.botones: list[list] = []
        self._id = 4000

    def enviar(self, chat, texto, botones=None, modo='HTML'):
        self.enviados.append(texto)
        if botones:
            self.botones.append(botones)
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

    def datos(self):
        return [d for tanda in self.botones for fila in tanda for _, d in fila]


@pytest.fixture
def entorno(monkeypatch):
    cx = sqlite3.connect(':memory:')
    cx.row_factory = sqlite3.Row
    cx.execute('PRAGMA foreign_keys = ON')
    alm = Almacen(cx)
    alm.inicializar(db.ESQUEMA)
    uid = alm.guardar_usuario('Juan', 'https://f', 'tok', '555')

    tg = TelegramFalso()
    monkeypatch.setattr(bot, 'telegram', tg)

    estado = {
        'txs': [
            _tx(1459, -185500, 'KAYBU SAS', 'Regalos', 'Vivir'),
            _tx(1458, -197800, 'MERCADO PAGO LIMITAD', 'Compras', 'Antojos'),
            _tx(1457, -194800, 'MERCADO PAGO LIMITAD', 'Compras', 'Antojos'),
            _tx(1441, -151495, 'Tierragro', 'Gato', 'Esencial'),
        ],
        'puts': [],
        'borrados': [],
    }

    def call(metodo, ruta, payload=None):
        if metodo == 'GET' and '/transactions/' in ruta:
            tid = ruta.rsplit('/', 1)[-1]
            for t in estado['txs']:
                if t['id'] == tid:
                    return {'data': t}
            raise movimientos.firefly.ApiError(404, 'no existe')
        if metodo == 'PUT':
            tid = ruta.rsplit('/', 1)[-1]
            estado['puts'].append((tid, payload))
            for t in estado['txs']:
                if t['id'] == tid:
                    t['attributes']['transactions'][0].update(
                        {
                            k: v
                            for k, v in payload['transactions'][0].items()
                            if k != 'transaction_journal_id'
                        }
                    )
            return {}
        if metodo == 'DELETE':
            estado['borrados'].append(ruta.rsplit('/', 1)[-1])
            return {}
        return {}

    def get_all(ruta):
        if '/categories' in ruta:
            return [
                {'attributes': {'name': c}}
                for c in ('Compras', 'Regalos', 'Gato', 'Ropa', 'Mercado')
            ]
        return estado['txs']

    monkeypatch.setattr(movimientos.firefly, 'get_all', get_all)
    monkeypatch.setattr(movimientos.firefly, 'call', call)
    monkeypatch.setattr(movimientos, 'etiquetas_mas_usadas', lambda limite=24: ['Uber'])
    monkeypatch.setattr(movimientos, 'categorias', lambda d=None: ['Compras', 'Ropa'])
    monkeypatch.setattr(
        bot.presupuestos, 'nombres_activos', lambda: ['Esencial', 'Vivir', 'Antojos']
    )
    monkeypatch.setattr(bot.ia, 'disponible', lambda: True)
    monkeypatch.setattr(
        bot,
        '_consultar_asesor',
        lambda cx, chat, txt: tg.enviados.append(f'[asesor] {txt}'),
    )

    def con_plan(plan):
        """Fija el plan que el modelo devolveria."""
        monkeypatch.setattr(bot.ia, 'entender_orden', lambda *a, **k: dict(plan))

    return alm, tg, estado, uid, con_plan


def _msg(texto):
    return {'message': {'message_id': 7, 'chat': {'id': '555'}, 'text': texto}}


def _tags_de(estado, tid):
    for t in estado['txs']:
        if t['id'] == tid:
            return t['attributes']['transactions'][0]['tags']
    return []


PLAN_BASE = {
    'accion': 'editar',
    'movimientos': [],
    'categoria': None,
    'presupuesto': None,
    'comercio': None,
    'etiquetas_agregar': [],
    'etiquetas_quitar': [],
    'confianza': 0.95,
    'explicacion': 'porque si',
}


class TestEjecutaElPlan:
    def test_etiqueta_varios_de_una(self, entorno):
        """La orden textual: «las ultimas 2 estan en compras, agregales la
        etiqueta Ropa». El modelo escoge 1458 y 1457 —las de Compras, no la de
        Regalos— y no toca la categoria, porque «estan en compras» es un filtro
        y no una orden."""
        alm, _tg, estado, _u, con_plan = entorno
        con_plan(
            {
                **PLAN_BASE,
                'movimientos': ['1458', '1457'],
                'etiquetas_agregar': ['Ropa'],
            }
        )
        bot.manejar_update(
            alm.cx, _msg('las ultimas 2 estan en compras, agregales la etiqueta Ropa')
        )
        assert {tid for tid, _ in estado['puts']} == {'1458', '1457'}
        assert 'Ropa' in _tags_de(estado, '1458')
        assert 'sin-confirmar' in _tags_de(estado, '1458'), 'no borra las que habia'

    def test_un_comercio_nuevo_se_puede_poner(self, entorno):
        """El enum viejo restringia el comercio a los que ya existian, asi que
        «Etre» era imposible de proponer."""
        alm, _tg, estado, _u, con_plan = entorno
        con_plan({**PLAN_BASE, 'movimientos': ['1441'], 'comercio': 'Etre'})
        bot.manejar_update(alm.cx, _msg('la de tierragro es la tienda Etre'))
        _, payload = estado['puts'][-1]
        assert payload['transactions'][0]['destination_name'] == 'Etre'

    def test_mueve_de_presupuesto(self, entorno):
        alm, _tg, estado, _u, con_plan = entorno
        con_plan({**PLAN_BASE, 'movimientos': ['1455', '1441'], 'presupuesto': 'Vivir'})
        bot.manejar_update(alm.cx, _msg('esas dos muevelas a Vivir'))
        for _tid, payload in estado['puts']:
            assert payload['transactions'][0]['budget_name'] == 'Vivir'

    def test_una_consulta_va_al_asesor(self, entorno):
        alm, tg, estado, _u, con_plan = entorno
        con_plan({**PLAN_BASE, 'accion': 'consultar', 'movimientos': ['1459']})
        bot.manejar_update(alm.cx, _msg('y la anterior a esa'))
        assert '[asesor]' in tg.todo
        assert estado['puts'] == [], 'una consulta no cambia nada'

    def test_borrar_pide_confirmacion(self, entorno):
        alm, tg, estado, _u, con_plan = entorno
        con_plan({**PLAN_BASE, 'accion': 'borrar', 'movimientos': ['1441']})
        bot.manejar_update(alm.cx, _msg('borra la de tierragro'))
        assert estado['borrados'] == []
        assert 'mB:1441:0' in tg.datos()

    def test_la_regla_de_presupuesto_se_guarda(self, entorno):
        """«Compras va en Antojos siempre» no cambia un movimiento: cambia una
        regla. Era lo que no habia forma de decir."""
        alm, tg, estado, _u, con_plan = entorno
        con_plan(
            {
                **PLAN_BASE,
                'accion': 'regla_presupuesto',
                'categoria': 'Compras',
                'presupuesto': 'Antojos',
                'confianza': 1.0,
            }
        )
        bot.manejar_update(alm.cx, _msg('Compras va en Antojos siempre'))
        assert alm.presupuesto_fijado('Compras') == 'Antojos'
        assert estado['puts'] == [], 'una regla no toca los movimientos'
        assert 'rp:0:0' in tg.datos(), 'ofrece aplicarla a los que ya estan'

    def test_responder_tambien_cierra_la_pregunta(self, entorno):
        """Si era la respuesta a algo que el bot pregunto, la pregunta se
        cierra: si no, seguiria preguntando por algo ya resuelto."""
        alm, _tg, _estado, uid, con_plan = entorno
        bid = alm.guardar_buzon(uid, 'graph', 'j@e.com')
        cid, _ = alm.guardar_correo(bid, '<m>', 'b', 'A', '2026-09-02', 'x')
        pid, _ = alm.crear_pendiente(
            correo_id=cid,
            usuario_id=uid,
            tipo='c',
            fecha='2026-09-02',
            valor=-151495.0,
            contraparte='Tierragro',
            estado='publicado',
            pregunta='categoria',
            external_id='e1',
        )
        alm.actualizar_pendiente(pid, firefly_id='1441')
        alm.marcar_preguntado(pid)
        alm.cx.commit()

        con_plan(
            {
                **PLAN_BASE,
                'accion': 'responder',
                'movimientos': ['1441'],
                'categoria': 'Gato',
            }
        )
        bot.manejar_update(alm.cx, _msg('fue la comida de la gata'))
        assert alm.pendiente(pid)['pregunta'] is None
        assert alm.contar_por_preguntar() == 0


class TestCuandoNoEstaSeguro:
    def test_con_confianza_baja_pregunta_antes(self, entorno):
        """Aplicar en silencio algo que no se entendio bien es lo unico
        inaceptable."""
        alm, tg, estado, _u, con_plan = entorno
        con_plan(
            {
                **PLAN_BASE,
                'movimientos': ['1441'],
                'categoria': 'Ropa',
                'confianza': 0.4,
            }
        )
        bot.manejar_update(alm.cx, _msg('esa cosa de por ahi ponla en ropa'))
        assert estado['puts'] == [], 'todavia no'
        assert 'ok:1441:0' in tg.datos(), 'boton de confirmar'
        assert 'Ropa' in tg.todo, 'tiene que decir QUE entendio'

        alm, _tg, estado, _u, con_plan = entorno
        alm, _tg, _estado, _u, con_plan = entorno
        con_plan(
            {
                **PLAN_BASE,
                'movimientos': ['1441'],
                'categoria': 'Ropa',
                'confianza': 0.4,
            }
        )
        bot.manejar_update(alm.cx, _msg('esa cosa ponla en ropa'))
        bot.manejar_update(
            alm.cx,
            {
                'callback_query': {
                    'id': 'q',
                    'data': 'ok:1441:0',
                    'message': {'message_id': 9, 'chat': {'id': '555'}},
                }
            },
        )
        _, payload = estado['puts'][-1]
        assert payload['transactions'][0]['category_name'] == 'Ropa'

    def test_un_plan_vacio_cae_al_respaldo(self, entorno):
        """`accion: nada` no puede bloquear el mensaje: se sigue al camino de
        patrones, que peor pero funciona."""
        alm, tg, _estado, _u, con_plan = entorno
        con_plan({**PLAN_BASE, 'accion': 'nada', 'confianza': 0.1})
        bot.manejar_update(alm.cx, _msg('cual fue la ultima?'))
        assert '[asesor]' in tg.todo, 'el respaldo lo mando al asesor'

    def test_si_la_llamada_falla_cae_al_respaldo(self, entorno, monkeypatch):
        """Sin esto, un fallo de red dejaria el bot mudo."""
        alm, tg, _e, _u, _cp = entorno

        def explota(*a, **k):
            raise RuntimeError('Gemini 503')

        monkeypatch.setattr(bot.ia, 'entender_orden', explota)
        bot.manejar_update(alm.cx, _msg('cuanto llevo gastado'))
        assert '[asesor]' in tg.todo

    def test_sin_api_key_no_se_intenta(self, entorno, monkeypatch):
        alm, tg, _e, _u, _cp = entorno
        monkeypatch.setattr(bot.ia, 'disponible', lambda: False)
        llamado = []
        monkeypatch.setattr(
            bot.ia,
            'entender_orden',
            lambda *a, **k: llamado.append(1) or dict(PLAN_BASE),
        )
        bot.manejar_update(alm.cx, _msg('cuanto llevo gastado'))
        assert llamado == [], 'no se llama sin key'
        assert '[asesor]' in tg.todo


class TestAplicarLaReglaALoViejo:
    def test_le_pone_presupuesto_a_los_que_no_tenian(self, entorno):
        """Fijar la regla solo para el futuro no sirve de mucho: los que ya
        estan sin presupuesto siguen sin el."""
        alm, _tg, estado, _u, _cp = entorno
        for t in estado['txs']:
            if t['id'] in ('1458', '1457'):
                t['attributes']['transactions'][0]['budget_name'] = None
        alm.fijar_presupuesto_de_categoria('Compras', 'Antojos')
        bot.manejar_update(
            alm.cx,
            {
                'callback_query': {
                    'id': 'q',
                    'data': 'rp:0:0',
                    'message': {'message_id': 9, 'chat': {'id': '555'}},
                }
            },
        )
        tocados = {tid for tid, _ in estado['puts']}
        assert tocados == {'1458', '1457'}
        for _tid, payload in estado['puts']:
            assert payload['transactions'][0]['budget_name'] == 'Antojos'

    def test_no_toca_los_que_ya_tenian(self, entorno):
        alm, _tg, estado, _u, _cp = entorno
        alm.fijar_presupuesto_de_categoria('Regalos', 'Antojos')
        bot.manejar_update(
            alm.cx,
            {
                'callback_query': {
                    'id': 'q',
                    'data': 'rp:0:0',
                    'message': {'message_id': 9, 'chat': {'id': '555'}},
                }
            },
        )
        assert '1459' not in {tid for tid, _ in estado['puts']}, 'ya tenia Vivir'

    def test_sin_reglas_puestas_avisa(self, entorno):
        alm, tg, _e, _u, _cp = entorno
        bot.manejar_update(
            alm.cx,
            {
                'callback_query': {
                    'id': 'q',
                    'data': 'rp:0:0',
                    'message': {'message_id': 9, 'chat': {'id': '555'}},
                }
            },
        )
        assert any('no hay reglas' in a for a in tg.avisos)


def _campo(estado, tid, clave):
    """Lo ultimo que se le mando a Firefly para ese movimiento."""
    for t, payload in reversed(estado['puts']):
        if t == tid and clave in payload['transactions'][0]:
            return payload['transactions'][0][clave]
    return None


class TestLotes:
    """Un mensaje puede darle valores DISTINTOS a movimientos distintos.

    Antes el plan tenia un solo juego de cambios para toda la lista, asi que
    «la de tierragro ponla en gato y la de uber en salidas» le ponia lo mismo a
    las dos: ganaba el ultimo campo. Y el texto que se le mostraba al usuario
    decia lo correcto, que es lo peor de todo -- parecia que habia funcionado.
    """

    def test_cada_lote_recibe_lo_suyo(self, entorno):
        alm, _tg, estado, _u, con_plan = entorno
        con_plan(
            {
                **PLAN_BASE,
                'movimientos': [],
                'lotes': [
                    {'movimientos': ['1441'], 'categoria': 'Gato'},
                    {'movimientos': ['1458'], 'categoria': 'Mercado'},
                ],
            }
        )
        bot.manejar_update(
            alm.cx, _msg('la de tierragro ponla en gato y la otra en mercado')
        )
        assert _campo(estado, '1441', 'category_name') == 'Gato'
        assert _campo(estado, '1458', 'category_name') == 'Mercado'

    def test_los_campos_de_arriba_se_ignoran_si_hay_lotes(self, entorno):
        """Si no, un `categoria` suelto arriba pisaria los lotes."""
        alm, _tg, estado, _u, con_plan = entorno
        con_plan(
            {
                **PLAN_BASE,
                'movimientos': ['1441', '1458'],
                'categoria': 'Compras',
                'lotes': [{'movimientos': ['1441'], 'categoria': 'Gato'}],
            }
        )
        bot.manejar_update(alm.cx, _msg('la de tierragro a gato'))
        assert {tid for tid, _ in estado['puts']} == {'1441'}
        assert _campo(estado, '1441', 'category_name') == 'Gato'

    def test_responder_cierra_la_pregunta_de_cada_lote(self, entorno):
        """Contestar tres preguntas de un tiro cerraba una sola y las otras dos
        quedaban abiertas sin decir nada."""
        alm, _tg, _e, uid, con_plan = entorno
        bid = alm.guardar_buzon(uid, 'graph', 'j@e.com')
        cid, _ = alm.guardar_correo(bid, '<m>', 'banco', 'Alerta', '2026-09-02', 'x')
        ids = []
        for i, (ffid, cat) in enumerate([('1441', 'Gato'), ('1458', 'Compras')]):
            pid, _ = alm.crear_pendiente(
                correo_id=cid,
                usuario_id=uid,
                tipo='compra_tarjeta',
                fecha='2026-09-02',
                valor=-100.0,
                moneda='COP',
                contraparte=f'X{i}',
                descripcion=f'X{i}',
                categoria=cat,
                estado='publicado',
                pregunta='categoria',
                external_id=f'lote-{i}',
            )
            alm.actualizar_pendiente(pid, firefly_id=ffid)
            ids.append(pid)
        alm.cx.commit()
        con_plan(
            {
                **PLAN_BASE,
                'accion': 'responder',
                'movimientos': [],
                'lotes': [
                    {'movimientos': ['1441'], 'categoria': 'Gato'},
                    {'movimientos': ['1458'], 'categoria': 'Mercado'},
                ],
            }
        )
        bot.manejar_update(alm.cx, _msg('la primera es del gato y la otra mercado'))
        for pid in ids:
            fila = alm.cx.execute(
                'SELECT pregunta FROM pendientes WHERE id = ?', (pid,)
            ).fetchone()
            assert fila['pregunta'] is None, f'el pendiente {pid} sigue abierto'


class TestEdicionMasiva:
    """«cambia todas a mercado» reescribia los diez movimientos sin preguntar
    -- el ingreso incluido, que quedaba con categoria de gasto."""

    def test_arriba_del_umbral_pide_confirmacion(self, entorno, monkeypatch):
        # El umbral se baja para no atar la prueba al numero de movimientos del
        # fixture: lo que se verifica es el mecanismo, no el numero.
        monkeypatch.setattr(bot, 'MAXIMO_SIN_CONFIRMAR', 3)
        alm, tg, estado, _u, con_plan = entorno
        con_plan(
            {
                **PLAN_BASE,
                'movimientos': ['1441', '1458', '1457', '1459'],
                'categoria': 'Mercado',
            }
        )
        bot.manejar_update(alm.cx, _msg('cambia todas a mercado'))
        assert estado['puts'] == [], 'no puede escribir antes de confirmar'
        assert '4 movimientos' in ' '.join(tg.enviados)

    def test_al_confirmar_se_aplica_el_plan_que_se_mostro(self, entorno, monkeypatch):
        """El boton volvia a preguntarle al modelo, asi que lo confirmado no era
        lo aplicado. Y como el umbral se recalculaba sobre el plan nuevo, una
        orden de mas de cuatro no se aplicaba NUNCA."""
        monkeypatch.setattr(bot, 'MAXIMO_SIN_CONFIRMAR', 3)
        alm, _tg, estado, _u, con_plan = entorno
        con_plan(
            {
                **PLAN_BASE,
                'movimientos': ['1441', '1458', '1457', '1459'],
                'categoria': 'Mercado',
            }
        )
        bot.manejar_update(alm.cx, _msg('cambia todas a mercado'))
        # si volviera a preguntarle al modelo, el plan seria OTRO
        monkeypatch.setattr(
            bot.ia,
            'entender_orden',
            lambda *a, **k: {**PLAN_BASE, 'movimientos': ['1441'], 'categoria': 'Ropa'},
        )
        bot.manejar_update(
            alm.cx,
            {
                'callback_query': {
                    'id': 'q',
                    'data': 'ok:1441:0',
                    'message': {'message_id': 9, 'chat': {'id': '555'}},
                }
            },
        )
        tocados = {tid for tid, _ in estado['puts']}
        assert tocados == {'1441', '1458', '1457', '1459'}
        assert _campo(estado, '1441', 'category_name') == 'Mercado', 'no Ropa'

    def test_debajo_del_umbral_pasa_directo(self, entorno):
        alm, _tg, estado, _u, con_plan = entorno
        con_plan({**PLAN_BASE, 'movimientos': ['1458', '1457'], 'categoria': 'Mercado'})
        bot.manejar_update(alm.cx, _msg('las ultimas 2 a mercado'))
        assert {tid for tid, _ in estado['puts']} == {'1458', '1457'}


class TestUnPresupuestoQueNoExiste:
    """«ponla en el presupuesto Viajes Largos» -- que no existe -- hacia que el
    modelo le pusiera la CATEGORIA «Viajes», que si existe: el movimiento
    perdia su categoria por algo que nunca se pidio."""

    def test_no_se_cambia_la_categoria_en_su_lugar(self, entorno):
        alm, tg, estado, _u, con_plan = entorno
        con_plan({**PLAN_BASE, 'movimientos': ['1441'], 'categoria': 'Viajes'})
        bot.manejar_update(
            alm.cx, _msg('la de tierragro ponla en el presupuesto Viajes Largos')
        )
        assert estado['puts'] == []
        assert 'No tengo un presupuesto con ese nombre' in ' '.join(tg.enviados)

    def test_un_presupuesto_que_si_existe_se_aplica(self, entorno):
        alm, _tg, estado, _u, con_plan = entorno
        con_plan({**PLAN_BASE, 'movimientos': ['1441'], 'presupuesto': 'Vivir'})
        bot.manejar_update(
            alm.cx, _msg('la de tierragro ponla en el presupuesto vivir')
        )
        assert _campo(estado, '1441', 'budget_name') == 'Vivir'


class TestLasEtiquetasLasEscribeElUsuario:
    """El modelo agregaba etiquetas que nadie pidio: a «es lo de google, eso es
    del trabajo» le ponia `reembolsable`."""

    def test_una_etiqueta_que_no_esta_en_el_mensaje_no_se_pone(self, entorno):
        alm, _tg, estado, _u, con_plan = entorno
        con_plan(
            {
                **PLAN_BASE,
                'movimientos': ['1441'],
                'categoria': 'Gato',
                'etiquetas_agregar': ['reembolsable'],
            }
        )
        bot.manejar_update(alm.cx, _msg('la de tierragro es comida del gato'))
        assert 'reembolsable' not in _tags_de(estado, '1441')

    def test_la_que_si_escribio_se_pone(self, entorno):
        alm, _tg, estado, _u, con_plan = entorno
        con_plan(
            {
                **PLAN_BASE,
                'movimientos': ['1441'],
                'etiquetas_agregar': ['Ropa'],
            }
        )
        bot.manejar_update(alm.cx, _msg('ponle el tag Ropa a la de tierragro'))
        assert 'Ropa' in _tags_de(estado, '1441')


class TestLaPreguntaQueVieneDeMas:
    """«cuanto llevo en antojos y cambia la ultima a mercado»: se hacia el
    cambio y la pregunta se perdia en silencio."""

    def test_se_hace_el_cambio_y_se_contesta(self, entorno):
        alm, tg, estado, _u, con_plan = entorno
        con_plan(
            {
                **PLAN_BASE,
                'movimientos': ['1441'],
                'categoria': 'Mercado',
                'consultar_tambien': True,
            }
        )
        bot.manejar_update(
            alm.cx, _msg('cuanto llevo en antojos y cambia la de tierragro a mercado')
        )
        assert _campo(estado, '1441', 'category_name') == 'Mercado'
        assert any('[asesor]' in e for e in tg.enviados), 'y contesta la pregunta'
