"""Decide a que cuenta, categoria y presupuesto va cada movimiento.

Dos trabajos distintos:

1. **Instrumento -> cuenta de Firefly.** Los 4 digitos de la tarjeta no bastan:
   el banco repone plasticos y el mismo numero cambia de producto en el tiempo.
   Se resuelve con productos.csv y LA FECHA del movimiento.

2. **Comercio -> categoria y presupuesto.** Se aprende del historico que ya esta
   clasificado a mano en Firefly, y crece con cada respuesta por Telegram.
"""

import csv
import os
import re

from finanzas import config as _cfg
from finanzas.adaptadores import db, firefly
from finanzas.adaptadores.almacen import Almacen
from finanzas.aplicacion import presupuestos, taxonomia
from finanzas.dominio import fechas as _fechas
from finanzas.dominio import texto as _texto

# El mapeo tarjeta -> cuenta es tuyo, no del codigo: se monta al lado del
# .env, en la raiz del proyecto.
PRODUCTOS = _cfg.ruta_proyecto('productos.csv')

UMBRAL = 0.72  # bajo esto, se le pregunta al usuario


# ------------------------------------------------------------ normalizacion

# Prefijos de pasarela que Bancolombia pega al nombre real del comercio.
# 'DLO*Didi' y 'DL*DIDI RIDES CO' son el mismo Didi.
# OJO con los espacios: el banco manda 'MERCADO PAGO*ZONAFIT', y si no se quita
# 'MERCADO PAGO' entero, la palabra MERCADO se cuela como si fuera el comercio y
# caza con 'MERCADO LIBRE'. Asi un gym termino clasificado como comida de gato.
_PASARELA = (
    r'DLO|DL|PAYU|PAYULATAM|MERCADO\s?PAGO|MERCADOPAGO|MP|EPAYCO|PSE'
    r'|WOMPI|BOLD|RAPPI|TPAGA|NEQUI'
)
# Como prefijo: 'DLO*Didi'
PASARELAS = re.compile(rf'^({_PASARELA})\s*\*\s*', re.I)
# Y como sufijo, que es igual de comun: 'UBER RIDES*DL'
PASARELAS_FIN = re.compile(rf'\*\s*({_PASARELA})\s*$', re.I)

RUIDO = re.compile(
    r'\b(COL|COLO|CO|BOGOTA|MEDELLIN|SAS|S\.A\.S|SA|LTDA|INC|COM)\b', re.I
)
# Numero de local o sucursal: 'CYCLE GEAR N169', 'DROGUERIA ALEMANA 47'
LOCAL = re.compile(r'\b[A-Z]?\d{1,5}\b')


normalizar = _texto.normalizar


_a_fecha = _fechas.a_fecha


# ------------------------------------------------- instrumento -> cuenta

_PROD = None


def _ruta_productos():
    """productos.csv no esta en el repo: tiene los ultimos digitos de las
    tarjetas. Se busca en tres lugares, en orden:

      1. PRODUCTOS_CSV en el entorno, con el contenido del CSV. Sirve para
         desplegar por Portainer sin montar archivos ni entrar por SSH.
      2. el volumen de datos (por si se monto ahi)
      3. al lado del codigo (que es como funciona en desarrollo)
    """

    inline = _cfg.get('PRODUCTOS_CSV')
    if inline and ',' in inline:
        destino = _cfg.ruta_datos('productos.csv')
        # Se aceptan tres formas de separar las filas, porque la UI de Portainer
        # pone cada variable en UNA linea: ahi un salto de linea real partiria
        # el valor en varias variables distintas.
        #   ';'   lo recomendado, es lo que genera la plantilla
        #   '\n'  la secuencia de dos caracteres, no un salto real
        #   saltos de linea de verdad, para cuando el valor viene de un archivo
        texto = inline.replace('\\n', '\n')
        if '\n' not in texto and ';' in texto:
            texto = '\n'.join(x.strip() for x in texto.split(';') if x.strip())

        # se reescribe solo si cambio, para no tocar disco en cada arranque
        actual = ''
        if os.path.exists(destino):
            with open(destino, encoding='utf-8') as fh:
                actual = fh.read()
        if texto.strip() != actual.strip():
            with open(destino, 'w', encoding='utf-8') as fh:
                fh.write(texto.rstrip('\n') + '\n')
        return destino

    en_datos = _cfg.ruta_datos('productos.csv')
    if os.path.exists(en_datos):
        return en_datos
    return PRODUCTOS


