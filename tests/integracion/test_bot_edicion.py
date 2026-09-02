"""Editar y consultar movimientos ya registrados, desde el chat.

Tres reclamos, textuales:

- «le dije al bot que me dijera cual era la ultima transaccion y me dijo que no»
- «necesito poder editar las transacciones desde el chat»
- «me sigue preguntando sobre las 3 transacciones, YA ESTAN REGISTRADAS BIEN»

Los tres tenian la misma raiz: el bot solo sabia de la COLA de pendientes. Una
vez algo estaba en Firefly, no lo podia mirar ni cambiar, y las preguntas
abiertas no habia forma de cerrarlas salvo contestarlas una por una.
"""

from __future__ import annotations

import sqlite3

import pytest

from finanzas.adaptadores import db
from finanzas.adaptadores.almacen import Almacen
from finanzas.aplicacion import movimientos
from finanzas.entrada import bot


def _tx(tx_id, monto, destino, categoria, fecha='2026-09-01', etiquetas=None):
    return {
        'id': str(tx_id),
        'attributes': {
            'transactions': [
                {
                    'date': f'{fecha}T00:00:00+02:00',
                    'amount': f'{abs(monto)}.0',
                    'type': 'withdrawal' if monto < 0 else 'deposit',
                    'description': destino,
                    'category_name': categoria,
                    'budget_name': None,
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
        self._id = 3000

    def enviar(self, chat, texto, botones=None, modo='HTML'):
        self.enviados.append(texto)
        if botones:
            self.botones.append(botones)
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

    def datos_de_botones(self):
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

    # La IA se apaga a proposito: estas pruebas cubren el camino de RESPALDO
    # por patrones. El camino del plan del modelo tiene sus propias pruebas,
    # con `entender_orden` simulado. Sin esto, cada prueba llamaria a Gemini.
    monkeypatch.setattr(bot.ia, 'disponible', lambda: False)

    estado = {
        'txs': [
            _tx(1456, -21040, 'Municipio Sabaneta', 'Mercado'),
            _tx(1455, -212000, 'Etre', 'Compras Casa'),
            _tx(1441, -151495, 'Tierragro', 'Gato'),
        ],
        'puts': [],
        'borrados': [],
        'etiquetas_quitadas': [],
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
            tid = ruta.rsplit('/', 1)[-1]
            estado['borrados'].append(tid)
            estado['txs'] = [t for t in estado['txs'] if t['id'] != tid]
            return {}
        return {}

    def get_all(ruta):
        """El doble atiende las dos rutas que se usan. Sin la de categorias, el
        menu de un movimiento salia SIN ningun boton cuando no hay reglas
        aprendidas todavia."""
        if '/categories' in ruta:
            return [
                {'attributes': {'name': c}}
                for c in ('Mercado', 'Gato', 'Compras Casa', 'Salidas', 'Ropa', 'Salud')
            ]
        return estado['txs']

    monkeypatch.setattr(movimientos.firefly, 'get_all', get_all)
    monkeypatch.setattr(movimientos.firefly, 'call', call)
    monkeypatch.setattr(
        movimientos.firefly,
        'quitar_etiqueta',
        lambda tx, etq: estado['etiquetas_quitadas'].append((tx, etq)) or True,
    )
    monkeypatch.setattr(
        bot.interprete,
        'catalogo',
        lambda cx, u: {
            'categorias': ['Mercado', 'Gato', 'Compras Casa'],
            'presupuestos': [],
            'comercios': [],
            'mapa': {},
        },
    )

    def buscar_cat(txt, cats):
        for c in cats:
            if c.lower().split()[0] in txt.lower():
                return [(3, c, f'dijiste {c}')]
        return []

    monkeypatch.setattr(bot.interprete, 'buscar_categoria', buscar_cat)
    return alm, tg, estado, uid


def _msg(texto, responde_a=None):
    m = {'message_id': 7, 'chat': {'id': '555'}, 'text': texto}
    if responde_a:
        m['reply_to_message'] = {'message_id': responde_a}
    return {'message': m}


def _cb(dato, mid=42):
    return {
        'callback_query': {
            'id': 'cq',
            'data': dato,
            'message': {'message_id': mid, 'chat': {'id': '555'}},
        }
    }


def _cat_de(estado, tid):
    for t in estado['txs']:
        if t['id'] == tid:
            return t['attributes']['transactions'][0]['category_name']
    return None


class TestVerLosUltimos:
    def test_ultimos_los_lista_y_los_hace_tocables(self, entorno):
        _alm, tg, _e, _u = entorno
        bot.manejar_update(_alm.cx, _msg('/ultimos'))
        assert 'Etre' in tg.todo and 'Tierragro' in tg.todo
        assert all(d.startswith('mv:') for d in tg.datos_de_botones())

    def test_ultimos_con_una_busqueda_filtra(self, entorno):
        alm, tg, _e, _u = entorno
        bot.manejar_update(alm.cx, _msg('/ultimos tierragro'))
        assert 'Tierragro' in tg.todo
        assert 'Etre' not in tg.todo

    def test_una_busqueda_sin_resultados_lo_dice(self, entorno):
        alm, tg, _e, _u = entorno
        bot.manejar_update(alm.cx, _msg('/ultimos zapateria'))
        assert 'No encontré' in tg.todo


class TestEditarConBotones:
    def test_tocar_uno_abre_su_menu(self, entorno):
        alm, tg, _e, _u = entorno
        bot.manejar_update(alm.cx, _cb('mv:1455:0'))
        assert 'Etre' in tg.todo
        datos = tg.datos_de_botones()
        assert any(d.startswith('mc:1455:') for d in datos), 'categorias'
        assert 'mx:1455:0' in datos, 'borrar'
        assert 'mt:1455:0' in datos, 'escribirlo'

    def test_elegir_una_categoria_la_cambia_en_firefly(self, entorno):
        alm, _tg, estado, uid = entorno
        alm.guardar_regla(uid, 'X', categoria='Mercado', direccion='gasto')
        bot.manejar_update(alm.cx, _cb('mc:1455:0'))
        assert _cat_de(estado, '1455') == 'Mercado'
        assert estado['puts'], 'tiene que llegar el PUT a Firefly'

    def test_confirmar_le_quita_sin_confirmar(self, entorno):
        alm, _tg, estado, _u = entorno
        bot.manejar_update(alm.cx, _cb('mk:1441:0'))
        assert estado['etiquetas_quitadas'] == [('1441', 'sin-confirmar')]

    def test_borrar_pide_confirmacion_antes(self, entorno):
        """Borrar de Firefly no se puede deshacer."""
        alm, tg, estado, _u = entorno
        bot.manejar_update(alm.cx, _cb('mx:1455:0'))
        assert estado['borrados'] == [], 'todavia no'
        assert 'mB:1455:0' in tg.datos_de_botones()

    def test_y_solo_borra_al_confirmar(self, entorno):
        alm, _tg, estado, _u = entorno
        bot.manejar_update(alm.cx, _cb('mB:1455:0'))
        assert estado['borrados'] == ['1455']

    def test_un_movimiento_que_ya_no_existe_avisa(self, entorno):
        alm, tg, _e, _u = entorno
        bot.manejar_update(alm.cx, _cb('mv:9999:0'))
        assert 'ya no existe' in tg.todo


class TestEditarPorTexto:
    def test_cambia_la_ultima(self, entorno):
        """«cambia la ultima a Mercado». La ultima es 1456 por id."""
        alm, _tg, estado, _u = entorno
        bot.manejar_update(alm.cx, _msg('cambia la ultima a Gato'))
        assert _cat_de(estado, '1456') == 'Gato'

    def test_identifica_por_comercio(self, entorno):
        alm, _tg, estado, _u = entorno
        bot.manejar_update(alm.cx, _msg('la de tierragro cambiala a Mercado'))
        assert _cat_de(estado, '1441') == 'Mercado'
        assert _cat_de(estado, '1456') == 'Mercado', 'la otra ya era Mercado'

    def test_cambia_el_comercio(self, entorno):
        alm, _tg, estado, _u = entorno
        bot.manejar_update(alm.cx, _msg('corrige la ultima, el comercio es Alcaldia'))
        _, payload = estado['puts'][-1]
        assert payload['transactions'][0]['destination_name'] == 'Alcaldia'

    def test_borrar_por_texto_pide_confirmacion(self, entorno):
        alm, tg, estado, _u = entorno
        bot.manejar_update(alm.cx, _msg('borra la ultima'))
        assert estado['borrados'] == []
        assert 'mB:1456:0' in tg.datos_de_botones()

    def test_si_no_sabe_a_cual_muestra_la_lista(self, entorno):
        """No se rinde: ofrece los ultimos con botones."""
        alm, tg, estado, _u = entorno
        bot.manejar_update(alm.cx, _msg('cambia esa cosa de por ahi'))
        assert 'No supe a cuál' in tg.todo
        assert any(d.startswith('mv:') for d in tg.datos_de_botones())
        assert estado['puts'] == []

    def test_una_respuesta_normal_no_se_toma_como_edicion(self, entorno):
        """«era Etre, venden cosas para la casa» es la respuesta a una pregunta
        abierta, no una orden de editar. Confundirlas haria que contestar
        modificara otro movimiento."""
        alm, _tg, estado, _u = entorno
        bot.manejar_update(alm.cx, _msg('era Etre, venden cosas para la casa'))
        assert estado['puts'] == []


class TestEscribirloDespuesDeTocar:
    def test_el_boton_de_escribir_ata_el_movimiento(self, entorno):
        alm, tg, _e, _u = entorno
        bot.manejar_update(alm.cx, _cb('mt:1455:0'))
        ed = alm.edicion_en_curso('555')
        assert ed['firefly_id'] == '1455'
        assert ed['mensaje_id'] == tg._id

    def test_y_la_respuesta_se_aplica_a_ese(self, entorno):
        alm, _tg, estado, _u = entorno
        bot.manejar_update(alm.cx, _cb('mt:1441:0'))
        mid = alm.edicion_en_curso('555')['mensaje_id']
        bot.manejar_update(alm.cx, _msg('es Mercado', responde_a=mid))
        assert _cat_de(estado, '1441') == 'Mercado'


class TestListo:
    def _con_preguntas(self, alm, uid):
        bid = alm.guardar_buzon(uid, 'graph', 'j@e.com')
        cid, _ = alm.guardar_correo(bid, '<m>', 'b', 'A', '2026-09-01', 'x')
        ids = []
        for i, (ff, cat, estado) in enumerate(
            (
                ('1441', 'Gato', 'publicado'),
                ('1455', None, 'publicado'),
                (None, None, 'nuevo'),
            )
        ):
            pid, _ = alm.crear_pendiente(
                correo_id=cid,
                usuario_id=uid,
                tipo='c',
                fecha='2026-09-01',
                valor=-1000.0 * (i + 1),
                contraparte=f'X{i}',
                categoria=cat,
                estado=estado,
                pregunta='categoria',
                external_id=f'e{i}',
            )
            if ff:
                alm.actualizar_pendiente(pid, firefly_id=ff)
            ids.append(pid)
        alm.cx.commit()
        return ids

    def test_cierra_todas_de_una(self, entorno):
        """El reclamo: «YA ESTAN REGISTRADAS BIEN, QUE MAS QUIERE». No habia
        forma de decirle que pare salvo contestar una por una."""
        alm, tg, _e, uid = entorno
        self._con_preguntas(alm, uid)
        assert alm.contar_por_preguntar() == 3
        bot.manejar_update(alm.cx, _msg('/listo'))
        assert alm.contar_por_preguntar() == 0
        assert 'Cerré' in tg.todo

    def test_adopta_la_categoria_que_firefly_ya_tenia(self, entorno):
        """Si el movimiento ya esta bien en Firefly, esa es la verdad."""
        alm, _tg, _e, uid = entorno
        ids = self._con_preguntas(alm, uid)
        bot.manejar_update(alm.cx, _msg('/listo'))
        # el segundo no tenia categoria y en Firefly es 'Compras Casa'
        assert alm.pendiente(ids[1])['categoria'] == 'Compras Casa'

    def test_lo_que_nunca_llego_a_firefly_se_descarta(self, entorno):
        """Cerrar la pregunta de algo que no esta publicado lo dejaria en el
        limbo: ni en Firefly ni preguntandose."""
        alm, _tg, _e, uid = entorno
        ids = self._con_preguntas(alm, uid)
        bot.manejar_update(alm.cx, _msg('/listo'))
        assert alm.pendiente(ids[2])['estado'] == 'descartado'

    def test_sin_nada_abierto_lo_dice_y_no_toca_nada(self, entorno):
        alm, tg, _e, _u = entorno
        bot.manejar_update(alm.cx, _msg('/listo'))
        assert 'No tengo nada abierto' in tg.todo

    def test_esta_en_la_ayuda(self, entorno):
        """Un comando que nadie sabe que existe no sirve."""
        assert '/listo' in bot.AYUDA
        assert '/ultimos' in bot.AYUDA


class TestMenuCompleto:
    """El menu de un movimiento ofrecia SEIS categorias de setenta y una, y
    nada mas: ni presupuesto, ni etiquetas, ni el nombre del comercio.

    «los botones del bot ya se quedan cortos».
    """

    def test_el_menu_ofrece_las_cuatro_cosas(self, entorno):
        alm, tg, _e, _u = entorno
        bot.manejar_update(alm.cx, _cb('mv:1455:0'))
        datos = tg.datos_de_botones()
        assert any(d.startswith('lc:1455') for d in datos), 'lista completa'
        assert 'pb:1455:0' in datos, 'presupuesto'
        assert 'le:1455:0' in datos, 'etiquetas'
        assert 'nc:1455:0' in datos, 'comercio'

    def test_la_ficha_muestra_lo_que_falta(self, entorno):
        """Sin ver el presupuesto no habia forma de notar que estaba vacio."""
        alm, tg, _e, _u = entorno
        bot.manejar_update(alm.cx, _cb('mv:1455:0'))
        assert 'presupuesto:' in tg.todo
        assert 'etiquetas:' in tg.todo

    def test_las_categorias_van_paginadas(self, entorno, monkeypatch):
        monkeypatch.setattr(
            movimientos, 'categorias', lambda d=None: [f'C{i}' for i in range(71)]
        )
        alm, tg, _e, _u = entorno
        bot.manejar_update(alm.cx, _cb('lc:1455:0'))
        datos = tg.datos_de_botones()
        assert any(d == 'lc:1455:1' for d in datos), 'boton de siguiente pagina'
        assert 'mv:1455:0' in datos, 'boton de volver'

    def test_el_indice_de_la_pagina_es_absoluto(self, entorno, monkeypatch):
        """Si el indice fuera relativo a la pagina, elegir en la pagina 3
        aplicaria la categoria de la pagina 1."""
        monkeypatch.setattr(
            movimientos, 'categorias', lambda d=None: [f'C{i}' for i in range(71)]
        )
        alm, tg, _e, _u = entorno
        bot.manejar_update(alm.cx, _cb('lc:1455:2'))
        indices = [
            int(d.split(':')[2]) for d in tg.datos_de_botones() if d.startswith('sc:')
        ]
        assert indices, 'la lista paginada manda botones «sc:», no «mc:»'
        assert min(indices) >= 20, f'la pagina 3 empieza en 20, no en {min(indices)}'

    def test_el_boton_de_la_pagina_3_de_verdad_aplica(self, entorno, monkeypatch):
        """Esto es lo que faltaba probar y por eso el bug vivio.

        La pantalla paginada y el menu del movimiento compartian el prefijo
        `mc:`, pero indexan listas distintas: seis categorias filtradas por
        direccion contra las setenta y una. Los botones del 6 en adelante
        contestaban «esa opcion ya no esta» y no se podia poner Mercado, Gato
        ni Ropa desde ahi. Verificar el INDICE no bastaba: hay que tocar el
        boton y ver que escriba.
        """
        monkeypatch.setattr(
            movimientos, 'categorias', lambda d=None: [f'C{i}' for i in range(71)]
        )
        alm, tg, estado, _u = entorno
        bot.manejar_update(alm.cx, _cb('lc:1455:2'))
        dato = next(d for d in tg.datos_de_botones() if d.startswith('sc:'))
        bot.manejar_update(alm.cx, _cb(dato))
        _, payload = estado['puts'][-1]
        i = int(dato.split(':')[2])
        assert payload['transactions'][0]['category_name'] == f'C{i}'

    def test_elegir_un_presupuesto_lo_aplica(self, entorno, monkeypatch):
        monkeypatch.setattr(
            bot.presupuestos,
            'nombres_activos',
            lambda: ['Esencial', 'Vivir', 'Antojos'],
        )
        alm, _tg, estado, _u = entorno
        bot.manejar_update(alm.cx, _cb('sb:1455:2'))
        _, payload = estado['puts'][-1]
        assert payload['transactions'][0]['budget_name'] == 'Antojos'

    def test_fijar_el_presupuesto_de_la_categoria_lo_recuerda(self, entorno):
        """«Compras siempre va en Antojos». El presupuesto se deducia del
        historico y solo con 80% de acuerdo; las repartidas de verdad —Compras
        7 a 2, Regalos 4 a 4— se quedaban sin presupuesto para siempre."""
        alm, tg, estado, _u = entorno
        for t in estado['txs']:
            if t['id'] == '1455':
                t['attributes']['transactions'][0]['budget_name'] = 'Antojos'
        bot.manejar_update(alm.cx, _cb('bp:1455:0'))
        assert alm.presupuesto_fijado('Compras Casa') == 'Antojos'
        assert 'de ahora' in tg.todo.lower()

    def test_no_fija_nada_si_falta_el_presupuesto(self, entorno):
        alm, tg, _e, _u = entorno
        bot.manejar_update(alm.cx, _cb('bp:1455:0'))
        assert alm.presupuestos_fijados() == {}
        assert any('primero' in a for a in tg.avisos)


class TestEtiquetasDesdeElChat:
    def test_elegir_una_etiqueta_la_agrega_sin_borrar_las_otras(
        self, entorno, monkeypatch
    ):
        """La API reemplaza `tags` completo. Si se mandara solo la nueva se
        perderia `sin-confirmar`, que es lo que la conciliacion usa para saber
        que falta cruzar contra el extracto."""
        monkeypatch.setattr(
            movimientos, 'etiquetas_mas_usadas', lambda limite=24: ['Ropa', 'Uber']
        )
        alm, _tg, estado, _u = entorno
        bot.manejar_update(alm.cx, _cb('se:1455:0'))
        _, payload = estado['puts'][-1]
        tags = payload['transactions'][0]['tags']
        assert 'Ropa' in tags
        assert 'sin-confirmar' in tags, 'no puede borrar las que ya estaban'

    def test_pedir_la_etiqueta_por_texto_recuerda_el_campo(self, entorno):
        """Sin recordar QUE se pidio, escribir «Ropa» se interpretaba como una
        categoria y la etiqueta nunca se ponia."""
        alm, _tg, _e, _u = entorno
        bot.manejar_update(alm.cx, _cb('ne:1455:0'))
        ed = alm.edicion_en_curso('555')
        assert ed['firefly_id'] == '1455'
        assert ed['campo'] == 'etiquetas'

    def test_y_el_texto_se_aplica_como_etiqueta(self, entorno):
        alm, _tg, estado, _u = entorno
        bot.manejar_update(alm.cx, _cb('ne:1455:0'))
        mid = alm.edicion_en_curso('555')['mensaje_id']
        bot.manejar_update(alm.cx, _msg('Ropa', responde_a=mid))
        _, payload = estado['puts'][-1]
        assert 'Ropa' in payload['transactions'][0]['tags']

    def test_el_comercio_por_texto_va_al_destino(self, entorno):
        """El correo llega como «MERCADO PAGO*...» y el comercio real hay que
        poderlo escribir."""
        alm, _tg, estado, _u = entorno
        bot.manejar_update(alm.cx, _cb('nc:1455:0'))
        mid = alm.edicion_en_curso('555')['mensaje_id']
        bot.manejar_update(alm.cx, _msg('Etre Ropa', responde_a=mid))
        _, payload = estado['puts'][-1]
        assert payload['transactions'][0]['destination_name'] == 'Etre Ropa'


class TestVariasDeUnaVez:
    def test_etiqueta_las_dos_ultimas(self, entorno):
        """La orden textual del reclamo."""
        alm, _tg, estado, _u = entorno
        bot.manejar_update(
            alm.cx, _msg('las ultimas 2 estan en compras, agregales la etiqueta Ropa')
        )
        tocados = {tid for tid, _ in estado['puts']}
        assert tocados == {'1456', '1455'}, 'las dos, no una'
        for _tid, payload in estado['puts']:
            assert 'Ropa' in payload['transactions'][0]['tags']

    def test_mueve_varias_de_presupuesto(self, entorno, monkeypatch):
        monkeypatch.setattr(
            bot.presupuestos,
            'nombres_activos',
            lambda: ['Esencial', 'Vivir', 'Antojos'],
        )
        alm, _tg, estado, _u = entorno
        bot.manejar_update(alm.cx, _msg('las ultimas 2 ponlas en Antojos'))
        assert len(estado['puts']) == 2
        for _tid, payload in estado['puts']:
            assert payload['transactions'][0]['budget_name'] == 'Antojos'

    def test_una_sola_sigue_funcionando(self, entorno):
        alm, _tg, estado, _u = entorno
        bot.manejar_update(alm.cx, _msg('cambia la ultima a Gato'))
        assert len(estado['puts']) == 1

    def test_reporta_cada_uno(self, entorno):
        alm, tg, _e, _u = entorno
        bot.manejar_update(alm.cx, _msg('las ultimas 2 etiquetalas como Ropa'))
        assert '2 movimientos' in tg.todo
