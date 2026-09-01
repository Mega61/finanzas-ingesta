"""Que todo archivo compile y todo modulo se pueda importar.

Existe por un error concreto y evitable: un reemplazo por regex dejo
`clasificador.py` con un bloque huerfano, el archivo no compilaba, y llego a
main. Las pruebas del dominio pasaban porque el dominio no importa ese modulo,
asi que nada lo detecto.

Es la prueba mas barata del repo y la que cubre la falla mas tonta.
"""

from __future__ import annotations

import importlib
import py_compile
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent

# Modulos de la app que tienen que poder importarse sin efectos secundarios.
# No estan los scripts de una vez ni los que piden credenciales al importar.
MODULOS_APP = [
    'config',
    'db',
    'firefly',
    'telegram',
    'taxonomia',
    'clasificador',
    'publicador',
    'conciliador',
    'presupuestos',
    'ia',
    'interprete',
    'asesor',
    'bot',
    'demonio',
    'servicio',
    'verificar',
    'parsers.bancolombia_alertas',
    'parsers.extracto_tarjeta',
    'ingesta.graph',
    'finanzas.dominio.dinero',
    'finanzas.dominio.texto',
    'finanzas.dominio.fechas',
    'finanzas.dominio.conciliacion',
]


def archivos_python() -> list[Path]:
    """Todo .py del repo, menos lo generado."""
    saltar = {
        '.git',
        '__pycache__',
        '.pytest_cache',
        '.ruff_cache',
        'build',
        'dist',
        '.venv',
        'venv',
    }
    salida = []
    for p in RAIZ.rglob('*.py'):
        if any(parte in saltar or parte.endswith('.egg-info') for parte in p.parts):
            continue
        salida.append(p)
    return sorted(salida)


@pytest.mark.parametrize('ruta', archivos_python(), ids=lambda p: p.name)
def test_compila(ruta: Path):
    """Sintaxis valida. Barato y detecta el 100% de los archivos roturados por
    un reemplazo automatico mal hecho."""
    try:
        py_compile.compile(str(ruta), doraise=True, quiet=2)
    except py_compile.PyCompileError as ex:
        pytest.fail(f'{ruta.relative_to(RAIZ)} no compila:\n{ex}')


@pytest.mark.parametrize('modulo', MODULOS_APP)
def test_importa(modulo: str):
    """Importable de verdad, no solo sintacticamente valido.

    Un archivo puede compilar y aun asi fallar al importar: un nombre que no
    existe, un import circular, o codigo a nivel de modulo que estalla.
    """
    if str(RAIZ) not in sys.path:
        sys.path.insert(0, str(RAIZ))
    try:
        importlib.import_module(modulo)
    except Exception as ex:  # se quiere reportar cualquier fallo, sea cual sea
        pytest.fail(f'no pude importar {modulo}: {type(ex).__name__}: {ex}')


def test_el_dominio_no_importa_io():
    """La regla de la arquitectura, verificada.

    El dominio es logica pura: si empieza a importar firefly, db o telegram,
    deja de ser testeable sin levantar el mundo y volvemos al problema
    original. Esta prueba es la que mantiene la frontera.
    """
    prohibidos = (
        'firefly',
        'db',
        'telegram',
        'ia',
        'requests',
        'urllib',
        'sqlite3',
        'msal',
        'config',
    )
    dominio = RAIZ / 'src' / 'finanzas' / 'dominio'
    problemas = []
    for py in sorted(dominio.glob('*.py')):
        texto = py.read_text(encoding='utf-8')
        for linea in texto.splitlines():
            limpia = linea.strip()
            if not (limpia.startswith('import ') or limpia.startswith('from ')):
                continue
            for malo in prohibidos:
                if limpia.startswith((f'import {malo}', f'from {malo}')):
                    problemas.append(f'{py.name}: {limpia}')
    assert not problemas, (
        'el dominio tiene que ser logica pura, sin I/O:\n  ' + '\n  '.join(problemas)
    )
