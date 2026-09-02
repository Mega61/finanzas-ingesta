"""Interpreta texto libre con Gemini, cuando el emparejamiento por palabras no
alcanza.

El caso que hay que resolver: contestas "fue la comida de la gata en tierragro"
y el sistema tiene que sacar categoria, comercio y presupuesto.

Dos ideas hacen que esto sea confiable y no una adivinanza:

1. **La respuesta va restringida por enum.** El esquema JSON solo admite TUS
   categorias, TUS presupuestos activos y TUS cuentas de gasto. El modelo no
   puede inventarse una categoria que no existe en tu Firefly.

2. **Primero se intenta gratis.** El emparejamiento por palabras contra tus
   propios nombres resuelve la mayoria de las frases sin gastar una peticion.
   Gemini entra solo cuando eso no alcanza.

Modelo por defecto: gemini-3.7-flash ($0.75/$3.75 por millon con el precio de
intro hasta el 31-dic-2026). Razona bastante mejor que los flash-lite, que
importa para el asesor. Con este volumen el costo queda bajo un dolar al mes.
`gemini-2.5-flash-lite` es mas barato pero se retira el 16-oct-2026.

Sin GEMINI_API_KEY todo sigue funcionando: se usa solo la heuristica.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping
from typing import Any

from finanzas import config

BASE = 'https://generativelanguage.googleapis.com/v1beta'
MODELO = config.get('GEMINI_MODELO', 'gemini-3.7-flash')
TIMEOUT = int(config.get('GEMINI_TIMEOUT', '60'))

# TRAMPA IMPORTANTE de los modelos que razonan (Gemini 2.5 en adelante):
# los tokens que el modelo gasta PENSANDO cuentan contra maxOutputTokens. Si el
# tope es justo, el modelo se lo gasta razonando y la respuesta sale cortada, o
# vacia, sin ningun error. Fue exactamente lo que paso con el asesor: tope de
# 900 y la respuesta se corto en media frase.
#
# Por eso: presupuesto de pensamiento explicito, tope de salida holgado, y
# deteccion de MAX_TOKENS para no devolver texto truncado como si estuviera bien.
THINKING_CLASIFICAR = int(config.get('GEMINI_THINKING_CLASIFICAR', '0'))
THINKING_ASESOR = int(config.get('GEMINI_THINKING_ASESOR', '1024'))
# Entender una orden si se beneficia de razonar un poco: hay que decidir la
# accion Y a que movimientos aplica, y equivocarse en lo segundo cambia el
# movimiento equivocado.
THINKING_ORDENES = int(config.get('GEMINI_THINKING_ORDENES', '512'))


def _config_generacion(
    max_salida: int, thinking: int, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    cfg = {'maxOutputTokens': max_salida, 'temperature': 0}
    # thinkingBudget=0 apaga el razonamiento. Para extraer datos con esquema no
    # aporta nada y solo se come el presupuesto; para el asesor si suma.
    cfg['thinkingConfig'] = {'thinkingBudget': thinking}
    if extra:
        cfg.update(extra)
    return cfg


def texto_de(respuesta: dict[str, Any], que: str = '') -> str:
    """Saca el texto y falla claro si vino truncado.

    Devolver una respuesta cortada como si estuviera completa es peor que
    fallar: el usuario lee media frase y no sabe que le falta la mitad.
    """
    try:
        c = respuesta['candidates'][0]
    except (KeyError, IndexError):
        raise SinIA(f'respuesta sin candidatos{que}: {str(respuesta)[:250]}') from None

    razon = c.get('finishReason')
    partes = (c.get('content') or {}).get('parts') or []
    txt = ''.join(p.get('text', '') for p in partes).strip()

    if razon == 'MAX_TOKENS':
        um = respuesta.get('usageMetadata') or {}
        pensados = um.get('thoughtsTokenCount', 0)
        raise SinIA(
            f'la respuesta se corto por tope de tokens{que}. '
            f'El modelo gasto {pensados} tokens pensando de '
            f'{um.get("candidatesTokenCount", "?")} disponibles. '
            f'Sube GEMINI_THINKING_* o el tope de salida.'
        )
    if not txt:
        raise SinIA(f'respuesta vacia{que} (finishReason={razon})')
    return txt


class SinIA(Exception):
    """No hay API key, o la llamada no sirvio."""


def disponible() -> bool:
    return bool(config.get('GEMINI_API_KEY'))


def _llamar(payload: dict[str, Any]) -> dict[str, Any]:
    key = config.get('GEMINI_API_KEY')
    if not key:
        raise SinIA('falta GEMINI_API_KEY')
    url = f'{BASE}/models/{MODELO}:generateContent'
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode('utf-8'), method='POST'
    )
    req.add_header('Content-Type', 'application/json')
    req.add_header('x-goog-api-key', key)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as ex:
        cuerpo = ex.read().decode('utf-8', 'replace')
        raise SinIA(f'Gemini HTTP {ex.code}: {cuerpo[:300]}') from None
    except urllib.error.URLError as ex:
        raise SinIA(f'no pude llegar a Gemini: {ex.reason}') from None


def modelos() -> list[str]:
    """Los modelos que admite esta API key. Sirve para verificar el setup."""
    key = config.get('GEMINI_API_KEY')
    if not key:
        raise SinIA('falta GEMINI_API_KEY')
    req = urllib.request.Request(f'{BASE}/models')
    req.add_header('x-goog-api-key', key)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            datos = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as ex:
        raise SinIA(
            f'HTTP {ex.code}: {ex.read().decode("utf-8", "replace")[:200]}'
        ) from None
    return [
        m['name'].split('/')[-1]
        for m in datos.get('models', [])
        if 'generateContent' in (m.get('supportedGenerationMethods') or [])
    ]


# ------------------------------------------------------------------ el prompt

INSTRUCCIONES = """Eres el clasificador de un sistema de finanzas personales que
usa Firefly III. El usuario responde en espanol coloquial colombiano sobre un
movimiento bancario, y tu trabajo es traducir esa respuesta a los valores que
existen en SU Firefly.

