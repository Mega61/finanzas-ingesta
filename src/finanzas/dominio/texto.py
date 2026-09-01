"""Normalizacion de nombres de comercio. Logica pura, sin I/O.

El banco no manda el nombre del comercio limpio: le pega el nombre de la
pasarela de pago, lo trunca, y le agrega numeros de local. Normalizar bien es
lo que decide si un movimiento se clasifica solo o hay que preguntarlo.

Cada regla de aqui salio de un caso real que fallo. Estan documentadas para que
nadie las "simplifique" sin saber que rompe.
"""

from __future__ import annotations

import re
import unicodedata

# Pasarelas de pago que el banco pega al nombre real del comercio.
#
# OJO con los espacios: el banco manda 'MERCADO PAGO*ZONAFIT'. Si no se quita
# 'MERCADO PAGO' completo, la palabra MERCADO se cuela como si fuera el
# comercio y caza con 'MERCADO LIBRE'. Asi un gimnasio quedo clasificado como
# comida de gato, con confianza alta, o sea sin preguntar.
_PASARELA = (
    r'DLO|DL|PAYU|PAYULATAM|MERCADO\s?PAGO|MERCADOPAGO|MP|EPAYCO|PSE'
    r'|WOMPI|BOLD|RAPPI|TPAGA|NEQUI'
)
_PREFIJO = re.compile(rf'^({_PASARELA})\s*\*\s*', re.I)
# Y como sufijo, que es igual de comun: 'UBER RIDES*DL'
_SUFIJO = re.compile(rf'\*\s*({_PASARELA})\s*$', re.I)

# Palabras que no distinguen a nadie: aparecen en cualquier comercio del pais.
_RUIDO = re.compile(
    r'\b(COL|COLO|CO|BOGOTA|MEDELLIN|SAS|S\.A\.S|SA|LTDA|INC|COM)\b', re.I
)

# Numero de local o sucursal: 'CYCLE GEAR N169', 'DROGUERIA ALEMANA 47'
_LOCAL = re.compile(r'\b[A-Z]?\d{1,5}\b')

# Palabras demasiado comunes para decidir una categoria o un comercio por si
# solas. Sin esto, 'comida' cazaba con 'Comida de calle' y le ganaba a que el
# usuario hubiera dicho 'gata', que era la senal real.
VAGAS = frozenset({
    'COMIDA', 'COMPRA', 'COMPRAS', 'GASTO', 'PAGO', 'PAGOS', 'COSAS',
    'ARTICULOS', 'TRANSPORTE', 'PERSONAL', 'CALLE', 'MES', 'MERCADO',
    'TIENDA', 'SUPER', 'ALMACEN', 'ALMACENES', 'CENTRO', 'GRUPO',
    'SERVICIO', 'SERVICIOS', 'COMERCIAL', 'DISTRIBUIDORA', 'INVERSIONES',
    'SOLUCIONES', 'COLOMBIA', 'NACIONAL',
})


def sin_tildes(texto: str) -> str:
    """Quita tildes y dieresis, deja las letras base."""
    if not texto:
        return ''
    return ''.join(
        c for c in unicodedata.normalize('NFD', str(texto))
        if unicodedata.category(c) != 'Mn'
    )


def normalizar(texto: str | None) -> str:
    """Deja un nombre de comercio comparable entre el banco y Firefly.

    >>> normalizar('MERCADO PAGO*ZONAFIT')
    'ZONAFIT'
    >>> normalizar('UBER RIDES*DL')
    'UBER RIDES'
    >>> normalizar('CYCLE GEAR N169')
    'CYCLE GEAR'
    >>> normalizar('6985')
    '6985'
    """
    if not texto:
        return ''
    t = sin_tildes(texto).upper()
    t = _SUFIJO.sub('', t)
    t = _PREFIJO.sub('', t)
    t = re.sub(r'[^A-Z0-9 ]', ' ', t)
    t = _RUIDO.sub(' ', t)
    # Los numeros de local solo se borran si queda texto de verdad. En una
    # transferencia a la cuenta '6985' el numero ES la identidad, y borrarlo
    # dejaba la clave vacia.
    if re.search(r'[A-Z]{3}', t):
        t = _LOCAL.sub(' ', t)
    return re.sub(r'\s+', ' ', t).strip()


def tokens(texto: str | None, minimo: int = 3) -> set[str]:
    """Las palabras utiles de un texto ya normalizado."""
    return {p for p in normalizar(texto).split() if len(p) >= minimo}


def tokens_distintivos(texto: str | None, minimo: int = 4) -> set[str]:
    """Las palabras que de verdad identifican algo: ni cortas ni vagas."""
    return {p for p in tokens(texto, minimo) if p not in VAGAS}


def es_numerico(texto: str | None) -> bool:
    """¿Es solo un numero? Una llave QR o un numero de cuenta lo es, y ahi el
    numero es la identidad: no se puede normalizar a nada."""
    n = normalizar(texto)
    return bool(n) and n.replace(' ', '').isdigit()
