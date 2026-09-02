"""Genera el bloque de variables de entorno para pegar en Portainer.

    python herramientas/generar_variables.py

Escribe `portainer-variables.txt` en la carpeta de arriba (fuera del repo,
porque lleva los tokens).

Lo importante: la UI de Portainer parte el bloque por saltos de linea, asi que
CADA VARIABLE TIENE QUE OCUPAR UNA SOLA LINEA. Un valor multilinea se
convierte en varias variables basura. Por eso `productos.csv` se serializa con
';' entre filas, y al final hay una comprobacion que falla si algo quedo con
salto de linea.
"""
import io
import json
import os
import sys

# la raiz del repo: estos scripts viven un nivel abajo
from finanzas import config
from finanzas.adaptadores import graph

AQUI = os.path.dirname(os.path.abspath(__file__))
SALIDA = config.ruta_personal('portainer-variables.txt')

# Estas hay que confirmarlas contra la instalacion de cada uno.
POR_CONFIRMAR = {
    'RED_FIREFLY': 'proxied',
    'FIREFLY_URL': 'http://firefly_iii_core:8080',
}

SECRETO = ('TOKEN', 'CLAVE', 'PASSWORD', 'SECRET')


def refresh_token_de_graph():
    """Lo saca del cache de MSAL, para que el contenedor no tenga que hacer el
    device flow (nadie veria el codigo)."""
    ruta = graph.CACHE
    if not os.path.exists(ruta):
        return None
    with open(ruta, encoding='utf-8') as fh:
        cache = json.load(fh)
    rt = cache.get('RefreshToken')
    if isinstance(rt, str):
        rt = json.loads(rt)
    if not rt:
        return None
    return next(iter(rt.values())).get('secret')


def productos_en_una_linea():
    """productos.csv -> una sola linea, con ';' entre filas."""
    ruta = os.path.join(AQUI, 'productos.csv')
    if not os.path.exists(ruta):
        return None
    with open(ruta, encoding='utf-8') as fh:
        filas = [ln.strip() for ln in fh
                 if ln.strip() and not ln.lstrip().startswith('#')]
    return ';'.join(filas)


def construir():
    pares = [
        ('TZ', config.get('TZ', 'America/Bogota')),
        ('RED_FIREFLY', POR_CONFIRMAR['RED_FIREFLY']),
        ('FIREFLY_URL', POR_CONFIRMAR['FIREFLY_URL']),
        ('FIREFLY_TOKEN', config.get('FIREFLY_TOKEN')),
        ('GRAPH_CLIENT_ID', config.get('GRAPH_CLIENT_ID')),
        ('GRAPH_AUTHORITY', config.get('GRAPH_AUTHORITY', 'consumers')),
        ('GRAPH_CUENTA', config.get('GRAPH_CUENTA')),
        ('GRAPH_REFRESH_TOKEN', refresh_token_de_graph()),
        ('GMAIL_USUARIO', config.get('GMAIL_USUARIO')),
        ('GMAIL_APP_PASSWORD', config.get('GMAIL_APP_PASSWORD')),
        ('TELEGRAM_TOKEN', config.get('TELEGRAM_TOKEN')),
        ('TELEGRAM_CHAT_ID_JUAN', config.get('TELEGRAM_CHAT_ID_JUAN')),
        ('TELEGRAM_CHAT_ID_NOVIA', config.get('TELEGRAM_CHAT_ID_NOVIA')),
        ('EXTRACTO_CLAVE', config.get('EXTRACTO_CLAVE') or config.get('CLAVE')),
        ('PRODUCTOS_CSV', productos_en_una_linea()),
        ('INGESTA_INTERVALO_MIN', config.get('INGESTA_INTERVALO_MIN', '15')),
        ('RESUMEN_HORA', config.get('RESUMEN_HORA', '21:00')),
        ('CONCILIAR_HORA', config.get('CONCILIAR_HORA', '03:30')),
        # a proposito: la primera vez conviene mirar el log en seco
        ('INGESTA_EN_SERIO', 'no'),
        ('INGESTA_DESDE', config.get('INGESTA_DESDE', '')),
    ]
    # las vacias se dejan afuera: Portainer no necesita variables en blanco
    return [(k, v) for k, v in pares if v not in (None, '')]


