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
        'propertyOrdering': [
            'categoria',
            'presupuesto',
            'comercio',
            'confianza',
            'razon',
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
