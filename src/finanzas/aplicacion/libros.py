"""Los libros de cada persona: a donde se publica y con que credencial.

Un libro es un destino de verdad -- el Firefly personal de alguien, el Actual de
un negocio -- con su propio token. La base guarda el NOMBRE de la variable de
entorno con el token, nunca el token: los secretos viven en el entorno del
contenedor, como hasta ahora.
"""

from __future__ import annotations

import json
from typing import Any

from finanzas import config
from finanzas.adaptadores import actual, firefly
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


def ajustes(libro: Any) -> dict[str, Any]:
    return json.loads(libro['ajustes']) if libro['ajustes'] else {}


def cliente(libro: Any) -> firefly.Cliente | actual.Cliente:
    """Con que hablarle a ese libro: un cliente de Firefly o uno de Actual."""
    token = config.get(libro['secreto_env'])
    if not token:
        raise LibroNoDisponible(
            f'falta {libro["secreto_env"]} en el entorno para el libro «{libro["nombre"]}»'
        )
    if libro['tipo'] == 'firefly':
        return firefly.Cliente(firefly.Conexion(libro['url'].rstrip('/'), token))
    if libro['tipo'] == 'actual':
        sync = ajustes(libro).get('sync_id')
        if not sync:
            raise LibroNoDisponible(f'el libro «{libro["nombre"]}» no tiene sync_id')
        return actual.Cliente(actual.Conexion(libro['url'].rstrip('/'), token, sync))
    raise LibroNoDisponible(f'no se publicar en un libro de tipo {libro["tipo"]}')


def firefly_de(alm: Almacen, usuario_id: int) -> Any | None:
    """El libro de Firefly de esa persona: el 'personal' si hay, si no el
    primero. None si no tiene ninguno."""
    de_firefly = [lb for lb in alm.libros_de(usuario_id) if lb['tipo'] == 'firefly']
    for lb in de_firefly:
        if lb['clave'] == 'personal':
            return lb
    return de_firefly[0] if de_firefly else None


def conexion_firefly_de(alm: Almacen, usuario_id: int) -> firefly.Conexion:
    """Con que Firefly atender a esa persona en el bot.

    Nunca cae al Firefly del entorno: si la persona no tiene uno, o falta su
    token, devuelve SIN_FIREFLY y cualquier llamada falla. Caer al del entorno
    era ver y editar la contabilidad de Juan.
    """
    lb = firefly_de(alm, usuario_id)
    if lb is None:
        return firefly.SIN_FIREFLY
    try:
        c = cliente(lb)
    except LibroNoDisponible:
        return firefly.SIN_FIREFLY
    return c.conexion if isinstance(c, firefly.Cliente) else firefly.SIN_FIREFLY
