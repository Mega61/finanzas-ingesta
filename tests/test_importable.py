"""Que todo archivo compile y todo modulo del paquete se pueda importar.

Existe por un error concreto y evitable: un reemplazo por regex dejo
`clasificador.py` con un bloque huerfano, el archivo no compilaba, y llego a
main. Las pruebas del dominio pasaban porque el dominio no importa ese modulo,
asi que nada lo detecto.

Es la prueba mas barata del repo y la que cubre la falla mas tonta.

La lista de modulos NO se escribe a mano: se descubre recorriendo el paquete.
Cuando era una lista quedo obsoleta el dia que los modulos cambiaron de sitio,
y de todas formas un modulo nuevo no entraba solo.
"""

from __future__ import annotations

import importlib
import py_compile
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
PAQUETE = RAIZ / 'src' / 'finanzas'

SALTAR_CARPETAS = {
    '.git',
    '__pycache__',
    '.pytest_cache',
    '.ruff_cache',
    'build',
    'dist',
    '.venv',
    'venv',
}


def archivos_python() -> list[Path]:
    """Todo .py del repo, menos lo generado."""
    return sorted(
        p
        for p in RAIZ.rglob('*.py')
        if not any(
            parte in SALTAR_CARPETAS or parte.endswith('.egg-info') for parte in p.parts
        )
    )


def modulos_del_paquete() -> list[str]:
    """Cada modulo de finanzas, como nombre con puntos."""
    salida = []
    for p in sorted(PAQUETE.rglob('*.py')):
        if '__pycache__' in p.parts:
            continue
        partes = list(p.relative_to(PAQUETE.parent).with_suffix('').parts)
        if partes[-1] == '__init__':
            partes.pop()
        salida.append('.'.join(partes))
    return salida


@pytest.mark.parametrize('ruta', archivos_python(), ids=lambda p: p.name)
def test_compila(ruta: Path):
    """Sintaxis valida. Barato, y detecta el 100% de los archivos roturados por
    un reemplazo automatico mal hecho."""
    try:
        py_compile.compile(str(ruta), doraise=True, quiet=2)
    except py_compile.PyCompileError as ex:
        pytest.fail(f'{ruta.relative_to(RAIZ)} no compila:\n{ex}')


@pytest.mark.parametrize('modulo', modulos_del_paquete())
def test_importa(modulo: str):
    """Importable de verdad, no solo sintacticamente valido.

    Un archivo puede compilar y aun asi fallar al importar: un nombre que no
    existe, un import circular, o codigo a nivel de modulo que estalla. Los tres
    han pasado.
    """
    try:
        importlib.import_module(modulo)
    except Exception as ex:  # se quiere reportar cualquier fallo, sea cual sea
        pytest.fail(f'no pude importar {modulo}: {type(ex).__name__}: {ex}')


def test_descubre_todas_las_capas():
    """Si el descubrimiento se rompe, `test_importa` pasa sin importar nada y
    la prueba se vuelve decorativa."""
    modulos = modulos_del_paquete()
    assert len(modulos) > 20, f'solo encontre {len(modulos)} modulos'
    for capa in ('dominio', 'adaptadores', 'aplicacion', 'entrada', 'parsers'):
        assert any(f'.{capa}.' in m for m in modulos), f'no encontre {capa}'


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
        'finanzas.adaptadores',
        'finanzas.aplicacion',
        'finanzas.entrada',
        'finanzas.config',
    )
    problemas = []
    for py in sorted((PAQUETE / 'dominio').glob('*.py')):
        for linea in py.read_text(encoding='utf-8').splitlines():
            limpia = linea.strip()
            if not limpia.startswith(('import ', 'from ')):
                continue
            for malo in prohibidos:
                if limpia.startswith((f'import {malo}', f'from {malo}')):
                    problemas.append(f'{py.name}: {limpia}')
    assert not problemas, (
        'el dominio tiene que ser logica pura, sin I/O:\n  ' + '\n  '.join(problemas)
    )
