"""Carga a Postgres: el esquema `finanzas` que lee Metabase.

Ese esquema vive DENTRO de la base de Firefly, asi que aqui se escribe contra
produccion. De ahi las dos reglas de este modulo:

  1. Una tabla se carga ENTERA o no se toca. El camino viejo
     (metabase/cargar_csv.py) hacia TRUNCATE y despues mandaba los INSERT en
     lotes de 400, cada uno en su propia llamada HTTP. Si el lote 3 de 10
     fallaba, la tabla quedaba con 800 filas de 4000 y el dashboard mostraba
     numeros plausibles pero falsos. Aqui el TRUNCATE y el COPY van en la
     MISMA transaccion: o entra todo, o la tabla se queda como estaba.

  2. El DSN no se imprime nunca. Lleva la contrasena de la base de Firefly.

Sin POSTGRES_DSN el modulo no hace nada y el resto del servicio sigue igual:
el dashboard se queda viejo, que es como estaba antes de existir esto.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from finanzas import config

TIMEOUT = int(config.get('POSTGRES_TIMEOUT', '60'))


class PostgresError(Exception):
    """Algo fallo hablando con Postgres. Nunca lleva el DSN en el mensaje."""


def dsn() -> str | None:
    """El DSN configurado, o None si no hay. No se registra en ningun log."""
    return config.get('POSTGRES_DSN') or None


def disponible() -> bool:
    """Si hay a donde cargar. Todo el pipeline de carga cuelga de esto."""
    return bool(dsn())


def censurar(texto: str) -> str:
    """Tapa la contrasena del DSN en cualquier texto que vaya a salir.

    No es paranoia: el mensaje de error de una conexion fallida puede traer la
    cadena completa, y ese texto termina en el log del contenedor y, si la
    ingesta encadena tres fallos, en un mensaje de Telegram. La clave que
    lleva es la de la base de PRODUCCION de Firefly.

    Se tapa la clave, no el resto: saber a que host y con que usuario se
    intento entrar es justo lo que hace falta para arreglar el problema.
    """
    cadena = dsn()
    if not cadena:
        return texto
    # postgresql://usuario:CLAVE@host/base  ->  solo el trozo entre : y @
    resto = cadena.partition('://')[2]
    credencial = resto.partition('@')[0]
    clave = credencial.partition(':')[2]
    return texto.replace(clave, '***') if clave else texto


def _conectar():
    """La conexion, con el import adentro a proposito.

    psycopg solo hace falta cuando de verdad se va a cargar. Importandolo
    arriba, un contenedor sin la dependencia no podria ni arrancar el bot.
    """
    cadena = dsn()
    if not cadena:
        raise PostgresError('no hay POSTGRES_DSN configurado')
    try:
        import psycopg
    except ImportError as ex:  # pragma: no cover - depende del entorno
        raise PostgresError(f'falta psycopg: {ex}') from ex
    try:
        return psycopg.connect(cadena, connect_timeout=TIMEOUT)
    except Exception as ex:
        raise PostgresError(f'no pude conectar: {censurar(str(ex))[:200]}') from ex


def _identificador(tabla: str) -> Any:
    """`finanzas.factura` -> Identifier('finanzas', 'factura').

    Los nombres de tabla de este modulo son constantes del codigo, no entrada
    de usuario, pero se citan igual: es una linea y quita el tema de encima.
    """
    from psycopg import sql

    return sql.Identifier(*tabla.split('.'))


def cargar(tabla: str, columnas: Sequence[str], filas: Iterable[Sequence]) -> int:
    """Reemplaza el contenido de `tabla`. Devuelve cuantas filas quedaron.

    TRUNCATE + COPY en una sola transaccion: si algo revienta a mitad, psycopg
    hace rollback al salir del `with` y la tabla queda intacta.
    """
    from psycopg import sql

    n = 0
    try:
        with _conectar() as cx, cx.cursor() as cur:
            cur.execute(sql.SQL('TRUNCATE {}').format(_identificador(tabla)))
            copia = sql.SQL('COPY {} ({}) FROM STDIN').format(
                _identificador(tabla),
                sql.SQL(', ').join(sql.Identifier(c) for c in columnas),
            )
            with cur.copy(copia) as cp:
                for fila in filas:
                    cp.write_row(fila)
                    n += 1
    except PostgresError:
        raise
    except Exception as ex:
        raise PostgresError(
            f'fallo cargando {tabla}: {censurar(str(ex))[:300]}'
        ) from ex
    return n


def consultar(sql_texto: str) -> list[tuple]:
    """SELECT suelto, para medir. No escribe nada."""
    try:
        with _conectar() as cx, cx.cursor() as cur:
            cur.execute(sql_texto)
            return cur.fetchall()
    except PostgresError:
        raise
    except Exception as ex:
        raise PostgresError(f'fallo consultando: {censurar(str(ex))[:300]}') from ex


def conteo(tabla: str) -> int:
    """Cuantas filas hay hoy en la tabla. Para el ensayo, antes de tocar."""
    from psycopg import sql

    try:
        with _conectar() as cx, cx.cursor() as cur:
            cur.execute(
                sql.SQL('SELECT count(*) FROM {}').format(_identificador(tabla))
            )
            fila = cur.fetchone()
            return int(fila[0]) if fila else 0
    except PostgresError:
        raise
    except Exception as ex:
        raise PostgresError(
            f'fallo contando {tabla}: {censurar(str(ex))[:300]}'
        ) from ex
