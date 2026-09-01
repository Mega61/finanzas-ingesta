"""Cliente de la Bot API de Telegram. Solo stdlib, sin dependencias nuevas."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

import config

API = 'https://api.telegram.org'
OFFSET = os.path.join(config.DATOS, 'telegram_offset')


class TelegramError(Exception):
    pass


def _token():
    tok = config.get('TELEGRAM_TOKEN')
    if not tok:
        raise TelegramError('falta TELEGRAM_TOKEN')
    return tok


def call(metodo, payload=None, timeout=60):
    url = f"{API}/bot{_token()}/{metodo}"
    datos = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(url, data=datos, method='POST' if datos else 'GET')
    if datos:
        req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as ex:
        cuerpo = ex.read().decode('utf-8', 'replace')
        raise TelegramError(f"HTTP {ex.code} en {metodo}: {cuerpo[:300]}") from None
    if not res.get('ok'):
        raise TelegramError(f"{metodo}: {res.get('description')}")
    return res.get('result')


def yo():
    return call('getMe')


# ------------------------------------------------------------------- offset
# El offset dice hasta que update ya se proceso. Se guarda en disco para que
# reiniciar el contenedor no reprocese respuestas viejas.

def leer_offset():
    try:
        with open(OFFSET, encoding='utf-8') as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return 0


def guardar_offset(v):
    os.makedirs(os.path.dirname(OFFSET) or '.', exist_ok=True)
    with open(OFFSET, 'w', encoding='utf-8') as fh:
        fh.write(str(v))


def updates(espera=30):
    """Long polling. Devuelve la lista y avanza el offset."""
    off = leer_offset()
    payload = {'timeout': espera, 'allowed_updates': ['message', 'callback_query']}
    if off:
        payload['offset'] = off
    res = call('getUpdates', payload, timeout=espera + 20) or []
    if res:
        guardar_offset(max(u['update_id'] for u in res) + 1)
    return res


# ------------------------------------------------------------------ enviar

def enviar(chat_id, texto, botones=None, modo='HTML'):
    """`botones` es una lista de filas, cada fila lista de (texto, dato)."""
    payload = {
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
    return call('sendMessage', payload)


def editar(chat_id, message_id, texto, modo='HTML'):
    """Se usa para reemplazar la pregunta por la respuesta: deja el chat
    limpio en vez de una fila de preguntas ya contestadas."""
    return call('editMessageText', {
        'chat_id': str(chat_id),
        'message_id': message_id,
        'text': texto[:4096],
        'parse_mode': modo,
        'disable_web_page_preview': True,
    })


def responder_callback(callback_id, texto=None):
    payload = {'callback_query_id': callback_id}
    if texto:
        payload['text'] = texto[:200]
    return call('answerCallbackQuery', payload)


def borrar_comandos():
    return call('deleteMyCommands')


def poner_comandos():
    return call('setMyCommands', {'commands': [
        {'command': 'pendientes', 'description': 'Lo que falta por clasificar'},
        {'command': 'resumen', 'description': 'Como va la conciliacion'},
        {'command': 'sinconfirmar', 'description': 'Lo que esta en Firefly sin confirmar'},
        {'command': 'ayuda', 'description': 'Que puede hacer este bot'},
    ]})
