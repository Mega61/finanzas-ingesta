# -*- coding: utf-8 -*-
"""Parser de las alertas por correo de Bancolombia.

Convierte un .eml de alertasynotificaciones@an.notificacionesbancolombia.com
en un Evento normalizado. Las 71 plantillas distintas que aparecen en el
archivo historico se reducen a 10 familias.

OJO: estas alertas NO son fuente de verdad para montos. Uber preautoriza el
precio estimado y despues cobra la tarifa real. Ver 09_ALERTAS_Y_UBERS.md.
Por eso todo evento que salga de aqui es provisional.
"""
import email
import email.utils
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from email import policy
from typing import Optional

REMITENTE = 'an.notificacionesbancolombia.com'


@dataclass
class Evento:
    tipo: str                      # compra_tarjeta | pago_qr | transferencia_salida | ...
    fecha: Optional[date]
    hora: Optional[str]
    moneda: str
    valor: float                   # negativo = sale plata, desde tu punto de vista
    instrumento: Optional[str]     # ultimos 4 digitos del producto
    clase_instrumento: str         # tarjeta | cuenta
    contraparte: str
    descripcion: str
    plantilla: str                 # que familia lo cazo, para depurar
    traslado_a: Optional[str] = None   # si es plata moviendose entre productos
                                       # propios, los 4 digitos del otro
    crudo: str = field(repr=False, default='')

    def dict(self):
        d = asdict(self)
        d['fecha'] = self.fecha.isoformat() if self.fecha else None
        d.pop('crudo', None)
        return d


# ---------------------------------------------------------------- utilidades

def _sin_tildes(t):
    return ''.join(c for c in unicodedata.normalize('NFD', t)
                   if unicodedata.category(c) != 'Mn')


def parse_monto(s):
    """Los montos vienen en dos formatos mezclados, a veces en el mismo correo:
    '178.679,08' (colombiano) y '205,967.00' (gringo). Tambien '9,000' sin
    decimales, que es 9000 y no 9.

    Regla: manda el ULTIMO separador. Si va seguido de exactamente 2 digitos
    y ahi termina el numero, es el separador decimal; si no, todos los
    separadores son de miles.
    """
    s = s.strip().replace(' ', '')
    m = re.search(r'[.,](\d+)$', s)
    if m and len(m.group(1)) == 2:
        entero = s[:m.start()].replace('.', '').replace(',', '')
        return float(f"{entero or 0}.{m.group(1)}")
    return float(re.sub(r'[.,]', '', s))


