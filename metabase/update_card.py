"""Reemplaza el SQL nativo de una card de Metabase.  Uso: python update_card.py <id> <archivo.sql>"""
import json, sys, os, io, urllib.request
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

env = {}
for line in open(os.path.join(os.path.dirname(__file__), '..', '.metabase.env'), encoding='utf-8'):
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, v = line.split('=', 1)
        env[k] = v.strip().strip("'").strip('"')
BASE, KEY = env['METABASE_URL'], env['METABASE_KEY']

def call(method, path, payload=None):
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={'x-api-key': KEY, 'Content-Type': 'application/json',
                                          'User-Agent': 'curl/8.0 metabase-admin'})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode('utf-8'))

card_id, sql_path = sys.argv[1], sys.argv[2]
sql = open(sql_path, encoding='utf-8').read().strip()

card = call('GET', f'/api/card/{card_id}')
dq = card['dataset_query']
stage = dq['stages'][0]
antes = stage['native']
if antes.strip() == sql:
    print(f'card {card_id}: sin cambios'); sys.exit(0)
stage['native'] = sql
call('PUT', f'/api/card/{card_id}', {'dataset_query': dq})

nuevo = call('GET', f'/api/card/{card_id}')['dataset_query']['stages'][0]['native']
ok = nuevo.strip() == sql
print(f"card {card_id} ({card['name']}): {'OK' if ok else 'MISMATCH'}  {len(antes)} -> {len(nuevo)} chars")
sys.exit(0 if ok else 1)
