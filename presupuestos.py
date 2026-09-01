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
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import firefly

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
    hoy = cuando or date.today()
    ini, fin = hoy.replace(day=1), _fin_de_mes(hoy)
    ruta = f"/api/v1/budgets?start={ini}&end={fin}"

    limites = {}
    try:
        for L in firefly.get_all(f"/api/v1/budget-limits?start={ini}&end={fin}"):
            a = L['attributes']
            bid = str(a.get('budget_id'))
            try:
                limites[bid] = limites.get(bid, 0.0) + abs(float(a.get('amount') or 0))
            except (TypeError, ValueError):
                pass
    except firefly.ApiError:
        pass

    salida = []
    for b in firefly.get_all(ruta):
        a = b['attributes']
        if not a.get('active'):
            continue
        gastado = 0.0
        for s in (a.get('spent') or []):
            try:
                gastado += abs(float(s.get('sum') or 0))
            except (TypeError, ValueError):
                pass
        lim = limites.get(str(b['id']))
        salida.append({
            'id': b['id'],
            'nombre': a['name'],
            'limite': lim,
            'gastado': gastado,
            'queda': (lim - gastado) if lim else None,
            'pct': (gastado / lim * 100) if lim else None,
        })
    return sorted(salida, key=lambda x: -(x['pct'] or 0))


def mapa_categoria(solo_activos=True, desde=None):
    """{categoria: {'presupuesto', 'seguro', 'reparto'}}.

    `seguro` dice si esa categoria apunta siempre al mismo presupuesto. Cuando
    es False hay que preguntar: son juicios de verdad, como si una comida en
    restaurante fue 'Vivir' o 'Antojos'.
    """
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
        return "No hay presupuestos activos."
    lineas = []
    for b in est:
        if b['limite']:
            barra_llena = int(min(b['pct'], 100) // 10)
            barra = '█' * barra_llena + '░' * (10 - barra_llena)
            alerta = ' ⚠️' if b['pct'] >= 100 else (' ⚡' if b['pct'] >= 85 else '')
            lineas.append(f"<code>{barra}</code> {b['pct']:>3.0f}% "
                          f"<b>{b['nombre']}</b>{alerta}")
            lineas.append(f"      {_plata(b['gastado'])} de {_plata(b['limite'])}"
                          f" · queda {_plata(b['queda'])}")
        else:
            lineas.append(f"<b>{b['nombre']}</b>: {_plata(b['gastado'])} "
                          f"<i>(sin tope puesto)</i>")
    return '\n'.join(lineas)


if __name__ == '__main__':
    print("=== presupuestos activos, este mes ===")
    est = estado()
    for b in est:
        lim = _plata(b['limite']) if b['limite'] else 'sin tope'
        pct = f"{b['pct']:.0f}%" if b['pct'] is not None else '-'
        print(f"  {b['nombre']:24} gastado={_plata(b['gastado']):>14} "
              f"de {lim:>14}  {pct}")

    print("\n=== categoria -> presupuesto ===")
    mapa = mapa_categoria()
    seguras = [c for c, d in mapa.items() if d['seguro']]
    dudosas = [c for c, d in mapa.items() if not d['seguro']]
    print(f"  {len(seguras)} categorias deciden solas")
    print(f"  {len(dudosas)} hay que preguntarlas:")
    for c in sorted(dudosas):
        print(f"    {c:28} {mapa[c]['reparto']}")
