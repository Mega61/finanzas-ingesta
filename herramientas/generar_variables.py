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
import re
import sys
from pathlib import Path

# la raiz del repo: estos scripts viven un nivel abajo
from finanzas import config
from finanzas.adaptadores import graph

AQUI = os.path.dirname(os.path.abspath(__file__))
SALIDA = config.ruta_personal('portainer-variables.txt')
STACK = config.ruta_proyecto('despliegue', 'stack.portainer.yml')

# Que NO se imprime en consola. Faltaba 'KEY' y esta misma herramienta —la que
# existe para manejar secretos— escupio la GEMINI_API_KEY completa al terminal.
SECRETO = ('TOKEN', 'CLAVE', 'PASSWORD', 'SECRET', 'KEY', 'API')


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
    ruta = config.ruta_proyecto('productos.csv')
    if not os.path.exists(ruta):
        return None
    with open(ruta, encoding='utf-8') as fh:
        filas = [
            ln.strip()
            for ln in fh
            if ln.strip() and not ln.lstrip().startswith('#')
        ]
    return ';'.join(filas)


def variables_del_stack():
    """Los nombres que el stack le pasa al contenedor, en su orden.

    Se leen del stack y NO de una lista escrita aqui. La lista a mano se
    desincronizo: le faltaban GEMINI_API_KEY y otras diez, y el archivo generado
    quedaba incompleto sin que nada lo dijera. Se noto el dia que hubo que
    recrear el stack desde cero.
    """
    texto = Path(STACK).read_text(encoding='utf-8')
    dentro, fuera = False, []
    for linea in texto.split('\n'):
        if re.match(r'^\s{4}environment:', linea):
            dentro = True
            continue
        if dentro:
            if re.match(r'^\s{0,4}\S', linea):
                break
            m = re.match(r'^\s{6}([A-Z][A-Z0-9_]+):', linea)
            if m:
                fuera.append(m.group(1))
    return fuera


# De donde sale cada valor que no es un simple config.get(). Lo que no este
# aqui se lee de la configuracion con su propio nombre.
ESPECIALES = {
    # El contenedor lo fija en el Dockerfile; no va por Portainer.
    'FINANZAS_DATOS': lambda: None,
    'GRAPH_REFRESH_TOKEN': lambda: (
        config.get('GRAPH_REFRESH_TOKEN') or refresh_token_de_graph()
    ),
    'PRODUCTOS_CSV': productos_en_una_linea,
    'EXTRACTO_CLAVE': lambda: config.get('EXTRACTO_CLAVE') or config.get('CLAVE'),
    'TZ': lambda: config.get('TZ', 'America/Bogota'),
}

# Estas NO se leen del .env local a proposito: el valor de desarrollo y el del
# contenedor son distintos.
#
# FIREFLY_URL en tu maquina es la URL publica, porque desde Windows no se
# resuelve el nombre de un contenedor. Dentro de la red de Docker tiene que ser
# el nombre del contenedor: asi no sale a internet ni pasa por el proxy inverso.
# Tomar el del .env metia la URL publica en el stack, que funciona pero da la
# vuelta por fuera.
#
# Se pueden fijar con FIREFLY_URL_CONTENEDOR y RED_FIREFLY en el .env.
SOLO_DESPLIEGUE = {
    'RED_FIREFLY': lambda: config.get('RED_FIREFLY') or 'proxied',
    'FIREFLY_URL': lambda: (
        config.get('FIREFLY_URL_CONTENEDOR') or 'http://firefly_iii_core:8080'
    ),
}


def construir():
    """(nombre, valor) para cada variable del stack que tenga algo que poner."""
    pares = []
    # RED_FIREFLY no esta en el bloque environment: es la red del stack.
    for nombre in ['RED_FIREFLY', *variables_del_stack()]:
        if nombre in SOLO_DESPLIEGUE:
            valor = SOLO_DESPLIEGUE[nombre]()
        elif nombre in ESPECIALES:
            valor = ESPECIALES[nombre]()
        else:
            valor = config.get(nombre)
        if valor not in (None, ''):
            pares.append((nombre, str(valor)))
    return pares


def faltantes(pares):
    """Las que el stack exige y no se pudieron llenar.

    El stack las marca con `:?` — sin ellas el contenedor no arranca.
    """
    texto = Path(STACK).read_text(encoding='utf-8')
    exigidas = set(re.findall(r'\$\{([A-Z][A-Z0-9_]+):\?', texto))
    return sorted(exigidas - set(dict(pares)))


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
    out.write(f"  RED_FIREFLY={SOLO_DESPLIEGUE['RED_FIREFLY']()}\n")
    out.write("    Es la red de Docker donde vive Firefly. Si el despliegue\n")
    out.write("    falla con 'network not found', corre en el servidor:\n")
    out.write("        docker network ls\n")
    out.write("    y usa el nombre que aparezca.\n\n")
    out.write(f"  FIREFLY_URL={SOLO_DESPLIEGUE['FIREFLY_URL']()}\n")
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
