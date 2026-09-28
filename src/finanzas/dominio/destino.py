"""A que libro va un movimiento. Logica pura, sin I/O.

La regla es de cero confianza, y la puso el usuario: un movimiento no llega a
NINGUN libro hasta saber con certeza a cual va. Mezclar la compra del estudio
con las cuentas personales -- o al reves -- descuadra las dos contabilidades, y
adivinar en silencio es peor que una pregunta mas.

Certeza hay en dos casos y solo en dos:

  unico     la persona tiene un solo libro activo: no hay a donde equivocarse
  usuario   la persona lo eligio con un toque

Todo lo demas -- la tarjeta que casi siempre es del estudio, lo que diga el
modelo, el historico -- es una SUGERENCIA: puede preseleccionar un boton, nunca
decidir. Por eso aqui no hay umbrales ni puntajes.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

UNICO = 'unico'
USUARIO = 'usuario'


@dataclass(frozen=True)
class Destino:
    libro_id: int
    por: str

    def como_campos(self) -> dict[str, int | str]:
        """Los campos de `pendientes` que lo guardan."""
        return {'libro_id': self.libro_id, 'destino_por': self.por}


def cierto(libros_activos: Iterable[int]) -> Destino | None:
    """El destino si es cierto sin preguntar, o None si hay que preguntar.

    >>> cierto([7])
    Destino(libro_id=7, por='unico')
    >>> cierto([7, 9]) is None
    True
    >>> cierto([]) is None
    True
    """
    ids = list(dict.fromkeys(libros_activos))
    return Destino(ids[0], UNICO) if len(ids) == 1 else None


def elegido(libro_id: int, libros_activos: Iterable[int]) -> Destino:
    """Lo que la persona eligio con un toque. Solo vale entre SUS libros: un
    boton viejo o forjado que apunte a un libro ajeno no puede elegirlo."""
    if libro_id not in set(libros_activos):
        raise ValueError(f'el libro {libro_id} no es de esta persona o no esta activo')
    return Destino(libro_id, USUARIO)
