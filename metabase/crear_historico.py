"""Crea las cards del historico y el dashboard que las contiene."""
import json, os, sys, io, urllib.request
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
env = {}
for line in open(os.path.join(os.path.dirname(__file__), '..', '.metabase.env'), encoding='utf-8'):
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, v = line.split('=', 1); env[k] = v.strip().strip("'").strip('"')
BASE, KEY = env['METABASE_URL'], env['METABASE_KEY']
def call(m, p, payload=None):
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(BASE + p, data=data, method=m,
        headers={'x-api-key': KEY, 'Content-Type': 'application/json', 'User-Agent': 'curl/8.0 metabase-admin'})
    try:
        with urllib.request.urlopen(req, timeout=180) as r: return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        print('HTTP', e.code, e.read().decode('utf-8', 'replace')[:500]); raise

BASE_DIR = os.path.join(os.path.dirname(__file__), 'sql', 'historico')
CARDS = [
    ('h1_gasto_mensual.sql',  'Gasto mensual en tarjetas (2022-2026)', 'line',
     'Compras con tarjeta de credito por mes. Excluye abonos. Ojo: la densidad de datos antes de 2025 es mucho menor.'),
    ('h2_producto_ano.sql',   'Gasto por tarjeta y ano',               'bar',
     'Producto logico: consolida las tarjetas que fueron reemplazadas (AMEX 0846 -> 7466, etc).'),
    ('h3_top_comercios.sql',  'Top 25 comercios de 4 anos',            'table',
     'Agrupado por descripcion del extracto, que viene truncada a ~22 caracteres.'),
    ('h4_flujo_ahorros.sql',  'Flujo mensual cuenta de ahorros',       'bar',
     'Entradas vs salidas de la cuenta de ahorros, mes a mes.'),
]
existentes = {c['name']: c['id'] for c in call('GET', '/api/card')}
ids = []
for archivo, nombre, display, desc in CARDS:
    if nombre in existentes:
        print(f'  ya existia: {nombre} (id {existentes[nombre]})'); ids.append(existentes[nombre]); continue
    sql = open(os.path.join(BASE_DIR, archivo), encoding='utf-8').read().strip()
    dq = {'lib/type': 'mbql/query', 'database': 2,
          'stages': [{'lib/type': 'mbql.stage/native', 'native': sql}]}
    c = call('POST', '/api/card', {'name': nombre, 'dataset_query': dq, 'display': display,
                                   'description': desc, 'visualization_settings': {}, 'collection_id': 5})
    print(f"  creada: {c['name']} (id {c['id']}, {display})"); ids.append(c['id'])

dashes = {d['name']: d['id'] for d in call('GET', '/api/dashboard')}
if 'Historico 2022-2026' in dashes:
    did = dashes['Historico 2022-2026']; print(f'  dashboard ya existia (id {did})')
else:
    d = call('POST', '/api/dashboard', {'name': 'Historico 2022-2026', 'collection_id': 5,
        'description': 'Los 4 anos que Firefly no ve. Fuente: extractos de Bancolombia en finanzas.movimientos.'})
    did = d['id']; print(f'  dashboard creado (id {did})')

d = call('GET', f'/api/dashboard/{did}')
if not d['dashcards']:
    layout = [(0,0,24,6), (6,0,12,7), (6,12,12,7), (13,0,24,6)]
    dcs = [{'id': -(i+1), 'card_id': cid, 'row': r, 'col': c, 'size_x': sx, 'size_y': sy,
            'series': [], 'visualization_settings': {}, 'parameter_mappings': []}
           for i, (cid, (r, c, sx, sy)) in enumerate(zip(ids, layout))]
    call('PUT', f'/api/dashboard/{did}', {'dashcards': dcs})
d = call('GET', f'/api/dashboard/{did}')
print(f"\n  dashboard '{d['name']}' con {len(d['dashcards'])} tarjetas:")
for dc in sorted(d['dashcards'], key=lambda x: (x['row'], x['col'])):
    print(f"    r{dc['row']:<3} c{dc['col']:<3} {dc['size_x']}x{dc['size_y']}  {(dc.get('card') or {}).get('name')}")
print(f"\n  URL: {BASE}/dashboard/{did}")
