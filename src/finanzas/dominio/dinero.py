"""Montos: parseo y formato. Logica pura, sin I/O.

Estaba repetido en cinco modulos con variaciones sutiles, que es como se
consiguen dos formatos distintos para el mismo numero en el mismo mensaje.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

# El banco manda dos formatos en el mismo buzon, a veces en el mismo correo:
#   '178.679,08'  colombiano: punto de miles, coma decimal
#   '205,967.00'  gringo: coma de miles, punto decimal
# Y ademas '9,000', que son nueve mil y no nueve.
_FIN_DECIMAL = re.compile(r'[.,](\d+)$')


def parse_monto(texto: str) -> float:
    """Convierte un monto en cualquiera de los dos formatos a float.

    La regla: manda el ULTIMO separador. Si va seguido de exactamente dos
    digitos y ahi termina el numero, es el separador decimal; si no, todos los
    separadores son de miles.

    >>> parse_monto('178.679,08')
    178679.08
    >>> parse_monto('205,967.00')
    205967.0
    >>> parse_monto('9,000')
    9000.0
    """
    if texto is None:
        raise ValueError('monto vacio')
    s = str(texto).strip().replace(' ', '').replace('$', '')
    negativo = s.startswith('-')
    s = s.lstrip('-+')
    if not s:
        raise ValueError(f'monto vacio: {texto!r}')

    m = _FIN_DECIMAL.search(s)
    if m and len(m.group(1)) == 2:
        entero = s[: m.start()].replace('.', '').replace(',', '')
        valor = float(f'{entero or 0}.{m.group(1)}')
    else:
        limpio = re.sub(r'[.,]', '', s)
        if not limpio.isdigit():
            raise ValueError(f'no pude leer el monto {texto!r}')
        valor = float(limpio)
    return -valor if negativo else valor


def _redondear(v: float, decimales: int) -> Decimal:
    """Redondeo comercial: el 0,5 sube.

    Python redondea al par mas cercano ('banker's rounding'), asi que
    f'{1234.5:,.0f}' da 1234 y no 1235. Para plata eso esta mal: un extracto
    nunca redondea para abajo un medio peso, y ademas hace que la misma cifra
    se vea distinta segun si termina en par o impar.
    """
    cuantos = Decimal(1).scaleb(-decimales)
    return Decimal(str(v)).quantize(cuantos, rounding=ROUND_HALF_UP)


def formatear(valor: float, moneda: str = 'COP', con_signo: bool = False) -> str:
    """Formato colombiano: punto de miles, sin decimales en pesos.

    >>> formatear(1234567.89)
    '$1.234.568'
    >>> formatear(-1234.5, con_signo=True)
    '-$1.235'
    >>> formatear(12.3456, 'USD')
    'US$12.35'
    """
    signo = ''
    if con_signo:
        signo = '-' if valor < 0 else '+'
    v = abs(valor)
    if (moneda or 'COP').upper() == 'USD':
        return f'{signo}US${_redondear(v, 2):,.2f}'
    return f'{signo}${_redondear(v, 0):,.0f}'.replace(',', '.')


def mismo_monto(a: float, b: float, tolerancia: float = 2.0) -> bool:
    """¿Son el mismo monto, permitiendo centavos de diferencia?

    La alerta y el extracto difieren en centavos por redondeo, y comparar con
    igualdad exacta hace que nada cuadre.
    """
    return abs(abs(a) - abs(b)) <= tolerancia
