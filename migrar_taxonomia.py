"""Convierte categorias que en realidad son atributos en etiquetas.

    python migrar_taxonomia.py                 # SECO, muestra que haria
    python migrar_taxonomia.py --en-serio      # lo aplica

La regla: **categoria = QUE compraste, etiqueta = POR QUE o PARA QUIEN.**

`Viaticos` no dice que compraste, dice que lo pago el trabajo. Un viatico puede
ser un restaurante, un taxi o un hotel. Como categoria, esconde en que se
gastaron 23 millones; como etiqueta, deja ver el que Y el por que a la vez.

Solo toca el ultimo mes a proposito. Reescribir anos de historia no vale la
pena: lo que importa es que de aqui en adelante quede bien, y mover el pasado
rompe los reportes que ya existen.
"""
import argparse
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import firefly

# Cuanto hacia atras se toca. Mas viejo que esto se deja quieto.
DIAS = 31

# categoria -> (etiqueta, categoria_nueva_o_None)
#
# Si la categoria nueva es None, hay que decidirla mirando el comercio: no se
# puede adivinar en que se gasto un viatico sin ver donde fue.
A_ETIQUETA = {
    'Viáticos': ('viatico', None),
    'Viaticos': ('viatico', None),
    'Gastos de trabajo': ('trabajo', None),
    'Compras Viaje': ('viaje', None),
    'Compras': ('compra-suelta', None),
    'Compras Presentación Personal': ('presentacion-personal', None),
    'Compras Presentacion Personal': ('presentacion-personal', None),
    'Regalos': ('regalo', None),
}

# Cuando no se puede deducir la categoria nueva, se intenta por el comercio.
# Es un mapa corto y explicito: lo que no cae aqui se pregunta.
POR_COMERCIO = {
    'UBER': 'Transporte Aplicación', 'DIDI': 'Transporte Aplicación',
    'TAXI': 'Transporte Aplicación',
    'EXITO': 'Mercado', 'D1': 'Mercado', 'ARA': 'Mercado', 'CARULLA': 'Mercado',
    'FARMATODO': 'Cuidado personal', 'DROGUERIA': 'Salud',
    'RAPPI': 'Domicilio',
    'AVIANCA': 'Viajes', 'LATAM': 'Viajes', 'HOTEL': 'Viajes',
    'STARBUCKS': 'Mecato', 'JUAN VALDEZ': 'Mecato',
    'AMAZON': 'Compras Tecnología', 'STEAM': 'Juegos',
}

# Fusiones simples: categoria -> categoria. Sin cambio semantico, solo juntar
# lo que estaba partido.
FUSIONES = {
    'Comida de calle': 'Restaurante',
    'Desayuno': 'Restaurante',
    'Café': 'Mecato',
    'Cafe': 'Mecato',
    'Mecato Gym': 'Mecato',
    'Medicamentos': 'Salud',
    'Homelab': 'Compras Tecnología',
    'Intereses': 'Intereses TC',
    'Intereses Compra de cartera': 'Intereses TC',
    'Reposición TC': 'Cuotas de manejo',
    'TCO': 'Cuotas de manejo',
    'Compras de utileria': 'Compras Casa',
    'Articulos Personales': 'Ropa',
    'Transporte Privado': 'Transporte Aplicación',
    'Compras Viaje': 'Viajes',
}

# Categorias que se quedan tal cual, aunque tengan poco uso. El usuario las
# confirmo: Juegos son juegos de Steam, Suplementos es una compra recurrente
# que no es mecato ni salud.
INTOCABLES = {'Juegos', 'Suplementos', 'Tatuaje', 'GBS Infra', 'Salud',
              'Declaración de Renta', 'Viajes'}

# Presupuesto por defecto de categorias que el usuario definio a mano.
PRESUPUESTO_FIJO = {
    'Suplementos': 'Vivir',
}


