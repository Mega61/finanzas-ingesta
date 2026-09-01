# -*- coding: utf-8 -*-
"""Verifica una por una las credenciales de la Fase 1.

    python automatizacion/verificar.py            # revisa todo lo que este configurado
    python automatizacion/verificar.py graph      # solo una cosa
    python automatizacion/verificar.py telegram

Cada prueba es independiente: si una falla las demas siguen. Ninguna escribe
nada en Firefly ni manda correos. Lo unico que escribe es el token de Graph en
automatizacion/.cache_graph.json, y un mensaje de prueba de Telegram si le
pasas el chat_id.
"""
import json
import os
import sys
import urllib.error
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
ENV_LOCAL = os.path.join(AQUI, '.env')
CACHE_GRAPH = os.path.join(AQUI, '.cache_graph.json')

VERDE, ROJO, AMAR, GRIS, FIN = '\033[92m', '\033[91m', '\033[93m', '\033[90m', '\033[0m'
if os.name == 'nt' and not os.environ.get('WT_SESSION'):
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleMode(
            ctypes.windll.kernel32.GetStdHandle(-11), 7)
    except Exception:
        VERDE = ROJO = AMAR = GRIS = FIN = ''


def ok(m):    print(f"  {VERDE}OK{FIN}    {m}")
def mal(m):   print(f"  {ROJO}FALLA{FIN} {m}")
def aviso(m): print(f"  {AMAR}OJO{FIN}   {m}")
def nota(m):  print(f"  {GRIS}·     {m}{FIN}")


sys.path.insert(0, AQUI)
import config  # noqa: E402


class _Cfg:
    """Acceso tipo diccionario a config, para no reescribir las pruebas."""

    def get(self, k, d=None):
        return config.get(k, d)


E = _Cfg()


def http(url, datos=None, cabeceras=None, metodo=None):
    req = urllib.request.Request(url, data=datos, method=metodo)
    for k, v in (cabeceras or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode('utf-8'))


# --------------------------------------------------------------------- Firefly

def probar_firefly():
    print("\nFirefly III")
    if not E.get('FIREFLY_URL') or not E.get('FIREFLY_TOKEN'):
        aviso("sin configurar (FIREFLY_URL / FIREFLY_TOKEN)")
        return False
    try:
        import firefly as F
        ok(F.whoami())
        cuentas = F.accounts_index()
        activos = [n for n, d in cuentas.items() if d['type'] in ('asset', 'liabilities')]
        ok(f"{len(cuentas)} cuentas visibles, {len(activos)} entre asset y liabilities")
        return True
    except Exception as ex:
        mal(f"{type(ex).__name__}: {ex}")
        return False


# ----------------------------------------------------------------------- Graph

