"""Carga un CSV a una tabla de Postgres via el endpoint SQL de Metabase, en lotes.
Uso: python cargar_csv.py <csv> <tabla> <col:tipo,col:tipo,...> [tam_lote]
Tipos: t=text  n=numeric  d=date  i=int   (vacio -> NULL en n/d/i)"""
import csv, json, os, sys, io, urllib.request
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

env = {}
for line in open(os.path.join(os.path.dirname(__file__), '..', '.metabase.env'), encoding='utf-8'):
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, v = line.split('=', 1); env[k] = v.strip().strip("'").strip('"')
BASE, KEY = env['METABASE_URL'], env['METABASE_KEY']

def sql(q):
    body = json.dumps({'database': 2, 'type': 'native', 'native': {'query': q}}).encode('utf-8')
    req = urllib.request.Request(BASE + '/api/dataset', data=body, method='POST',
        headers={'x-api-key': KEY, 'Content-Type': 'application/json', 'User-Agent': 'curl/8.0 metabase-admin'})
    def benigno(txt):
        # Metabase responde con error cuando la sentencia no devuelve filas.
        # No es un fallo: el INSERT/DDL ya se ejecuto y confirmo.
        return 'ResultSet' in txt or 'result set' in txt.lower()
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            d = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        cuerpo = e.read().decode('utf-8', 'replace')
        if benigno(cuerpo): return None
        raise RuntimeError(f'HTTP {e.code}: ' + cuerpo[:500])
    err = d.get('error') or ''
    if err and not benigno(err):
        raise RuntimeError(err[:400])
    return d

def lit(v, t):
    v = (v or '').strip()
    if t in 'ndi' and v == '': return 'NULL'
    if t == 't' and v == '':  return 'NULL'
    if t in 'ni': return v
    return "'" + v.replace("'", "''") + "'"

csv_path, tabla, spec = sys.argv[1], sys.argv[2], sys.argv[3]
lote = int(sys.argv[4]) if len(sys.argv) > 4 else 400
cols = [c.split(':') for c in spec.split(',')]
nombres = [c[0] for c in cols]; tipos = [c[1] for c in cols]

with open(csv_path, encoding='utf-8', newline='') as f:
    filas = list(csv.DictReader(f))
sql(f'TRUNCATE {tabla}')
print(f'{csv_path}: {len(filas)} filas -> {tabla} (tabla vaciada antes de cargar)')

enviadas = 0
for i in range(0, len(filas), lote):
    trozo = filas[i:i+lote]
    vals = ','.join('(' + ','.join(lit(r.get(n), t) for n, t in zip(nombres, tipos)) + ')' for r in trozo)
    sql(f'INSERT INTO {tabla} ({",".join(nombres)}) VALUES {vals}')
    enviadas += len(trozo)
    print(f'  {enviadas}/{len(filas)}', end='\r', flush=True)
print(f'  {enviadas}/{len(filas)} listo   ')
n = sql(f'SELECT count(*) FROM {tabla}')['data']['rows'][0][0]
print(f'  la tabla ahora tiene {n} filas')
