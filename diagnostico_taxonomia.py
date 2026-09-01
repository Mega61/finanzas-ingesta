"""Diagnostico de categorias y etiquetas. NO cambia nada.

    python diagnostico_taxonomia.py            # tabla en consola
    python diagnostico_taxonomia.py --json     # para generar el informe

Saca, por cada categoria: cuantas veces se uso, cuanta plata movio, en que
presupuestos cayo, desde y hasta cuando se uso, y una propuesta de fusion
cuando la categoria es de cola larga.

Las propuestas son sugerencias con criterio explicito, no ordenes. La idea es
que el usuario apruebe o corrija cada una antes de tocar el libro.
"""
import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import firefly

from finanzas.dominio import dinero as _dinero

# Debajo de este uso se considera cola larga y se propone fusion.
COLA_LARGA = 5

# Fusiones propuestas: categoria -> a donde. Solo donde el padre semantico es
# claro. Lo que no esta aqui se deja quieto aunque tenga poco uso.
FUSIONES = {
    'Comida de calle': 'Restaurante',
    'Desayuno': 'Restaurante',
    'Cafe': 'Mecato',
    'Café': 'Mecato',
    'Suplementos': 'Mecato',
    'Mecato Gym': 'Mecato',
    'Medicamentos': 'Salud',
    'Partido Futbol': 'Salidas',
    'Concierto': 'Salidas',
    'Juegos': 'Salidas',
    'Apuesta': 'Salidas',
    'Compras Viaje': 'Viajes',
    'Articulos Personales': 'Compras Presentacion Personal',
    'Transporte Privado': 'Transporte Aplicacion',
    'Compras de utileria': 'Compras Casa',
    'Homelab': 'Compras Tecnologia',
    'Intereses': 'Intereses TC',
    'Intereses Compra de cartera': 'Intereses TC',
    'Reposicion TC': 'Cuotas de manejo',
    'Reposición TC': 'Cuotas de manejo',
    'TCO': 'Cuotas de manejo',
}

# Categorias que NO son gasto de consumo: son mecanica contable. Se dejan
# aparte para que no ensucien los reportes de gasto.
CONTABLES = {'Abono', 'Ajuste de cuentas', 'Reconciliacion', 'Reconciliación',
             'Transferencia', 'Avance', 'Inversion', 'Inversión', 'Prestamos',
             'Perdida', 'Merma', 'Emergencia'}

# Los tags que SI aportan algo que ningun otro campo carga.
TAGS_UTILES_PREFIJOS = ('viaje-', 'recon-', 'proyecto-', 'reembolso')
TAGS_UTILES_EXACTOS = {'reembolsable', 'sin-confirmar', 'ingesta-automatica'}


def _norm(s):
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFD', s or '')
                   if unicodedata.category(c) != 'Mn').upper()


def recolectar():
    cats_existentes = {c['attributes']['name']
                       for c in firefly.get_all('/api/v1/categories')}
    activos = {b['attributes']['name'] for b in firefly.get_all('/api/v1/budgets')
               if b['attributes'].get('active')}

    uso = collections.Counter()
    monto = collections.Counter()
    presup = collections.defaultdict(collections.Counter)
    primera, ultima = {}, {}
    tags = collections.Counter()
    tag_cats = collections.defaultdict(collections.Counter)
    tag_cuentas = collections.defaultdict(collections.Counter)

    for t in firefly.get_all('/api/v1/transactions'):
        for s in t['attributes']['transactions']:
            tipo = (s.get('type') or '').lower()
            fecha = (s.get('date') or '')[:10]
            c = (s.get('category_name') or '').strip()
            if tipo == 'withdrawal' and c:
                uso[c] += 1
                try:
                    monto[c] += abs(float(s.get('amount') or 0))
                except (TypeError, ValueError):
                    pass
                b = (s.get('budget_name') or '').strip()
                if b:
                    presup[c][b] += 1
                if c not in primera or fecha < primera[c]:
                    primera[c] = fecha
                if c not in ultima or fecha > ultima[c]:
                    ultima[c] = fecha
            for tg in (s.get('tags') or []):
                tags[tg] += 1
                if c:
                    tag_cats[tg][c] += 1
                # En un gasto el comercio es el DESTINO y la tarjeta el
                # ORIGEN. Mirando solo uno, las etiquetas que duplican el
                # comercio no se detectaban.
                for lado in ('source_name', 'destination_name'):
                    if s.get(lado):
                        tag_cuentas[tg][s[lado]] += 1

    return {
        'existentes': cats_existentes, 'activos': activos, 'uso': uso,
        'monto': monto, 'presup': presup, 'primera': primera, 'ultima': ultima,
        'tags': tags, 'tag_cats': tag_cats, 'tag_cuentas': tag_cuentas,
    }


