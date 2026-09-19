"""Apunta las cards de Mercado a v_mercado_linea en vez de v_canasta.

    python metabase/repuntar_mercado.py            # solo dice que haria
    python metabase/repuntar_mercado.py --aplicar  # lo hace

v_canasta arranca de las FACTURAS, asi que una compra sin factura electronica
no existe para ella. En septiembre de 2026 eso eran 115.830 de 1.729.284 que
sencillamente no aparecian, y el total de la card nunca podia cuadrar con el
de Firefly.

v_mercado_linea arranca de los MOVIMIENTOS de Firefly y engancha la factura al
lado. Expone las mismas columnas que v_canasta —valor_pagado, bloque,
categoria, producto, cantidad, cufe, codigo, sede, cadena— justo para que este
cambio sea una palabra y no catorce reescrituras.
"""

import io
import json
import os
import sys
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

VIEJA = 'finanzas.v_canasta'
NUEVA = 'finanzas.v_mercado_linea'

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


def etapas(dq):
    salida = list(dq.get('stages') or [])
    if dq.get('native'):
        salida.append(dq['native'])
    return salida


def main():
    aplicar = '--aplicar' in sys.argv
    n = 0
    for resumen in call('GET', '/api/card'):
        cid = resumen['id']
        card = call('GET', f'/api/card/{cid}')
        dq = card.get('dataset_query') or {}

        tocada = False
        for etapa in etapas(dq):
            sql = etapa.get('native') or etapa.get('query')
            if not isinstance(sql, str) or VIEJA not in sql:
                continue
            nuevo = sql.replace(VIEJA, NUEVA)
            if 'native' in etapa:
                etapa['native'] = nuevo
            else:
                etapa['query'] = nuevo
            tocada = True

        if not tocada:
            continue
        n += 1
        print(f'  card {cid:<5} {card["name"][:52]}')
        if aplicar:
            call('PUT', f'/api/card/{cid}', {'dataset_query': dq})

    print()
    if aplicar:
        print(f'{n} cards ahora leen {NUEVA}.')
    else:
        print(f'{n} cards CAMBIARIAN. Nada se escribio. Agrega --aplicar.')


if __name__ == '__main__':
    main()
