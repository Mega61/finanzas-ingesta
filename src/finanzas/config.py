"""Configuracion, y las tres carpetas de las que depende todo.

Secretos — gana el primero que tenga valor:

  1. Variables de entorno del proceso   <- es asi en el contenedor (stack.env)
  2. <PROYECTO>/.env                    <- es asi en el Windows de desarrollo
  3. <PERSONAL>/.firefly.env            <- compatibilidad con lo que ya existia

Y tres carpetas que antes eran DOS variables (`AQUI` y `RAIZ`) con tres
significados encima. `RAIZ` queria decir «la carpeta arriba del codigo», y eso
solo funcionaba porque el codigo estaba justo debajo de la carpeta personal. Al
mover los modulos a src/finanzas/ dejo de significar nada, asi que ahora las
tres son explicitas y las tres se pueden fijar por entorno:

  PROYECTO   la raiz del repo: .env, productos.csv, el token de Graph en dev.
             Se encuentra subiendo hasta el pyproject.toml. FINANZAS_PROYECTO.
  PERSONAL   tus datos, FUERA del repo: «Extractos Bancolombia», «Mensajes de
             Bancolombia». Por defecto, la carpeta que contiene al proyecto.
             FINANZAS_PERSONAL.
  DATOS      el volumen de ejecucion: finanzas.db y el token de Graph en el
             contenedor. FINANZAS_DATOS.

Nunca imprime un valor. `describir()` esta hecho para poder depurar sin filtrar
nada al log.
"""

from __future__ import annotations

import os
from pathlib import Path


def _buscar_proyecto() -> Path:
    """La raiz del repo: el primer directorio hacia arriba con pyproject.toml.

    No se calcula como «N niveles arriba de __file__» a proposito: ese numero
    cambia cada vez que un modulo se mueve de capa, y cuando cambia el sintoma
    es un .env que no se encuentra, sin ninguna pista.
    """
    for carpeta in Path(__file__).resolve().parents:
        if (carpeta / 'pyproject.toml').exists():
            return carpeta
    # Instalado como paquete, sin repo alrededor: solo queda el entorno.
    return Path.cwd()


PROYECTO = Path(os.environ.get('FINANZAS_PROYECTO') or _buscar_proyecto())
PERSONAL = Path(os.environ.get('FINANZAS_PERSONAL') or PROYECTO.parent)
DATOS = Path(os.environ.get('FINANZAS_DATOS') or PROYECTO)

ARCHIVOS = [
    PROYECTO / '.env',
    PERSONAL / '.firefly.env',
    PERSONAL / '.bancolombia.env',
]


def _leer(ruta: str | Path) -> dict[str, str]:
    d: dict[str, str] = {}
    ruta = Path(ruta)
    if not ruta.exists():
        return d
    with ruta.open(encoding='utf-8') as fh:
        for cruda in fh:
            linea = cruda.strip()
            if not linea or linea.startswith('#') or '=' not in linea:
                continue
            k, v = linea.split('=', 1)
            v = v.strip().strip('"').strip("'")
            if v:
                d[k.strip()] = v
    return d


_ARCHIVO: dict[str, str] = {}
for _r in ARCHIVOS:
    for _k, _v in _leer(_r).items():
        _ARCHIVO.setdefault(_k, _v)


def get(clave: str, defecto: str | None = None) -> str | None:
    """El entorno del proceso siempre gana sobre los archivos."""
    v = os.environ.get(clave)
    if v:
        return v
    return _ARCHIVO.get(clave, defecto)


def requerir(*claves: str) -> list[str]:
    """Los valores, EN ORDEN, como lista. No es un dict.

    Se usa como `url, tok = requerir('FIREFLY_URL', 'FIREFLY_TOKEN')`. Leerlo
    como `requerir('X')['X']` no falla al importar, solo al llamar.
    """
    faltan = [c for c in claves if not get(c)]
    if faltan:
        raise RuntimeError(
            'Falta configuracion: '
            + ', '.join(faltan)
            + f'\nPonlos en {PROYECTO / ".env"} o como variables de entorno.'
        )
    return [get(c) for c in claves]


def ruta_datos(*partes: str) -> str:
    """Una ruta dentro del volumen de datos, creandolo si hace falta."""
    DATOS.mkdir(parents=True, exist_ok=True)
    return str(DATOS.joinpath(*partes))


def ruta_proyecto(*partes: str) -> str:
    """Una ruta dentro del repo: productos.csv, .env."""
    return str(PROYECTO.joinpath(*partes))


def ruta_personal(*partes: str) -> str:
    """Una ruta dentro de tus datos, fuera del repo: extractos, correos."""
    return str(PERSONAL.joinpath(*partes))


def describir() -> str:
    """Que hay configurado, sin revelar valores. Para logs y diagnostico."""
    interes = [
        'FIREFLY_URL',
        'FIREFLY_TOKEN',
        'GRAPH_CLIENT_ID',
        'GRAPH_AUTHORITY',
        'GRAPH_CUENTA',
        'GMAIL_USUARIO',
        'GMAIL_APP_PASSWORD',
        'TELEGRAM_TOKEN',
        'TELEGRAM_CHAT_ID_JUAN',
        'TELEGRAM_CHAT_ID_NOVIA',
    ]
    fuera = []
    for c in interes:
        v = get(c)
        if not v:
            estado = 'falta'
        elif (
            c.endswith(('_URL', '_CUENTA', '_USUARIO', '_AUTHORITY')) or 'CHAT_ID' in c
        ):
            estado = v  # estos no son secretos
        else:
            estado = f'[{len(v)} chars]'  # estos si
        fuera.append(f'{c}={estado}')
    return '\n'.join(fuera)


def describir_rutas() -> str:
    """Las tres carpetas, resueltas. Es lo primero que hay que mirar cuando
    algo «no encuentra» un archivo."""
    return '\n'.join(
        [
            f'PROYECTO = {PROYECTO}',
            f'PERSONAL = {PERSONAL}',
            f'DATOS    = {DATOS}',
        ]
    )
