"""Los guardianes de pruebas/ tambien corren con `pytest tests`.

`pruebas/test_config.py` y `pruebas/test_alertas.py` son scripts que se corren
solos y que el CI invoca aparte. Eso los dejaba fuera del bucle rapido: existian
y funcionaban, pero editando el codigo no avisaban nada hasta llegar al CI.

Y avisan de cosas que importan. Al anotar los tipos de `telegram.py` se me fue
un `config.get('TELEGRAM_BOT_TOKEN')` cuando la variable de verdad es
`TELEGRAM_TOKEN`: el bot se hubiera quedado mudo en el contenedor, sin que
ningun import ni ninguna prueba de `tests/` lo notara. `test_config.py` lo
detecta, pero yo no lo estaba corriendo. Ahora corre siempre.

Se envuelven en vez de moverse porque el CI y el README los invocan por su ruta.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
for ruta in (RAIZ, RAIZ / 'pruebas'):
    if str(ruta) not in sys.path:
        sys.path.insert(0, str(ruta))


def test_toda_variable_que_el_codigo_lee_llega_al_contenedor():
    """stack.portainer.yml pasa SOLO lo que lista en `environment:`. Si el
    codigo lee una variable que no esta ahi, en el contenedor vale None y el
    sintoma no apunta a la causa: GEMINI_API_KEY estuvo puesta en Portainer y
    nunca llego, y desde afuera se veia como «sin API key»."""
    import test_config

    test_config.test_stack_pasa_todo()


def test_el_ejemplo_documenta_lo_que_el_codigo_lee():
    import test_config

    test_config.test_ejemplo_documenta_lo_que_el_codigo_lee()


@pytest.mark.parametrize(
    'nombre',
    [
        'test_parse_monto',
        'test_fecha_hora_invertidas',
        'test_compra_rechazada_se_descarta',
        'test_signo',
    ],
)
def test_el_parser_de_alertas(nombre: str):
    """Las unitarias del parser. `test_archivo_completo` no se incluye: depende
    de los 863 correos reales, que no estan en el repo, y el CI lo corre aparte
    con `python pruebas/test_alertas.py`."""
    import test_alertas

    getattr(test_alertas, nombre)()
