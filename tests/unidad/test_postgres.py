"""El adaptador de Postgres, sin Postgres.

Lo que de verdad importa aqui no es que sepa hablar SQL —eso lo prueba la
carga real— sino las dos promesas que hace el modulo:

  1. Sin POSTGRES_DSN no revienta nada: el servicio sigue igual y el dashboard
     simplemente se queda viejo.
  2. El DSN, que lleva la clave de la base de Firefly, no se filtra en los
     mensajes de error.
"""

from __future__ import annotations

import psycopg
import pytest

from finanzas.adaptadores import postgres
from finanzas.aplicacion import facturas

CLAVE = 'clave-secretisima-de-produccion'
DSN = f'postgresql://mega:{CLAVE}@postgress:5432/firefly'


@pytest.fixture
def sin_dsn(monkeypatch):
    monkeypatch.setenv('POSTGRES_DSN', '')
    monkeypatch.setattr(
        postgres.config, 'get', lambda k, d=None: '' if k == 'POSTGRES_DSN' else d
    )


def test_sin_dsn_no_esta_disponible(sin_dsn):
    assert postgres.disponible() is False
    assert postgres.dsn() is None


def test_sin_dsn_cargar_avisa_en_vez_de_reventar_raro(sin_dsn):
    with pytest.raises(postgres.PostgresError, match='no hay POSTGRES_DSN'):
        postgres.cargar('finanzas.producto', ('nit',), [])


def test_sin_dsn_el_pipeline_se_salta_y_no_rompe(sin_dsn):
    """La promesa 1: el resto del servicio no se entera."""
    assert facturas.cargar_a_postgres(None) == {'omitido': 'sin POSTGRES_DSN'}


def test_el_dsn_no_viaja_en_el_error(monkeypatch):
    """La promesa 2: un fallo de conexion no puede escupir la contrasena.

    Sale por el log del contenedor y, tras tres fallos seguidos, por Telegram.
    """
    monkeypatch.setattr(
        postgres.config, 'get', lambda k, d=None: DSN if k == 'POSTGRES_DSN' else d
    )

    def explota(*a, **k):
        raise RuntimeError(f'no me pude conectar usando {DSN}')

    monkeypatch.setattr(psycopg, 'connect', explota)
    with pytest.raises(postgres.PostgresError) as ex:
        postgres.cargar('finanzas.producto', ('nit',), [])
    assert CLAVE not in str(ex.value)


def test_el_identificador_separa_esquema_y_tabla():
    rendido = postgres._identificador('finanzas.producto').as_string(None)
    assert rendido == '"finanzas"."producto"'


@pytest.mark.parametrize('conjunto', facturas.CONJUNTOS, ids=lambda c: c[1])
def test_cada_conjunto_produce_tantos_valores_como_columnas(conjunto):
    """El guardian que importa: CSV y Postgres comparten CONJUNTOS, asi que si
    alguien agrega una columna en un lado y no en el otro, el COPY mandaria un
    numero de campos distinto al que la tabla espera y la carga entera falla.
    """
    _nombre, _tabla, columnas, _consulta, adaptar = conjunto
    fila = adaptar(dict.fromkeys(columnas, ''))
    assert len(fila) == len(columnas)
