"""Lo que alguien cuenta por el chat: «45 mil de esmaltes con la nu».

Es el respaldo de cuando no hay modelo: solo entiende montos escritos con
numeros. «cuarenta mil» en letras no lo entiende, y eso esta bien: en ese caso
el bot pide el monto en vez de adivinarlo.
"""

from __future__ import annotations

import re

_MONTO = re.compile(
    r'(?<![\w.,])(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d{1,2})?)\s*'
    r'(mil|k|lucas?|palos?|millones|millon|m\b)?',
    re.I,
)

_MULTIPLICA = {
    'mil': 1_000,
    'k': 1_000,
    'luca': 1_000,
    'lucas': 1_000,
    'palo': 1_000_000,
    'palos': 1_000_000,
    'millon': 1_000_000,
    'millones': 1_000_000,
    'm': 1_000_000,
}


def monto_dicho(texto: str | None) -> float | None:
    """El primer monto del texto, en pesos. None si no hay ninguno.

    >>> monto_dicho('45 mil de esmaltes')
    45000.0
    >>> monto_dicho('pague 45.000 en la nu')
    45000.0
    >>> monto_dicho('12k almuerzo')
    12000.0
    >>> monto_dicho('1,5 palos del arriendo')
    1500000.0
    >>> monto_dicho('hola') is None
    True
    """
    if not texto:
        return None
    m = _MONTO.search(texto)
    if not m:
        return None
    numero, sufijo = m.group(1), (m.group(2) or '').lower()
    if re.fullmatch(r'\d{1,3}(?:[.,]\d{3})+', numero):
        valor = float(re.sub(r'[.,]', '', numero))
    else:
        valor = float(numero.replace(',', '.'))
    return valor * _MULTIPLICA.get(sufijo, 1)


_INGRESO = re.compile(
    r'\b(me pagaron|me transfirieron|recibi|me consignaron|vendi|cobre)\b', re.I
)


def es_ingreso(texto: str | None) -> bool:
    """Si lo que cuenta es plata que ENTRA.

    >>> es_ingreso('me pagaron 80 mil de unas unas')
    True
    >>> es_ingreso('pague 80 mil')
    False
    """
    return bool(texto and _INGRESO.search(texto))
