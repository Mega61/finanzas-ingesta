"""Que toda variable que el codigo lee de verdad llegue al contenedor.

Existe por un bug concreto: se agrego GEMINI_API_KEY a .env.ejemplo y al codigo,
pero no a `stack.portainer.yml`. El stack pasa SOLO las variables listadas
explicitamente en `environment:`, asi que la key estaba puesta en Portainer y
nunca llegaba al contenedor. Desde afuera se veia como «sin API key», sin
ninguna pista de por que.

Explicitar las variables es lo correcto por seguridad (no se filtra el entorno
del host al contenedor), pero entonces la lista hay que mantenerla, y eso es lo
que verifica esto.

Antes tenia una lista de catorce modulos escrita a mano. La lista quedo
obsoleta el dia que los modulos se movieron a src/, y de todas formas un modulo
nuevo que leyera una variable nueva no se revisaba. Ahora escanea el arbol.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
DESPLIEGUE = RAIZ / 'despliegue'

# Estas NO tienen que estar en el stack, y por que:
EXENTAS = {
    # las pone el Dockerfile como ENV, con el commit del build
    'GIT_SHA',
    'BUILD_FECHA',
    # tienen default en el codigo y no hay razon para tocarlas por stack
    'FIREFLY_UA',
    'FIREFLY_TIMEOUT',
    'GEMINI_TIMEOUT',
    # nombre viejo, se lee por compatibilidad con .bancolombia.env
    'CLAVE',
    # las declara el stack aparte, no dentro de environment
    'FINANZAS_DATOS',
    # las tres carpetas: solo se fijan para mover la instalacion de sitio
    'FINANZAS_PROYECTO',
    'FINANZAS_PERSONAL',
    # Estas dos las lee el GENERADOR del bloque de Portainer, no el servicio.
    # RED_FIREFLY es la red del stack (va en `networks:`, no en environment) y
    # FIREFLY_URL_CONTENEDOR es lo que acaba siendo FIREFLY_URL alla dentro.
    'RED_FIREFLY',
    'FIREFLY_URL_CONTENEDOR',
}

# Lo que el stack puede pasar sin que nadie lo lea con config.get: TZ la usa
# el contenedor, y FIREFLY_URL/FINANZAS_DATOS se leen por otras vias.
TOLERADAS_EN_EL_STACK = {'TZ', 'FINANZAS_DATOS', 'FIREFLY_URL'}


def _fuentes() -> str:
    """Todo el codigo que puede leer configuracion."""
    archivos = list((RAIZ / 'src').rglob('*.py')) + list(
        (RAIZ / 'herramientas').glob('*.py')
    )
    return '\n'.join(p.read_text(encoding='utf-8') for p in archivos)


def variables_del_codigo() -> set[str]:
    """Las que el codigo lee con config.get(...) o config.requerir(...)."""
    todo = _fuentes()
    leidas = set(re.findall(r"""config\.get\(\s*['"]([A-Z][A-Z0-9_]+)['"]""", todo))
    leidas |= set(re.findall(r"""_cfg\.get\(\s*['"]([A-Z][A-Z0-9_]+)['"]""", todo))
    # requerir() acepta varias de una: requerir('A', 'B')
    for grupo in re.findall(r'config\.requerir\(([^)]*)\)', todo):
        leidas |= set(re.findall(r"""['"]([A-Z][A-Z0-9_]+)['"]""", grupo))
    return leidas


def variables_documentadas() -> set[str]:
    texto = (DESPLIEGUE / '.env.ejemplo').read_text(encoding='utf-8')
    return set(re.findall(r'^([A-Z][A-Z0-9_]+)=', texto, re.M))


def variables_del_stack() -> set[str]:
    """Las claves del bloque `environment:` del stack.

    Se saca a mano y no con pyyaml para no meter una dependencia solo para
    esto: en ese bloque las claves son `NOMBRE:` con seis espacios.
    """
    texto = (DESPLIEGUE / 'stack.portainer.yml').read_text(encoding='utf-8')
    dentro, salida = False, set()
    for linea in texto.split('\n'):
        if re.match(r'^\s{4}environment:', linea):
            dentro = True
            continue
        if dentro:
            if re.match(r'^\s{0,4}\S', linea):
                break
            m = re.match(r'^\s{6}([A-Z][A-Z0-9_]+):', linea)
            if m:
                salida.add(m.group(1))
    return salida


def test_encuentra_los_tres_archivos():
    """Si una ruta se rompe, las otras pruebas comparan conjuntos vacios y
    pasan sin verificar nada."""
    assert (DESPLIEGUE / '.env.ejemplo').exists()
    assert (DESPLIEGUE / 'stack.portainer.yml').exists()
    assert variables_del_codigo(), 'no encontre ni una config.get() en el codigo'