def productos():
    # Cache de modulo: la lista se lee del disco una vez por proceso.
    global _PROD  # noqa: PLW0603
    if _PROD is None:
        _PROD = []
        ruta = _ruta_productos()
        if not os.path.exists(ruta):
            raise RuntimeError(
                f'No encuentro productos.csv.\n'
                f'Buscado en: {ruta}\n'
                f'Copia productos.ejemplo.csv a productos.csv y pon tus datos, '
                f'o define PRODUCTOS_CSV en el entorno con el contenido.'
            )
        with open(ruta, encoding='utf-8') as fh:
            # productos.ejemplo.csv trae comentarios arriba; sin saltarlos,
            # DictReader tomaria el primer '#' como cabecera.
            lineas = [ln for ln in fh if not ln.lstrip().startswith('#')]
        for r in csv.DictReader(lineas):
            if not (r.get('instrumento') or '').strip():
                continue
            _PROD.append(
                {
                    'instrumento': r['instrumento'].strip(),
                    'clase': (r.get('clase') or 'tarjeta').strip(),
                    'cuenta': (r.get('cuenta_firefly') or '').strip(),
                    'desde': _a_fecha(r.get('desde')),
                    'hasta': _a_fecha(r.get('hasta')),
                }
            )
    return _PROD


def cuenta_principal(fecha=None):
    """La cuenta de ahorros. Varias plantillas dicen solo 'en tu cuenta de
    Ahorros' sin los 4 digitos, y sin esto esos ingresos quedaban sin cuenta."""
    f = _a_fecha(fecha)
    for p in productos():
        if p['clase'] != 'cuenta':
            continue
        if f and p['desde'] and f < p['desde']:
            continue
        if f and p['hasta'] and f > p['hasta']:
            continue
        return p['cuenta']
    return None


def cuenta_de_instrumento(instrumento, fecha):
    """Los 4 digitos + la fecha -> nombre de la cuenta de Firefly.

    Devuelve None si no hay match: eso significa plastico nuevo que no esta en
    productos.csv, y el movimiento se va a preguntar por Telegram en vez de
    caer en la cuenta equivocada.
    """
    if not instrumento:
        return None
    f = _a_fecha(fecha)
    candidatos = [p for p in productos() if p['instrumento'] == str(instrumento)]
    if not candidatos:
        return None
    if f:
        for p in candidatos:
            desde_ok = p['desde'] is None or f >= p['desde']
            hasta_ok = p['hasta'] is None or f <= p['hasta']
            if desde_ok and hasta_ok:
                return p['cuenta']
    # Sin fecha, o fuera de todo rango: si todos apuntan a la misma cuenta la
    # respuesta es la misma de todas formas.
    cuentas = {p['cuenta'] for p in candidatos}
    return cuentas.pop() if len(cuentas) == 1 else None


# ---------------------------------------------- comercio -> categoria