Reglas:
- Elige SOLO valores de las listas dadas. No inventes categorias ni comercios.
- La descripcion del banco suele venir truncada o con el nombre de una pasarela
  de pago pegado. 'MERCADO PAGO*TIERRAG' es la pasarela Mercado Pago cobrando
  para el comercio 'TIERRAG', que probablemente sea 'Tierragro'.
- Si el usuario menciona un comercio que se parece a uno de la lista, usa el de
  la lista.
- Si el usuario dice para quien o para que era, eso decide la categoria.
  'comida de la gata' o 'granos de la michi' es la categoria del gato.
- El presupuesto es un bloque grande de gasto, no el concepto. Si el usuario da
  senales de que fue un gasto necesario, va al presupuesto esencial; si suena a
  gusto o capricho, al de antojos.
- confianza: 0.9 o mas solo si el usuario fue explicito. Si estas
  interpretando o adivinando, ponla por debajo de 0.7.
- razon: una frase corta, en espanol, explicando por que. El usuario la va a
  leer para confirmar o corregir.
"""


def _esquema(
    categorias: Iterable[str], presupuestos: Iterable[str], comercios: Iterable[str]
) -> dict[str, Any]:
    def enum(vals):
        # se recorta porque el esquema viaja en cada peticion
        return {'type': 'string', 'enum': list(vals)[:250]}

    presupuestos = list(presupuestos)  # ver la nota en `_esquema_orden`
    comercios = list(comercios)
    props = {
        'categoria': enum(categorias),
        'confianza': {'type': 'number'},
        'razon': {'type': 'string'},
    }
    req = ['categoria', 'confianza', 'razon']
    if presupuestos:
        props['presupuesto'] = enum(presupuestos)
    if comercios:
        props['comercio'] = enum(comercios)
    return {
        'type': 'object',
        'properties': props,
        'required': req,
        # Solo lo que existe: un campo listado aqui y ausente de `properties`
        # hace que la API rechace el esquema entero. Ver `_esquema_orden`.
        'propertyOrdering': [
            k
            for k in ('categoria', 'presupuesto', 'comercio', 'confianza', 'razon')
            if k in props
        ],
    }


def interpretar(
    texto: str,
    movimiento: Mapping[str, Any],
    categorias: Iterable[str],
    presupuestos: Iterable[str],
    comercios: Iterable[str],
    similares: list[tuple[str, str, str]] | None = None,
) -> dict[str, Any]:
    """texto: lo que escribio el usuario.
    movimiento: dict con fecha, valor, moneda, contraparte, descripcion.
    similares: lista de (descripcion, categoria, presupuesto) de compras
               parecidas del historico, para que el modelo tenga con que anclar.

    Devuelve dict con categoria, presupuesto, comercio, confianza, razon.
    """
    m = movimiento
    partes = [
        'MOVIMIENTO DEL BANCO',
        f'  fecha: {m.get("fecha")}',
        f'  monto: {m.get("valor")} {m.get("moneda") or "COP"}',
        f'  lo que dice el banco: {m.get("contraparte") or m.get("descripcion")}',
        f'  cuenta: {m.get("cuenta_firefly") or "?"}',
        '',
        f'LO QUE RESPONDIO EL USUARIO: {texto}',
    ]
    if similares:
        partes += ['', 'COMPRAS PARECIDAS YA CLASIFICADAS (para anclar):']
        for d, c, p in similares[:12]:
            partes.append(
                f"  '{d}' -> categoria '{c}'" + (f", presupuesto '{p}'" if p else '')
            )
    partes += ['', 'CATEGORIAS DISPONIBLES: ' + ', '.join(sorted(categorias))]
    if presupuestos:
        partes.append('PRESUPUESTOS ACTIVOS: ' + ', '.join(sorted(presupuestos)))
    if comercios:
        partes.append('COMERCIOS CONOCIDOS: ' + ', '.join(sorted(comercios)[:250]))

    payload = {
        'systemInstruction': {'parts': [{'text': INSTRUCCIONES}]},
        'contents': [{'role': 'user', 'parts': [{'text': '\n'.join(partes)}]}],
        'generationConfig': _config_generacion(
            # holgado: el esquema con los enum hace que la salida sea corta,
            # pero el tope tiene que dejar espacio de sobra
            max_salida=1200,
            thinking=THINKING_CLASIFICAR,
            extra={
                'responseMimeType': 'application/json',
                'responseSchema': _esquema(categorias, presupuestos, comercios),
            },
        ),
    }
    r = _llamar(payload)
    crudo = texto_de(r, ' al clasificar')
    try:
        d = json.loads(crudo)
    except json.JSONDecodeError:
        # por si viniera envuelto en ```json
        mm = re.search(r'\{.*\}', crudo, re.S)
        if not mm:
            raise SinIA(f'no devolvio JSON: {crudo[:200]}') from None
        d = json.loads(mm.group(0))

    d.setdefault('confianza', 0.5)
    d.setdefault('razon', '')
    # el enum ya lo garantiza, pero por si el modelo se sale del esquema
    if d.get('categoria') and d['categoria'] not in categorias:
        d['categoria'] = None
    if d.get('presupuesto') and d['presupuesto'] not in presupuestos:
        d['presupuesto'] = None
    return d


# ------------------------------------------------------- entender una orden

ORDENES = """Eres el cerebro de un bot de finanzas personales por Telegram. El
usuario te escribe en espanol coloquial colombiano y tu decides QUE quiere
hacer y SOBRE QUE movimientos.