def probar_graph():
    print("\nHotmail via Microsoft Graph")
    cid = E.get('GRAPH_CLIENT_ID')
    if not cid:
        aviso("sin configurar (GRAPH_CLIENT_ID). Ver FASE1_SETUP.md paso 1.")
        return False
    try:
        import msal
    except ImportError:
        mal("falta la libreria msal:  pip install msal")
        return False

    autoridad = f"https://login.microsoftonline.com/{E.get('GRAPH_AUTHORITY', 'consumers')}"
    cache = msal.SerializableTokenCache()
    if os.path.exists(CACHE_GRAPH):
        cache.deserialize(open(CACHE_GRAPH, encoding='utf-8').read())

    app = msal.PublicClientApplication(cid, authority=autoridad, token_cache=cache)
    scopes = ['Mail.Read']

    res = None
    cuentas = app.get_accounts()
    if cuentas:
        nota(f"ya hay un token guardado para {cuentas[0].get('username')}")
        res = app.acquire_token_silent(scopes, account=cuentas[0])

    if not res:
        flujo = app.initiate_device_flow(scopes=scopes)
        if 'user_code' not in flujo:
            mal(f"no pude iniciar el device flow: {flujo.get('error_description', flujo)}")
            nota("casi siempre es que falta poner 'Allow public client flows' = Yes")
            return False
        print()
        print(f"  {AMAR}==> Abre {flujo['verification_uri']} y escribe el codigo:  "
              f"{flujo['user_code']}{FIN}")
        print(f"  {GRIS}(esperando... esto solo pasa una vez){FIN}")
        res = app.acquire_token_by_device_flow(flujo)

    if 'access_token' not in res:
        mal(f"{res.get('error')}: {res.get('error_description', '')[:300]}")
        return False

    with open(CACHE_GRAPH, 'w', encoding='utf-8') as fh:
        fh.write(cache.serialize())
    ok("token obtenido y guardado en .cache_graph.json")

    cab = {'Authorization': 'Bearer ' + res['access_token']}
    try:
        yo = http('https://graph.microsoft.com/v1.0/me', cabeceras=cab)
        ok(f"cuenta: {yo.get('userPrincipalName') or yo.get('mail') or yo.get('displayName')}")
    except urllib.error.HTTPError as ex:
        aviso(f"/me devolvio {ex.code} (normal en cuentas personales sin User.Read)")

    # lo que de verdad importa: puede leer las alertas del banco?
    consulta = ("https://graph.microsoft.com/v1.0/me/messages"
                "?$search=%22from:notificacionesbancolombia.com%22&$top=5"
                "&$select=subject,receivedDateTime,from")
    try:
        r = http(consulta, cabeceras=cab)
        msgs = r.get('value', [])
        ok(f"lectura de correo funciona — {len(msgs)} alertas de Bancolombia en la muestra")
        for m in msgs[:3]:
            nota(f"{m.get('receivedDateTime', '')[:10]}  {m.get('subject', '')[:58]}")
        if not msgs:
            aviso("no encontro alertas. Revisa que este buzon sea el que recibe los correos"
                  " del banco, o que no esten en una carpeta excluida de la busqueda.")
        return True
    except urllib.error.HTTPError as ex:
        mal(f"no pude leer correo: HTTP {ex.code} {ex.read().decode('utf-8', 'replace')[:200]}")
        nota("si dice 'insufficient privileges', falta el permiso Mail.Read en Entra")
        return False


# ------------------------------------------------------------------ Gmail IMAP

def probar_gmail():
    print("\nGmail via IMAP")
    usuario, clave = E.get('GMAIL_USUARIO'), E.get('GMAIL_APP_PASSWORD')
    if not usuario or not clave:
        aviso("sin configurar (GMAIL_USUARIO / GMAIL_APP_PASSWORD)")
        return False
    import imaplib
    clave = clave.replace(' ', '')          # Google lo muestra en bloques de 4
    if len(clave) != 16:
        aviso(f"el app password tiene {len(clave)} caracteres, se esperaban 16")
    try:
        M = imaplib.IMAP4_SSL('imap.gmail.com', 993)
    except Exception as ex:
        mal(f"no pude conectar a imap.gmail.com: {ex}")
        return False
    try:
        M.login(usuario, clave)
    except imaplib.IMAP4.error as ex:
        mal(f"login rechazado: {ex}")
        nota("causas tipicas: falta activar verificacion en 2 pasos, es la clave normal")
        nota("de la cuenta y no un app password, o IMAP esta apagado en Gmail.")
        return False
    ok(f"login correcto como {usuario}")
    try:
        M.select('INBOX', readonly=True)
        typ, datos = M.search(None, '(ALL)')
        total = len(datos[0].split()) if datos and datos[0] else 0
        ok(f"INBOX accesible, {total} mensajes")
    except Exception as ex:
        mal(f"no pude leer INBOX: {ex}")
    finally:
        try:
            M.logout()
        except Exception:
            pass
    return True


# -------------------------------------------------------------------- Telegram

