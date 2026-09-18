"""Ejecuta una card con un valor de filtro.  Uso: python run_card.py <id> [valor_fecha]"""
import json,sys,os,io,urllib.request
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
env={}
for line in open(os.path.join(os.path.dirname(__file__),'..','.metabase.env'),encoding='utf-8'):
    line=line.strip()
    if line and not line.startswith('#') and '=' in line:
        k,v=line.split('=',1); env[k]=v.strip().strip("'").strip('"')
BASE,KEY=env['METABASE_URL'],env['METABASE_KEY']
cid=sys.argv[1]; val=sys.argv[2] if len(sys.argv)>2 else None
body={}
if val:
    body['parameters']=[{'type':'date/relative','value':val,
                         'target':['dimension',['template-tag','mes'],{'stage-number':0}]}]
req=urllib.request.Request(f'{BASE}/api/card/{cid}/query/json',
    data=json.dumps(body).encode('utf-8'), method='POST',
    headers={'x-api-key':KEY,'Content-Type':'application/json','User-Agent':'curl/8.0 metabase-admin'})
try:
    with urllib.request.urlopen(req,timeout=180) as r:
        rows=json.loads(r.read().decode('utf-8'))
except urllib.error.HTTPError as e:
    body=e.read().decode('utf-8','replace')
    try:
        j=json.loads(body); print('HTTP',e.code,'->',j.get('error') or j.get('message') or json.dumps(j,ensure_ascii=False)[:600])
    except Exception: print('HTTP',e.code,'->',body[:600])
    sys.exit(1)
if isinstance(rows,dict): print('ERROR:',json.dumps(rows,ensure_ascii=False)[:400]); sys.exit(1)
for row in rows: print('   ',json.dumps(row,ensure_ascii=False))
