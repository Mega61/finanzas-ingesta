"""Crea una card nativa nueva copiando la estructura de filtro de una existente.
Uso: python create_card.py <card_modelo> <archivo.sql> "<nombre>" <display> <collection_id>"""
import json,sys,os,io,uuid,urllib.request
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
        with urllib.request.urlopen(req,timeout=120) as r: return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        print('HTTP',e.code,'->',e.read().decode('utf-8','replace')[:700]); raise

modelo,sql_path,nombre,display,col = sys.argv[1],sys.argv[2],sys.argv[3],sys.argv[4],int(sys.argv[5])
sql=open(sql_path,encoding='utf-8').read().strip()
dq=json.loads(json.dumps(call('GET',f'/api/card/{modelo}')['dataset_query']))
st=dq['stages'][0]
st['native']=sql
# uuid propio para el tag y su dimension, para no compartirlos con la card modelo
tag=st['template-tags']['mes']
tag['id']=str(uuid.uuid4())
if isinstance(tag.get('dimension'),list) and isinstance(tag['dimension'][1],dict):
    tag['dimension'][1]['lib/uuid']=str(uuid.uuid4())
nueva=call('POST','/api/card',{'name':nombre,'dataset_query':dq,'display':display,
                               'visualization_settings':{},'collection_id':col})
print(f"card creada: id={nueva['id']}  {nueva['name']}")