def sembrar_desde_firefly(cx, usuario_id):
    """Construye las reglas iniciales con el historico ya clasificado a mano.

    Lo aprendido sale de DOS fuentes, y el orden importa:

    1. **El nombre de la cuenta de gasto/ingreso** (`Grupo Super`, `Cafe Central`,
       `AgendaPro`). Esos SI son nombres de comercio, y son los que se parecen
       a lo que manda el banco. Es la fuente buena.
    2. **La descripcion** (`ALMUERZO SUPER`, `TAXI AEROPUERTO`). Son notas
       escritas a mano sobre *que* fue la compra, no el comercio. Sirven de
       apoyo, pero pesan menos: si mandaran, `ALMUERZO SUPER` nunca cazaria
       con `SUPER NORTE 45`.

    Se lee de la API, no de un CSV local, para que funcione igual dentro del
    contenedor.
    """

    porclave = {}

    def anotar(clave, cat, pres, contraparte, peso, origen, direccion=None):
        if not clave or len(clave) < 3:
            return
        d = porclave.setdefault(
            clave,
            {'cat': {}, 'pres': {}, 'cp': {}, 'peso': 0, 'origen': origen, 'dir': {}},
        )
        if direccion:
            d['dir'][direccion] = d['dir'].get(direccion, 0) + peso
        # si el mismo texto aparece como nombre de comercio y como descripcion,
        # manda comercio: es el que sirve para emparejar por palabras
        if peso > d['peso']:
            d['origen'] = origen
        d['peso'] = max(d['peso'], peso)
        if cat:
            d['cat'][cat] = d['cat'].get(cat, 0) + peso
        if pres:
            d['pres'][pres] = d['pres'].get(pres, 0) + peso
        if contraparte:
            d['cp'][contraparte] = d['cp'].get(contraparte, 0) + peso

    txs = firefly.get_all('/api/v1/transactions')
    n_splits = 0
    for t in txs:
        for s in t.get('attributes', {}).get('transactions', []):
            n_splits += 1
            tipo = (s.get('type') or '').lower()
            cat = (s.get('category_name') or '').strip()
            pres = (s.get('budget_name') or '').strip()
            if tipo == 'withdrawal':
                contraparte = (s.get('destination_name') or '').strip()
                direccion = 'gasto'
            elif tipo == 'deposit':
                contraparte = (s.get('source_name') or '').strip()
                direccion = 'ingreso'
            else:
                continue  # los transfers no ensenan comercios
            if not cat and not contraparte:
                continue
            # fuente 1: el nombre del comercio. peso alto.
            anotar(
                normalizar(contraparte),
                cat,
                pres,
                contraparte,
                3,
                'comercio',
                direccion,
            )
            # fuente 2: la descripcion escrita a mano. peso bajo.
            anotar(
                normalizar(s.get('description')),
                cat,
                pres,
                contraparte,
                1,
                'descripcion',
                direccion,
            )

    def top(d):
        return max(d, key=d.get) if d else None

    n = 0
    for clave, d in porclave.items():
        cat, pres = top(d['cat']), top(d['pres'])
        # no se aprende una categoria que ya se retiro: se deja el comercio para
        # no perderlo, pero la categoria se va a preguntar
        if cat in taxonomia.RETIRADAS:
            cat = None
        elif cat in taxonomia.FUSIONES:
            cat = taxonomia.FUSIONES[cat]
        if not cat and not d['cp']:
            continue
        # Cuantas veces clasificaste ESTO con ESTA categoria, a mano, en
        # Firefly. Un comercio que pusiste 3 veces igual ya lo confirmaste tu:
        # solo que lo confirmaste en Firefly y no en el bot. Sin esto, el modo
        # estricto preguntaba 840 movimientos el primer dia.
        veces = d['cat'].get(cat, 0) if cat else 0
        unanime = cat and len(d['cat']) == 1
        db.regla_guardar(
            cx,
            usuario_id,
            clave,
            cuenta_firefly=top(d['cp']),
            categoria=cat,
            presupuesto=pres,
            origen=d['origen'],
            direccion=top(d['dir']),
            aciertos=(veces if unanime else 0),
        )
        n += 1
    return n, n_splits


