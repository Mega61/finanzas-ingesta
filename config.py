# -*- coding: utf-8 -*-
"""Configuracion. Una sola forma de leer secretos en todos los entornos.

Orden de precedencia (gana el primero que tenga valor):

  1. Variables de entorno del proceso   <- es asi en el contenedor (stack.env)
  2. automatizacion/.env                <- es asi en el Windows de desarrollo
  3. ../.firefly.env                    <- compatibilidad con lo que ya existia

Nunca imprime un valor. `describir()` esta hecho para poder depurar sin filtrar
nada al log.
"""
import os

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)

ARCHIVOS = [
    os.path.join(AQUI, '.env'),
    os.path.join(RAIZ, '.firefly.env'),
    os.path.join(RAIZ, '.bancolombia.env'),
]

# Donde viven la base de la cola y el token de Graph. En el contenedor es un
# volumen; en Windows, la propia carpeta automatizacion/.
DATOS = os.environ.get('FINANZAS_DATOS') or AQUI


def _leer(ruta):
    d = {}
    if not os.path.exists(ruta):
        return d
    with open(ruta, encoding='utf-8') as fh:
        for linea in fh:
            linea = linea.strip()
            if not linea or linea.startswith('#') or '=' not in linea:
                continue
            k, v = linea.split('=', 1)
            v = v.strip().strip('"').strip("'")
            if v:
                d[k.strip()] = v
    return d


_ARCHIVO = {}
for _r in ARCHIVOS:
    for _k, _v in _leer(_r).items():
        _ARCHIVO.setdefault(_k, _v)


def get(clave, defecto=None):
    """El entorno del proceso siempre gana sobre los archivos."""
    v = os.environ.get(clave)
    if v:
        return v
    return _ARCHIVO.get(clave, defecto)


def requerir(*claves):
    """Devuelve los valores, o levanta con la lista de lo que falta."""
    faltan = [c for c in claves if not get(c)]
    if faltan:
        raise RuntimeError(
            "Falta configuracion: " + ', '.join(faltan) +
            f"\nPonlos en {os.path.join(AQUI, '.env')} o como variables de entorno."
        )
    return [get(c) for c in claves]


def ruta_datos(*partes):
    os.makedirs(DATOS, exist_ok=True)
    return os.path.join(DATOS, *partes)


def describir():
    """Que hay configurado, sin revelar valores. Para logs y diagnostico."""
    interes = [
        'FIREFLY_URL', 'FIREFLY_TOKEN',
        'GRAPH_CLIENT_ID', 'GRAPH_AUTHORITY', 'GRAPH_CUENTA',
        'GMAIL_USUARIO', 'GMAIL_APP_PASSWORD',
        'TELEGRAM_TOKEN', 'TELEGRAM_CHAT_ID_JUAN', 'TELEGRAM_CHAT_ID_NOVIA',
    ]
    fuera = []
    for c in interes:
        v = get(c)
        if not v:
            estado = 'falta'
        elif c.endswith(('_URL', '_CUENTA', '_USUARIO', '_AUTHORITY')) or 'CHAT_ID' in c:
            estado = v                      # estos no son secretos
        else:
            estado = f"[{len(v)} chars]"    # estos si
        fuera.append(f"{c}={estado}")
    return '\n'.join(fuera)


if __name__ == '__main__':
    print(f"DATOS = {DATOS}\n")
    print(describir())