Se te da la lista de sus movimientos recientes con su id, y los catalogos de
categorias, presupuestos y etiquetas que existen. Devuelves un plan.

LAS ACCIONES:

  consultar   Pregunta algo sobre sus finanzas: «cuanto llevo gastado»,
              «cual fue la ultima», «me alcanza para una bici», «y la anterior
              a esa». No cambia nada. Si dudas entre consultar y editar y no
              hay un verbo de cambio claro, es consultar.
              PREGUNTAR SI SE PUEDE HACER ALGO NO ES PEDIR QUE SE HAGA: «¿y eso
              lo puedo poner en esencial?», «¿deberia moverlo a antojos?», «¿eso
              va en vivir?» son consultar, no editar. Se reconocen por el signo
              de pregunta y por «puedo», «deberia», «se puede», «conviene»,
              «vale la pena». El usuario esta pensando en voz alta y quiere una
              opinion con numeros; si se le cambia el movimiento en vez de
              contestarle, se le cambio algo que no habia decidido.

  editar      Pide cambiar algo YA registrado: la categoria, el presupuesto,
              las etiquetas o el nombre del comercio. Puede ser sobre varios:
              «las ultimas 2 estan en compras, agregales la etiqueta Ropa».

  responder   Esta contestando a que corresponde un movimiento que el bot le
              pregunto: «fue la comida de la gata en tierragro», «esto fue el
              gym», «era Etre, una empresa que vende cosas para la casa». Esto
              tambien acaba en un cambio, pero es una respuesta, no una orden.

  borrar      Pide borrar un movimiento de verdad. NO confundir con quitar una
              etiqueta.

  regla_presupuesto  Dice que una CATEGORIA siempre va a un PRESUPUESTO:
              «Compras va en Antojos», «Regalos siempre es Vivir». Eso no
              cambia un movimiento, cambia una regla. Llena `categoria` y
              `presupuesto`.

  clasificar_producto   Esta diciendo QUE ES un producto de supermercado que
              se le pregunto. Ojo: los productos son LINEAS DE FACTURA del
              super (Exito, D1), no movimientos del banco. Si en la lista de
              PRODUCTOS PENDIENTES hay uno y el mensaje describe que es ese
              producto —«es el costo del domicilio», «eso es jabon», «arroz»—
              esta accion, NO editar. Llena `producto_id`, `producto_grupo` y
              `producto_categoria`.

  nada        No entendiste. Mejor eso que inventar.

