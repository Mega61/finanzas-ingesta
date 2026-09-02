"""La API de Telegram, cruda. Solo transporte: aqui no hay nada de finanzas.

`call` desenvuelve el campo `result` de la respuesta, asi que todo lo de este
modulo devuelve el objeto de Telegram DIRECTO. Escribir `r['result']['...']`
sobre lo que devuelve `enviar` no da error: da None en silencio. Congelado en
tests/unidad/test_telegram_contrato.py, que existe porque ya paso dos veces.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from finanzas import config, registro

API = 'https://api.telegram.org'
OFFSET = os.path.join(config.DATOS, 'telegram_offset')

# Una fila de botones: (texto que se ve, dato que viaja en el callback).
Boton = tuple[str, str]
Botones = list[list[Boton]]


class TelegramError(Exception):
    pass


def _token() -> str:
    tok = config.get('TELEGRAM_TOKEN')
    if not tok:
        raise TelegramError('falta TELEGRAM_TOKEN')
    return tok


def call(metodo: str, payload: dict[str, Any] | None = None, timeout: int = 60) -> Any:
    """Devuelve el `result` ya desenvuelto, o levanta TelegramError."""
    url = f'{API}/bot{_token()}/{metodo}'
    datos = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(url, data=datos, method='POST' if datos else 'GET')
    if datos:
        req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as ex:
        cuerpo = ex.read().decode('utf-8', 'replace')
        raise TelegramError(f'HTTP {ex.code} en {metodo}: {cuerpo[:300]}') from None
    if not res.get('ok'):
        raise TelegramError(f'{metodo}: {res.get("description")}')
    return res.get('result')


def yo() -> dict[str, Any]:
    return call('getMe')


# ------------------------------------------------------------------- offset
# El offset dice hasta que update ya se proceso. Se guarda en disco para que
# reiniciar el contenedor no reprocese respuestas viejas.


def leer_offset() -> int:
    try:
        with open(OFFSET, encoding='utf-8') as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return 0


def guardar_offset(v: int) -> None:
    os.makedirs(os.path.dirname(OFFSET) or '.', exist_ok=True)
    with open(OFFSET, 'w', encoding='utf-8') as fh:
        fh.write(str(v))


def updates(espera: int = 30) -> list[dict[str, Any]]:
    """Long polling. Devuelve la lista y avanza el offset.

    Solo puede haber UN consumidor de getUpdates por bot: con dos, Telegram
    responde HTTP 409 para siempre. Por eso el servicio corre un unico proceso.
    """
    off = leer_offset()
    payload: dict[str, Any] = {
        'timeout': espera,
        'allowed_updates': ['message', 'callback_query'],
    }
    if off:
        payload['offset'] = off
    res = call('getUpdates', payload, timeout=espera + 20) or []
    if res:
        guardar_offset(max(u['update_id'] for u in res) + 1)
    return res


# ------------------------------------------------------------------ enviar


def enviar(
    chat_id: str | int, texto: str, botones: Botones | None = None, modo: str = 'HTML'
) -> dict[str, Any]:
    """Devuelve el mensaje creado: `{'message_id': ..., ...}`, sin sobre.

    Los recortes no son cosmeticos: la API rechaza el mensaje COMPLETO si el
    texto pasa de 4096 caracteres o si algun `callback_data` pasa de 64 bytes.
    Por eso en el callback viaja el INDICE de la opcion y no su texto.
    """
    payload: dict[str, Any] = {
        'chat_id': str(chat_id),
        'text': texto[:4096],
        'parse_mode': modo,
        'disable_web_page_preview': True,
    }
    if botones:
        payload['reply_markup'] = {
            'inline_keyboard': [
                [{'text': t[:64], 'callback_data': d[:64]} for t, d in fila]
                for fila in botones
            ]
        }
    return _con_respaldo_plano('sendMessage', payload)


def editar(
    chat_id: str | int, message_id: int, texto: str, modo: str = 'HTML'
) -> dict[str, Any]:
    """Se usa para reemplazar la pregunta por la respuesta: deja el chat
    limpio en vez de una fila de preguntas ya contestadas."""
    return _con_respaldo_plano(
        'editMessageText',
        {
            'chat_id': str(chat_id),
            'message_id': message_id,
            'text': texto[:4096],
            'parse_mode': modo,
            'disable_web_page_preview': True,
        },
    )


def _con_respaldo_plano(metodo: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Si el HTML no le gusta a Telegram, se manda el mismo texto SIN formato.

    Telegram rechaza el mensaje COMPLETO cuando el HTML esta mal, y el bot se
    queda mudo. Y el HTML se rompe con cosas de todos los dias: un comercio que
    se llame «Cafe & Bar <3» -- que ademas queda guardado en Firefly, asi que a
    partir de ahi TODA pantalla que lo muestre queda irrenderizable -- o el
    recorte a 4096 cortando por la mitad una etiqueta `<i>`.

    Escapar en el origen sigue siendo lo correcto y se hace; esto es la red
    debajo. Un mensaje feo se lee; uno que no llega, no.
    """
    try:
        return call(metodo, payload)
    except TelegramError as ex:
        if 'parse' not in str(ex).lower() and 'entit' not in str(ex).lower():
            raise
        registro.aviso(f'HTML rechazado por Telegram, lo mando plano: {str(ex)[:120]}')
        plano = dict(payload)
        plano.pop('parse_mode', None)
        plano['text'] = _sin_etiquetas(plano.get('text') or '')
        return call(metodo, plano)


def _sin_etiquetas(texto: str) -> str:
    """Quita el marcado para el respaldo plano. No es un saneador de HTML:
    es para que no se vean `<b>` sueltos en un mensaje ya rechazado."""
    limpio = re.sub(r'</?[a-zA-Z][^>]*>', '', texto)
    return (
        limpio.replace('&lt;', '<')
        .replace('&gt;', '>')
        .replace('&quot;', '"')
        .replace('&amp;', '&')
    )


def responder_callback(callback_id: str, texto: str | None = None) -> Any:
    """Hay que llamarlo SIEMPRE, incluso cuando no se entendio el boton:
    Telegram deja el botoncito girando hasta que se contesta el callback."""
    payload: dict[str, Any] = {'callback_query_id': callback_id}
    if texto:
        payload['text'] = texto[:200]
    return call('answerCallbackQuery', payload)


def borrar_comandos() -> Any:
    return call('deleteMyCommands')


def poner_comandos(comandos: list[tuple[str, str]]) -> Any:
    """El menu que Telegram muestra al escribir «/».

    La lista llega de afuera a proposito: aqui habia una copia escrita a mano
    con cuatro de los siete comandos, o sea una tercera lista para
    desincronizarse. El duenio de esa informacion es el bot, no el transporte.
    """
    return call(
        'setMyCommands',
        {
            'commands': [
                {'command': c.lstrip('/'), 'description': d[:256]} for c, d in comandos
            ]
        },
    )
