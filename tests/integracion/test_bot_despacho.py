"""Que hace el bot con cada update que le llega.

Esta parte no tenia ni una prueba, y es justo donde vivio el peor bug: una
respuesta por texto resolvia la pregunta MAS RECIENTE en vez de la que se
estaba contestando, asi que una compra quedaba con la categoria de otra.

Aqui Telegram y Firefly son dobles: se verifica que el bot llame lo correcto y
que la base quede como debe, sin salir a la red.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from finanzas.adaptadores import db
from finanzas.adaptadores.almacen import Almacen
from finanzas.entrada import bot

RAIZ = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(RAIZ))

ESQUEMA = db.ESQUEMA


class TelegramFalso:
    """Anota todo en vez de mandarlo. `enviar` devuelve el mensaje SIN el sobre
    `result`, igual que el de verdad (telegram.call ya lo desenvuelve)."""

    class TelegramError(Exception):
        pass

    def __init__(self):
        self.enviados: list[tuple[str, str]] = []
        self.editados: list[tuple[str, int, str]] = []
        self.avisos: list[str] = []
        self._siguiente_id = 1000

    def enviar(self, chat, texto, botones=None, modo='HTML'):
        self.enviados.append((str(chat), texto))
        self._siguiente_id += 1
        return {'message_id': self._siguiente_id, 'text': texto}

    def editar(self, chat, message_id, texto, modo='HTML'):
        self.editados.append((str(chat), message_id, texto))
        return {'message_id': message_id}

    def responder_callback(self, cq_id, texto=None, alerta=False):
        self.avisos.append(texto or '')

    # lo que el bot no usa en estas pruebas
    def obtener_updates(self, *a, **k):
        return []


@pytest.fixture
def entorno(monkeypatch, tmp_path):
    """El bot con la base en memoria y Telegram de mentiras."""

    cx = sqlite3.connect(':memory:')
    cx.row_factory = sqlite3.Row
    cx.execute('PRAGMA foreign_keys = ON')
    alm = Almacen(cx)
    alm.inicializar(ESQUEMA)

    tg = TelegramFalso()
    monkeypatch.setattr(bot, 'telegram', tg)

    # La IA se apaga a proposito: estas pruebas cubren el camino de RESPALDO
    # por patrones. El camino del plan del modelo tiene sus propias pruebas,
    # con `entender_orden` simulado. Sin esto, cada prueba llamaria a Gemini.
    monkeypatch.setattr(bot.ia, 'disponible', lambda: False)
    monkeypatch.setattr(db, 'ruta', lambda: str(tmp_path / 'x.db'))

    uid = alm.guardar_usuario('Juan', 'https://f.ejemplo', 'tok', '555')
    bid = alm.guardar_buzon(uid, 'graph', 'juan@ejemplo.com')
    cid, _ = alm.guardar_correo(
        bid, '<m@banco>', 'banco', 'Alerta', '2026-09-01', 'Compraste ...'
    )
    return bot, alm, tg, uid, cid


def _pendiente(alm, uid, cid, **extra):
    args = {
        'correo_id': cid,
        'usuario_id': uid,
        'tipo': 'compra_tarjeta',
        'fecha': '2026-09-01',
        'valor': -50000.0,
        'contraparte': 'TIERRAGRO',
        'external_id': f'bc-{extra.pop("eid", "a")}',
        'estado': 'publicado',
        'pregunta': 'categoria',
    }
    args.update(extra)
    pid, _ = alm.crear_pendiente(**args)
    alm.cx.commit()
    return pid


def _toque(dato, chat='555', mid=77):
    return {
        'callback_query': {
            'id': 'cq1',
            'data': dato,
            'message': {'message_id': mid, 'chat': {'id': chat}},
        }
    }


def _mensaje(texto, chat='555', responde_a=None):
    m = {'message_id': 5, 'chat': {'id': chat}, 'text': texto}
    if responde_a:
        m['reply_to_message'] = {'message_id': responde_a}
    return {'message': m}


class TestRuteo:
    def test_un_boton_que_no_existe_no_tumba_el_bot(self, entorno):
        """Un callback viejo puede llegar despues de un redespliegue con otras
        letras. Antes de la tabla de despacho esto caia en un else vacio y el
        usuario se quedaba con el botoncito girando."""
        bot, alm, tg, *_ = entorno
        bot.manejar_update(alm.cx, _toque('z:1:0'))
        assert tg.avisos == ['no entendí ese botón']

    @pytest.mark.parametrize('dato', ['basura', 'c:abc:0', 'c:1', ''])
    def test_un_callback_deforme_se_contesta_igual(self, entorno, dato):
        """Telegram deja el boton girando hasta que se contesta el callback, asi
        que hay que contestar incluso lo que no se entiende."""
        bot, alm, tg, *_ = entorno
        bot.manejar_update(alm.cx, _toque(dato))
        assert tg.avisos == ['no entendí ese botón']

    def test_un_comando_desconocido_responde_con_la_ayuda(self, entorno):
        bot, alm, tg, *_ = entorno
        bot.manejar_update(alm.cx, _mensaje('/inventado'))
        assert '/inventado' in tg.enviados[0][1]

    def test_el_comando_con_arroba_del_grupo_tambien_sirve(self, entorno):
        """En grupos Telegram manda /ayuda@mi_bot."""
        bot, alm, tg, *_ = entorno
        bot.manejar_update(alm.cx, _mensaje('/ayuda@finanzas_bot'))
        assert tg.enviados[0][1] == bot.AYUDA

    def test_un_update_sin_mensaje_no_hace_nada(self, entorno):
        """Llegan updates de edicion, de encuestas, de miembros que entran."""
        bot, alm, tg, *_ = entorno
        bot.manejar_update(alm.cx, {'edited_message': {'text': 'hola'}})
        bot.manejar_update(alm.cx, {})
        assert tg.enviados == [] and tg.avisos == []

    def test_un_mensaje_vacio_no_hace_nada(self, entorno):
        """Una foto sin texto llega como message sin 'text'."""
        bot, alm, tg, *_ = entorno
        bot.manejar_update(alm.cx, {'message': {'chat': {'id': '555'}}})
        assert tg.enviados == []


class TestAyuda:
    def test_todo_comando_esta_descrito(self, entorno):
        """La ayuda se arma de DESCRIPCIONES. Si alguien agrega un comando y no
        lo describe, nadie lo va a usar nunca."""
        bot, *_ = entorno
        descritos = {c for c, _ in bot.DESCRIPCIONES}
        # /start no se describe: no se escribe, lo manda Telegram al abrir.
        assert set(bot.COMANDOS) - descritos == {'/start'}

    def test_no_se_describe_nada_que_no_exista(self, entorno):
        bot, *_ = entorno
        descritos = {c for c, _ in bot.DESCRIPCIONES}
        assert descritos <= set(bot.COMANDOS)

    def test_la_ayuda_los_menciona_todos(self, entorno):
        bot, *_ = entorno
        for c, _ in bot.DESCRIPCIONES:
            assert c in bot.AYUDA


class TestStart:
    def test_el_primer_start_vincula_el_chat(self, entorno):
        bot, alm, tg, *_ = entorno
        alm.cx.execute('UPDATE usuarios SET telegram_chat_id = NULL')
        alm.cx.commit()
        bot.manejar_update(alm.cx, _mensaje('/start', chat='999'))
        assert alm.usuario_por_nombre('Juan')['telegram_chat_id'] == '999'
        assert tg.enviados[0][1] == bot.AYUDA

    def test_un_segundo_start_de_otro_chat_no_roba_la_cuenta(self, entorno):
        """Si alguien mas encuentra el bot, no puede quedarse con las finanzas
        del usuario ya vinculado."""
        bot, alm, _tg, *_ = entorno
        bot.manejar_update(alm.cx, _mensaje('/start', chat='intruso'))
        assert alm.usuario_por_nombre('Juan')['telegram_chat_id'] == '555'


class TestResponderConBotones:
    def test_elegir_una_categoria_la_aplica_y_reescribe_el_mensaje(self, entorno):
        bot, alm, tg, uid, cid = entorno
        pid = _pendiente(alm, uid, cid)
        alm.guardar_sugerencias(pid, ['Gato', 'Mercado'])
        bot.manejar_update(alm.cx, _toque(f'c:{pid}:0'))
        assert tg.avisos == ['Gato']
        assert tg.editados and 'Gato' in tg.editados[0][2]
        assert alm.pendiente(pid)['categoria'] == 'Gato'

    def test_un_indice_fuera_de_rango_avisa_y_no_toca_nada(self, entorno):
        """Las sugerencias se regeneran; un boton viejo puede apuntar a la
        opcion 4 de una lista que ahora tiene 2."""
        bot, alm, tg, uid, cid = entorno
        pid = _pendiente(alm, uid, cid)
        alm.guardar_sugerencias(pid, ['Gato', 'Mercado'])
        bot.manejar_update(alm.cx, _toque(f'c:{pid}:9'))
        assert tg.avisos == ['esa opción ya no está']
        assert alm.pendiente(pid)['categoria'] is None

    def test_descartar_lo_saca_de_la_cola(self, entorno):
        bot, alm, _tg, uid, cid = entorno
        pid = _pendiente(alm, uid, cid)
        bot.manejar_update(alm.cx, _toque(f'x:{pid}:0'))
        p = alm.pendiente(pid)
        assert p['estado'] == 'descartado'
        assert p['pregunta'] is None, 'descartado no se vuelve a preguntar'

    def test_dejarlo_como_esta_lo_confirma(self, entorno):
        bot, alm, _tg, uid, cid = entorno
        pid = _pendiente(alm, uid, cid, pregunta='monto')
        bot.manejar_update(alm.cx, _toque(f'k:{pid}:0'))
        p = alm.pendiente(pid)
        assert (p['estado'], p['pregunta']) == ('confirmado', None)


class TestPedirTexto:
    def test_ata_el_mensaje_al_movimiento(self, entorno):
        """Es lo unico que hace que la respuesta libre caiga en el movimiento
        correcto. Antes guardaba un marcador que nadie leia, y encima pisaba
        las sugerencias: si despues tocabas un boton, el indice no apuntaba a
        nada."""
        bot, alm, tg, uid, cid = entorno
        pid = _pendiente(alm, uid, cid)
        alm.guardar_sugerencias(pid, ['Gato', 'Mercado'])
        bot.manejar_update(alm.cx, _toque(f't:{pid}:0'))
        eco = tg.enviados[0]
        assert str(pid) in eco[1]
        assert alm.pendiente_de_mensaje('555', 1001) == pid
        assert alm.sugerencias(pid) == ['Gato', 'Mercado'], 'no las pisa'

    def test_dos_preguntas_abiertas_no_se_confunden(self, entorno):
        """El caso exacto del bug: dos movimientos preguntados, se contesta el
        primero, y la respuesta tiene que ir al primero."""
        bot, alm, _tg, uid, cid = entorno
        a = _pendiente(alm, uid, cid, eid='a', contraparte='TIERRAGRO')
        b = _pendiente(alm, uid, cid, eid='b', contraparte='ZONA FIT')
        bot.manejar_update(alm.cx, _toque(f't:{a}:0'))
        bot.manejar_update(alm.cx, _toque(f't:{b}:0'))
        assert alm.pendiente_de_mensaje('555', 1001) == a
        assert alm.pendiente_de_mensaje('555', 1002) == b
