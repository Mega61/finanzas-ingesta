"""Pone el filtro de mes de las cards en el mes ACTUAL.

El dashboard de mercado llevaba meses mostrando el mes ANTERIOR sin decirlo.
Las quince cards de mercado traen el filtro `mes` con default `past1months`,
que en Metabase es «el mes pasado», mientras que las siete cards de
presupuesto usan `thismonth`. O sea que las dos mitades del mismo tablero
contestaban preguntas de meses distintos, una al lado de la otra.

Asi se veia el 18 de septiembre de 2026: «Gasto del mes» pintaba hasta agosto
—septiembre ni aparecia— y «Comida: que compraste» listaba un SOLOMO
EXTRANJERO comprado el 11 de agosto en La Vaquita. El dato estaba bien
guardado y bien fechado; la etiqueta de la card era la que mentia.

Uso:
    python metabase/fijar_mes.py            # solo dice que haria
    python metabase/fijar_mes.py --aplicar  # lo hace
"""

import io
import json
import os
import sys
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

VIEJO = 'past1months'
NUEVO = 'thismonth'

env = {}
ruta_env = os.path.join(os.path.dirname(__file__), '..', '.metabase.env')
for line in open(ruta_env, encoding='utf-8'):
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, v = line.split('=', 1)
        env[k] = v.strip().strip("'").strip('"')
BASE, KEY = env['METABASE_URL'], env['METABASE_KEY']


def call(method, path, payload=None):
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={
            'x-api-key': KEY,
            'Content-Type': 'application/json',
            'User-Agent': 'curl/8.0 metabase-admin',
        },
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode('utf-8'))


def etapas_con_tags(dq):
    """Las cards viven en dos formatos segun cuando se crearon."""
    salida = list(dq.get('stages') or [])
    if dq.get('native'):
        salida.append(dq['native'])
    return salida


def main():
    aplicar = '--aplicar' in sys.argv
    cambiadas = 0

    for resumen in call('GET', '/api/card'):
        cid = resumen['id']
        card = call('GET', f'/api/card/{cid}')
        dq = card.get('dataset_query') or {}

        objetivo = []
        for etapa in etapas_con_tags(dq):
            for nombre, tag in (etapa.get('template-tags') or {}).items():
                if tag.get('default') == VIEJO:
                    objetivo.append((etapa, nombre, tag))
        if not objetivo:
            continue

        for _etapa, nombre, tag in objetivo:
            print(f'  card {cid:<5} {card["name"][:46]:<48} {nombre}: {VIEJO} -> {NUEVO}')
            tag['default'] = NUEVO
        cambiadas += 1

        if aplicar:
            # Solo se manda lo que cambia. Mandar la card entera de vuelta
            # arrastra campos calculados que el servidor rechaza.
            call('PUT', f'/api/card/{cid}', {'dataset_query': dq})

    print()
    if not cambiadas:
        print('Nada que cambiar: ninguna card trae el default viejo.')
    elif aplicar:
        print(f'{cambiadas} cards actualizadas.')
    else:
        print(f'{cambiadas} cards CAMBIARIAN. Nada se escribio.')
        print('Para hacerlo de verdad:  python metabase/fijar_mes.py --aplicar')


if __name__ == '__main__':
    main()