def main():
    pares = construir()

    # LA COMPROBACION QUE IMPORTA
    malas = [k for k, v in pares if '\n' in str(v) or '\r' in str(v)]
    if malas:
        sys.exit(f"ERROR: estas variables tienen salto de linea y romperian "
                 f"el bloque de Portainer: {', '.join(malas)}")

    faltan = [k for k in ('FIREFLY_TOKEN', 'TELEGRAM_TOKEN', 'PRODUCTOS_CSV')
              if k not in dict(pares)]

    out = io.StringIO()
    out.write("VARIABLES DE ENTORNO PARA EL STACK DE PORTAINER\n")
    out.write("=" * 72 + "\n\n")
    out.write("Este archivo TIENE TUS TOKENS. No lo subas a ningun repo.\n")
    out.write("Vive fuera de automatizacion/, que es el repo publico.\n\n")
    out.write("COMO PEGARLO\n")
    out.write("  Portainer -> tu stack -> seccion 'Environment variables'\n")
    out.write("  -> boton 'Advanced mode' -> pega el bloque de abajo completo.\n")
    out.write("  Cada linea es UNA variable. Ninguna ocupa dos lineas: eso ya\n")
    out.write("  esta verificado por el generador.\n\n")
    out.write("=" * 72 + "\n")
    out.write("PEGAR DESDE LA LINEA SIGUIENTE\n")
    out.write("=" * 72 + "\n")
    for k, v in pares:
        out.write(f"{k}={v}\n")
    out.write("=" * 72 + "\n")
    out.write("FIN DEL BLOQUE\n")
    out.write("=" * 72 + "\n\n")

    out.write("QUE CONFIRMAR ANTES DE DESPLEGAR\n\n")
    out.write(f"  RED_FIREFLY={POR_CONFIRMAR['RED_FIREFLY']}\n")
    out.write("    Es la red de Docker donde vive Firefly. Si el despliegue\n")
    out.write("    falla con 'network not found', corre en el servidor:\n")
    out.write("        docker network ls\n")
    out.write("    y usa el nombre que aparezca.\n\n")
    out.write(f"  FIREFLY_URL={POR_CONFIRMAR['FIREFLY_URL']}\n")
    out.write("    Es el nombre del contenedor de Firefly mas el puerto interno.\n")
    out.write("    Si no resuelve, se puede usar la URL publica.\n\n")
    out.write("  INGESTA_EN_SERIO=no\n")
    out.write("    Arranca en 'no' a proposito. Mira el log del contenedor,\n")
    out.write("    confirma que los movimientos que ve son correctos, y despues\n")
    out.write("    cambialo a 'si' y actualiza el stack.\n\n")
    out.write("  PRODUCTOS_CSV\n")
    out.write("    Va en una sola linea, con ';' entre filas. El servicio lo\n")
    out.write("    desarma solo al arrancar.\n\n")
    out.write("  GRAPH_REFRESH_TOKEN\n")
    out.write("    Evita que el contenedor tenga que pedir el codigo de device\n")
    out.write("    flow, que nadie veria. Solo se usa en el primer arranque:\n")
    out.write("    despues rota solo dentro del volumen. Si algun dia deja de\n")
    out.write("    servir, saca uno nuevo con: python verificar.py graph\n")
    if faltan:
        out.write(f"\nOJO: faltan por configurar: {', '.join(faltan)}\n")

    with open(SALIDA, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(out.getvalue())

    print(f"escrito: {SALIDA}")
    print(f"  {len(pares)} variables, todas de una sola linea\n")
    for k, bruto in pares:
        v = str(bruto)
        muestra = f"[{len(v)} chars]" if any(s in k for s in SECRETO) else v[:52]
        print(f"    {k:24} = {muestra}")
    if faltan:
        print(f"\n  OJO, faltan: {', '.join(faltan)}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
