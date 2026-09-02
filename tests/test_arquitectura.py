"""Reglas de estructura que no se pueden romper sin darse cuenta.

No prueban comportamiento: prueban que las capas siguen separadas. Cada una
existe porque su violacion ya causo un bug de produccion, y una vez arreglada
la unica forma de que no vuelva es que falle una prueba.

Las listas de archivos se descubren recorriendo el paquete. Cuando estaban
escritas a mano quedaron obsoletas el dia que los modulos cambiaron de capa, y
las pruebas pasaron a verificar archivos que ya no existian.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from finanzas.adaptadores import db

RAIZ = Path(__file__).resolve().parent.parent
PAQUETE = RAIZ / 'src' / 'finanzas'
ALMACEN = PAQUETE / 'adaptadores' / 'almacen.py'

# Las cuatro capas, de abajo hacia arriba. Solo se puede depender hacia abajo.
CAPAS = ('dominio', 'adaptadores', 'aplicacion', 'entrada')

# Los cuatro modulos que hablan con el mundo. Son contratos: todo lo demas
# depende de la forma exacta de lo que devuelven, y equivocarse ahi no da error
# — da None en silencio. De ahi que se les exija firma completa.
FRONTERA = (
    PAQUETE / 'config.py',
    PAQUETE / 'adaptadores' / 'firefly.py',
    PAQUETE / 'adaptadores' / 'telegram.py',
    PAQUETE / 'adaptadores' / 'ia.py',
)


def _modulos() -> list[Path]:
    return sorted(
        p
        for p in PAQUETE.rglob('*.py')
        if '__pycache__' not in p.parts and p.name != '__init__.py'
    )


def _id(p: Path) -> str:
    return str(p.relative_to(PAQUETE)).replace('\\', '/')


def _importados(archivo: Path) -> set[str]:
    arbol = ast.parse(archivo.read_text(encoding='utf-8'))
    fuera = set()
    for n in ast.walk(arbol):
        if isinstance(n, ast.Import):
            fuera |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            fuera.add(n.module)
    return fuera


@pytest.mark.parametrize('archivo', _modulos(), ids=_id)
def test_el_sql_solo_vive_en_el_almacen(archivo: Path):
    """Antes habia 69 consultas repartidas en siete archivos, varias con la
    misma logica escrita distinto. Cambiar el esquema obligaba a cazarlas todas
    y siempre se escapaba una."""
    if archivo == ALMACEN:
        return
    if archivo.name == 'db.py':
        pytest.skip('capa de compatibilidad; delega en el almacen')
    culpables = [
        n
        for n, linea in enumerate(archivo.read_text(encoding='utf-8').splitlines(), 1)
        if 'cx.execute' in linea or 'conn.execute' in linea
    ]
    assert not culpables, (
        f'{_id(archivo)} ejecuta SQL directo en las lineas {culpables}. '
        f'Agrega un metodo con nombre en Almacen y llamalo desde aca.'
    )


@pytest.mark.parametrize('archivo', _modulos(), ids=_id)
def test_nadie_crea_tablas_en_tiempo_de_ejecucion(archivo: Path):
    """bot.py creaba tres tablas con CREATE TABLE IF NOT EXISTS al vuelo, asi
    que esquema.sql no era la fuente de verdad y las pruebas contra una base
    limpia no veian el mismo esquema que produccion."""
    assert 'CREATE TABLE' not in archivo.read_text(encoding='utf-8').upper(), (
        f'{_id(archivo)} crea tablas a mano. El esquema va en esquema.sql.'
    )


def test_el_esquema_declara_todas_las_tablas_que_usa_el_almacen():
    """Cada tabla que el almacen nombra tiene que existir en esquema.sql."""
    esquema = Path(db.ESQUEMA).read_text(encoding='utf-8')
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


@pytest.mark.parametrize(
    'archivo', sorted((PAQUETE / 'dominio').glob('*.py')), ids=lambda p: p.name
)
def test_el_dominio_no_sabe_de_sqlite(archivo: Path):
    """El dominio es logica pura. Si importa la base, deja de poderse probar
    sin montar una — que es exactamente por lo que las reglas de conciliacion
    llevaban dos anos sin una sola prueba."""
    prohibidos = {i.split('.')[0] for i in _importados(archivo)} & {
        'sqlite3',
        'requests',
        'urllib',
        'msal',
    }
    de_arriba = {
        i
        for i in _importados(archivo)
        if i.startswith(
            (
                'finanzas.adaptadores',
                'finanzas.aplicacion',
                'finanzas.entrada',
                'finanzas.config',
            )
        )
    }
    assert not prohibidos, f'{archivo.name} importa {prohibidos}'
    assert not de_arriba, f'{archivo.name} importa de una capa de arriba: {de_arriba}'


@pytest.mark.parametrize('archivo', _modulos(), ids=_id)
def test_las_flechas_solo_bajan(archivo: Path):
    """Una capa puede depender de las de abajo, nunca de las de arriba. Sin esta
    regla el grafo se vuelve una maraña y nada se puede probar por separado."""
    partes = archivo.relative_to(PAQUETE).parts
    if len(partes) < 2 or partes[0] not in CAPAS:
        return  # config.py, cli.py y registro.py son transversales
    nivel = CAPAS.index(partes[0])
    arriba = set(CAPAS[nivel + 1 :])
    violaciones = sorted(
        i
        for i in _importados(archivo)
        if i.startswith('finanzas.') and i.split('.')[1] in arriba
    )
    assert not violaciones, (
        f'{_id(archivo)} esta en «{partes[0]}» e importa de arriba: '
        f'{", ".join(violaciones)}'
    )


@pytest.mark.parametrize('archivo', FRONTERA, ids=_id)
def test_la_frontera_esta_anotada(archivo: Path):
    """El bug: leer lo que devuelve `telegram.enviar` como
    `r['result']['message_id']` cuando `call` ya quito el sobre. Y el otro, al
    anotar ese mismo modulo: `config.requerir('X')['X']`, cuando requerir
    devuelve una lista. Los dos se ven de una si la firma esta escrita."""
    arbol = ast.parse(archivo.read_text(encoding='utf-8'))
    sin = []
    for n in arbol.body:
        if not isinstance(n, ast.FunctionDef):
            continue
        faltan = [a.arg for a in n.args.args if a.annotation is None]
        if n.returns is None:
            faltan.append('-> retorno')
        if faltan:
            sin.append(f'{n.name}({", ".join(faltan)})')
    assert not sin, f'{_id(archivo)} sin anotar: {sin}'


def test_no_quedan_remiendos_de_sys_path():
    """Cada `sys.path.insert` era el sintoma de que los modulos eran archivos
    sueltos y no un paquete. El paquete se instala; si vuelve a aparecer uno, es
    que algo se salio de src/."""
    culpables = [
        f'{_id(p)}:{n}'
        for p in _modulos()
        for n, linea in enumerate(p.read_text(encoding='utf-8').splitlines(), 1)
        if 'sys.path.insert' in linea
    ]
    assert not culpables, f'sys.path remendado en {culpables}'
