"""Baja correo de Hotmail / Outlook.com con Microsoft Graph.

Por que Graph y no IMAP: en cuentas personales de Microsoft el app password
para IMAP esta muerto desde ~octubre de 2024 (responde NO LOGIN). Queda OAuth,
y ya que hay que registrar la app en Entra de todas formas, Graph es mejor que
XOAUTH2 sobre IMAP: JSON en vez de la maquina de estados de IMAP, y consultas
`delta` para traer solo lo nuevo.

El refresh token se guarda en un cache de MSAL en el volumen de datos. Los de
cuenta personal rotan en cada uso y no expiran mientras se usen, asi que con
sync diario nunca caduca.
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from finanzas import config, registro
from finanzas.adaptadores import db

GRAPH = 'https://graph.microsoft.com/v1.0'
CACHE = os.path.join(config.DATOS, '.cache_graph.json')

# Solo nos interesan estos remitentes. Cualquier otra cosa en el buzon no se
# baja: es correo personal y no tiene por que entrar a la base.
REMITENTES = [
    'notificacionesbancolombia.com',
    'documentosbancolombia.com',
]

# Las facturas electronicas NO se filtran por remitente, y es a proposito.
# Exito manda desde efactura@exito.com, D1 desde Felectronica@d1.com.co y
# Supervaquita desde siesafe@siesa.com, que es su proveedor de facturacion y
# le factura a medio pais. Filtrar por dominio significa editar el codigo cada
# vez que cambies de supermercado.
#
# Lo que si es estable es el asunto que exige la DIAN:
#     NIT;RAZON SOCIAL;NUMERO;TIPO;NOMBRE COMERCIAL
# Los 430 correos del archivo local lo cumplen, los tres proveedores incluidos.
# Con esto un supermercado nuevo funciona el dia que le compres.
ASUNTO_DIAN = re.compile(r'^\s*\d{6,12};[^;]+;[^;]+;\d{2};')


class SinAutorizacion(Exception):
    """No hay token y no se puede pedir uno sin que el usuario intervenga."""


def _app():
    import msal

    client_id = config.get('GRAPH_CLIENT_ID')
    if not client_id:
        raise SinAutorizacion('falta GRAPH_CLIENT_ID')
    autoridad = 'https://login.microsoftonline.com/' + config.get(
        'GRAPH_AUTHORITY', 'consumers'
    )
    cache = msal.SerializableTokenCache()
    if os.path.exists(CACHE):
        with open(CACHE, encoding='utf-8') as fh:
            cache.deserialize(fh.read())
    app = msal.PublicClientApplication(
        client_id, authority=autoridad, token_cache=cache
    )
    return app, cache


def _guardar_cache(cache):
    if cache.has_state_changed:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with open(CACHE, 'w', encoding='utf-8') as fh:
            fh.write(cache.serialize())


def token(interactivo=False):
    """Token de acceso. En el servidor NUNCA es interactivo: si el cache no
    sirve, se levanta SinAutorizacion y el bot avisa por Telegram."""
    app, cache = _app()
    scopes = ['Mail.Read']
    cuentas = app.get_accounts()
    if cuentas:
        res = app.acquire_token_silent(scopes, account=cuentas[0])
        if res and 'access_token' in res:
            _guardar_cache(cache)
            return res['access_token']

    # Arranque en frio dentro de un contenedor: no hay cache y no se puede
    # hacer el device flow porque nadie va a ver el codigo. Con el refresh
    # token en una variable de entorno se siembra el cache y de ahi en
    # adelante rota solo.
    refresh = config.get('GRAPH_REFRESH_TOKEN')
    if refresh:
        res = app.acquire_token_by_refresh_token(refresh, scopes)
        if res and 'access_token' in res:
            _guardar_cache(cache)
            return res['access_token']
        if not interactivo:
            raise SinAutorizacion(
                f'GRAPH_REFRESH_TOKEN no sirvio: '
                f'{res.get("error")}: {res.get("error_description", "")[:200]}. '
                'Vuelve a sacarlo con: python verificar.py graph'
            )

    if not interactivo:
        raise SinAutorizacion(
            'no hay token de Graph. Pon GRAPH_REFRESH_TOKEN en el entorno, o '
            'corre en tu maquina: python verificar.py graph'
        )

    flujo = app.initiate_device_flow(scopes=scopes)
    if 'user_code' not in flujo:
        raise SinAutorizacion(
            f'no pude iniciar device flow: {flujo.get("error_description", flujo)}. '
            "Casi siempre falta poner 'Allow public client flows' = Yes en Entra."
        )
    registro.aviso(
        f'\nAbre {flujo["verification_uri"]} y escribe el codigo: '
        f'{flujo["user_code"]}\n'
    )
    res = app.acquire_token_by_device_flow(flujo)
    if 'access_token' not in res:
        raise SinAutorizacion(f'{res.get("error")}: {res.get("error_description")}')
    _guardar_cache(cache)
    return res['access_token']


def _http(url, tok):
    req = urllib.request.Request(url)
    req.add_header('Authorization', 'Bearer ' + tok)
    req.add_header('Accept', 'application/json')
    # Sin esto Graph devuelve el cuerpo en HTML; lo queremos en texto plano.
    req.add_header('Prefer', 'outlook.body-content-type="text"')
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as ex:
        cuerpo = ex.read().decode('utf-8', 'replace')
        raise RuntimeError(f'Graph HTTP {ex.code}: {cuerpo[:400]}') from None


def _es_del_banco(msg):
    frm = ((msg.get('from') or {}).get('emailAddress') or {}).get('address', '')
    return any(d in frm.lower() for d in REMITENTES)


def es_factura(msg):
    """Una factura electronica: el asunto con formato DIAN y algo adjunto."""
    asunto = msg.get('subject') or ''
    return bool(ASUNTO_DIAN.match(asunto)) and bool(msg.get('hasAttachments'))


def _nos_interesa(msg):
    return _es_del_banco(msg) or es_factura(msg)


CAMPOS = (
    'id,internetMessageId,subject,receivedDateTime,from,body,bodyPreview,hasAttachments'
)


def adjuntos_zip(tok, mensaje_id):
    """Los ZIP de un correo, como (nombre, bytes).

    Graph devuelve el contenido en base64 dentro del mismo JSON mientras el
    adjunto sea chico — los de factura pesan 40-70 KB, o sea que nunca hay que
    pedir el stream aparte.
    """
    import base64

    url = f'{GRAPH}/me/messages/{urllib.parse.quote(mensaje_id)}/attachments'
    datos = _http(url, tok)
    fuera = []
    for a in datos.get('value', []):
        nombre = a.get('name') or ''
        contenido = a.get('contentBytes')
        if not contenido:
            continue
        if not nombre.lower().endswith('.zip'):
            continue
        try:
            fuera.append((nombre, base64.b64decode(contenido)))
        except Exception as ex:  # adjunto corrupto: no tumba la pasada
            registro.aviso(f'adjunto ilegible en {nombre}: {ex}')
    return fuera


def mensajes(tok, desde=None, tope=None):
    """Itera los mensajes del banco, del mas nuevo al mas viejo.

    `desde` es una fecha ISO: solo trae lo recibido despues. Se usa el filtro
    del servidor para no bajar el buzon completo.
    """
    filtros = []
    if desde:
        filtros.append(f'receivedDateTime gt {desde}')
    q = {
        '$select': CAMPOS,
        '$top': '50',
        '$orderby': 'receivedDateTime desc',
    }
    if filtros:
        q['$filter'] = ' and '.join(filtros)
    url = f'{GRAPH}/me/messages?' + urllib.parse.urlencode(q)

    vistos = 0
    while url:
        datos = _http(url, tok)
        for m in datos.get('value', []):
            if not _nos_interesa(m):
                continue
            yield m
            vistos += 1
            if tope and vistos >= tope:
                return
        url = datos.get('@odata.nextLink')


def cuerpo_plano(msg):
    """El texto del correo. Con la cabecera Prefer ya viene en texto, pero si
    llegara HTML se desarma igual."""
    b = msg.get('body') or {}
    contenido = b.get('content') or msg.get('bodyPreview') or ''
    if (b.get('contentType') or '').lower() == 'html':
        t = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', contenido, flags=re.S | re.I)
        t = re.sub(r'<[^>]+>', ' ', t)
        t = t.replace('&nbsp;', ' ').replace('&amp;', '&')
        contenido = re.sub(r'&[a-z]+;', ' ', t)
    return contenido


def bajar(cx, buzon_id, desde=None, tope=None, interactivo=False):
    """Baja lo nuevo a correos_crudos. Devuelve (nuevos, repetidos).

    De las facturas electronicas guarda ademas el XML de adentro del ZIP, en
    `facturas_crudas`. Se hace aqui y no en el parser porque el adjunto solo
    se puede pedir mientras se tenga el id del mensaje en Graph: despues el
    correo crudo ya no lo tiene.
    """

    tok = token(interactivo=interactivo)
    nuevos = repetidos = 0
    for m in mensajes(tok, desde=desde, tope=tope):
        mid = m.get('internetMessageId') or m.get('id')
        frm = ((m.get('from') or {}).get('emailAddress') or {}).get('address', '')
        correo_id, era_nuevo = db.correo_guardar(
            cx,
            buzon_id,
            mid,
            frm,
            m.get('subject'),
            m.get('receivedDateTime'),
            cuerpo_plano(m),
        )
        if era_nuevo:
            nuevos += 1
            if es_factura(m):
                _guardar_xml(cx, correo_id, tok, m)
        else:
            repetidos += 1
    return nuevos, repetidos


def _guardar_xml(cx, correo_id, tok, msg):
    """Saca los XML del ZIP adjunto y los deja en facturas_crudas."""
    import io as _io
    import zipfile

    for nombre, crudo in adjuntos_zip(tok, msg['id']):
        try:
            z = zipfile.ZipFile(_io.BytesIO(crudo))
        except zipfile.BadZipFile:
            registro.aviso(f'ZIP ilegible: {nombre}')
            continue
        for interno in z.namelist():
            if not interno.lower().endswith('.xml'):
                continue
            db.factura_cruda_guardar(
                cx, correo_id, interno, z.read(interno).decode('utf-8', 'replace')
            )
