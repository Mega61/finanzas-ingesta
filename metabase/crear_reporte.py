"""Crea el dashboard 'Reporte del mes'."""
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
        print('HTTP', e.code, e.read().decode('utf-8', 'replace')[:400]); raise

D = os.path.join(os.path.dirname(__file__), 'sql', 'reporte')
NUEVAS = [
    ('r0_deuda_total.sql',        'Deuda real hoy',              'scalar', 'Ultimo extracto de cada tarjeta mas lo comprado despues del corte. No es lo que dice Firefly.'),
    ('r0b_proximo_pago.sql',      'Proximo pago',                'scalar', 'El pago total pendiente con la fecha limite mas cercana.'),
    ('r0c_cupo_usado.sql',        'Cupo usado',                  'gauge',  'Porcentaje del cupo total que estas usando hoy.'),
    ('r2_pagos_pendientes.sql',   'Que hay que pagar y cuando',  'table',  'Ordenado por urgencia. dias_restantes negativo = vencido.'),
    ('r1_estado_tarjetas.sql',    'Estado real de cada tarjeta', 'table',  'Deuda al corte del banco, mas compras posteriores, contra el cupo real del extracto.'),
    ('r3_firefly_vs_extracto.sql','Firefly vs extracto',         'table',  'La columna descuadre es lo que NO se explica por compras post-corte. Deberia ser ~0.'),
]
existentes = {c['name']: c['id'] for c in call('GET', '/api/card')}
ids = []
for arch, nombre, disp, desc in NUEVAS:
    if nombre in existentes:
        ids.append(existentes[nombre]); print(f'  ya existia: {nombre}'); continue
    sql = open(os.path.join(D, arch), encoding='utf-8').read().strip()
    dq = {'lib/type':'mbql/query','database':2,'stages':[{'lib/type':'mbql.stage/native','native':sql}]}
    c = call('POST','/api/card',{'name':nombre,'dataset_query':dq,'display':disp,'description':desc,
                                 'visualization_settings':{},'collection_id':5})
    ids.append(c['id']); print(f"  creada: {nombre} (id {c['id']})")

# cards que ya existen y se reutilizan, con su filtro de fecha
ING, BUCKET = existentes.get('Ingresos: base vs real'), existentes.get('Gastable por bucket')

dashes = {d['name']: d['id'] for d in call('GET','/api/dashboard')}
if 'Reporte del mes' in dashes:
    did = dashes['Reporte del mes']
else:
    d = call('POST','/api/dashboard', {'name':'Reporte del mes','collection_id':5,
        'description':'Plata real: extractos del banco para deuda y cupos, Firefly para el movimiento del mes.'})
    did = d['id']; print(f'  dashboard creado (id {did})')

d = call('GET', f'/api/dashboard/{did}')
if not d['dashcards']:
    # filtro de fecha, solo para las dos cards que lo aceptan
    pid = 'f3ch4rep'
    call('PUT', f'/api/dashboard/{did}', {'parameters':[
        {'id':pid,'name':'Fecha','slug':'fecha','type':'date/relative','default':'thismonth','sectionId':'date'}]})
    lay = [(0,0,8,4),(0,8,8,4),(0,16,8,4),(4,0,24,6),(10,0,24,7),(17,0,24,7)]
    dcs = [{'id':-(i+1),'card_id':cid,'row':r,'col':c,'size_x':sx,'size_y':sy,
            'series':[],'visualization_settings':{},'parameter_mappings':[]}
           for i,(cid,(r,c,sx,sy)) in enumerate(zip(ids, lay))]
    n = len(dcs)
    for j,(cid,(r,c,sx,sy)) in enumerate([(ING,(24,0,12,6)),(BUCKET,(24,12,12,6))]):
        if cid:
            dcs.append({'id':-(n+j+1),'card_id':cid,'row':r,'col':c,'size_x':sx,'size_y':sy,
                        'series':[],'visualization_settings':{},
                        'parameter_mappings':[{'parameter_id':pid,'card_id':cid,
                            'target':['dimension',['template-tag','mes'],{'stage-number':0}]}]})
    call('PUT', f'/api/dashboard/{did}', {'dashcards':dcs})

d = call('GET', f'/api/dashboard/{did}')
print(f"\n  '{d['name']}' — filtro: {[p['name'] for p in d.get('parameters',[])]}")
for dc in sorted(d['dashcards'], key=lambda x:(x['row'],x['col'])):
    print(f"    r{dc['row']:<3} c{dc['col']:<3} {dc['size_x']}x{dc['size_y']}  {(dc.get('card') or {}).get('name')}")
print(f"\n  {BASE}/dashboard/{did}")
