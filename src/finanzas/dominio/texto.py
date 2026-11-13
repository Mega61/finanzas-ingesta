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
# Son DOS listas y la diferencia importa, porque tenerlas juntas ya costo caro.
#
# PURAS: no son un comercio, nunca. Una compra por Bold puede ser cualquier
# cosa, asi que la palabra no dice absolutamente nada de que compraste. Se
# quitan siempre, con asterisco o sin el, porque el banco manda las dos formas:
# 'MERCADO PAGO*TIERRAG' y tambien 'BOLD CO ONLINE RTFE'.
#
# El dia que no se quitaban sin asterisco, el sembrador aprendio del historico
# la regla 'BOLD -> Inversion' con 9 aciertos, y desde ahi TODA compra por Bold
# entraba como inversion con 0.88 de confianza: sin preguntar. Ver
# PASARELAS_PURAS en las reglas del clasificador.
PASARELAS_PURAS = (
    'DLO',
    'DL',
    'PAYU',
    'PAYULATAM',
    'MERCADO PAGO',
    'MERCADOPAGO',
    'MP',
    'EPAYCO',
    'WOMPI',
    'BOLD',
    'TPAGA',
)

# TAMBIEN COMERCIO: son pasarela cuando vienen con asterisco delante del nombre
# real, pero por si solas significan algo. 'DOMICILIO RAPPI' y 'SACAR NEQUI'
# los escribio el usuario y quieren decir justo eso; borrar la palabra dejaria
# el movimiento sin identidad.
PASARELAS_AMBIGUAS = ('PSE', 'RAPPI', 'NEQUI')


def _alternativa(nombres: tuple[str, ...]) -> str:
    """Una alternancia de regex con esos nombres.

    Las mas largas primero: sin eso 'MP' caza dentro de 'MERCADO PAGO' y deja
    'ERCADO PAGO'. Y el espacio pasa a `\\s*`, para que valga tanto
    'MERCADO PAGO' como 'MERCADOPAGO'.
    """
    orden = sorted(nombres, key=len, reverse=True)
    # re.escape escapa el espacio en unas versiones y no en otras, asi que se
    # desescapa primero y despues se convierte.
    return '|'.join(
        re.escape(p).replace('\\ ', ' ').replace(' ', r'\s*') for p in orden
    )


_ALT = _alternativa(PASARELAS_PURAS + PASARELAS_AMBIGUAS)

# OJO con los espacios: el banco manda 'MERCADO PAGO*ZONAFIT'. Si no se quita
# 'MERCADO PAGO' completo, la palabra MERCADO se cuela como si fuera el
# comercio y caza con 'MERCADO LIBRE'. Asi un gimnasio quedo clasificado como
# comida de gato, con confianza alta, o sea sin preguntar.
_PREFIJO = re.compile(rf'^({_ALT})\s*\*\s*', re.I)
# Y como sufijo, que es igual de comun: 'UBER RIDES*DL'
_SUFIJO = re.compile(rf'\*\s*({_ALT})\s*$', re.I)

# Las puras, tambien como palabra suelta y en cualquier posicion del nombre.
_PURA_SUELTA = re.compile(rf'\b({_alternativa(PASARELAS_PURAS)})\b', re.I)

# Palabras que no distinguen a nadie: aparecen en cualquier comercio del pais.
_RUIDO = re.compile(
    r'\b(COL|COLO|CO|BOGOTA|MEDELLIN|SAS|S\.A\.S|SA|LTDA|INC|COM)\b', re.I
)

# Numero de local o sucursal: 'CYCLE GEAR N169', 'DROGUERIA ALEMANA 47'.
#
# El numero suelto se quita siempre; letra+numero solo cuando el numero tiene
# DOS o mas digitos. Antes el patron era `[A-Z]?\d{1,5}` y se comia el «D1» de
# TIENDA D1 SABANETA: una de las cadenas de supermercado mas grandes del pais
# perdia su identidad y quedaba como 'TIENDA SABANETA', que no distingue nada
# (TIENDA es una palabra vaga). Y 'Mercado D1' quedaba en 'MERCADO', peor aun.
#
# Medido contra los 1129 nombres distintos que hay en Firefly: cambian 8, todos
# de D1, y en los 8 el resultado nuevo es el correcto.
_LOCAL = re.compile(r'\b(?:\d{1,5}|[A-Z]\d{2,5})\b')

