"""Agrega una card al dashboard 2 mapeandola al filtro Fecha.
Uso: python add_to_dashboard.py <card_id> <row> <col> <size_x> <size_y>"""
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
        with urllib.request.urlopen(req,timeout=120) as r: return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        print('HTTP',e.code,'->',e.read().decode('utf-8','replace')[:700]); raise

cid,row,col,sx,sy = int(sys.argv[1]),int(sys.argv[2]),int(sys.argv[3]),int(sys.argv[4]),int(sys.argv[5])
d=call('GET','/api/dashboard/2')
if any(dc['card_id']==cid for dc in d['dashcards']):
    print(f'la card {cid} ya esta en el dashboard'); sys.exit(0)
pid=d['parameters'][0]['id']
dcs=[{k:v for k,v in dc.items() if k in
      ('id','card_id','row','col','size_x','size_y','parameter_mappings','visualization_settings','series','dashboard_tab_id')}
     for dc in d['dashcards']]
dcs.append({'id':-1,'card_id':cid,'row':row,'col':col,'size_x':sx,'size_y':sy,
            'series':[],'visualization_settings':{},
            'parameter_mappings':[{'parameter_id':pid,'card_id':cid,
                'target':['dimension',['template-tag','mes'],{'stage-number':0}]}]})
call('PUT','/api/dashboard/2',{'dashcards':dcs})
d2=call('GET','/api/dashboard/2')
for dc in sorted(d2['dashcards'],key=lambda x:(x['row'],x['col'])):
    print(f"  r{dc['row']:<3} c{dc['col']:<3} {dc['size_x']}x{dc['size_y']}  card {str(dc['card_id']):<4} "
          f"{(dc.get('card') or {}).get('name','')[:30]:<32} mapeos={len(dc.get('parameter_mappings') or [])}")