def probar_telegram():
    print("\nTelegram")
    tok = E.get('TELEGRAM_TOKEN')
    if not tok:
        aviso("sin configurar (TELEGRAM_TOKEN). Ver FASE1_SETUP.md paso 3.")
        return False
    base = f"https://api.telegram.org/bot{tok}"
    try:
        yo = http(base + '/getMe')
    except urllib.error.HTTPError as ex:
        mal(f"token rechazado (HTTP {ex.code}). Revisa que lo pegaste completo.")
        return False
    except Exception as ex:
        mal(f"{type(ex).__name__}: {ex}")
        return False
    if not yo.get('ok'):
        mal(f"getMe respondio: {yo}")
        return False
    b = yo['result']
    ok(f"bot @{b.get('username')} ({b.get('first_name')})")

    try:
        upd = http(base + '/getUpdates')
        chats = {}
        for u in upd.get('result', []):
            msg = u.get('message') or u.get('edited_message') or {}
            ch = msg.get('chat') or {}
            if ch.get('id'):
                chats[ch['id']] = (
                    f"{ch.get('first_name', '')} {ch.get('last_name', '')}".strip()
                    or ch.get('username') or ch.get('title') or '?')
        if chats:
            ok(f"{len(chats)} chat(s) que ya le escribieron:")
            for cid, nombre in chats.items():
                nota(f"chat_id = {cid}   ({nombre})")
            nota("copia esos numeros a TELEGRAM_CHAT_ID_JUAN / _NOVIA en el .env")
        else:
            aviso("nadie le ha escrito todavia. Abre Telegram, busca el bot por su")
            aviso(f"usuario @{b.get('username')}, mandale /start, y vuelve a correr esto.")
    except Exception as ex:
        aviso(f"no pude leer getUpdates: {ex}")

    destino = E.get('TELEGRAM_CHAT_ID_JUAN')
    if destino:
        try:
            payload = json.dumps({
                'chat_id': destino,
                'text': 'Prueba desde verificar.py — si lees esto, el bot quedo listo.'
            }).encode()
            r = http(base + '/sendMessage', datos=payload,
                     cabeceras={'Content-Type': 'application/json'})
            if r.get('ok'):
                ok(f"mensaje de prueba enviado al chat {destino}")
        except Exception as ex:
            mal(f"no pude enviar al chat {destino}: {ex}")
    return True


PRUEBAS = {
    'firefly': probar_firefly,
    'graph': probar_graph,
    'gmail': probar_gmail,
    'telegram': probar_telegram,
}


def main():
    pedidas = [a.lower() for a in sys.argv[1:]] or list(PRUEBAS)
    malas = [p for p in pedidas if p not in PRUEBAS]
    if malas:
        sys.exit(f"no conozco: {', '.join(malas)}\nopciones: {', '.join(PRUEBAS)}")

    if not os.path.exists(ENV_LOCAL):
        print(f"{AMAR}No existe {ENV_LOCAL}{FIN}")
        print(f"{GRIS}Copia .env.ejemplo a .env y llena lo que tengas. "
              f"Lo que este vacio se salta.{FIN}")

    print("=" * 62)
    print("Verificacion de credenciales — Fase 1")
    print("=" * 62)

    res = {}
    for p in pedidas:
        try:
            res[p] = PRUEBAS[p]()
        except KeyboardInterrupt:
            print("\ncancelado")
            sys.exit(130)
        except Exception as ex:
            mal(f"error inesperado en {p}: {type(ex).__name__}: {ex}")
            res[p] = False

    print("\n" + "=" * 62)
    listos = [p for p, v in res.items() if v]
    faltan = [p for p, v in res.items() if not v]
    print(f"Listos:  {', '.join(listos) if listos else '(ninguno)'}")
    if faltan:
        print(f"Faltan:  {', '.join(faltan)}")
        print("\nCuando los cuatro esten en verde, avisame y sigo con la ingesta.")
    else:
        print("\nTodo verde. Avisame y arranco la ingesta.")
    print("=" * 62)


if __name__ == '__main__':
    main()