def clasificar_tag(tg, d):
    """Por que existe este tag, y si aporta algo."""
    t = _norm(tg)
    if tg in TAGS_UTILES_EXACTOS or any(tg.startswith(p) for p in TAGS_UTILES_PREFIJOS):
        return 'util', 'no lo carga ningun otro campo'
    # ¿duplica la cuenta de origen? (tarjeta)
    cuentas = d['tag_cuentas'].get(tg) or {}
    for cta in cuentas:
        if _norm(tg) in _norm(cta) or _norm(cta) in _norm(tg):
            return 'duplica_cuenta', f"ya esta en la cuenta «{cta}»"
    # ¿duplica la categoria?
    cats = d['tag_cats'].get(tg) or {}
    for c in cats:
        if _norm(tg) == _norm(c):
            return 'duplica_categoria', f"ya esta en la categoria «{c}»"
    # ¿esta describiendo QUE fue la compra? Eso es trabajo de la categoria.
    # Si el tag aparece casi siempre con la misma categoria, no aporta nada:
    # es una subdivision de esa categoria.
    if cats:
        total = sum(cats.values())
        top = max(cats, key=cats.get)
        if total >= 4 and cats[top] / total >= 0.7:
            return 'subcategoria', (f"casi siempre cae en «{top}»: es una "
                                    f"subdivision de esa categoria")
    if d['tags'][tg] <= 2:
        return 'residual', f"usado solo {d['tags'][tg]} vez/veces"
    return 'revisar', 'no es obvio, decidilo tu'


def informe(d):
    filas = []
    for c in sorted(d['existentes']):
        n = d['uso'].get(c, 0)
        destino = FUSIONES.get(c)
        if n == 0:
            accion, por_que = 'borrar', 'nunca se uso'
        elif c in CONTABLES:
            accion, por_que = 'dejar', 'no es gasto de consumo, es mecanica contable'
        elif destino:
            accion = 'fusionar'
            por_que = f"se solapa con «{destino}»"
            if n >= COLA_LARGA * 3:
                accion, por_que = 'revisar', (f"propondria fusionar en «{destino}», "
                                              f"pero tiene {n} usos: decidilo tu")
        elif n < COLA_LARGA:
            accion, por_que = 'revisar', f"solo {n} usos y no le veo padre claro"
        else:
            accion, por_que = 'dejar', 'se usa de verdad'
        reparto = d['presup'].get(c) or {}
        filas.append({
            'categoria': c, 'usos': n, 'monto': d['monto'].get(c, 0.0),
            'desde': d['primera'].get(c, ''), 'hasta': d['ultima'].get(c, ''),
            'presupuestos': {k: v for k, v in reparto.items() if k in d['activos']},
            'accion': accion, 'destino': destino, 'por_que': por_que,
        })
    filas.sort(key=lambda f: (-f['usos'], f['categoria']))

    tfilas = []
    for tg, n in d['tags'].most_common():
        clase, por_que = clasificar_tag(tg, d)
        tfilas.append({'tag': tg, 'usos': n, 'clase': clase, 'por_que': por_que})
    return {'categorias': filas, 'tags': tfilas}


def _plata(v):
    return _dinero.formatear(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    d = recolectar()
    inf = informe(d)

    if a.json:
        print(json.dumps(inf, ensure_ascii=False, indent=1))
        return 0

    print("=" * 78)
    print("CATEGORIAS")
    print("=" * 78)
    for grupo in ('borrar', 'fusionar', 'revisar', 'dejar'):
        gs = [f for f in inf['categorias'] if f['accion'] == grupo]
        if not gs:
            continue
        print(f"\n--- {grupo.upper()} ({len(gs)}) ---")
        for f in gs:
            dest = f" -> {f['destino']}" if f['destino'] else ''
            print(f"  {f['usos']:4}  {_plata(f['monto']):>16}  {f['categoria']}{dest}")
            if grupo != 'dejar':
                print(f"        {f['por_que']}")

    print("\n" + "=" * 78)
    print("ETIQUETAS")
    print("=" * 78)
    por_clase = collections.defaultdict(list)
    for t in inf['tags']:
        por_clase[t['clase']].append(t)
    orden = ['util', 'duplica_cuenta', 'duplica_categoria', 'subcategoria',
             'revisar', 'residual']
    for cl in orden:
        ts = por_clase.get(cl) or []
        if not ts:
            continue
        total = sum(t['usos'] for t in ts)
        print(f"\n--- {cl} ({len(ts)} etiquetas, {total} usos) ---")
        for t in ts[:18]:
            print(f"  {t['usos']:4}  {t['tag']:28} {t['por_que']}")
        if len(ts) > 18:
            print(f"  ... y {len(ts)-18} mas")
    return 0


if __name__ == '__main__':
    sys.exit(main())