REGLAS DURAS:

- `movimientos` son ids de la lista que se te dio. NUNCA inventes uno. Si la
  orden no dice a cual, dejalo vacio y baja la confianza.
- «las que estan en X» es un FILTRO para saber a cuales se refiere, no una
  orden de ponerles X. Si dice «las ultimas 2 estan en compras, agregales la
  etiqueta Ropa», el unico cambio es la etiqueta: la categoria no se toca.
- Si se te dice LO QUE SE ACABA DE CAMBIAR y el mensaje es una correccion
  —«no espera, era el mercado», «me equivoque, era X», «no, mejor ponlo en»—
  va sobre ESOS ids. Sin esto la correccion se le escribia a un movimiento que
  el usuario nunca menciono.
- «la ultima» es el PRIMERO de la lista, que viene del mas nuevo al mas viejo.
  «la anterior a esa» es el segundo. «las ultimas 2» son los dos primeros.
- `categoria` y `presupuesto` solo pueden ser de los catalogos. Si el usuario
  nombra algo que no esta, deja el campo vacio y dilo en `explicacion`. Y NO lo
  reemplaces por el parecido de OTRO catalogo: si pide «el presupuesto Viajes
  Largos» y ese presupuesto no existe, no le pongas la CATEGORIA «Viajes». Eso
  le borra la categoria que tenia por algo que nunca pidio.
- Si el mensaje le pone valores DISTINTOS a movimientos distintos —«la de
  tierragro ponla en gato y la de uber en salidas»— usa `lotes`: un lote por
  cada grupo que recibe lo mismo, cada uno con sus propios ids y sus propios
  campos. Los campos de arriba (`movimientos`, `categoria`, ...) son solo para
  cuando TODOS reciben lo mismo. Si mandas tres ids arriba con una sola
  categoria, los tres quedan con esa categoria, que no es lo que pidio.
  Lo mismo aplica cuando contesta varias preguntas de un tiro: «tierragro es
  comida de gato, zona fit es el gym y google es del trabajo» son tres lotes.
- `comercio` SI es libre: el banco manda el nombre de la pasarela de pago
  («MERCADO PAGO*XX», «BOLD CO...») y el negocio real solo lo sabe el usuario.
  Llenalo cuando te de un nombre propio de negocio.
- Las etiquetas son libres y ADITIVAS. Poner una no quita las que ya estan.
  `etiquetas_quitar` solo cuando pida explicitamente quitarlas.
- Distingue etiqueta de categoria: «agregale la etiqueta Ropa» es una etiqueta
  aunque «Ropa» tambien sea una categoria.
- Distingue categoria de presupuesto por el catalogo del que sale el nombre.
  «ponla en Gato» es categoria; «ponla en Antojos» es presupuesto.
- `confianza`: 0.9+ cuando la orden es inequivoca; 0.5-0.7 cuando adivinas a
  cual movimiento; menos de 0.5 si de verdad no sabes.
- `explicacion` en una linea, en espanol, para mostrarsela al usuario.
- Un PRODUCTO no es un MOVIMIENTO. «fletes gravado» es una linea de la factura
  del super; una transaccion es un cargo del banco. Si el usuario contesta algo
  que describe un producto pendiente, es `clasificar_producto`, y no le ofrezcas
  cambiarle la categoria a una compra del banco.
- `producto_grupo` y `producto_categoria` tienen que ser un par valido de la
  lista de GRUPOS Y CATEGORIAS DE PRODUCTO que se te da.
