"""Una sola forma de escribir al log.

Antes `servicio.py` tenia su propio `log()` con marca de tiempo y todo lo demas
usaba `print` pelado, asi que en los logs del contenedor la mitad de las lineas
tenia hora y la otra mitad no. Cuando algo fallaba de madrugada no se podia
saber a que hora.

Es deliberadamente diminuto: escribe a stdout con `flush=True`, que es lo que
Docker recoge. No hay niveles ni configuracion porque no hacen falta todavia;
lo que hacia falta era que fuera UNO.
"""

from __future__ import annotations

import sys
from typing import TextIO

from finanzas.dominio import fechas


def log(donde: str, mensaje: str, salida: TextIO | None = None) -> None:
    """`donde` es de donde viene: 'ingesta', 'bot', 'conciliar'.

    La hora es de Bogota, no del TZ del proceso: el contenedor puede arrancar
    en UTC y entonces las horas del log no coinciden con las del extracto.
    """
    ts = fechas.ahora().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{ts}] {donde:9} {mensaje}', flush=True, file=salida or sys.stdout)


def aviso(mensaje: str) -> None:
    """Algo que el usuario tiene que ver, sin la ceremonia de la marca de
    tiempo. Para el avance de un comando que el usuario esta mirando correr."""
    print(mensaje, flush=True)
