"""Reglas de estructura que no se pueden romper sin darse cuenta.

No prueban comportamiento: prueban que las capas siguen separadas. Cada una
existe porque su violacion ya causo un bug de produccion, y una vez arreglada
la unica forma de que no vuelva es que falle una prueba.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
DOMINIO = RAIZ / 'src' / 'finanzas' / 'dominio'
ALMACEN = RAIZ / 'src' / 'finanzas' / 'adaptadores' / 'almacen.py'

# Los modulos planos de la aplicacion. No incluye herramientas de un solo uso
# ni los scripts de diagnostico, que si pueden consultar la base a mano.
APP = (
    'bot.py',
    'db.py',
    'demonio.py',
    'servicio.py',
    'publicador.py',
    'conciliador.py',
    'clasificador.py',
    'interprete.py',
    'asesor.py',
    'presupuestos.py',
    'taxonomia.py',
    'firefly.py',
    'telegram.py',
    'config.py',
    'ia.py',
    'verificar.py',
)

SQL = re.compile(r'\b(SELECT|INSERT|UPDATE|DELETE|CREATE)\s', re.IGNORECASE)


def _fuente(nombre: str) -> str:
    return (RAIZ / nombre).read_text(encoding='utf-8')


@pytest.mark.parametrize('nombre', APP)
def test_el_sql_solo_vive_en_el_almacen(nombre: str):
    """Antes habia 69 consultas repartidas en siete archivos, varias con la
    misma logica escrita distinto. Cambiar el esquema obligaba a cazarlas todas
    y siempre se escapaba una."""
    fuente = _fuente(nombre)
    if nombre == 'db.py':
        pytest.skip('capa de compatibilidad; delega en el almacen')
    culpables = [
        n
        for n, linea in enumerate(fuente.splitlines(), 1)
        if 'cx.execute' in linea or 'conn.execute' in linea
    ]
    assert not culpables, (
        f'{nombre} ejecuta SQL directo en las lineas {culpables}. '
        f'Agrega un metodo con nombre en Almacen y llamalo desde aca.'
    )


@pytest.mark.parametrize('nombre', APP)
def test_nadie_crea_tablas_en_tiempo_de_ejecucion(nombre: str):
    """bot.py creaba tres tablas con CREATE TABLE IF NOT EXISTS al vuelo, asi
    que esquema.sql no era la fuente de verdad y las pruebas contra una base
    limpia no veian el mismo esquema que produccion."""
    assert 'CREATE TABLE' not in _fuente(nombre).upper(), (
        f'{nombre} crea tablas a mano. El esquema va en esquema.sql.'
    )


def test_el_esquema_declara_todas_las_tablas_que_usa_el_almacen():
    """Cada tabla que el almacen nombra tiene que existir en esquema.sql."""
    esquema = (RAIZ / 'esquema.sql').read_text(encoding='utf-8')
    declaradas = set(
        re.findall(
            r'CREATE\s+(?:TABLE|VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_]+)',
            esquema,
            re.IGNORECASE,
        )
    )
    usadas = set(
        re.findall(
            r'(?:FROM|INTO|UPDATE)\s+([a-z_]+)', ALMACEN.read_text(encoding='utf-8')
        )
    )
    faltan = sorted(usadas - declaradas - {'sqlite_master'})
    assert not faltan, f'el almacen usa {faltan} y esquema.sql no las declara'


@pytest.mark.parametrize('archivo', sorted(DOMINIO.glob('*.py')), ids=lambda p: p.name)
def test_el_dominio_no_sabe_de_sqlite(archivo: Path):
    """El dominio es logica pura. Si importa la base, deja de poderse probar
    sin montar una — que es exactamente por lo que las reglas de conciliacion
    llevaban dos anos sin una sola prueba."""
    arbol = ast.parse(archivo.read_text(encoding='utf-8'))
    importados = {
        (n.module or '').split('.')[0]
        for n in ast.walk(arbol)
        if isinstance(n, ast.ImportFrom)
    } | {
        a.name.split('.')[0]
        for n in ast.walk(arbol)
        if isinstance(n, ast.Import)
        for a in n.names
    }
    prohibidos = importados & {
        'sqlite3',
        'db',
        'firefly',
        'telegram',
        'ia',
        'requests',
        'urllib',
        'msal',
        'config',
    }
    assert not prohibidos, f'{archivo.name} importa {prohibidos}'
