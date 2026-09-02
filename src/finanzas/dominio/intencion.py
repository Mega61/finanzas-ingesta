"""A que se refiere un mensaje escrito a mano. Logica pura, sin I/O.

Cuando llega texto libre al bot hay que decidir dos cosas, y las dos se
equivocaban:

1. **¿Es una respuesta o una pregunta?** El asesor solo se consultaba cuando NO
   habia ninguna pregunta abierta. O sea que mientras el bot te estuviera
   preguntando algo — que es casi siempre — no se le podia preguntar nada a el.

2. **¿A cual de los movimientos abiertos responde?** Primero se tomaba el mas
   reciente, y contestar la tercera de seis resolvia la sexta: la categoria
   caia en el movimiento equivocado. Se arreglo pasando a NO adivinar, o sea
   pidiendo «responde al mensaje», y eso es igual de malo por el otro lado: el
   usuario escribe «era Etre, venden cosas para la casa» y el bot le contesta
   que no sabe de que le habla.

La salida correcta no es adivinar ni rendirse: es LEER el mensaje. «la comida de
la gata en tierragro» nombra un comercio que esta en uno de los movimientos
abiertos y en ninguno de los otros. Cuando el texto senala a uno solo, se
resuelve ese y se dice cual. Cuando de verdad no senala a ninguno, se pregunta
con botones, que es un toque.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from finanzas.dominio import texto as _texto

# --------------------------------------------------------------- el asesor

# Marcas de que el mensaje es una consulta y no la respuesta a una pregunta.
# El sesgo es a proposito: mandar una respuesta al asesor solo deja el
# movimiento sin resolver, mientras mandar una pregunta al interprete puede
# meterle una categoria inventada a una compra.
_DELIBERA = re.compile(
    r'\b(deberia|debo|me\s+alcanza|alcanza|puedo|podria|conviene|vale\s+la\s+pena'
    r'|es\s+buena\s+idea|que\s+opinas|me\s+recomiendas|recomiendas|aguanta'
    r'|me\s+da|tengo\s+para|quiero\s+comprar|estoy\s+pensando|si\s+compro)\b',
    re.I,
)
_CONSULTA = re.compile(
    r'\b(cuanto|cuanta|cuantos|cuantas|cual|cuales|como\s+voy|como\s+vamos'
    r'|que\s+me\s+queda|que\s+queda|me\s+queda|donde\s+estoy|resumen'
    r'|cuando|por\s?que|porque)\b',
    re.I,
)

# Marcas de que el mensaje es una respuesta: habla de algo que YA paso.
_PASADO = re.compile(
    r'\b(fue|fueron|era|eran|es|son|compre|compramos|pague|pagamos|gaste'
    r'|gastamos|saque|sacamos|esto|eso|ese|esa|estos)\b',
    re.I,
)


def es_para_el_asesor(txt: str | None) -> bool:
    """¿El mensaje es una consulta al asesor, y no una respuesta?

    >>> es_para_el_asesor('¿me alcanza para una bici?')
    True
    >>> es_para_el_asesor('fue la comida de la gata en tierragro')
    False
    >>> es_para_el_asesor('cuanto llevo gastado este mes')
    True
    >>> es_para_el_asesor('era Etre, venden cosas para la casa')
    False
    """
    if not txt:
        return False
    t = _texto.sin_tildes(str(txt)).lower().strip()
    if not t:
        return False

    pregunta = t.endswith('?') or t.startswith('¿')
    delibera = bool(_DELIBERA.search(t))
    consulta = bool(_CONSULTA.search(t))
    pasado = bool(_PASADO.search(t))

    # «deberia comprar...» es del asesor incluso sin signo de pregunta: nadie
    # responde a que categoria va una compra deliberando sobre ella.
    if delibera:
        return True
    # Una consulta informativa gana al pasado: «cuanto gaste en mercado» habla
    # en pasado y sigue siendo una pregunta.
    if consulta:
        return True
    # Un signo de pregunta suelto: es del asesor salvo que hable de algo que ya
    # paso, porque «esto fue el gym?» es una respuesta dudando.
    return bool(pregunta and not pasado)


# ------------------------------------------------- a que movimiento apunta

# Un monto escrito a mano: '212000', '212.000', '212 mil', '212k'.
_MONTO = re.compile(r'(\d[\d.,]*)\s*(mil|k|m|millon(?:es)?)?\b', re.I)

_MULTIPLICADOR = {
    None: 1,
    'mil': 1_000,
    'k': 1_000,
    'm': 1_000_000,
    'millon': 1_000_000,
    'millones': 1_000_000,
}

# Cuanto puede desviarse el monto que el usuario escribe del real. Se escribe
# «los 212 mil» para 212.000 exactos, pero tambien «los 457» para 457.000.
_TOLERANCIA_MONTO = 0.02


@dataclass(frozen=True)
class Coincidencia:
    """Que tanto un texto apunta a un movimiento, y por que."""

    id: int
    puntaje: float
    razones: tuple[str, ...]

    @property
    def senalado(self) -> bool:
        """¿Hay evidencia de verdad, y no solo ruido?"""
        return self.puntaje >= 1.0


def montos_mencionados(txt: str | None) -> set[float]:
    """Los montos que el texto nombra, en pesos.

    >>> sorted(montos_mencionados('los 212 mil de bold'))
    [212000.0]
    >>> sorted(montos_mencionados('el de 457.000'))
    [457000.0]
    """
    if not txt:
        return set()
    fuera = set()
    for crudo, sufijo in _MONTO.findall(str(txt)):
        limpio = crudo.replace('.', '').replace(',', '')
        if not limpio.isdigit():
            continue
        n = float(limpio)
        mult = _MULTIPLICADOR.get((sufijo or '').lower() or None, 1)
        # Un numero de cuatro cifras o mas ya viene en pesos: '212000 mil' no
        # son doscientos doce millones, es que escribio las dos cosas.
        fuera.add(n if (mult > 1 and n >= 10_000) else n * mult)
    return fuera


def _monto_coincide(mencionados: set[float], valor: float) -> bool:
    v = abs(float(valor))
    return any(abs(m - v) <= max(1.0, v * _TOLERANCIA_MONTO) for m in mencionados)


def a_que_movimiento(
    txt: str | None,
    movimientos: list[dict],
    categoria_implicada: str | None = None,
) -> list[Coincidencia]:
    """Ordena los movimientos por cuanto los senala el texto.

    Cada movimiento es un dict con al menos `id`, `valor` y el nombre del
    comercio en `contraparte` o `descripcion`. Si trae `categoria`, la del
    clasificador, tambien se usa.

    `categoria_implicada` es la categoria que el interprete saco del texto. La
    calcula el llamador y se pasa ya resuelta para que esto siga siendo logica
    pura: comparar dos nombres, no ir a buscarlos.

    El comercio pesa mas que la categoria y la categoria mas que el monto:
    nombrar «tierragro» identifica un movimiento, decir «el gym» lo identifica
    si solo uno es un gimnasio, y «212» puede ser coincidencia. Las palabras
    vagas no cuentan — 'compra' o 'mercado' aparecen en cualquier cosa.
    """
    if not movimientos:
        return []
    palabras = _texto.tokens_distintivos(txt)
    montos = montos_mencionados(txt)
    implicada = _texto.normalizar(categoria_implicada) if categoria_implicada else ''

    fuera = []
    for m in movimientos:
        nombre = m.get('contraparte') or m.get('descripcion') or ''
        del_movimiento = _texto.tokens_distintivos(nombre)
        razones: list[str] = []
        puntaje = 0.0

        comunes = palabras & del_movimiento
        if comunes:
            puntaje += 2.0 * len(comunes)
            razones.append('nombraste ' + ', '.join(sorted(comunes)))

        # Coincidencia parcial: el banco trunca ('TIERRAG' por 'TIERRAGRO'), asi
        # que un token del movimiento puede ser prefijo de lo que escribiste.
        if not comunes:
            for a in palabras:
                for b in del_movimiento:
                    corto, largo = sorted((a, b), key=len)
                    if len(corto) >= 5 and largo.startswith(corto):
                        puntaje += 1.5
                        razones.append(f'{a} ≈ {b}')
                        break

        # La categoria que ya trae el movimiento. «esto fue el gym» no nombra
        # ZONAFIT, pero ZONAFIT ya venia clasificado como Gimnasio y ninguno de
        # los otros lo esta.
        if implicada and _texto.normalizar(m.get('categoria')) == implicada:
            puntaje += 1.5
            razones.append(f'ya venia como {m.get("categoria")}')

        if montos and _monto_coincide(montos, m.get('valor') or 0):
            puntaje += 1.0
            razones.append('el monto cuadra')

        fuera.append(Coincidencia(int(m['id']), puntaje, tuple(razones)))

    return sorted(fuera, key=lambda c: (-c.puntaje, c.id))


def hay_un_ganador(
    coincidencias: list[Coincidencia], margen: float = 1.0
) -> Coincidencia | None:
    """El movimiento senalado sin ambiguedad, o None.

    Exige que el primero tenga evidencia de verdad Y que le saque `margen` al
    segundo. Sin el margen, dos movimientos del mismo comercio se resolverian a
    la suerte, que es justo el bug que se estaba arreglando.
    """
    if not coincidencias:
        return None
    mejor = coincidencias[0]
    if not mejor.senalado:
        return None
    if len(coincidencias) > 1 and mejor.puntaje - coincidencias[1].puntaje < margen:
        return None
    return mejor


# ------------------------------------------------------ pedir un cambio

# Verbos con los que se pide cambiar algo YA registrado. Se exigen explicitos:
# «era Etre» es la respuesta a una pregunta abierta, no una orden de editar, y
# confundirlas haria que contestar una pregunta modificara otro movimiento.
# El sufijo `\w{0,5}` es por los pronombres pegados del espanol: «cambiala»,
# «ponlo», «pasamela», «corrigele». Sin eso `\bcambia\b` no caza con «cambiala»
# y la orden se iba por otro camino sin que nada lo dijera.
_EDITAR = re.compile(
    r'\b(cambia\w{0,5}|cambiar|corrig\w{0,5}|corregir|pon\w{0,5}|pas\w{0,5}'
    r'|muev\w{0,5}|actualiza\w{0,5}|edita\w{0,5}|renombra\w{0,5}'
    r'|reclasifica\w{0,5}|marca\w{0,5})\b',
    re.I,
)
_BORRAR = re.compile(
    r'\b(borra\w{0,5}|borrar|elimina\w{0,5}|eliminar|quita\w{0,5}|quitar)\b',
    re.I,
)
# «la ultima», «el ultimo», «la mas reciente»
_LA_ULTIMA = re.compile(
    r'\b(la|el)\s+(ultima|ultimo|mas\s+reciente)\b|\bultima\s+transaccion\b'
    r'|\bultimo\s+movimiento\b',
    re.I,
)
# «el comercio es Etre», «se llama Etre», «de Etre»
_COMERCIO = re.compile(
    r'\b(?:el\s+)?(?:comercio|negocio|tienda|lugar)\s+(?:es|era|se\s+llama)\s+'
    r'(.{2,40}?)\s*$',
    re.I,
)


# Verbos de etiquetar. Van aparte porque «agrega» y «etiqueta» no son cambios
# de categoria: son otra operacion, y ademas son ADITIVAS. Poner una etiqueta
# no puede borrar las que ya estan —`sin-confirmar` e `ingesta-automatica` las
# usa la conciliacion— asi que se leen, se agrega, y se escriben.
_ETIQUETAR = re.compile(
    r'\b(agrega\w{0,5}|agregar|anade\w{0,5}|anadir|sumale|ponle|pon'
    r'|etiqueta\w{0,5}|etiquetar|tagea\w{0,5}|marca\w{0,5})\b',
    re.I,
)
_QUITAR_ETIQUETA = re.compile(
    r'\b(quita\w{0,5}|quitar|saca\w{0,5}|sacar|borra\w{0,5}|elimina\w{0,5})\b'
    r'.{0,20}\b(etiqueta|tag|label)\b',
    re.I,
)
# El nombre de la etiqueta. Se corta en coma o fin de frase para no tragarse
# «... la etiqueta Ropa a las ultimas dos».
_NOMBRE_ETIQUETA = re.compile(
    r'\b(?:etiquetas?|tags?|labels?)\s+(?:de\s+|como\s+)?'
    r'([^\W\d_][\w -]{1,28}?)'
    r'\s*(?:,|\.|$|\ba\s+la|\ba\s+los|\ba\s+las|\ben\s+la|\bde\s+la)',
    re.I,
)
# «etiquetalas como Ropa», «marcalas como Ropa»
_COMO = re.compile(
    r'\b(?:etiqueta\w{0,5}|marca\w{0,5}|tagea\w{0,5})\s+como\s+'
    r'([^\W\d_][\w -]{1,28}?)\s*(?:,|\.|$)',
    re.I,
)

# Cuantos movimientos: «las ultimas 2», «las 2 ultimas», «las ultimas dos».
_PALABRA_NUMERO = {
    'un': 1,
    'una': 1,
    'uno': 1,
    'dos': 2,
    'tres': 3,
    'cuatro': 4,
    'cinco': 5,
    'seis': 6,
    'siete': 7,
    'ocho': 8,
    'nueve': 9,
    'diez': 10,
}
_CUANTAS = re.compile(
    r'\b(?:ultim\w+|primer\w+)\s+(\d{1,2}|' + '|'.join(_PALABRA_NUMERO) + r')\b'
    r'|\b(\d{1,2}|' + '|'.join(_PALABRA_NUMERO) + r')\s+(?:ultim\w+|primer\w+)\b',
    re.I,
)


@dataclass(frozen=True)
class Edicion:
    """Lo que se entendio de una orden de cambio."""

    pide_cambio: bool
    borrar: bool = False
    la_ultima: bool = False
    comercio: str | None = None
    monto: float | None = None
    # Las etiquetas son ADITIVAS: poner una no borra las que ya estan, porque
    # `sin-confirmar` e `ingesta-automatica` las usa la conciliacion.
    etiqueta_agregar: str | None = None
    etiqueta_quitar: str | None = None
    # «las ultimas 2», «las 2 ultimas». None = una sola.
    cuantas: int | None = None
    # «las que estan en compras»: filtra por categoria.
    categoria_filtro: str | None = None


def es_edicion(txt: str | None) -> Edicion:
    """¿El mensaje pide cambiar o borrar algo ya registrado?

    >>> es_edicion('cambia la ultima a Mercado').pide_cambio
    True
    >>> es_edicion('era Etre, venden cosas para la casa').pide_cambio
    False
    >>> es_edicion('borra la ultima').borrar
    True
    """
    if not txt:
        return Edicion(False)
    t = _texto.sin_tildes(str(txt)).lower().strip()

    # Etiquetas primero: «agregales la etiqueta Ropa» pide un cambio aunque no
    # lleve ninguno de los verbos de recategorizar.
    quitar_etq = bool(_QUITAR_ETIQUETA.search(t))
    nombre_etq = _nombre_de_etiqueta(txt)
    pide_etq = bool(nombre_etq) and (quitar_etq or bool(_ETIQUETAR.search(t)))

    borrar = bool(_BORRAR.search(t)) and not pide_etq
    cambiar = bool(_EDITAR.search(t))
    if not (borrar or cambiar or pide_etq):
        return Edicion(False)

    m = _COMERCIO.search(str(txt))
    montos = montos_mencionados(txt)
    return Edicion(
        pide_cambio=True,
        borrar=borrar,
        la_ultima=bool(_LA_ULTIMA.search(t)),
        comercio=m.group(1).strip(' .,') if m else None,
        # Un monto solo se toma como monto NUEVO si lo pide explicitamente;
        # si no, el numero suele ser para identificar cual movimiento es.
        monto=(
            next(iter(sorted(montos)))
            if montos and re.search(r'\b(son|es|vale|valen|monto)\b', t)
            else None
        ),
        etiqueta_agregar=nombre_etq if (pide_etq and not quitar_etq) else None,
        etiqueta_quitar=nombre_etq if (pide_etq and quitar_etq) else None,
        cuantas=cuantas_pide(txt),
        categoria_filtro=_categoria_mencionada(txt),
    )


def _nombre_de_etiqueta(txt: str) -> str | None:
    """El nombre de la etiqueta que se nombra, o None.

    Se corta en coma o fin de frase para no tragarse «... la etiqueta Ropa a
    las ultimas dos».
    """
    for patron in (_COMO, _NOMBRE_ETIQUETA):
        m = patron.search(str(txt))
        if m:
            nombre = m.group(1).strip(' .,;:')
            if nombre and _texto.sin_tildes(nombre).lower() not in (
                'de',
                'la',
                'el',
                'los',
                'las',
                'a',
                'y',
            ):
                return nombre
    return None


def cuantas_pide(txt: str | None) -> int | None:
    """«las ultimas 2» -> 2. «la ultima» -> None (una sola).

    Existe porque una orden puede ser sobre VARIOS movimientos —«las ultimas 2
    estan en compras, agregales la etiqueta Ropa»— y todo el camino de edicion
    resolvia un solo objetivo.
    """
    if not txt:
        return None
    t = _texto.sin_tildes(str(txt)).lower()
    m = _CUANTAS.search(t)
    if not m:
        return None
    crudo = m.group(1) or m.group(2)
    if crudo is None:
        return None
    n = int(crudo) if crudo.isdigit() else _PALABRA_NUMERO.get(crudo, 0)
    return n if 2 <= n <= 20 else None


# «las que estan en compras», «las de mercado»
_EN_CATEGORIA = re.compile(
    r'\b(?:estan?\s+en|en\s+la\s+categoria|categoria|categorizadas?\s+(?:en|como)'
    r'|de\s+la\s+categoria)\s+([^\W\d_][\w ]{2,28}?)'
    r'\s*(?:,|\.|$|\by\b|\bagrega|\bponle|\betiqueta)',
    re.I,
)


def _categoria_mencionada(txt: str) -> str | None:
    m = _EN_CATEGORIA.search(str(txt))
    return m.group(1).strip(' .,') if m else None


# --------------------------------------------------- seguir la conversacion

# Formas de continuar una pregunta anterior. No llevan verbo ni signo de
# interrogacion, asi que por si solas no parecen preguntas.
#
# «y la anterior a esa» se tomo como la RESPUESTA a una pregunta abierta y el
# bot contesto mostrando el movimiento de Google Workspace con botones de
# categoria. La palabra «esa» cuenta como pasado y anulaba la regla del signo de
# interrogacion, asi que ni con «?» se salvaba.
# (a) Referencias explicitas a otro elemento de la conversacion. Valen por si
#     solas: ninguna de estas describe una compra.
_REFERENCIA = re.compile(
    r'\b(anterior|anteriores|penultima|penultimo|siguiente'
    r'|antes\s+de\s+(esa|ese|esto|eso)|la\s+otra|el\s+otro|las\s+otras'
    r'|(la|el|lo)\s+de\s+antes|de\s+antes'
    r'|cual\s+mas|algo\s+mas|mas\s+detalle|mas\s+detalles|explicame'
    r'|ampliame|amplia|detallame|detalla)\b',
    re.I,
)
# «y esa», «y eso» NO estan arriba a proposito: «y ese fue el mercado del mes
# que hicimos en el exito» empieza igual y describe una compra. Caen en (b), que
# exige que el mensaje sea corto y sin contenido propio.
# (b) Empezar por una palabra de continuacion. Sola NO basta: «ya te dije, es
#     mercado» empieza asi y es una respuesta. Se exige ademas que el mensaje
#     sea muy corto y que no traiga ninguna palabra que pueda ser un comercio,
#     porque «y fue en tierragro» tambien empieza por «y» y si nombra uno.
_CONTINUA = re.compile(r'^(y|ok|ya|pero|entonces|ah|osea|o\s+sea)\b', re.I)


def es_seguimiento(txt: str | None) -> bool:
    """¿Continua la conversacion anterior en vez de empezar algo?

    Es lo que evita romper el hilo. «y la anterior a esa» no lleva verbo ni
    signo de interrogacion, asi que no parecia una pregunta: caia en el camino
    de las respuestas y el bot sacaba un movimiento cualquiera con botones de
    categoria. Y peor, la palabra «esa» cuenta como pasado y anulaba la regla
    del signo de interrogacion, asi que ni escribiendo «?» se salvaba.

    >>> es_seguimiento('y la anterior a esa')
    True
    >>> es_seguimiento('fue la comida de la gata en tierragro')
    False
    >>> es_seguimiento('ya te dije, es mercado')
    False
    """
    if not txt:
        return False
    t = _texto.sin_tildes(str(txt)).lower().strip()
    if not t:
        return False
    if _REFERENCIA.search(t):
        return True
    if not _CONTINUA.search(t):
        return False
    return len(t.split()) <= 4 and not _texto.tokens_distintivos(t)