# Palabras demasiado comunes para decidir una categoria o un comercio por si
# solas. Sin esto, 'comida' cazaba con 'Comida de calle' y le ganaba a que el
# usuario hubiera dicho 'gata', que era la senal real.
VAGAS = frozenset(
    {
        'COMIDA',
        'COMPRA',
        'COMPRAS',
        'GASTO',
        'PAGO',
        'PAGOS',
        'COSAS',
        'ARTICULOS',
        'TRANSPORTE',
        'PERSONAL',
        'CALLE',
        'MES',
        'MERCADO',
        'TIENDA',
        'SUPER',
        'ALMACEN',
        'ALMACENES',
        'CENTRO',
        'GRUPO',
        'SERVICIO',
        'SERVICIOS',
        'COMERCIAL',
        'DISTRIBUIDORA',
        'INVERSIONES',
        'SOLUCIONES',
        'COLOMBIA',
        'NACIONAL',
    }
)


def sin_tildes(texto: str) -> str:
    """Quita tildes y dieresis, deja las letras base."""
    if not texto:
        return ''
    return ''.join(
        c
        for c in unicodedata.normalize('NFD', str(texto))
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
    # Las pasarelas puras se van tambien sin asterisco, pero solo si queda algo
    # detras: si el movimiento es SOLO el nombre de la pasarela, borrarlo lo
    # dejaria sin identidad y no habria con que preguntar.
    sin_pasarela = _PURA_SUELTA.sub(' ', t)
    if sin_pasarela.strip():
        t = sin_pasarela
    t = _RUIDO.sub(' ', t)
    # Los numeros de local solo se borran si queda texto de verdad. En una
    # transferencia a la cuenta '6985' el numero ES la identidad, y borrarlo
    # dejaba la clave vacia.
    if re.search(r'[A-Z]{3}', t):
        t = _LOCAL.sub(' ', t)
    return re.sub(r'\s+', ' ', t).strip()


def es_pasarela_pura(texto: str | None) -> bool:
    """¿Este nombre es SOLO una pasarela de pago, sin comercio detras?

    Sirve para no aprender una regla con el. 'BOLD' como patron caza con toda
    compra hecha por Bold, que puede ser cualquier cosa: es la definicion de una
    regla que hace dano.
    """
    if not texto:
        return False
    limpio = sin_tildes(str(texto)).upper()
    limpio = re.sub(r'[^A-Z0-9 ]', ' ', limpio)
    limpio = _RUIDO.sub(' ', limpio)
    limpio = re.sub(r'\s+', ' ', limpio).strip()
    if not limpio:
        return False
    return any(limpio == re.sub(r'\s+', ' ', p) for p in PASARELAS_PURAS)


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


# --------------------------------------------------------------- cadenas
# El nombre que el banco manda cambia por DATAFONO, no por comercio:
#
#   TIENDA D1 SABANETA P    TIENDA D1 SABANETA S    KOBA COLOMBIA
#   EXITO SABANETA          ALMACENES EXITO         GRUPO EXITO
#
# `normalizar` los deja distintos —y hace bien, son textos distintos— asi que
# cada uno estrena clave de regla, nadie le tiene cuenta aprendida, y el
# publicador cae en el nombre crudo. Firefly entonces CREA una cuenta de gasto
# nueva con ese nombre. Asi aparecieron 'EXITO SABANETA', 'KOBA COLOMBIA',
# 'ALMACENES EXITO' y 'TIENDA D1 SABANETA P' al lado de 'Grupo Éxito' (128
# movimientos desde 2025) y 'D1' (22), partiendo en pedazos el historico de
# las tres cadenas justo cuando se quiere cuadrar el mes.
#
# Esto NO reemplaza a las reglas: solo actua cuando no hay ninguna, antes de
# rendirse y usar el texto crudo. Una regla aprendida o contestada por
# Telegram sigue mandando.
CADENAS = (
    ('Grupo Éxito', re.compile(r'\b(EXITO|CARULLA)\b')),
    ('D1', re.compile(r'\b(D1|KOBA)\b')),
    ('Supermu', re.compile(r'\b(SUPERMU|SUPERVAQUITA|VAQUITA)\b')),
)


def cuenta_de_cadena(nombre: str | None) -> str | None:
    """La cuenta de Firefly de la cadena, o None si no se reconoce.

    >>> cuenta_de_cadena('TIENDA D1 SABANETA P')
    'D1'
    >>> cuenta_de_cadena('KOBA COLOMBIA')
    'D1'
    >>> cuenta_de_cadena('ALMACENES EXITO')
    'Grupo Éxito'
    >>> cuenta_de_cadena('PANADERIA LA 33') is None
    True
    """
    if not nombre:
        return None
    limpio = normalizar(nombre)
    for cuenta, patron in CADENAS:
        if patron.search(limpio):
            return cuenta
    return None


# Los codigos de un solo uso que manda el banco («Bancolombia te informa la
# Clave Dinamica 503167»). Llegan del mismo remitente que los pagos
# programados, y no tienen por que quedar guardados en ninguna base: se
# reconocen ANTES de bajar el correo y se saltan.
_CODIGO_DE_SEGURIDAD = re.compile(
    r'(informa la clave din[aá]mica\s+\d|le enviamos su clave din[aá]mica'
    r'|es el c[oó]digo que necesitas para|este c[oó]digo es de un solo uso)',
    re.I,
)


def es_codigo_de_seguridad(texto: str | None) -> bool:
    return bool(texto and _CODIGO_DE_SEGURIDAD.search(texto))


# ------------------------------------------------------- lugar y marca
# El banco manda el PUNTO DE VENTA: la marca primero y despues el centro
# comercial, el barrio o el municipio, cortado a 20 letras:
#
#   MINISO VIVA ENVIGADO    TIENDA D1 SAB AVES M    THE PARKING VIVA ENV
#   IKEA ENVIGADO           HOMECENTER ENVIGADO     STARBUCKS VIVA ENVIG
#
# Y en Firefly hay cuentas que SON un lugar: 'Viva Envigado' y 'Aves Maria'
# son los parqueaderos donde se deja la moto, con categoria Transporte Moto.
# Emparejar por palabras sin saber esto hizo que un Miniso, un D1 y un
# Homecenter quedaran como parqueadero: compartian VIVA, AVES o ENVIGADO, que
# dicen DONDE fue la compra, no QUE se compro.
#
# Estas palabras nunca hacen coincidir un comercio. La lista no tiene que
# estar completa: una palabra de lugar que falte solo vuelve a abrir la
# posibilidad de un parecido equivocado, que igual queda preguntado.
LUGARES = frozenset(
    {
        # municipios del valle de Aburra y alrededores
        'MEDELLIN',
        'ENVIGADO',
        'SABANETA',
        'ITAGUI',
        'BELLO',
        'ESTRELLA',
        'CALDAS',
        'COPACABANA',
        'GIRARDOTA',
        'RIONEGRO',
        'RETIRO',
        'GUARNE',
        'MARINILLA',
        'BOGOTA',
        'CALI',
        'CARTAGENA',
        'BARRANQUILLA',
        # centros comerciales
        'VIVA',
        'AVES',
        'MARIA',
        'MAYORCA',
        'MOLINOS',
        'OVIEDO',
        'SANTAFE',
        'TESORO',
        'UNICENTRO',
        'OCEANOMALL',
        'PREMIUM',
        'ARKADIA',
        'FLORIDA',
        'VIZCAYA',
        'ANDINO',
        'CITYPLAZA',
        'PLAZA',
        'MALL',
        'OUTLET',
        # barrios y zonas
        'POBLADO',
        'LAURELES',
        'ESTADIO',
        'BELEN',
        'ENVIGADENO',
        'NORTE',
        'SUR',
        'ORIENTE',
        'OCCIDENTE',
    }
)

# Las abreviaturas que el propio banco usa para la sucursal, en cualquier
# posicion: 'TIENDA D1 SAB AVES M'.
ABREVIATURAS = frozenset({'SAB', 'ENV', 'ENVI', 'ENVIG', 'ENVIGA', 'MDE', 'ITA'})

# Y el corte a 20 letras: 'ENVIGADO' llega como 'ENVIGA', 'SABANETA' como
# 'SABANE'. Un prefijo de 3 o mas letras de un lugar cuenta como lugar solo si
# es la ULTIMA palabra, que es la unica que el corte puede dejar a medias.
_PREFIJO_MINIMO = 3


def es_lugar(palabra: str, ultima: bool = False) -> bool:
    """¿Esta palabra (ya normalizada) dice donde, y no quien?

    >>> es_lugar('SAB'), es_lugar('ENVIGA', ultima=True), es_lugar('MINISO')
    (True, True, False)
    """
    if palabra in LUGARES or palabra in ABREVIATURAS:
        return True
    return (
        ultima
        and len(palabra) >= _PREFIJO_MINIMO
        and any(lugar.startswith(palabra) for lugar in LUGARES)
    )


def _palabras_de_lugar(palabras: list[str]) -> list[bool]:
    """Para cada palabra, si es de lugar. La primera nunca: es la marca
    ('VIVA AEROBUS' es una aerolinea)."""
    n = len(palabras)
    return [i > 0 and es_lugar(p, ultima=i == n - 1) for i, p in enumerate(palabras)]


def marca(clave: str | None) -> list[str]:
    """Las palabras de la marca: la primera siempre, y las siguientes hasta
    la primera palabra de lugar. Lo que viene despues es la sucursal.

    >>> marca('MINISO VIVA ENVIGADO')
    ['MINISO']
    >>> marca('TIENDA D1 SAB AVES M')
    ['TIENDA', 'D1']
    >>> marca('VIVA AEROBUS')
    ['VIVA', 'AEROBUS']
    """
    palabras = (clave or '').split()
    fuera = []
    for p, es in zip(palabras, _palabras_de_lugar(palabras), strict=True):
        if es:
            break
        fuera.append(p)
    return fuera


def lugar(clave: str | None) -> list[str]:
    """Las palabras de lugar de un nombre, sin la primera (que es la marca)."""
    palabras = (clave or '').split()
    return [
        p for p, es in zip(palabras, _palabras_de_lugar(palabras), strict=True) if es
    ]


def nucleo(patron: str | None) -> set[str]:
    """Lo que identifica a una regla: sus palabras menos los lugares.

    Aqui la primera palabra NO se salva: 'VIVA ENVIGADO' y 'AVES MARIA' son
    cuentas que no tienen mas que lugar, y su nucleo queda vacio. Esas reglas
    son de un SITIO, no de un comercio, y no se emparejan por palabras.

    >>> sorted(nucleo('VIVA ENVIGADO')), sorted(nucleo('VIVA AEROBUS'))
    ([], ['AEROBUS'])
    """
    return {
        p for p in (patron or '').split() if p not in LUGARES and p not in ABREVIATURAS
    }


# Pagar el parqueadero del centro comercial: la unica compra donde el lugar SI
# es el comercio. 'THE PARKING VIVA ENV' va a la cuenta 'Viva Envigado'.
PARQUEADERO = re.compile(r'\b(PARKING|PARQUEADERO|PARQ|PARQUEO|PARKIN)\b')


# Marcas tan conocidas que no hace falta haberlas visto para saber que son.
# Solo actuan cuando NO hay regla aprendida, y solo si la categoria existe en
# el libro de quien compra: una regla tuya, o una respuesta por Telegram,
# siempre mandan. Lo que decide esta tabla igual se pregunta (no es una regla
# exacta), pero ya llega a Firefly con la categoria probable y no en blanco.
#
# Se compara contra el nombre SIN espacios, porque el banco los pone y los
# quita: 'HOMECENTER' y 'HOME CENTER', 'MCDONAL D NVIGA O'.
MARCAS = (
    ('McDonalds', 'Restaurante', re.compile(r'^MA?CDONAL')),
    ('Burger King', 'Restaurante', re.compile(r'^BURGERKING')),
    ('KFC', 'Restaurante', re.compile(r'^KFC')),
    ('Frisby', 'Restaurante', re.compile(r'^FRISBY')),
    ('El Corral', 'Restaurante', re.compile(r'^(HAMBURGUESAS)?ELCORRAL')),
    ('Ikea', 'Compras Casa', re.compile(r'^IKEA')),
    ('Homecenter', 'Compras Casa', re.compile(r'^HOMECENTER')),
    ('Easy', 'Compras Casa', re.compile(r'^EASY')),
)


def marca_conocida(clave: str | None) -> tuple[str, str, re.Pattern[str]] | None:
    """(cuenta, categoria, patron) de una marca de la tabla, o None.

    >>> marca_conocida('MCDONAL D NVIGA O')[:2]
    ('McDonalds', 'Restaurante')
    >>> marca_conocida('MINISO VIVA ENVIGADO') is None
    True
    """
    compacto = (clave or '').replace(' ', '')
    for cuenta, categoria, patron in MARCAS:
        if patron.search(compacto):
            return cuenta, categoria, patron
    return None