# Palabras demasiado comunes en nombres de comercio para que compartirlas
# signifique algo. Sin esto, cualquier 'TIENDA X' cazaba con cualquier
# 'TIENDA Y'.
GENERICAS = {
    'MERCADO',
    'PAGO',
    'PAGOS',
    'TIENDA',
    'SUPER',
    'COMPRA',
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


def _tokens(s):
    # el dominio ya normaliza; aqui llega texto ya normalizado
    return {t for t in (s or '').split() if len(t) > 2}


class Indice:
    """Las reglas en memoria, en DOS universos separados.

    `comercio` son nombres de comercio de verdad ('Grupo Super', 'Farmacia Central'),
    que vienen de las cuentas de gasto e ingreso de Firefly. Son pocos y
    limpios, asi que ahi si se puede emparejar por palabras.

    `descripcion` son notas escritas a mano ('ALMUERZO SUPER', 'MERCADO SUPER').
    Son muchas y ruidosas: emparejar por palabras ahi haria que cualquier
    'ALMUERZO ...' cazara con cualquier otro. Solo se usan para coincidencia
    exacta o por subcadena.

    Mezclarlos fue lo que dejo el acierto en 54%: 'SUPER NORTE 45' competia
    contra diez patrones con la palabra SUPER y ninguno era el comercio.
    """

    def __init__(self, cx, usuario_id):
        filas = Almacen(cx).reglas(usuario_id)

        self.exacto = {}
        for r in filas:
            prev = self.exacto.get(r['patron'])
            # empata: gana comercio, y luego el de mas aciertos
            if (
                prev is None
                or (r['origen'] == 'comercio' and prev['origen'] != 'comercio')
                or (
                    r['origen'] == prev['origen']
                    and (r['aciertos'] or 0) > (prev['aciertos'] or 0)
                )
            ):
                self.exacto[r['patron']] = r

        es_comercio = [
            r for r in filas if r['origen'] in ('comercio', 'usuario', 'manual')
        ]
        es_desc = [r for r in filas if r['origen'] == 'descripcion']
        # el mas largo primero: 'UBER RIDES' debe ganarle a 'UBER'
        self.comercio = sorted(es_comercio, key=lambda r: -len(r['patron']))
        self.desc = sorted(es_desc, key=lambda r: -len(r['patron']))

        # en cuantos nombres de comercio distintos aparece cada palabra. Sirve
        # para saber si compartir esa palabra dice algo: SUPER aparece en 1 solo
        # comercio, asi que compartirla es senal fuerte.
        self.doc_freq = {}
        for r in self.comercio:
            for t in _tokens(r['patron']):
                self.doc_freq[t] = self.doc_freq.get(t, 0) + 1

    def _por_token(self, clave):
        """La mejor regla de comercio que comparta una palabra distintiva."""
        tk = _tokens(clave)
        if not tk:
            return None, 0.0
        mejor, mejor_s = None, 0.0
        for r in self.comercio:
            tp = _tokens(r['patron'])
            comunes = tk & tp
            if not comunes:
                continue
            # una palabra que aparece en muchos comercios no distingue nada
            distintivas = [
                t
                for t in comunes
                if len(t) >= 4 and t not in GENERICAS and self.doc_freq.get(t, 99) <= 3
            ]
            if not distintivas:
                continue
            # coeficiente de solape, no Jaccard: 'SUPER NORTE 45' vs 'GRUPO
            # SUPER' da 0.5 con Jaccard y se cae, pero 1/min(2,2) = 0.5 ...
            # lo que decide es que la palabra compartida sea distintiva.
            s = len(comunes) / min(len(tk), len(tp))
            if s > mejor_s:
                mejor, mejor_s = r, s
        if mejor is not None and mejor_s >= 0.5:
            return mejor, 0.80
        return None, 0.0

    def buscar(self, clave):
        """Devuelve (regla, confianza) o (None, 0), de mas fuerte a mas debil."""
        if not clave:
            return None, 0.0

        # 1. identico
        r = self.exacto.get(clave)
        if r is not None:
            return r, 0.92

        # 2. el nombre del comercio esta dentro de lo que dijo el banco:
        #    'FARMACIA' dentro de 'FARMACIA NORTE S'
        for r in self.comercio:
            p = r['patron']
            if len(p) >= 4 and p in clave:
                return r, 0.88

        # 3. al contrario: 'DIDI' dentro de 'DIDI RIDES'
        for r in self.comercio:
            if len(clave) >= 4 and clave in r['patron']:
                return r, 0.84

        # 4. comparten una palabra distintiva: 'SUPER NORTE 45' con 'GRUPO SUPER'
        r, conf = self._por_token(clave)
        if r is not None:
            return r, conf

        # 5. ya sin comercios: las descripciones, solo por subcadena y con
        #    confianza baja a proposito.
        for r in self.desc:
            p = r['patron']
            if len(p) >= 6 and (p in clave or clave in p):
                return r, 0.73

        return None, 0.0


def clasificar(cx, usuario_id, evento, indice=None):
    """evento: dict con tipo, fecha, instrumento, contraparte, descripcion...

    Devuelve dict con cuenta_firefly, cuenta_destino, categoria, presupuesto,
    confianza, decidido_por, pregunta.
    """
    fecha = evento.get('fecha')
    r = {
        'cuenta_firefly': cuenta_de_instrumento(evento.get('instrumento'), fecha),
        'cuenta_destino': None,
        'categoria': None,
        'presupuesto': None,
        'confianza': 0.0,
        'decidido_por': None,
        'pregunta': None,
        'etiquetas': None,
    }

    # Traslado entre productos propios: los dos extremos son cuentas mias y no
    # hay categoria que adivinar.
    if evento.get('traslado_a'):
        r['cuenta_destino'] = cuenta_de_instrumento(evento['traslado_a'], fecha)
        if evento.get('tipo') == 'pago_tarjeta':
            # sale de la cuenta de ahorros y abona la tarjeta
            r['cuenta_firefly'] = r['cuenta_firefly'] or cuenta_principal(fecha)
        if r['cuenta_firefly'] and r['cuenta_destino']:
            r['confianza'] = 0.95
            r['decidido_por'] = 'traslado'
            return r
        r['pregunta'] = 'categoria'
        return r

    if not r['cuenta_firefly'] and evento.get('clase_instrumento') == 'cuenta':
        # 'en tu cuenta de Ahorros', sin los 4 digitos
        r['cuenta_firefly'] = cuenta_principal(fecha)

    if not r['cuenta_firefly']:
        # plastico desconocido: preguntar antes que adivinar
        r['pregunta'] = 'categoria'
        return r

    idx = indice if indice is not None else Indice(cx, usuario_id)
    clave = normalizar(evento.get('contraparte') or evento.get('descripcion'))
    regla, conf = idx.buscar(clave)
    if regla is not None:
        # Una regla vieja puede apuntar a una categoria que ya se retiro por ser
        # un atributo ('Viaticos') o que se fusiono. Se resuelve aqui para que
        # el historico no siga reinyectando la taxonomia vieja.
        cat_final, etiqueta, preguntar = taxonomia.resolver(regla['categoria'])
        r['cuenta_destino'] = regla['cuenta_firefly']
        r['categoria'] = cat_final
        # El presupuesto sale de tres sitios, en este orden:
        #   1. la lista fija de taxonomia (hoy solo Suplementos -> Vivir)
        #   2. lo que traiga la regla aprendida
        #   3. el mapa categoria -> presupuesto del historico de Firefly
        #
        # El tercero faltaba, y por eso un gasto con categoria Mercado —que en
        # el historico apunta a Esencial 49 de 49 veces— entraba a Firefly SIN
        # presupuesto y habia que ponerselo a mano. El mapa esta cacheado por
        # proceso: si no, cada movimiento releeria todo el historico.
        # Lo que el usuario haya dicho gana sobre la regla aprendida: si dijo
        # «Compras va en Antojos», eso manda aunque el historico este repartido.
        r['presupuesto'] = (
            presupuestos.presupuesto_de_categoria(cat_final, cx) or regla['presupuesto']
        )
        r['etiquetas'] = etiqueta
        r['confianza'] = 0.4 if preguntar else conf
        r['decidido_por'] = 'historico' if regla['origen'] == 'historico' else 'regla'
        if preguntar:
            r['pregunta'] = 'categoria'
            return r
    else:
        r['cuenta_destino'] = (evento.get('contraparte') or '').strip() or None
        r['confianza'] = 0.3
        r['decidido_por'] = 'sin_regla'

    r['pregunta'] = None if _es_seguro(regla, conf, clave) else 'categoria'
    return r


# Modo estricto: solo se publica sin preguntar cuando la certeza viene de una
# respuesta del propio usuario, no de una heuristica. Al principio pregunta
# harto, pero cada comercio se pregunta UNA vez y despues baja mucho.
def _estricto():
    v = str(_cfg.get('CLASIFICADOR_ESTRICTO', 'si')).lower()
    return v in ('1', 'si', 'sí', 'yes', 'true')


def _es_seguro(regla, conf, clave):
    """¿Se puede publicar esto sin preguntar?"""
    if regla is None or not regla['categoria']:
        return False
    if not _estricto():
        return conf >= UMBRAL
    # el patron tiene que coincidir EXACTO, no por subcadena ni por palabras
    if regla['patron'] != clave:
        return False
    # y la regla tiene que venir de una respuesta suya, o de una del historico
    # que ya se haya usado varias veces sin que la corrigiera
    if regla['origen'] in ('usuario', 'manual'):
        return True
    return (regla['aciertos'] or 0) >= 3