def parse_fecha(s):
    """dd/mm/yyyy, dd/mm/yy, yyyy/mm/dd."""
    s = (s or '').strip()
    for fmt in ('%d/%m/%Y', '%d/%m/%y', '%Y/%m/%d', '%d-%m-%Y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


_ES_HORA = re.compile(r'^\d{1,2}:\d{2}(:\d{2})?$')


def _ordenar_fecha_hora(a, b):
    """Varias plantillas traen la fecha y la hora invertidas:
    'el 07:57 a las 10/12/2025'. Se decide por la forma, no por la posicion.
    """
    if _ES_HORA.match(a or ''):
        return b, a
    return a, b


def _html_a_texto(h):
    t = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', h, flags=re.S | re.I)
    t = re.sub(r'<[^>]+>', ' ', t)
    t = t.replace('&nbsp;', ' ').replace('&amp;', '&')
    t = re.sub(r'&[a-z]+;', ' ', t)
    return re.sub(r'\[https?://[^\]]+\]', ' ', t)


def cuerpo_mensaje(msg):
    """Prefiere text/plain (Bancolombia manda la misma frase, mas limpia).
    Unos pocos correos vienen solo en HTML, y ahi hay que desarmarlo."""
    plano = ''
    for parte in msg.walk():
        if parte.get_content_type() == 'text/plain':
            try:
                plano = parte.get_content()
            except Exception:
                plano = ''
            if plano.strip():
                return plano
    for parte in msg.walk():
        if parte.get_content_type() == 'text/html':
            try:
                return _html_a_texto(parte.get_content())
            except Exception:
                pass
    return plano


def cuerpo_texto(ruta):
    with open(ruta, encoding='utf-8', errors='replace') as fh:
        msg = email.message_from_file(fh, policy=policy.default)
    return msg, cuerpo_mensaje(msg)


def _normalizar(texto):
    t = _sin_tildes(texto.replace('�', ' '))
    return re.sub(r'\s+', ' ', t).strip()


# -------------------------------------------------------------- a la basura
# Correos que traen monto y pinta de transaccion pero NO son un movimiento.
# El primero es el importante: una compra rechazada nunca llega al extracto,
# y si se cuela infla el gasto. Los demas son avisos administrativos.

IGNORAR = [
    ('compra_rechazada',   r'tu compra con T\.?\s?cred.*?no fue exitosa'),
    ('debito_no_ejecutado', r'el debito Programado.*?no fue ejecutado'),
    ('factura_inscrita',   r'Inscribiste las facturas servicios convenio'),
    ('factura_programada', r'Programaste la factura del convenio'),
    ('factura_disponible', r'(su factura inscrita.*?se vence|La factura que inscribiste.*?esta lista)'),
    ('clave_dinamica',     r'(Inscribiste tu Clave Dinamica|inscribiste al servicio de Clave Dinamica)'),
    ('topes',              r'Actualizaste los topes de tus transacciones'),
    # Los avisos de extracto no son movimientos, pero si son la materia prima
    # de la Fase 5: hay que guardarlos y no contarlos como plantilla nueva.
    ('aviso_extracto',     r'(disponible tu extracto|tu extracto (mensual|del mes|para el)'
                           r'|los extractos de tus|extracto de tu cuenta'
                           r'|Recibe Tus Extractos|Reporte Anual Costos Totales'
                           r'|archivo esta protegido con una clave)'),
]
IGNORAR = [(n, re.compile(p, re.I)) for n, p in IGNORAR]


def motivo_ignorar(texto_normalizado):
    """Devuelve el nombre de la regla si el correo hay que descartarlo."""
    for nombre, rx in IGNORAR:
        if rx.search(texto_normalizado):
            return nombre
    return None


# ------------------------------------------------------------------ familias

_P = []


def _familia(nombre, patron):
    def deco(fn):
        _P.append((nombre, re.compile(patron, re.I), fn))
        return fn
    return deco


@_familia('compra_tcred', r'Compraste\s+(COP|USD)\s*([\d.,]+)\s+en\s+(.+?)\s*con tu T\.?\s?Cred\s*\*+\s?(\d{3,4})\s*,?\s*el\s+([\d/]+)\s+a las\s+([\d:]+)')
def _f1(m, msg):
    f, h = _ordenar_fecha_hora(m.group(5), m.group(6))
    com = m.group(3).strip(' .,')
    return Evento('compra_tarjeta', parse_fecha(f), h, m.group(1).upper(),
                  -parse_monto(m.group(2)), m.group(4), 'tarjeta',
                  com, com, 'compra_tcred')


@_familia('compra_asociada', r'Compraste\s+(COP|USD)\s*([\d.,]+)\s+en\s+(.+?)\s*,\s*el\s+([\d/:]+)\s+a las\s+([\d/:]+)\.?\s*Esta compra esta asociada a T\.?\s?Cred\s*\*+\s?(\d{3,4})')
def _f2(m, msg):
    f, h = _ordenar_fecha_hora(m.group(4), m.group(5))
    com = m.group(3).strip(' .,')
    return Evento('compra_tarjeta', parse_fecha(f), h, m.group(1).upper(),
                  -parse_monto(m.group(2)), m.group(6), 'tarjeta',
                  com, com, 'compra_asociada')


@_familia('pago_qr', r'pagaste\s+\$\s*([\d.,]+)\s+por codigo QR desde tu cuenta\s*\*+\s?(\d{3,4})\s+a la llave\s+(\S+)\s+el\s+([\d/]+)\s+a las\s+([\d:]+)')
def _f3(m, msg):
    return Evento('pago_qr', parse_fecha(m.group(4)), m.group(5), 'COP',
                  -parse_monto(m.group(1)), m.group(2), 'cuenta',
                  m.group(3), f"PAGO QR llave {m.group(3)}", 'pago_qr')


@_familia('transf_llave', r'transferiste\s+\$\s*([\d.,]+)\s+a la llave\s+(\S+)\s+desde tu cuenta\s*\*+\s?(\d{3,4})\s+a\s+(.+?)\s+el\s+([\d/]+)\s+a las\s+([\d:]+)')
def _f4(m, msg):
    dest = m.group(4).strip()
    return Evento('transferencia_salida', parse_fecha(m.group(5)), m.group(6), 'COP',
                  -parse_monto(m.group(1)), m.group(3), 'cuenta',
                  dest, f"TRANSF A {dest}", 'transf_llave')


@_familia('transf_cuenta', r'Transferiste\s+\$\s*([\d.,]+)\s+(?:por QR\s+)?desde tu cuenta\s*\*?\s?(\d{3,4})\s+a la cuenta\s*\*?\s?(\d+)\s*,?\s*el\s+([\d/]+)(?:\s+a las)?\s+([\d:]+)')
def _f5(m, msg):
    return Evento('transferencia_salida', parse_fecha(m.group(4)), m.group(5), 'COP',
                  -parse_monto(m.group(1)), m.group(2), 'cuenta',
                  m.group(3), f"TRANSF A CUENTA {m.group(3)}", 'transf_cuenta')


@_familia('transf_entrada', r'recibiste una transferencia de\s+(.+?)\s+por\s+\$\s*([\d.,]+)\s+en tu cuenta\s*\*+\s?(\d{3,4}).*?el\s+([\d/]+)\s+a las\s+([\d:]+)')
def _f6(m, msg):
    org = m.group(1).strip()
    return Evento('transferencia_entrada', parse_fecha(m.group(4)), m.group(5), 'COP',
                  parse_monto(m.group(2)), m.group(3), 'cuenta',
                  org, f"TRANSF DE {org}", 'transf_entrada')


@_familia('pago_producto', r'Pagaste\s+\$\s*([\d.,]+)\s+a\s+(.+?)\s+desde tu producto\s*\*?\s?(\d{3,4})\s+el\s+([\d/]+)\s+([\d:]+)')
def _f7(m, msg):
    ben = m.group(2).strip()
    return Evento('pago_factura', parse_fecha(m.group(4)), m.group(5), 'COP',
                  -parse_monto(m.group(1)), m.group(3), 'cuenta',
                  ben, f"PAGO A {ben}", 'pago_producto')


@_familia('ingreso_nomina', r'Recibiste un pago\s+(?:de\s+)?(Nomina|PROVEEDOR)?\s*(?:de\s+)?(.+?)\s+por\s+\$\s*([\d.,]+)\s+en tu cuenta de Ahorros\s+el\s+([\d/]+)\s+a las\s+([\d:]+)')
def _f8(m, msg):
    concepto = (m.group(1) or 'pago').capitalize()
    org = m.group(2).strip()
    return Evento('ingreso', parse_fecha(m.group(4)), m.group(5), 'COP',
                  parse_monto(m.group(3)), None, 'cuenta',
                  org, f"{concepto} {org}", 'ingreso_nomina')


@_familia('ingreso_cuenta', r'Recibiste un pago por\s+\$\s*([\d.,]+)\s+de\s+(.+?)\s+a tu cuenta\s+(\S+?)\s*,\s*el\s+([\d/:]+)\s+a las\s+([\d/:]+)')
def _f9(m, msg):
    f, h = _ordenar_fecha_hora(m.group(4), m.group(5))
    org = m.group(2).strip()
    return Evento('ingreso', parse_fecha(f), h, 'COP',
                  parse_monto(m.group(1)), None, 'cuenta',
                  org, f"Pago {org}", 'ingreso_cuenta')


@_familia('debito_tarjeta', r'Debitamos de tu cuenta/bolsillo\s+\$\s*([\d.,]+)\s+para abonar a la deuda.*?tarjeta de credito\s+(\S+)\s*\*+\s?(\d{3,4})')
def _f10(m, msg):
    # esta plantilla no trae fecha en el texto: se usa la del correo
    fecha = None
    try:
        fecha = email.utils.parsedate_to_datetime(msg['date']).date()
    except Exception:
        pass
    # la plata sale de la cuenta y abona la deuda de la tarjeta: es un traslado
    ev = Evento('pago_tarjeta', fecha, None, 'COP',
                -parse_monto(m.group(1)), None, 'cuenta',
                f"{m.group(2)} {m.group(3)}",
                f"ABONO DEUDA {m.group(2)} {m.group(3)}", 'debito_tarjeta')
    ev.traslado_a = m.group(3)
    return ev


@_familia('boton_bancolombia', r'Transferiste\s+\$\s*([\d.,]+)\s+por Boton Bancolombia a\s+(.+?)\s+desde producto\s*\*+\s?(\d{3,4})\.?\s*([\d/]+)\s+([\d:]+)')
def _f11(m, msg):
    ben = m.group(2).strip()
    return Evento('pago_pse', parse_fecha(m.group(4)), m.group(5), 'COP',
                  -parse_monto(m.group(1)), m.group(3), 'cuenta',
                  ben, f"PAGO PSE {ben}", 'boton_bancolombia')


@_familia('transf_entrada_simple', r'Recibiste una transferencia por\s+\$\s*([\d.,]+)\s+de\s+(.+?)\s+en tu cuenta\s*\*+\s?(\d{3,4})\s*,?\s*el\s+([\d/]+)\s+a las\s+([\d:]+)')
def _f12(m, msg):
    org = m.group(2).strip()
    return Evento('transferencia_entrada', parse_fecha(m.group(4)), m.group(5), 'COP',
                  parse_monto(m.group(1)), m.group(3), 'cuenta',
                  org, f"TRANSF DE {org}", 'transf_entrada_simple')


@_familia('avance', r'Hiciste un avance de\s+\$\s*([\d.,]+)\s+en tu\s+(.+?)\s+el\s+([\d/:]+)\s+([\d/:]+)\s+desde tu T\.?\s?Credito\s*\*+\s?(\d{3,4})\s+a la cuenta\s*\*+\s?(\d{3,4})')
def _f13(m, msg):
    # Un avance no aparece como compra en el extracto de la tarjeta: sube la
    # deuda y mete efectivo en la cuenta. En Firefly es un traslado.
    f, h = _ordenar_fecha_hora(m.group(3), m.group(4))
    ev = Evento('avance', parse_fecha(f), h, 'COP',
                parse_monto(m.group(1)), m.group(5), 'tarjeta',
                f"AVANCE {m.group(2).strip()}",
                f"AVANCE a cuenta {m.group(6)}", 'avance')
    ev.traslado_a = m.group(6)
    return ev


# --------------------------------------------------------------------- API

class Descartado(Exception):
    """El correo se reconocio pero no es un movimiento."""

    def __init__(self, motivo):
        self.motivo = motivo
        super().__init__(motivo)


def parse_texto(texto, msg=None, asunto=None):
    """Devuelve un Evento, o levanta Descartado si es un aviso administrativo,
    o devuelve None si no se reconocio (eso es un bug: hay que mirarlo).

    El `asunto` es opcional pero ayuda: algunos avisos solo se identifican por
    el asunto, no por el cuerpo."""
    t = _normalizar(texto)
    motivo = motivo_ignorar(_normalizar(f"{asunto or ''} {texto}"))
    if motivo:
        raise Descartado(motivo)
    for nombre, rx, fn in _P:
        m = rx.search(t)
        if m:
            ev = fn(m, msg if msg is not None else {})
            ev.crudo = t[:300]
            return ev
    return None


def parse_eml(ruta):
    msg, plano = cuerpo_texto(ruta)
    if not plano.strip():
        return None
    return parse_texto(plano, msg, asunto=msg.get('subject'))
