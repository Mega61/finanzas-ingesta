"""Ejecuta todas las tarjetas del dashboard 2 con un valor de filtro y reporta ok/error."""
import json,sys,os,io,urllib.request
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8',errors='replace')
env={}
for line in open(os.path.join(os.path.dirname(__file__),'..','.metabase.env'),encoding='utf-8'):
    line=line.strip()
    if line and not line.startswith('#') and '=' in line:
        k,v=line.split('=',1); env[k]=v.strip().strip("'").strip('"')
BASE,KEY=env['METABASE_URL'],env['METABASE_KEY']
def call(method,path,payload=None):
    data=json.dumps(payload).encode('utf-8') if payload is not None else None
    req=urllib.request.Request(BASE+path,data=data,method=method,
        headers={'x-api-key':KEY,'Content-Type':'application/json','User-Agent':'curl/8.0 metabase-admin'})
    try:
        with urllib.request.urlopen(req,timeout=180) as r: return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return {'__error__': e.read().decode('utf-8','replace')[:200]}
val=sys.argv[1]
d=call('GET','/api/dashboard/2'); pid=d['parameters'][0]['id']
print(f'== filtro = {val} ==')
for dc in sorted(d['dashcards'],key=lambda x:(x['row'],x['col'])):
    nm=(dc.get('card') or {}).get('name','')
    body={'parameters':[{'id':pid,'type':'date/relative','value':val,
          'target':['dimension',['template-tag','mes'],{'stage-number':0}]}]} if dc['parameter_mappings'] else {'parameters':[]}
    r=call('POST',f"/api/dashboard/2/dashcard/{dc['id']}/card/{dc['card_id']}/query/json",body)
    if isinstance(r,dict) and '__error__' in r: print(f'  {nm[:28]:<30} ERROR {r["__error__"][:90]}')
    else: print(f'  {nm[:28]:<30} ok  ({len(r)} fila/s)  {json.dumps(r[0],ensure_ascii=False)[:78] if r else ""}')
