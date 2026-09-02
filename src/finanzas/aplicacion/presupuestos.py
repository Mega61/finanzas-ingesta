"""Estado de los presupuestos, y la relacion categoria -> presupuesto.

Tres cosas:

1. **Que presupuesto le toca a una categoria.** Se aprende del historico, pero
   solo mirando los presupuestos ACTIVOS. Eso importa: en junio de 2026 la
   estructura cambio de presupuestos por concepto (Arriendo, Mercado, Factura
   EPM) a bloques (Esencial, Vivir, Antojos...). Contando los retirados, el
   66% de las categorias se veian ambiguas; filtrando a los activos, la
   ambiguedad que queda es real y hay que preguntarla.

2. **Como va cada presupuesto este mes.** Cuanto se gasto, cuanto queda.

3. **Si un movimiento lo revienta.** Para avisar en el momento y no a fin de mes.
"""

import contextlib
import time

from finanzas.adaptadores import firefly
from finanzas.adaptadores.almacen import Almacen
from finanzas.aplicacion import taxonomia
from finanzas.dominio import dinero as _dinero
from finanzas.dominio import fechas as _fechas

# Debajo de esto no se considera que la categoria decida el presupuesto sola.
UMBRAL_DETERMINISTICO = 0.8


_fin_de_mes = _fechas.fin_de_mes


def activos():
    """[{'id','nombre'}] de los presupuestos activos."""
    salida = []
    for b in firefly.get_all('/api/v1/budgets'):
        a = b['attributes']
        if a.get('active'):
            salida.append({'id': b['id'], 'nombre': a['name']})
    return salida


def nombres_activos():
    return [b['nombre'] for b in activos()]


def estado(cuando=None):
    """Como va cada presupuesto activo en el mes de `cuando`.

    Devuelve [{'nombre','limite','gastado','queda','pct'}]. `limite` es None si
    no hay tope puesto para el periodo: ahi no se puede hablar de reventar.
    """
    hoy = cuando or _fechas.hoy()
    ini, fin = hoy.replace(day=1), _fin_de_mes(hoy)
    ruta = f'/api/v1/budgets?start={ini}&end={fin}'

    limites = {}
    try:
        for L in firefly.get_all(f'/api/v1/budget-limits?start={ini}&end={fin}'):
            a = L['attributes']
            bid = str(a.get('budget_id'))
            with contextlib.suppress(TypeError, ValueError):
                limites[bid] = limites.get(bid, 0.0) + abs(float(a.get('amount') or 0))
    except firefly.ApiError:
        pass

    salida = []
    for b in firefly.get_all(ruta):
        a = b['attributes']
        if not a.get('active'):
            continue
        gastado = 0.0
        for s in a.get('spent') or []:
            with contextlib.suppress(TypeError, ValueError):
                gastado += abs(float(s.get('sum') or 0))
        lim = limites.get(str(b['id']))
        salida.append(
            {
                'id': b['id'],
                'nombre': a['name'],
                'limite': lim,
                'gastado': gastado,
                'queda': (lim - gastado) if lim else None,
                'pct': (gastado / lim * 100) if lim else None,
            }
        )
    return sorted(salida, key=lambda x: -(x['pct'] or 0))


# El mapa se calcula leyendo TODOS los gastos de Firefly, asi que no puede
# recalcularse por cada movimiento que se clasifica. Se guarda por proceso, con
# caducidad: si cambias la relacion categoria -> presupuesto en Firefly, entra
# sola en la siguiente media hora sin reiniciar el contenedor.
MINUTOS_DE_CACHE = 30
_cache_mapa: dict[tuple, tuple[float, dict]] = {}


def olvidar_cache():
    """Para las pruebas y para forzar una relectura."""
    _cache_mapa.clear()


def presupuesto_de_categoria(categoria, cx=None, mapa=None):
    """El presupuesto que le toca a una categoria, en orden de autoridad:

      1. Lo que TU dijiste (tabla presupuesto_por_categoria). Es una decision.
      2. La lista fija del codigo (taxonomia).
      3. El historico, solo si decide solo (80% o mas).

    El primero faltaba, y por eso las categorias de verdad repartidas se
    quedaban sin presupuesto para siempre: 'Compras' esta 7 a 2 entre Antojos e
    Imprevistos (78%, justo por debajo del umbral) y 'Regalos' esta 4 a 4. Sin
    forma de zanjarlo, cada compra de esas entraba a Firefly sin presupuesto y
    habia que ponerselo a mano.
    """
    if not categoria:
        return None
    if cx is not None:
        fijado = Almacen(cx).presupuesto_fijado(categoria)
        if fijado:
            return fijado
    fijo = taxonomia.presupuesto_de(categoria)
    if fijo:
        return fijo
    return presupuesto_seguro(categoria, mapa)


