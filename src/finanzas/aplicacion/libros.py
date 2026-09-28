"""Los libros de cada persona: a donde se publica y con que credencial.

Un libro es un destino de verdad -- el Firefly personal de alguien, el Actual de
un negocio -- con su propio token. La base guarda el NOMBRE de la variable de
entorno con el token, nunca el token: los secretos viven en el entorno del
contenedor, como hasta ahora.
"""

from __future__ import annotations

from typing import Any

from finanzas import config
from finanzas.adaptadores import firefly
from finanzas.adaptadores.almacen import Almacen
from finanzas.dominio import destino as _destino


class LibroNoDisponible(Exception):
    """El libro existe pero no se le puede hablar: tipo sin soporte todavia, o
    falta su token en el entorno."""


def asegurar_libro_del_entorno(alm: Almacen, usuario_id: int) -> int:
    """El Firefly de siempre, el de FIREFLY_URL y FIREFLY_TOKEN, como el libro
    'personal' de ese usuario. Idempotente: se llama en cada arranque."""
    url, _ = config.requerir('FIREFLY_URL', 'FIREFLY_TOKEN')
    return alm.guardar_libro(
        usuario_id,
        'personal',
        'Personal',
        'firefly',
        url.rstrip('/'),
        'FIREFLY_TOKEN',
        en_serio=True,
    )


def destino_seguro(alm: Almacen, usuario_id: int) -> dict[str, Any]:
    """Los campos del destino si es cierto sin preguntar; {} si no lo es.

    Un {} deja el movimiento sin libro, y sin libro no se publica: queda
    esperando a que la persona elija.
    """
    d = _destino.cierto(lib['id'] for lib in alm.libros_de(usuario_id))
    return d.como_campos() if d else {}


def cliente(libro: Any) -> firefly.Cliente:
    """Con que hablarle a ese libro."""
    if libro['tipo'] != 'firefly':
        raise LibroNoDisponible(
            f'el libro «{libro["nombre"]}» es {libro["tipo"]}: todavia no se publica ahi'
        )
    token = config.get(libro['secreto_env'])
    if not token:
        raise LibroNoDisponible(
            f'falta {libro["secreto_env"]} en el entorno para el libro «{libro["nombre"]}»'
        )
    return firefly.Cliente(firefly.Conexion(libro['url'].rstrip('/'), token))
