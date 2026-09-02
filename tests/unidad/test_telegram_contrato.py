"""Que devuelve cada funcion de la capa de Telegram.

`call` ya desenvuelve el campo `result` de la respuesta de la API, asi que
`enviar` devuelve el mensaje DIRECTO, no {'ok':..., 'result':{...}}. Escribir
`r['result']['message_id']` no falla: devuelve None en silencio, y el mensaje
no queda atado al movimiento — o sea, contestar por texto resuelve la pregunta
equivocada. Ya paso dos veces. De ahi esta prueba.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import sys
from pathlib import Path

import pytest

from finanzas.adaptadores import telegram
from finanzas.entrada import bot

RAIZ = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(RAIZ))


@pytest.fixture
def tg(monkeypatch):

    monkeypatch.setattr(telegram, '_token', lambda: 'ficticio')
    return telegram


def _respuesta(cuerpo: dict):
    """Un urlopen de mentiras que devuelve `cuerpo` como la API de Telegram."""

    class _R:
        def read(self):
            return json.dumps(cuerpo).encode('utf-8')

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    return lambda *a, **k: _R()


def test_enviar_devuelve_el_mensaje_sin_el_sobre(tg, monkeypatch):
    monkeypatch.setattr(
        tg.urllib.request,
        'urlopen',
        _respuesta({'ok': True, 'result': {'message_id': 4242, 'text': 'hola'}}),
    )
    msg = tg.enviar('123', 'hola')
    assert msg['message_id'] == 4242, 'call ya quito el sobre `result`'
    assert 'result' not in msg


def test_un_error_de_la_api_es_una_excepcion_no_un_none(tg, monkeypatch):
    """Si devolviera None, el llamador guardaria un mensaje sin id y la
    conversacion quedaria desalineada sin que nadie se enterara."""
    monkeypatch.setattr(
        tg.urllib.request,
        'urlopen',
        _respuesta({'ok': False, 'description': 'chat not found'}),
    )
    with pytest.raises(tg.TelegramError, match='chat not found'):
        tg.enviar('123', 'hola')


def test_el_texto_se_recorta_al_limite_de_telegram(tg, monkeypatch):
    """La API rechaza mas de 4096 caracteres. El resumen diario con muchos
    movimientos los pasa."""
    visto = {}

    def espia(metodo, payload=None, timeout=60):
        visto.update(payload)
        return {'message_id': 1}

    monkeypatch.setattr(tg, 'call', espia)
    tg.enviar('123', 'x' * 5000)
    assert len(visto['text']) == 4096


def test_los_botones_se_recortan_al_limite_del_callback(tg, monkeypatch):
    """callback_data aguanta 64 bytes. Por eso viaja el indice de la opcion y
    no el texto: 'Restaurantes y domicilios' con el id de un pendiente largo
    se pasaba, y Telegram rechazaba el teclado completo."""
    visto = {}

    def espia(metodo, payload=None, timeout=60):
        visto.update(payload)
        return {'message_id': 1}

    monkeypatch.setattr(tg, 'call', espia)
    tg.enviar('123', 'hola', botones=[[('t' * 100, 'd' * 100)]])
    boton = visto['reply_markup']['inline_keyboard'][0][0]
    assert len(boton['text']) == 64
    assert len(boton['callback_data']) == 64


class TestMenuDeComandos:
    """`poner_comandos` tenia la lista escrita a mano aqui, con cuatro de los
    siete comandos: una tercera copia para desincronizarse, despues de la ayuda
    y la tabla de despacho. Ahora la lista llega de afuera."""

    def test_le_quita_la_barra_al_nombre(self, tg, monkeypatch):
        """La API los quiere sin `/`: con barra rechaza la llamada completa y el
        menu se queda con lo que hubiera antes."""
        visto = {}
        monkeypatch.setattr(
            tg, 'call', lambda m, payload=None, timeout=60: visto.update(payload) or {}
        )
        tg.poner_comandos([('/resumen', 'como va'), ('ayuda', 'esto')])
        assert [c['command'] for c in visto['commands']] == ['resumen', 'ayuda']

    def test_manda_los_que_le_pasen_y_solo_esos(self, tg, monkeypatch):
        visto = {}
        monkeypatch.setattr(
            tg, 'call', lambda m, payload=None, timeout=60: visto.update(payload) or {}
        )

        tg.poner_comandos(list(bot.DESCRIPCIONES))
        enviados = {c['command'] for c in visto['commands']}
        assert enviados == {c.lstrip('/') for c, _ in bot.DESCRIPCIONES}

    def test_recorta_la_descripcion_al_limite(self, tg, monkeypatch):
        visto = {}
        monkeypatch.setattr(
            tg, 'call', lambda m, payload=None, timeout=60: visto.update(payload) or {}
        )
        tg.poner_comandos([('/x', 'd' * 400)])
        assert len(visto['commands'][0]['description']) == 256


class TestLosDoblesSeParecenAlDeVerdad:
    """Un doble mas permisivo que el adaptador real no prueba nada: prueba el
    doble.

    Asi llego a produccion un crash: `telegram.editar` NO aceptaba botones,
    pero el doble de las pruebas de productos si (`editar(chat, mid, texto,
    botones=None, modo='HTML')`). Sus pruebas pasaban en verde mientras en el
    servidor la botonera se colaba como cuarto argumento posicional -- donde va
    el modo -- y Telegram contestaba «unsupported parse_mode». Tocar
    «Alimentacion» al clasificar un producto no hacia nada.

    Esta prueba compara las firmas de los dobles contra las del adaptador.
    """

    @staticmethod
    def _dobles():
        """Las clases de los archivos de prueba que se hacen pasar por Telegram."""
        encontrados = []
        carpeta = RAIZ / 'tests' / 'integracion'
        for archivo in sorted(carpeta.glob('test_*.py')):
            # Por ruta y no por nombre de modulo: `tests/` no es un paquete.
            spec = importlib.util.spec_from_file_location(archivo.stem, archivo)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            for nombre in dir(mod):
                obj = getattr(mod, nombre)
                if (
                    isinstance(obj, type)
                    and 'elegram' in nombre
                    and hasattr(obj, 'enviar')
                ):
                    encontrados.append((archivo.name, obj))
        return encontrados

    def test_hay_dobles_que_revisar(self):
        """Si el descubrimiento se rompe, la prueba pasaria sin revisar nada."""
        assert self._dobles(), 'no encontre ningun doble de Telegram'

    @pytest.mark.parametrize('funcion', ['enviar', 'editar'])
    def test_los_parametros_van_en_el_mismo_orden(self, funcion):
        de_verdad = [
            p
            for p in inspect.signature(getattr(telegram, funcion)).parameters
            if p not in ('chat_id', 'message_id')
        ]
        for archivo, doble in self._dobles():
            metodo = getattr(doble, funcion, None)
            if metodo is None:
                continue
            del_doble = [
                p
                for p in inspect.signature(metodo).parameters
                if p not in ('self', 'chat', 'chat_id', 'message_id', 'mid')
            ]
            assert del_doble == de_verdad, (
                f'{archivo}: el doble de `{funcion}` recibe {del_doble} y el '
                f'adaptador {de_verdad}. Un doble mas permisivo esconde el bug.'
            )