def presupuesto_seguro(categoria, mapa=None):
    """El presupuesto de esa categoria, SOLO si decide sola.

    Devuelve None cuando el historico esta repartido: ahi es un juicio de verdad
    ('Restaurante' entre Vivir y Antojos) y hay que preguntarlo, no adivinarlo.

    Existe porque el clasificador no consultaba este mapa: sacaba el presupuesto
    de una lista escrita a mano con una sola entrada y de lo que trajera la
    regla aprendida. Un movimiento con categoria Mercado —que en el historico
    apunta a Esencial 49 de 49 veces— entraba a Firefly SIN presupuesto, y habia
    que ponerselo a mano.
    """
    if not categoria:
        return None
    info = (mapa if mapa is not None else mapa_categoria()).get(categoria)
    if not info or not info['seguro']:
        return None
    return info['presupuesto']


def mapa_categoria(solo_activos=True, desde=None, usar_cache=True):
    """{categoria: {'presupuesto', 'seguro', 'reparto'}}.

    `seguro` dice si esa categoria apunta siempre al mismo presupuesto. Cuando
    es False hay que preguntar: son juicios de verdad, como si una comida en
    restaurante fue 'Vivir' o 'Antojos'.
    """
    llave = (solo_activos, desde)
    if usar_cache and llave in _cache_mapa:
        cuando, guardado = _cache_mapa[llave]
        if time.time() - cuando < MINUTOS_DE_CACHE * 60:
            return guardado

    act = set(nombres_activos()) if solo_activos else None
    cuenta = {}
    for t in firefly.get_all('/api/v1/transactions?type=withdrawal'):
        for s in t['attributes']['transactions']:
            c = (s.get('category_name') or '').strip()
            p = (s.get('budget_name') or '').strip()
            if not c or not p:
                continue
            if act is not None and p not in act:
                continue
            if desde and s.get('date', '')[:10] < desde:
                continue
            cuenta.setdefault(c, {})
            cuenta[c][p] = cuenta[c].get(p, 0) + 1

    mapa = {}
    for c, reparto in cuenta.items():
        total = sum(reparto.values())
        top = max(reparto, key=reparto.get)
        mapa[c] = {
            'presupuesto': top,
            'seguro': (reparto[top] / total) >= UMBRAL_DETERMINISTICO,
            'reparto': reparto,
        }
    _cache_mapa[llave] = (time.time(), mapa)
    return mapa


def revienta(nombre_presupuesto, monto, est=None):
    """Si sumar `monto` al presupuesto lo pasa del 100%.

    Devuelve None si no aplica (sin tope, o no lo pasa), o un dict con el antes
    y el despues para poder avisar con numeros.
    """
    est = est if est is not None else estado()
    for b in est:
        if b['nombre'] != nombre_presupuesto:
            continue
        if not b['limite']:
            return None
        antes = b['gastado']
        despues = antes + abs(float(monto))
        if despues <= b['limite']:
            return None
        return {
            'nombre': b['nombre'],
            'limite': b['limite'],
            'antes': antes,
            'despues': despues,
            'exceso': despues - b['limite'],
            'pct': despues / b['limite'] * 100,
        }
    return None


def _plata(v):
    return _dinero.formatear(v)


def formatear(est=None):
    """Tabla de texto para el resumen diario de Telegram."""
    est = est if est is not None else estado()
    if not est:
        return 'No hay presupuestos activos.'
    lineas = []
    for b in est:
        if b['limite']:
            barra_llena = int(min(b['pct'], 100) // 10)
            barra = '█' * barra_llena + '░' * (10 - barra_llena)
            alerta = ' ⚠️' if b['pct'] >= 100 else (' ⚡' if b['pct'] >= 85 else '')
            lineas.append(
                f'<code>{barra}</code> {b["pct"]:>3.0f}% <b>{b["nombre"]}</b>{alerta}'
            )
            lineas.append(
                f'      {_plata(b["gastado"])} de {_plata(b["limite"])}'
                f' · queda {_plata(b["queda"])}'
            )
        else:
            lineas.append(
                f'<b>{b["nombre"]}</b>: {_plata(b["gastado"])} <i>(sin tope puesto)</i>'
            )
    return '\n'.join(lineas)