def _norm(s):
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFD', s or '')
                   if unicodedata.category(c) != 'Mn').upper()


def categoria_por_comercio(comercio):
    cn = _norm(comercio)
    for clave, cat in POR_COMERCIO.items():
        if clave in cn:
            return cat
    return None


def planear(desde):
    """Que se haria con cada transaccion. No toca nada."""
    plan = []
    for t in firefly.get_all(f'/api/v1/transactions?start={desde}'):
        for s in t['attributes']['transactions']:
            if (s.get('type') or '').lower() != 'withdrawal':
                continue
            cat = (s.get('category_name') or '').strip()
            if not cat or cat in INTOCABLES:
                continue
            comercio = (s.get('destination_name') or '').strip()
            tags = list(s.get('tags') or [])
            cambios = {}
            notas = []

            if cat in A_ETIQUETA:
                etq, nueva = A_ETIQUETA[cat]
                if etq not in tags:
                    tags = tags + [etq]
                    cambios['tags'] = tags
                nueva = nueva or categoria_por_comercio(comercio)
                if nueva:
                    cambios['category_name'] = nueva
                    notas.append(f"categoria {cat} -> etiqueta «{etq}» + «{nueva}»")
                else:
                    # no se puede adivinar: se pone la etiqueta y se deja la
                    # categoria, para preguntarla despues
                    notas.append(f"etiqueta «{etq}» puesta, pero la categoria "
                                 f"real hay que preguntarla (comercio: {comercio})")
            elif cat in FUSIONES:
                cambios['category_name'] = FUSIONES[cat]
                notas.append(f"fusion {cat} -> {FUSIONES[cat]}")

            destino_cat = cambios.get('category_name', cat)
            fijo = PRESUPUESTO_FIJO.get(destino_cat)
            if fijo and (s.get('budget_name') or '').strip() != fijo:
                cambios['budget_name'] = fijo
                notas.append(f"presupuesto -> {fijo}")

            if cambios:
                plan.append({
                    'id': t['id'], 'fecha': s['date'][:10],
                    'monto': abs(float(s.get('amount') or 0)),
                    'desc': s.get('description') or '', 'comercio': comercio,
                    'cat_vieja': cat, 'cambios': cambios, 'notas': notas,
                })
    return plan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--en-serio', action='store_true')
    ap.add_argument('--dias', type=int, default=DIAS)
    a = ap.parse_args()

    desde = (date.today() - timedelta(days=a.dias)).isoformat()
    print(f"ventana: desde {desde} (ultimos {a.dias} dias)")
    print("nada mas viejo se toca\n")

    plan = planear(desde)
    if not plan:
        print("no hay nada que cambiar en esa ventana")
        return 0

    sin_resolver = [p for p in plan if 'category_name' not in p['cambios']
                    and p['cat_vieja'] in A_ETIQUETA]
    print(f"{len(plan)} transacciones a tocar"
          + ("" if a.en_serio else "   [SECO, no escribe]") + "\n")
    for p in plan:
        print(f"  id={p['id']:5} {p['fecha']} ${p['monto']:>11,.0f}  "
              f"{p['desc'][:30]:32} [{p['comercio'][:18]}]")
        for n in p['notas']:
            print(f"        {n}")

    if sin_resolver:
        print(f"\n{len(sin_resolver)} quedan con la etiqueta puesta pero sin "
              f"categoria real resuelta. El bot las va a preguntar.")

    if not a.en_serio:
        print("\ncorre con --en-serio para aplicarlo")
        return 0

    ok = mal = 0
    for p in plan:
        try:
            firefly.actualizar_split(p['id'], **p['cambios'])
            ok += 1
        except firefly.ApiError as ex:
            print(f"  MAL id={p['id']}: {str(ex)[:160]}")
            mal += 1
    print(f"\naplicado: {ok} ok, {mal} con error")
    return 0 if not mal else 1


if __name__ == '__main__':
    sys.exit(main())