def test_el_stack_le_pasa_al_contenedor_todo_lo_que_el_codigo_lee():
    faltan = sorted(
        (variables_del_codigo() | variables_documentadas())
        - variables_del_stack()
        - EXENTAS
    )
    assert not faltan, (
        f'{len(faltan)} variables no llegan al contenedor: '
        f'{", ".join(faltan)}.\nAgregalas al bloque environment: de '
        f'despliegue/stack.portainer.yml, o a EXENTAS aqui si de verdad no '
        f'hacen falta.'
    )


def test_el_stack_no_acumula_variables_que_nadie_lee():
    """Una variable en el stack que ningun codigo lee es basura que confunde:
    parece configuracion viva y no hace nada."""
    sobran = sorted(
        variables_del_stack()
        - variables_del_codigo()
        - variables_documentadas()
        - TOLERADAS_EN_EL_STACK
    )
    assert not sobran, f'el stack pasa {len(sobran)} que nadie lee: {", ".join(sobran)}'


def test_el_ejemplo_documenta_lo_que_el_codigo_lee():
    """.env.ejemplo es lo que alguien copia para arrancar. Si el codigo lee algo
    que no esta ahi, arranca sin ello y el sintoma no apunta a la causa."""
    sin_documentar = sorted(variables_del_codigo() - variables_documentadas() - EXENTAS)
    assert not sin_documentar, (
        f'{len(sin_documentar)} variables que el codigo lee no estan en '
        f'despliegue/.env.ejemplo: {", ".join(sin_documentar)}'
    )


@pytest.mark.parametrize(
    'clave', ['FIREFLY_TOKEN', 'TELEGRAM_TOKEN', 'GEMINI_API_KEY', 'GRAPH_CLIENT_ID']
)
def test_los_secretos_de_verdad_estan_en_el_stack(clave: str):
    """Los cuatro sin los que no arranca nada. Se listan aparte porque el
    conjunto de arriba pasa igual si el codigo deja de leer uno por un typo:
    con GEMINI_API_KEY paso exactamente eso."""
    assert clave in variables_del_stack()


class TestElGeneradorCubreElStack:
    """`herramientas/generar_variables.py` produce el bloque que se pega en
    Portainer. Tenia su propia lista escrita a mano y le faltaban once
    variables, GEMINI_API_KEY entre ellas.

    Eso no se noto hasta que hubo que recrear el stack desde cero y el archivo
    generado estaba incompleto. Ahora deriva del stack, y esto lo verifica.
    """

    @staticmethod
    def _generador():
        ruta = RAIZ / 'herramientas' / 'generar_variables.py'
        spec = importlib.util.spec_from_file_location('gv', ruta)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_conoce_todas_las_variables_del_stack(self):
        gv = self._generador()
        del_stack = set(gv.variables_del_stack())
        assert del_stack == variables_del_stack(), (
            'el generador y esta prueba leen el mismo bloque; si difieren, uno '
            'de los dos parsers esta mal'
        )

    def test_no_deja_fuera_ninguna_que_el_stack_exija(self):
        """Las marcadas con `:?` en el stack: sin ellas el contenedor no
        arranca."""
        gv = self._generador()
        exigidas = set(
            re.findall(
                r'\$\{([A-Z][A-Z0-9_]+):\?',
                (DESPLIEGUE / 'stack.portainer.yml').read_text(encoding='utf-8'),
            )
        )
        sabe_llenar = set(gv.SOLO_DESPLIEGUE) | set(gv.ESPECIALES) | exigidas
        assert exigidas <= sabe_llenar

    def test_firefly_url_del_contenedor_no_sale_del_env_local(self):
        """En Windows FIREFLY_URL es la URL publica, porque no se resuelve el
        nombre de un contenedor. Dentro de Docker tiene que ser el nombre del
        contenedor, o el trafico sale a internet y vuelve por el proxy."""
        gv = self._generador()
        assert 'FIREFLY_URL' in gv.SOLO_DESPLIEGUE
        assert 'RED_FIREFLY' in gv.SOLO_DESPLIEGUE

    def test_no_imprime_las_llaves_de_api(self):
        """La herramienta que existe para manejar secretos escupio la
        GEMINI_API_KEY completa al terminal, porque su lista de que es secreto
        no incluia 'KEY'."""
        gv = self._generador()
        for palabra in ('TOKEN', 'PASSWORD', 'KEY', 'API', 'CLAVE', 'SECRET'):
            assert palabra in gv.SECRETO

    def test_productos_csv_se_busca_en_la_raiz_del_proyecto(self):
        """Al mover las herramientas a su carpeta, la ruta relativa quedo
        apuntando a herramientas/productos.csv, que no existe."""
        codigo = (RAIZ / 'herramientas' / 'generar_variables.py').read_text(
            encoding='utf-8'
        )
        assert "ruta_proyecto('productos.csv')" in codigo
