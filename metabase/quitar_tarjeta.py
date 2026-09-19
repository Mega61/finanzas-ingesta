"""Quita una tarjeta de un dashboard, sin borrar la card.

    python metabase/quitar_tarjeta.py 5 99            # solo dice que haria
    python metabase/quitar_tarjeta.py 5 99 --aplicar  # lo hace

La card sigue existiendo y se puede volver a colgar; lo unico que cambia es
que deja de ocupar sitio en ese tablero. 'Excluido del dashboard: tecnologia
y electrodomesticos' es justo eso: una card que explica lo que el dashboard
NO cuenta, colgada dentro del dashboard.
"""

import io
import json
import os
import sys
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

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


def main():
    dash_id, card_id = int(sys.argv[1]), int(sys.argv[2])
    aplicar = '--aplicar' in sys.argv

    dash = call('GET', f'/api/dashboard/{dash_id}')
    dcs = dash.get('dashcards') or []
    quedan = [dc for dc in dcs if (dc.get('card') or {}).get('id') != card_id]

    fuera = len(dcs) - len(quedan)
    if not fuera:
        print(f'La card {card_id} no esta en el dashboard «{dash["name"]}».')
        return

    for dc in dcs:
        if (dc.get('card') or {}).get('id') == card_id:
            print(f'  quitar: «{dc["card"]["name"]}» (card {card_id})')
    print(f'  «{dash["name"]}»: {len(dcs)} tarjetas -> {len(quedan)}')
    print()

    if not aplicar:
        print('Nada se escribio. Agrega --aplicar.')
        return

    call('PUT', f'/api/dashboard/{dash_id}', {'dashcards': quedan})
    print('Hecho. La card sigue existiendo, solo dejo de estar en el tablero.')


if __name__ == '__main__':
    main()
