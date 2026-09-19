"""Aplica un archivo de ddl/ contra Postgres, por el endpoint SQL de Metabase.

Uso:
    python metabase/aplicar_ddl.py ddl/06_mercado.sql            # solo lista
    python metabase/aplicar_ddl.py ddl/06_mercado.sql --aplicar  # lo hace

Parte el archivo en sentencias quitando PRIMERO los comentarios. Partir por
';' a secas no sirve: varios comentarios de estos archivos llevan punto y coma
adentro («...la contaba en agosto; la plata salio en septiembre») y el archivo
quedaba cortado por la mitad de una frase.
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


def benigno(txt):
    """Metabase responde error cuando la sentencia no devuelve filas.

    Un CREATE VIEW no devuelve nada, asi que SIEMPRE cae aqui. No es un fallo:
    la sentencia ya se ejecuto y confirmo.
    """
    return 'ResultSet' in txt or 'result set' in txt.lower()


def sql(q):
    body = json.dumps(
        {'database': 2, 'type': 'native', 'native': {'query': q}}
    ).encode('utf-8')
    req = urllib.request.Request(
        BASE + '/api/dataset',
        data=body,
        method='POST',
        headers={
            'x-api-key': KEY,
            'Content-Type': 'application/json',
            'User-Agent': 'curl/8.0 metabase-admin',
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            d = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        cuerpo = e.read().decode('utf-8', 'replace')
        if benigno(cuerpo):
            return None
        raise RuntimeError(f'HTTP {e.code}: {cuerpo[:400]}')
    err = d.get('error') or ''
    if err and not benigno(str(err)):
        raise RuntimeError(str(err)[:400])
    return d


def sentencias(texto):
    """El archivo, partido en sentencias y sin comentarios."""
    limpio = []
    for linea in texto.splitlines():
        corte = linea.find('--')
        limpio.append(linea if corte < 0 else linea[:corte])
    return [s.strip() for s in '\n'.join(limpio).split(';') if s.strip()]


def main():
    ruta = sys.argv[1]
    if not os.path.isabs(ruta):
        ruta = os.path.join(os.path.dirname(__file__), ruta.removeprefix('metabase/'))
    aplicar = '--aplicar' in sys.argv
    trozos = sentencias(open(ruta, encoding='utf-8').read())

    print(f'{os.path.basename(ruta)}: {len(trozos)} sentencias')
    for i, s in enumerate(trozos, 1):
        titulo = ' '.join(s.split())[:76]
        print(f'  {i}. {titulo}')
        if aplicar:
            sql(s)
            print('     aplicada')
    print()
    print('Listo.' if aplicar else 'Nada se escribio. Agrega --aplicar.')


if __name__ == '__main__':
    main()
