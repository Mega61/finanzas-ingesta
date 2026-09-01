"""Convierte una respuesta en espanol coloquial en categoria, comercio y
presupuesto.

    "fue la comida de la gata en tierragro"
        -> categoria 'Gato', comercio 'Tierragro', presupuesto 'Esencial'

Dos etapas, y la primera es gratis:

1. **Heuristica.** Empareja las palabras de la respuesta contra TUS nombres de
   categorias, TUS cuentas de gasto y una tabla corta de sinonimos coloquiales
   (gata, michi, minina -> la categoria del gato). Resuelve la mayoria de las
   frases sin gastar una peticion de API.

2. **Gemini.** Solo si la heuristica no encontro categoria, o si encontro varias
   y no puede decidir. La respuesta va restringida por enum a tus valores
   reales, asi que no puede inventarse nada.

Sin GEMINI_API_KEY funciona solo con la etapa 1.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import clasificador
import db

# Formas coloquiales -> nombre de la categoria en Firefly. La clave es el
# nombre normalizado de la categoria, para que resuelva contra la lista real.
#
# Es una lista corta a proposito: lo demas lo resuelve Gemini. Y crece sola,
# porque cada respuesta confirmada queda como regla del comercio.
SINONIMOS = {
    'GATO': ['GATA', 'GATICA', 'GATICO', 'MICHI', 'MICHINA', 'MININA', 'MININO',
             'FELINA', 'FELINO', 'ARENA', 'CONCENTRADO', 'VETERINARIO',
             'VETERINARIA'],
    'MERCADO': ['MERCAR', 'MERCADITO', 'SUPERMERCADO', 'REMESA', 'DESPENSA'],
    'RESTAURANTE': ['ALMORZAMOS', 'ALMUERZO', 'CENAMOS', 'CENA', 'COMIMOS',
                    'RESTAURANTE'],
    'DESAYUNO': ['DESAYUNAMOS', 'DESAYUNO'],
    'MECATO': ['MECATO', 'DULCE', 'DULCES', 'GASEOSA', 'PAQUETE', 'CHICLES',
               'HELADO', 'PICADA'],
    'DOMICILIO': ['DOMI', 'DOMICILIO', 'PEDIMOS'],
    'TRANSPORTE APLICACION': ['UBER', 'DIDI', 'TAXI', 'CABIFY'],
    'TRANSPORTE MOTO': ['GASOLINA', 'TANQUEAR', 'TANQUEE', 'COMBUSTIBLE',
                        'TANQUEO'],
    'GIMNASIO': ['GYM', 'GIMNASIO', 'ENTRENO', 'MENSUALIDAD'],
    'CUIDADO PERSONAL': ['BARBERIA', 'PELUQUERIA', 'UNAS', 'MANICURE',
                         'PEDICURE'],
    'MEDICAMENTOS': ['DROGUERIA', 'FARMACIA', 'MEDICINA', 'REMEDIO', 'PASTILLAS'],
    'FACTURAS': ['FACTURA', 'RECIBO', 'LUZ', 'AGUA', 'INTERNET', 'EPM', 'TIGO'],
    'CAFE': ['CAFE', 'TINTO', 'CAPUCHINO'],
    'ROPA': ['ROPA', 'CAMISA', 'PANTALON', 'ZAPATOS', 'TENIS'],
    'SALIDAS': ['RUMBA', 'BAR', 'TRAGOS', 'CERVEZAS', 'DISCOTECA'],
}

# Palabras demasiado genericas para decidir una categoria por si solas. Sin
# esto 'comida' cazaba con 'Comida de calle' y le ganaba a que el usuario
# hubiera dicho 'gata', que es la senal de verdad.
PALABRAS_VAGAS = {'COMIDA', 'COMPRA', 'COMPRAS', 'GASTO', 'PAGO', 'COSAS',
                  'ARTICULOS', 'TRANSPORTE', 'PERSONAL', 'CALLE', 'MES'}

# Senales de que el gasto fue por gusto y no por necesidad. Ayudan a elegir
# presupuesto cuando la categoria no lo decide sola.
SENAL_ANTOJO = ['ANTOJO', 'CAPRICHO', 'GANAS', 'GUSTO', 'PORQUE QUISE',
                'ME PROVOCO', 'REGALO', 'SALIDA', 'PASEO', 'CELEBRA']
SENAL_ESENCIAL = ['NECESARIO', 'TOCABA', 'SE ACABO', 'URGENTE', 'OBLIGATORIO',
                  'MANTENIMIENTO', 'REPARAR', 'ARREGLAR', 'MEDICINA', 'REMEDIO']


def _norm(t):
    return clasificador.normalizar(t)


def _tokens(t):
    return {x for x in _norm(t).split() if len(x) > 2}


# --------------------------------------------------------------- heuristica

def _buscar_categoria(texto, categorias):
    """Devuelve [(nivel, categoria, por_que)] ordenado de mas fuerte a mas debil.

    Los niveles importan mas que el orden de busqueda:

      3  el nombre completo de la categoria aparece en el texto
      2  un sinonimo coloquial curado ('gata' -> Gato). Alta precision.
      1  comparten una palabra larga que NO es vaga

    Sin niveles, 'la comida de la gata' cazaba 'Comida de calle' por la palabra
    'comida' y le ganaba a 'Gato', que era la senal real.
    """
    tn = _norm(texto)
    tk = _tokens(texto)
    hallados = {}

    def anotar(nivel, cat, razon):
        if cat not in hallados or hallados[cat][0] < nivel:
            hallados[cat] = (nivel, cat, razon)

    # nivel 3: el nombre completo, con limites de palabra
    for c in categorias:
        cn = _norm(c)
        if len(cn) >= 4 and re.search(rf'\b{re.escape(cn)}\b', tn):
            anotar(3, c, f"dijiste «{c}»")

    # nivel 2: sinonimos curados. \b a los DOS lados: sin eso 'GAS' cazaba
    # dentro de 'GASOLINA' y mandaba la gasolina a Facturas.
    for clave, palabras in SINONIMOS.items():
        if not any(re.search(rf'\b{re.escape(p)}\b', tn) for p in palabras):
            continue
        dicha = next(p for p in palabras if re.search(rf'\b{re.escape(p)}\b', tn))
        for c in categorias:
            if _norm(c) == clave:
                anotar(2, c, f"«{dicha.lower()}» es {c}")
                break

    # nivel 1: palabra compartida, si no es de las vagas
    for c in categorias:
        comunes = [x for x in (tk & _tokens(c))
                   if len(x) >= 5 and x not in PALABRAS_VAGAS]
        if comunes:
            anotar(1, c, f"dijiste «{comunes[0].lower()}»")

    return sorted(hallados.values(), key=lambda h: (-h[0], -len(h[1])))


def _buscar_comercio(texto, comercios, excluir=None):
    """El comercio que menciona el texto, si alguno de la lista aparece.

    Con limites de palabra: sin eso, decir 'mercado del mes' proponia el
    comercio 'Minimercado Amonte', porque MERCADO esta dentro de MINIMERCADO.
    Y un comercio equivocado manda la transaccion a la cuenta de gasto
    equivocada, que es peor que no proponer nada.
    """
    tn = _norm(texto)
    tk = _tokens(texto)
    # Las palabras que ya explicaron la categoria no pueden ademas elegir el
    # comercio: decir 'mercado del mes' no significa comprar en 'Mercado Libre'.
    vetadas = set(excluir or ())
    mejor, mejor_largo = None, 0
    for m in comercios:
        mn = _norm(m)
        if len(mn) < 4:
            continue
        # el nombre completo aparece como palabra: 'tierragro' en el texto
        if re.search(rf'\b{re.escape(mn)}\b', tn) and len(mn) > mejor_largo:
            mejor, mejor_largo = m, len(mn)
            continue
        # o una palabra del texto ES una palabra del comercio, no una parte
        mt = _tokens(m)
        comunes = [t for t in (tk & mt)
                   if len(t) >= 5 and t not in PALABRAS_VAGAS
                   and t not in vetadas]
        if comunes and len(mn) > mejor_largo:
            mejor, mejor_largo = m, len(mn)
    return mejor


def _senal_presupuesto(texto):
    tn = _norm(texto)
    if any(s in tn for s in SENAL_ANTOJO):
        return 'antojo'
    if any(s in tn for s in SENAL_ESENCIAL):
        return 'esencial'
    return None


# ------------------------------------------------------------------ catalogo

def catalogo(cx, usuario_id):
    """Lo que existe en el Firefly del usuario, para restringir la respuesta."""
    import firefly
    import presupuestos
    import taxonomia
    cats = sorted({c['attributes']['name']
                   for c in firefly.get_all('/api/v1/categories')})
    # ni la heuristica ni Gemini pueden proponer una categoria retirada
    cats = taxonomia.vigentes(cats)
    comercios = sorted({a['attributes']['name']
                        for a in firefly.get_all('/api/v1/accounts?type=expense')})
    return {
        'categorias': cats,
        'presupuestos': presupuestos.nombres_activos(),
        'comercios': comercios,
        'mapa': presupuestos.mapa_categoria(),
    }


def similares(cx, usuario_id, pendiente, texto, limite=12):
    """Compras ya clasificadas que se parecen, para anclar la interpretacion."""
    tk = _tokens(texto) | _tokens(pendiente['contraparte'] or '')
    if not tk:
        return []
    filas = cx.execute(
        """SELECT patron, categoria, presupuesto FROM reglas
           WHERE categoria IS NOT NULL AND categoria <> ''
             AND (usuario_id = ? OR usuario_id IS NULL)""",
        (usuario_id,)).fetchall()
    puntuadas = []
    for r in filas:
        comunes = tk & _tokens(r['patron'])
        if comunes:
            puntuadas.append((len(comunes), r['patron'], r['categoria'],
                              r['presupuesto']))
    puntuadas.sort(reverse=True)
    return [(p, c, pr) for _, p, c, pr in puntuadas[:limite]]


# -------------------------------------------------------------------- api

def interpretar(cx, usuario_id, pendiente, texto, cat=None):
    """Devuelve dict con categoria, presupuesto, comercio, confianza, razon,
    fuente ('heuristica' | 'gemini' | 'nada') y `pedir_presupuesto`.
    """
    cat = cat or catalogo(cx, usuario_id)
    r = {'categoria': None, 'presupuesto': None, 'comercio': None,
         'confianza': 0.0, 'razon': '', 'fuente': 'nada',
         'pedir_presupuesto': False}

    # --- etapa 1: gratis
    hallazgos = _buscar_categoria(texto, cat['categorias'])
    # se vetan las palabras de la categoria ganadora para buscar el comercio
    vetadas = _tokens(hallazgos[0][1]) if hallazgos else set()
    comercio = _buscar_comercio(texto, cat['comercios'], excluir=vetadas)

    # ¿el mejor hallazgo gana claramente? Si el segundo esta en un nivel mas
    # bajo, no hay competencia y no hace falta gastar una peticion de API.
    claro = bool(hallazgos) and (
        len(hallazgos) == 1 or hallazgos[0][0] > hallazgos[1][0])

    if claro:
        nivel, r['categoria'], r['razon'] = hallazgos[0]
        r['comercio'] = comercio
        r['confianza'] = {3: 0.88, 2: 0.85, 1: 0.72}[nivel]
        if comercio:
            r['confianza'] = min(0.93, r['confianza'] + 0.05)
        r['fuente'] = 'heuristica'
    elif hallazgos and not ia_disponible():
        # empate y sin IA: se toma el primero pero con confianza baja, para que
        # el bot lo muestre como propuesta y no como hecho
        nivel, r['categoria'], r['razon'] = hallazgos[0]
        r['comercio'] = comercio
        r['confianza'] = 0.6
        r['fuente'] = 'heuristica'
        otras = ', '.join(h[1] for h in hallazgos[1:3])
        r['razon'] += f" · tambien podria ser {otras}"

    # --- etapa 2: Gemini, solo si no quedo claro
    if not claro:
        d = _con_gemini(cx, usuario_id, pendiente, texto, cat)
        if d and d.get('categoria'):
            for k in ('categoria', 'presupuesto', 'comercio'):
                if d.get(k):
                    r[k] = d[k]
            r['confianza'] = float(d.get('confianza') or 0.5)
            r['razon'] = d.get('razon') or r['razon']
            r['fuente'] = 'gemini'

    if not r['categoria']:
        return r

    # --- presupuesto
    if not r['presupuesto']:
        info = cat['mapa'].get(r['categoria'])
        if info and info['seguro']:
            r['presupuesto'] = info['presupuesto']
        elif info:
            # la categoria no decide sola: se mira si el texto da alguna senal
            senal = _senal_presupuesto(texto)
            opciones = list(info['reparto'])
            elegida = None
            if senal == 'antojo':
                elegida = next((o for o in opciones if 'ANTOJO' in _norm(o)), None)
            elif senal == 'esencial':
                elegida = next((o for o in opciones if 'ESENCIAL' in _norm(o)), None)
            if elegida:
                r['presupuesto'] = elegida
                r['razon'] += f" · '{senal}' apunta a {elegida}"
            else:
                r['pedir_presupuesto'] = True
        else:
            r['pedir_presupuesto'] = True
    return r


def ia_disponible():
    try:
        import ia
        return ia.disponible()
    except Exception:
        return False


def _con_gemini(cx, usuario_id, pendiente, texto, cat):
    try:
        import ia
        if not ia.disponible():
            return None
        return ia.interpretar(
            texto,
            {'fecha': pendiente['fecha'], 'valor': pendiente['valor'],
             'moneda': pendiente['moneda'],
             'contraparte': pendiente['contraparte'],
             'descripcion': pendiente['descripcion'],
             'cuenta_firefly': pendiente['cuenta_firefly']},
            cat['categorias'], cat['presupuestos'], cat['comercios'],
            similares=similares(cx, usuario_id, pendiente, texto))
    except Exception as ex:
        print(f"  gemini no pudo: {ex}")
        return None


if __name__ == '__main__':
    db.inicializar()
    cx = db.conectar()
    cat = catalogo(cx, 1)
    print(f"catalogo: {len(cat['categorias'])} categorias, "
          f"{len(cat['presupuestos'])} presupuestos, "
          f"{len(cat['comercios'])} comercios")
    print(f"IA: {'Gemini disponible' if ia_disponible() else 'sin API key, solo heuristica'}\n")

    fake = {'fecha': '2026-09-01', 'valor': -151495.0, 'moneda': 'COP',
            'contraparte': 'MERCADO PAGO*TIERRAG',
            'descripcion': 'MERCADO PAGO*TIERRAG',
            'cuenta_firefly': 'MASTERCARD BLACK'}
    frases = [
        'fue la comida de la gata en tierragro',
        'le compre granos a la michina',
        'mercado del mes',
        'almorzamos afuera, fue un antojo',
        'gasolina de la moto',
        'esto fue el gym',
    ]
    for f in frases:
        d = interpretar(cx, 1, fake, f, cat=cat)
        print(f"  {f!r}")
        print(f"     -> categoria={d['categoria']!r} presupuesto={d['presupuesto']!r} "
              f"comercio={d['comercio']!r}")
        print(f"        conf={d['confianza']:.2f} fuente={d['fuente']} "
              f"pedir_presupuesto={d['pedir_presupuesto']}")
        print(f"        razon: {d['razon']}")
    cx.close()