"""


def _esquema_orden(
    ids: Iterable[str],
    categorias: Iterable[str],
    presupuestos: Iterable[str],
    productos: Iterable[str] = (),
    grupos: Iterable[str] = (),
    cats_producto: Iterable[str] = (),
) -> dict[str, Any]:
    """El esquema de la respuesta.

    Los enum son la red de seguridad que importa: sin ellos el modelo inventa
    una categoria que no existe o un id de movimiento que no le dimos, y el bot
    la aplica. Con el enum, la API no deja que salga otra cosa.

    `comercio` y las etiquetas quedan LIBRES a proposito: el nombre del negocio
    y una etiqueta nueva son justo lo que el usuario aporta y el sistema no
    puede conocer de antemano.
    """

    def enum(vals):
        return {'type': 'string', 'enum': list(vals)[:250]}

    # Se materializan: `if categorias` sobre un generador vacio da verdadero
    # y volveriamos a mandar el enum vacio que se quiso evitar.
    lista_ids = list(ids)[:60]
    categorias = list(categorias)
    presupuestos = list(presupuestos)
    props = {
        'accion': {
            'type': 'string',
            'enum': [
                'consultar',
                'editar',
                'responder',
                'borrar',
                'regla_presupuesto',
                'clasificar_producto',
                'nada',
            ],
        },
        'movimientos': {
            'type': 'array',
            'items': enum(lista_ids) if lista_ids else {'type': 'string'},
        },
        # No van en `required`: la API rechaza un enum con cadena vacia,
        # asi que la forma de decir «ninguna» es omitir el campo. Y si la lista
        # viene vacia —Firefly sin presupuestos, instalacion nueva— el campo no
        # va: un enum vacio tumba la peticion completa y el bot arranca sin
        # cerebro.
        **({'categoria': enum(categorias)} if categorias else {}),
        **({'presupuesto': enum(presupuestos)} if presupuestos else {}),
        'comercio': {'type': 'string'},
        'etiquetas_agregar': {'type': 'array', 'items': {'type': 'string'}},
        'etiquetas_quitar': {'type': 'array', 'items': {'type': 'string'}},
        # Un mensaje puede darle valores distintos a movimientos distintos.
        # Sin esto solo cabia UN juego de cambios para toda la lista, asi que
        # «la de tierragro a gato y la de uber a salidas» le ponia lo mismo a
        # las dos: el modelo lo explicaba bien y se escribia mal.
        'lotes': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'movimientos': {
                        'type': 'array',
                        'items': enum(lista_ids) if lista_ids else {'type': 'string'},
                    },
                    **({'categoria': enum(categorias)} if categorias else {}),
                    **({'presupuesto': enum(presupuestos)} if presupuestos else {}),
                    'comercio': {'type': 'string'},
                    'etiquetas_agregar': {
                        'type': 'array',
                        'items': {'type': 'string'},
                    },
                    'etiquetas_quitar': {
                        'type': 'array',
                        'items': {'type': 'string'},
                    },
                },
                'required': ['movimientos'],
            },
        },
        **(
            {
                'producto_id': enum(list(productos)[:40]),
                'producto_grupo': enum(grupos),
                'producto_categoria': enum(cats_producto),
            }
            if productos
            else {}
        ),
        'confianza': {'type': 'number'},
        'explicacion': {'type': 'string'},
    }
    # El orden se arma de las propiedades que DE VERDAD estan. Listar un campo
    # que no existe hace que la API rechace el esquema entero con un 404
    # «Requested entity was not found», que no dice nada, y el plan falla en
    # silencio: el bot cae al respaldo por patrones y parece tonto sin motivo.
    # Pasaba siempre que no habia productos pendientes, o sea casi siempre.
    orden = [
        'accion',
        'movimientos',
        'categoria',
        'presupuesto',
        'comercio',
        'etiquetas_agregar',
        'etiquetas_quitar',
        'lotes',
        'producto_id',
        'producto_grupo',
        'producto_categoria',
        'confianza',
        'explicacion',
    ]
    return {
        'type': 'object',
        'properties': props,
        'required': ['accion', 'confianza', 'explicacion'],
        'propertyOrdering': [k for k in orden if k in props],
    }


def entender_orden(
    texto: str,
    movimientos: list[Mapping[str, Any]],
    categorias: Iterable[str],
    presupuestos: Iterable[str],
    etiquetas: Iterable[str] = (),
    abiertas: list[Mapping[str, Any]] | None = None,
    historial: list[tuple[str, str]] | None = None,
    productos: list[Mapping[str, Any]] | None = None,
    grupos_producto: Mapping[str, Any] | None = None,
    tocados: list[str] | None = None,
) -> dict[str, Any]:
    """Que quiere hacer el usuario, y sobre que movimientos.

    Esta es la pieza que faltaba. Antes a Gemini solo se le preguntaba «¿que
    categoria?» sobre UN movimiento, con el comercio restringido a un enum de
    los que ya existian —asi que ni podia proponer un nombre nuevo— y todo lo
    demas lo decidian expresiones regulares: a cual te referias, si era
    pregunta o respuesta, las etiquetas, el plural, los presupuestos. Cada
    fallo del bot salio de ahi.

    Levanta SinIA si no hay API key o la llamada falla, para que el llamador
    pueda caer al camino de regex.
    """
    lineas = [f'MENSAJE DEL USUARIO: {texto}', '']
    if tocados:
        lineas.append(
            'LO QUE SE ACABA DE CAMBIAR (ids): '
            + ', '.join(tocados)
            + '. Si el mensaje corrige o rectifica —«no espera», «me equivoque»,'
            ' «mejor ponlo en»— es SOBRE ESTOS, no sobre el mas reciente.'
        )
        lineas.append('')
    if historial:
        lineas.append('LA CONVERSACION HASTA AHORA (lo ultimo al final):')
        for rol, txt in historial[-6:]:
            lineas.append(f'  {rol}: {txt[:180]}')
        lineas.append('')
    lineas.append('SUS MOVIMIENTOS RECIENTES (del mas nuevo al mas viejo):')
    for m in movimientos[:40]:
        etqs = [
            e
            for e in (m.get('etiquetas') or [])
            if e.lower() not in ('sin-confirmar', 'ingesta-automatica')
            and not e.lower().startswith('recon-')
        ]
        lineas.append(
            f'  id={m.get("id")} {m.get("fecha")} {m.get("valor")} '
            f'"{m.get("destino") or m.get("descripcion")}" '
            f'categoria={m.get("categoria") or "-"} '
            f'presupuesto={m.get("presupuesto") or "-"} '
            f'etiquetas={",".join(etqs) or "-"}'
        )
    if abiertas:
        lineas += ['', 'MOVIMIENTOS QUE EL BOT LE PREGUNTO Y SIGUEN SIN RESOLVER:']
        for p in abiertas[:10]:
            lineas.append(
                f'  id_firefly={p.get("firefly_id") or "?"} '
                f'{p.get("fecha")} {p.get("valor")} '
                f'"{p.get("contraparte") or p.get("descripcion")}"'
            )
    if productos:
        lineas += [
            '',
            'PRODUCTOS DE SUPERMERCADO PENDIENTES (lineas de factura, NO '
            'movimientos del banco):',
        ]
        for pr in productos[:20]:
            lineas.append(
                f'  producto_id={pr.get("id")} '
                f'"{pr.get("descripcion") or pr.get("codigo")}"'
            )
    if grupos_producto:
        lineas += ['', 'GRUPOS Y CATEGORIAS DE PRODUCTO:']
        for g, cs in grupos_producto.items():
            lineas.append(f'  {g}: {", ".join(cs)}')
    lineas += ['', 'CATEGORIAS: ' + ', '.join(sorted(categorias))]
    lineas.append('PRESUPUESTOS: ' + ', '.join(sorted(presupuestos)))
    if etiquetas:
        lineas.append('ETIQUETAS QUE YA USA: ' + ', '.join(sorted(etiquetas)[:60]))

    payload = {
        'systemInstruction': {'parts': [{'text': ORDENES}]},
        'contents': [{'role': 'user', 'parts': [{'text': chr(10).join(lineas)}]}],
        'generationConfig': _config_generacion(
            1600,
            THINKING_ORDENES,
            {
                'responseMimeType': 'application/json',
                'responseSchema': _esquema_orden(
                    [str(m.get('id')) for m in movimientos[:40] if m.get('id')],
                    categorias,
                    presupuestos,
                    [str(p.get('id')) for p in (productos or []) if p.get('id')],
                    list(grupos_producto or {}),
                    sorted({c for cs in (grupos_producto or {}).values() for c in cs}),
                ),
            },
        ),
    }
    crudo = texto_de(_llamar(payload), ' al entender la orden')
    try:
        d = json.loads(crudo)
    except json.JSONDecodeError as ex:
        raise SinIA(f'no pude leer el plan: {ex}; crudo={crudo[:200]}') from None
    # Se limpia lo que venga vacio, para que el llamador solo vea lo que hay.
    for k in (
        'categoria',
        'presupuesto',
        'comercio',
        'producto_id',
        'producto_grupo',
        'producto_categoria',
    ):
        if not (d.get(k) or '').strip():
            d[k] = None
    for k in ('etiquetas_agregar', 'etiquetas_quitar', 'movimientos'):
        d[k] = [x for x in (d.get(k) or []) if str(x).strip()]
    return d
