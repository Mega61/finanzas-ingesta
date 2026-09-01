"""Fechas y horas. Logica pura, sin I/O.

Este modulo existe por un problema concreto: en este sistema conviven TRES
relojes distintos.

  el banco     manda hora local de Colombia, sin decir la zona
  Graph        devuelve UTC con la Z al final
  el contenedor corre con TZ=America/Bogota

Colombia esta en UTC-5 y no cambia con el horario de verano, asi que la
diferencia es fija: un movimiento de las 22:00 en Bogota es del dia SIGUIENTE
en UTC. Comparar un naive con un aware, o restar dos naive de relojes
distintos, corre las fechas un dia. En un sistema que empareja por fecha con
tolerancia, eso ensucia el emparejamiento sin dar ningun error.

La regla: aqui todo lo que representa un instante es aware. Lo que representa
un dia de calendario es `date`, que no tiene zona y no la necesita.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

# Colombia: UTC-5 fijo, sin horario de verano.
BOGOTA = timezone(timedelta(hours=-5), 'America/Bogota')

_FORMATOS_FECHA = (
    '%Y-%m-%d',  # ISO, lo que usa la base
    '%d/%m/%Y',  # el banco
    '%d/%m/%y',  # el banco, ano corto
    '%Y/%m/%d',  # una variante de las alertas
    '%d-%m-%Y',
)

# El periodo de los extractos de 2026 viene en espanol abreviado:
# '30 jul - 30 ago. 2026'
MESES_ES = {
    'ene': 1,
    'feb': 2,
    'mar': 3,
    'abr': 4,
    'may': 5,
    'jun': 6,
    'jul': 7,
    'ago': 8,
    'sep': 9,
    'set': 9,
    'oct': 10,
    'nov': 11,
    'dic': 12,
}
_PERIODO_ES = re.compile(
    r'(\d{1,2})\s+([a-z]{3})\.?\s*[-–]\s*(\d{1,2})\s+([a-z]{3})\.?\s*(\d{4})',
    re.I,
)

_ES_HORA = re.compile(r'^\d{1,2}:\d{2}(:\d{2})?$')


def hoy(tz: timezone = BOGOTA) -> date:
    """El dia de hoy en la zona que importa, no en la del servidor."""
    return datetime.now(tz).date()


def ahora(tz: timezone = BOGOTA) -> datetime:
    """El instante actual, siempre aware."""
    return datetime.now(tz)


def a_fecha(valor: str | date | datetime | None) -> date | None:
    """Cualquier cosa que parezca fecha -> date. None si no se entiende.

    Devuelve None en vez de lanzar porque el texto viene del banco y a veces
    viene raro: el movimiento se pregunta en vez de tumbar la pasada.

    >>> a_fecha('2026-09-01')
    datetime.date(2026, 9, 1)
    >>> a_fecha('01/09/2026')
    datetime.date(2026, 9, 1)
    >>> a_fecha('nada')
    """
    if valor is None or valor == '':
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    s = str(valor).strip()
    # ISO con hora, con o sin zona
    if 'T' in s:
        s_corto = s[:10]
        try:
            return datetime.strptime(s_corto, '%Y-%m-%d').date()
        except ValueError:
            pass
    for fmt in _FORMATOS_FECHA:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def a_instante(valor: str | datetime | None, tz: timezone = BOGOTA) -> datetime | None:
    """Texto -> datetime AWARE. Si el texto no trae zona, se asume `tz`.

    Asumir la zona explicitamente es el punto: un naive que se compara con un
    aware lanza TypeError, y uno que se resta con otro naive de otro reloj da
    un resultado silenciosamente corrido.
    """
    if valor is None or valor == '':
        return None
    if isinstance(valor, datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=tz)
    s = str(valor).strip().replace('Z', '+00:00')
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        f = a_fecha(s)
        if f is None:
            return None
        d = datetime(f.year, f.month, f.day)
    return d if d.tzinfo else d.replace(tzinfo=tz)


def ordenar_fecha_hora(a: str | None, b: str | None) -> tuple[str | None, str | None]:
    """Devuelve (fecha, hora) decidiendo por la FORMA, no por la posicion.

    Varias plantillas del banco traen los dos campos invertidos:
    'el 07:57 a las 10/12/2025'. Confiar en el orden pone la hora como fecha.

    >>> ordenar_fecha_hora('07:57', '10/12/2025')
    ('10/12/2025', '07:57')
    >>> ordenar_fecha_hora('10/12/2025', '07:57')
    ('10/12/2025', '07:57')
    """
    if _ES_HORA.match(a or ''):
        return b, a
    return a, b


def periodo_espanol(texto: str) -> tuple[date | None, date | None]:
    """'30 jul - 30 ago. 2026' -> (2026-07-30, 2026-08-30).

    Si el primer mes es mayor que el segundo, el periodo cruza el ano nuevo y
    la fecha inicial es del ano anterior.

    >>> periodo_espanol('30 jul - 30 ago. 2026')
    (datetime.date(2026, 7, 30), datetime.date(2026, 8, 30))
    >>> periodo_espanol('30 dic - 30 ene. 2027')
    (datetime.date(2026, 12, 30), datetime.date(2027, 1, 30))
    """
    m = _PERIODO_ES.search(texto or '')
    if not m:
        return None, None
    d1, m1, d2, m2, anio_txt = m.groups()
    n1, n2 = MESES_ES.get(m1.lower()), MESES_ES.get(m2.lower())
    if not n1 or not n2:
        return None, None
    anio = int(anio_txt)
    try:
        hasta = date(anio, n2, int(d2))
        desde = date(anio - 1 if n1 > n2 else anio, n1, int(d1))
    except ValueError:
        return None, None
    return desde, hasta


def fin_de_mes(d: date) -> date:
    """El ultimo dia del mes de `d`.

    >>> fin_de_mes(date(2026, 2, 10))
    datetime.date(2026, 2, 28)
    >>> fin_de_mes(date(2024, 2, 10))
    datetime.date(2024, 2, 29)
    """
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def inicio_de_mes(d: date) -> date:
    return d.replace(day=1)


def dias_entre(a: date | str | None, b: date | str | None) -> int | None:
    """Dias absolutos entre dos fechas. None si alguna no se entiende."""
    fa, fb = a_fecha(a), a_fecha(b)
    if fa is None or fb is None:
        return None
    return abs((fa - fb).days)
