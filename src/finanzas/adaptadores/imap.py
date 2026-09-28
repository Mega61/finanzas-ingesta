"""Baja correo por IMAP. Es el camino de Gmail, con app password.

Por que IMAP y no la API de Gmail: `gmail.readonly` es un scope restringido, y
mientras la app de Google no pase una auditoria el refresh token se vence cada
7 dias. Con 2 pasos activado, el app password sobre IMAP no vence.

Tres cosas que parecen detalles y no lo son:

- **La carpeta de «Todos» se busca por su bandera `\\All`, no por el nombre.**
  En una cuenta en espanol no se llama "[Gmail]/All Mail" sino "[Gmail]/Todos",
  y con el nombre fijo el buzon parecia vacio. Se lee esa carpeta y no INBOX
  porque un filtro de Gmail puede archivar las alertas.
- **El cursor es el UID, y el UID solo vale con su UIDVALIDITY.** Si el
  servidor cambia UIDVALIDITY, los UID viejos no significan nada y se vuelve a
  buscar por fecha. El Message-ID sigue siendo la clave de dedupe, asi que
  releer no duplica.
- **Solo se bajan los remitentes del banco.** Es correo personal: lo demas no
  tiene por que entrar a la base.
"""

from __future__ import annotations

import contextlib
import email
import imaplib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from email import policy
from email.message import EmailMessage

from finanzas.dominio import fechas

REMITENTES = ('notificacionesbancolombia.com',)
TIMEOUT = 60

# IMAP pide el mes en ingles ('01-Sep-2026'). strftime('%b') lo da en el idioma
# del sistema, y en un contenedor en espanol la busqueda falla sin decir por que.
_MESES = (
    'Jan',
    'Feb',
    'Mar',
    'Apr',
    'May',
    'Jun',
    'Jul',
    'Aug',
    'Sep',
    'Oct',
    'Nov',
    'Dec',
)


def fecha_imap(d: date) -> str:
    return f'{d.day:02d}-{_MESES[d.month - 1]}-{d.year}'


class SinAutorizacion(Exception):
    """El servidor rechazo la clave: casi siempre, un app password revocado."""


@dataclass(frozen=True)
class Correo:
    uid: int
    message_id: str
    remitente: str
    asunto: str
    fecha: str
    mensaje: EmailMessage


def _carpeta_de_todos(cx: imaplib.IMAP4) -> str:
    """El nombre de la carpeta con la bandera \\All, entre comillas."""
    tipo, carpetas = cx.list()
    if tipo == 'OK':
        for c in carpetas or []:
            linea = c.decode('utf-8', 'replace') if isinstance(c, bytes) else str(c)
            if '\\All' in linea:
                m = re.search(r'"([^"]+)"\s*$', linea) or re.search(r'(\S+)\s*$', linea)
                if m:
                    return f'"{m.group(1)}"'
    return 'INBOX'


def _abrir(host: str, usuario: str, clave: str) -> imaplib.IMAP4_SSL:
    nombre, _, puerto = host.partition(':')
    cx = imaplib.IMAP4_SSL(nombre, int(puerto or 993), timeout=TIMEOUT)
    try:
        cx.login(usuario, clave.replace(' ', ''))
    except imaplib.IMAP4.error as ex:
        raise SinAutorizacion(f'{usuario}: {ex}') from None
    return cx


def bajar(
    host: str,
    usuario: str,
    clave: str,
    cursor: str | None = None,
    dias: int = 30,
    remitentes: tuple[str, ...] = REMITENTES,
) -> tuple[list[Correo], str | None]:
    """Los correos del banco nuevos desde el cursor. Devuelve (correos, cursor).

    `cursor` es "UIDVALIDITY:UID" del ultimo leido. Sin cursor, o si
    UIDVALIDITY cambio, busca los ultimos `dias` dias.
    """
    cx = _abrir(host, usuario, clave)
    try:
        carpeta = _carpeta_de_todos(cx)
        tipo, datos = cx.select(carpeta, readonly=True)
        if tipo != 'OK':
            raise RuntimeError(f'no pude abrir {carpeta}: {datos}')
        validez = _uidvalidity(cx)
        ultimo = _ultimo_uid(cursor, validez)

        uids: set[int] = set()
        for rem in remitentes:
            if ultimo is not None:
                criterio = ['UID', f'{ultimo + 1}:*', 'FROM', f'"{rem}"']
            else:
                desde = fecha_imap(fechas.hoy() - timedelta(days=dias))
                criterio = ['SINCE', desde, 'FROM', f'"{rem}"']
            tipo, r = cx.uid('SEARCH', *criterio)
            if tipo == 'OK' and r and r[0]:
                uids |= {int(x) for x in r[0].split()}
        # `UID n:*` devuelve el ultimo aunque sea menor que n: se filtra.
        if ultimo is not None:
            uids = {u for u in uids if u > ultimo}

        correos = list(_leer(cx, sorted(uids)))
        tope = max([ultimo or 0, *uids]) if (uids or ultimo) else None
        nuevo_cursor = f'{validez}:{tope}' if tope else cursor
        return correos, nuevo_cursor
    finally:
        with contextlib.suppress(imaplib.IMAP4.error, OSError):
            cx.logout()


def _uidvalidity(cx: imaplib.IMAP4) -> str:
    r = cx.response('UIDVALIDITY')
    valores = r[1] if r and len(r) > 1 else None
    if valores and valores[0]:
        v = valores[0]
        return v.decode() if isinstance(v, bytes) else str(v)
    return '0'


def _ultimo_uid(cursor: str | None, validez: str) -> int | None:
    if not cursor or ':' not in cursor:
        return None
    v, _, uid = cursor.partition(':')
    if v != validez or not uid.isdigit():
        return None
    return int(uid)


def _leer(cx: imaplib.IMAP4, uids: list[int]) -> Iterator[Correo]:
    for uid in uids:
        # BODY.PEEK no marca el correo como leido: es el buzon de la persona.
        tipo, datos = cx.uid('FETCH', str(uid), '(BODY.PEEK[])')
        if tipo != 'OK' or not datos or not isinstance(datos[0], tuple):
            continue
        msg = email.message_from_bytes(datos[0][1], policy=policy.default)
        yield Correo(
            uid=uid,
            message_id=str(msg.get('message-id') or f'<imap-{uid}@{cx.host}>'),
            remitente=str(msg.get('from') or ''),
            asunto=str(msg.get('subject') or ''),
            fecha=str(msg.get('date') or ''),
            mensaje=msg,
        )
