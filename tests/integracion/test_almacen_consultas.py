"""Las consultas del almacen que no cubre test_almacen.py.

test_almacen.py prueba las reglas del negocio (idempotencia, la cola, las
restricciones del esquema). Aqui van las consultas de lectura y las de estado:
poco glamurosas, pero son las que alimentan el resumen diario, la conciliacion
y el diagnostico, y varias tenian un filtro sutil que ya se equivoco una vez.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from finanzas.adaptadores.almacen import Almacen

ESQUEMA = Path(__file__).resolve().parent.parent.parent / 'esquema.sql'


@pytest.fixture
def alm() -> Almacen:
    cx = sqlite3.connect(':memory:')
    cx.row_factory = sqlite3.Row
    cx.execute('PRAGMA foreign_keys = ON')
    a = Almacen(cx)
    a.inicializar(ESQUEMA)
    return a


@pytest.fixture
def usuario(alm: Almacen) -> int:
    return alm.guardar_usuario('Juan', 'https://f.ejemplo', 'tok', '555')


@pytest.fixture
def correo(alm: Almacen, usuario: int) -> int:
    bid = alm.guardar_buzon(usuario, 'graph', 'juan@ejemplo.com')
    cid, _ = alm.guardar_correo(
        bid, '<m@banco>', 'banco', 'Alerta', '2026-09-01', 'Compraste ...'
    )
    return cid


def _mov(alm, usuario, correo, **extra):
    args = {
        'correo_id': correo,
        'usuario_id': usuario,
        'tipo': 'compra_tarjeta',
        'fecha': '2026-09-01',
        'valor': -50000.0,
        'external_id': f'bc-{extra.pop("eid", "a")}',
    }
    args.update(extra)
    pid, _ = alm.crear_pendiente(**args)
    alm.cx.commit()
    return pid


class TestAbrir:
    def test_abrir_crea_el_esquema_y_deja_las_claves_ajenas_activas(self, tmp_path):
        """Sin `PRAGMA foreign_keys = ON` SQLite acepta un correo_id que no
        existe: los huerfanos aparecen semanas despues, sin rastro."""
        a = Almacen.abrir(tmp_path / 'n.db', ESQUEMA)
        assert a.cx.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert a.contar_por_tabla('pendientes') == 0
        a.cerrar()

    def test_abrir_sin_esquema_no_crea_nada(self, tmp_path):
        (tmp_path / 'v.db').touch()
        a = Almacen.abrir(tmp_path / 'v.db')
        assert (
            a.cx.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
            == 0
        )
        a.cerrar()

    def test_las_filas_vienen_por_nombre(self, tmp_path):
        """Todo el codigo lee r['columna']. Sin row_factory eso es un TypeError."""
        a = Almacen.abrir(tmp_path / 'n.db', ESQUEMA)
        uid = a.guardar_usuario('Juan', 'u', 't')
        assert a.usuarios()[0]['id'] == uid
        a.cerrar()


class TestUsuarios:
    def test_guardar_dos_veces_actualiza_y_no_duplica(self, alm):
        a = alm.guardar_usuario('Juan', 'https://viejo', 'tok1')
        b = alm.guardar_usuario('Juan', 'https://nuevo', 'tok2')
        assert a == b
        assert alm.contar_por_tabla('usuarios') == 1
        assert alm.usuario_por_nombre('Juan')['firefly_url'] == 'https://nuevo'

    def test_guardar_sin_chat_no_borra_el_chat_que_ya_tenia(self, alm):
        """El demonio reasegura el usuario en cada arranque, leyendo del .env,
        donde el chat_id no siempre esta. Si lo pisara con NULL, el bot dejaria
        de saber a quien escribirle despues de cada reinicio."""
        alm.guardar_usuario('Juan', 'u', 't', '555')
        alm.guardar_usuario('Juan', 'u', 't', None)
        assert alm.usuario_por_nombre('Juan')['telegram_chat_id'] == '555'

    def test_quien_no_existe_da_none(self, alm):
        assert alm.usuario_por_nombre('Nadie') is None


class TestBuzones:
    def test_filtra_por_proveedor(self, alm, usuario):
        alm.guardar_buzon(usuario, 'graph', 'juan@hotmail.com')
        alm.guardar_buzon(usuario, 'imap', 'juan@gmail.com')
        assert len(alm.buzones()) == 2
        assert [b['direccion'] for b in alm.buzones('imap')] == ['juan@gmail.com']

    def test_marcar_sync_deja_la_marca_de_tiempo(self, alm, usuario):
        """`ultimo_sync` decide si la siguiente bajada trae 30 dias o solo 2."""
        bid = alm.guardar_buzon(usuario, 'graph', 'juan@ejemplo.com')
        assert alm.buzones()[0]['ultimo_sync'] is None
        alm.marcar_sync(bid)
        assert alm.buzones()[0]['ultimo_sync'] is not None

    def test_un_error_se_guarda_y_no_apaga_el_buzon(self, alm, usuario):
        """Un fallo de red no puede dejar el buzon inactivo: se reintenta en la
        pasada siguiente."""
        bid = alm.guardar_buzon(usuario, 'graph', 'juan@ejemplo.com')
        alm.marcar_error_buzon(bid, 'HTTP 503')
        b = alm.buzones()[0]
        assert b['ultimo_error'] == 'HTTP 503'
        assert b['activo'] == 1

    def test_el_primer_buzon_es_el_mas_viejo(self, alm, usuario):
        primero = alm.guardar_buzon(usuario, 'graph', 'a@ejemplo.com')
        alm.guardar_buzon(usuario, 'imap', 'b@ejemplo.com')
        assert alm.primer_buzon(usuario)['id'] == primero

    def test_sin_buzones_da_none(self, alm, usuario):
        assert alm.primer_buzon(usuario) is None


class TestCorreos:
    def test_solo_devuelve_los_no_procesados(self, alm, usuario):
        bid = alm.guardar_buzon(usuario, 'graph', 'j@ejemplo.com')
        a, _ = alm.guardar_correo(bid, '<1>', 'b', 's', 'f', 'uno')
        alm.guardar_correo(bid, '<2>', 'b', 's', 'f', 'dos')
        alm.cx.commit()
        assert alm.contar_correos_sin_procesar() == 2
        alm.marcar_correo_procesado(a)
        assert alm.contar_correos_sin_procesar() == 1
        assert [c['id'] for c in alm.correos_sin_procesar()] != [a]

    def test_el_limite_se_respeta(self, alm, usuario):
        bid = alm.guardar_buzon(usuario, 'graph', 'j@ejemplo.com')
        for i in range(5):
            alm.guardar_correo(bid, f'<{i}>', 'b', 's', 'f', 'x')
        alm.cx.commit()
        assert len(alm.correos_sin_procesar(limite=2)) == 2


class TestPorInstrumento:
    """Lo que cruza el conciliador contra el extracto. Los filtros de moneda y
    estado importan: las facturas de AMEX vienen en USD y las alertas en COP, y
    mezclarlas produjo 93 fantasmas falsos de 115."""

    def test_filtra_por_tarjeta_y_por_rango(self, alm, usuario, correo):
        _mov(alm, usuario, correo, eid='a', instrumento='7466', fecha='2026-08-15')
        _mov(alm, usuario, correo, eid='b', instrumento='7466', fecha='2026-09-20')
        _mov(alm, usuario, correo, eid='c', instrumento='2567', fecha='2026-08-15')
        r = alm.pendientes_del_instrumento('7466', '2026-08-01', '2026-08-31')
        assert len(r) == 1 and r[0]['fecha'] == '2026-08-15'

    def test_filtra_por_moneda(self, alm, usuario, correo):
        _mov(
            alm,
            usuario,
            correo,
            eid='a',
            instrumento='7466',
            fecha='2026-08-15',
            moneda='COP',
        )
        _mov(
            alm,
            usuario,
            correo,
            eid='b',
            instrumento='7466',
            fecha='2026-08-16',
            moneda='USD',
        )
        r = alm.pendientes_del_instrumento(
            '7466', '2026-08-01', '2026-08-31', moneda='COP'
        )
        assert [x['moneda'] for x in r] == ['COP']

    def test_filtra_por_estado(self, alm, usuario, correo):
        _mov(
            alm,
            usuario,
            correo,
            eid='a',
            instrumento='7466',
            fecha='2026-08-15',
            estado='publicado',
        )
        _mov(
            alm,
            usuario,
            correo,
            eid='b',
            instrumento='7466',
            fecha='2026-08-16',
            estado='descartado',
        )
        r = alm.pendientes_del_instrumento(
            '7466', '2026-08-01', '2026-08-31', estado='publicado'
        )
        assert len(r) == 1

    def test_sale_ordenado_por_fecha(self, alm, usuario, correo):
        """El emparejamiento recorre las dos listas en orden; desordenado, empareja
        el movimiento equivocado."""
        _mov(alm, usuario, correo, eid='c', instrumento='7466', fecha='2026-08-20')
        _mov(alm, usuario, correo, eid='a', instrumento='7466', fecha='2026-08-05')
        _mov(alm, usuario, correo, eid='b', instrumento='7466', fecha='2026-08-12')
        r = alm.pendientes_del_instrumento('7466', '2026-08-01', '2026-08-31')
        assert [x['fecha'] for x in r] == ['2026-08-05', '2026-08-12', '2026-08-20']


class TestLecturasDelResumen:
    def test_sin_confirmar_cuenta_y_suma(self, alm, usuario, correo):
        _mov(alm, usuario, correo, eid='a', estado='publicado', valor=-1000.0)
        _mov(alm, usuario, correo, eid='b', estado='publicado', valor=-2000.0)
        _mov(alm, usuario, correo, eid='c', estado='confirmado', valor=-9000.0)
        t = alm.total_sin_confirmar()
        assert (t['n'], t['t']) == (2, -3000.0)
        assert len(alm.sin_confirmar()) == 2

    def test_lo_ya_visto_no_cuenta_como_sin_confirmar(self, alm, usuario, correo):
        pid = _mov(alm, usuario, correo, eid='a', estado='publicado')
        alm.actualizar_pendiente(pid, visto_en='2026-09-01T10:00:00')
        assert alm.total_sin_confirmar()['n'] == 0

    def test_sin_confirmar_sale_lo_mas_nuevo_primero(self, alm, usuario, correo):
        _mov(alm, usuario, correo, eid='a', estado='publicado', fecha='2026-08-01')
        _mov(alm, usuario, correo, eid='b', estado='publicado', fecha='2026-09-01')
        assert alm.sin_confirmar()[0]['fecha'] == '2026-09-01'

    def test_el_resumen_agrupa_por_estado(self, alm, usuario, correo):
        _mov(alm, usuario, correo, eid='a', estado='publicado')
        _mov(alm, usuario, correo, eid='b', estado='publicado')
        filas = alm.resumen()
        assert filas and any(f['n'] == 2 for f in filas)

    def test_el_resumen_de_un_usuario_no_ve_al_otro(self, alm, usuario, correo):
        _mov(alm, usuario, correo, eid='a', estado='publicado')
        otro = alm.guardar_usuario('Novia', 'u', 't')
        assert alm.resumen(otro) == []

    def test_sin_nada_publicado_no_hay_sospechosos(self, alm):
        assert alm.contar_sospechosos() == 0


class TestEstadosYConteos:
    def test_por_estado_trae_solo_esos(self, alm, usuario, correo):
        _mov(alm, usuario, correo, eid='a', estado='nuevo')
        _mov(alm, usuario, correo, eid='b', estado='error')
        _mov(alm, usuario, correo, eid='c', estado='publicado')
        assert len(alm.pendientes_por_estado('nuevo', 'error')) == 2
        assert len(alm.pendientes_por_estado('publicado')) == 1

    def test_los_abiertos_de_un_chat_salen_por_el_chat_no_por_el_usuario(
        self, alm, usuario, correo
    ):
        """El bot solo conoce el chat_id; el usuario se resuelve con el join."""
        _mov(alm, usuario, correo, eid='a', estado='publicado', pregunta='categoria')
        assert len(alm.pendientes_abiertos_de_chat('555')) == 1
        assert alm.pendientes_abiertos_de_chat('otro') == []

    def test_borrar_reglas_devuelve_cuantas_borro(self, alm, usuario):
        alm.guardar_regla(usuario, 'EXITO', categoria='Mercado')
        alm.guardar_regla(usuario, 'UBER', categoria='Transporte')
        assert alm.contar_reglas() == 2
        assert alm.borrar_reglas() == 2
        assert alm.contar_reglas() == 0

    def test_reglas_por_origen(self, alm, usuario):
        alm.guardar_regla(usuario, 'A', categoria='X', origen='comercio')
        alm.guardar_regla(usuario, 'B', categoria='Y', origen='comercio')
        alm.guardar_regla(usuario, 'C', categoria='Z', origen='usuario')
        por = {r['origen']: r['n'] for r in alm.reglas_por_origen()}
        assert por == {'comercio': 2, 'usuario': 1}

    def test_reglas_con_categoria_ignora_las_vacias(self, alm, usuario):
        """El interprete compara el texto libre contra estas. Una regla sin
        categoria no aporta nada y ensucia el puntaje."""
        alm.guardar_regla(usuario, 'CON', categoria='Mercado')
        alm.guardar_regla(usuario, 'SIN', categoria='')
        assert [r['patron'] for r in alm.reglas_con_categoria(usuario)] == ['CON']


class TestPropuestas:
    def test_ida_y_vuelta(self, alm, usuario, correo):
        pid = _mov(alm, usuario, correo)
        alm.guardar_propuesta(pid, 'Gato', 'Esencial', 'TIERRAGRO', False)
        pr = alm.propuesta(pid)
        assert (pr['categoria'], pr['presupuesto'], pr['comercio']) == (
            'Gato',
            'Esencial',
            'TIERRAGRO',
        )
        assert pr['pedir_presupuesto'] == 0

    def test_el_booleano_se_guarda_como_entero(self, alm, usuario, correo):
        """SQLite no tiene booleano; si se guarda un bool crudo, leerlo con
        `if pr['pedir_presupuesto']` funciona por accidente y se rompe al
        comparar con 1."""
        pid = _mov(alm, usuario, correo)
        alm.guardar_propuesta(pid, 'Gato', None, None, True)
        assert alm.propuesta(pid)['pedir_presupuesto'] == 1

    def test_la_segunda_propuesta_reemplaza(self, alm, usuario, correo):
        pid = _mov(alm, usuario, correo)
        alm.guardar_propuesta(pid, 'Gato', None, None, False)
        alm.guardar_propuesta(pid, 'Mercado', None, None, False)
        assert alm.propuesta(pid)['categoria'] == 'Mercado'
        assert alm.contar_por_tabla('propuestas') == 1

    def test_sin_propuesta_da_none(self, alm, usuario, correo):
        assert alm.propuesta(_mov(alm, usuario, correo)) is None


class TestBitacora:
    def test_anota_el_exito(self, alm, usuario, correo):
        pid = _mov(alm, usuario, correo)
        alm.anotar(
            'crear',
            usuario_id=usuario,
            pendiente_id=pid,
            firefly_id='42',
            payload={'x': 1},
            ok=True,
        )
        r = alm.cx.execute('SELECT * FROM bitacora').fetchone()
        assert (r['accion'], r['firefly_id'], r['ok']) == ('crear', '42', 1)

    def test_el_payload_se_guarda_como_json_legible(self, alm, usuario, correo):
        """Es lo unico que queda cuando Firefly rechaza algo, asi que tiene que
        poder leerse tal cual para reproducir la llamada."""
        import json

        pid = _mov(alm, usuario, correo)
        alm.anotar(
            'crear',
            pendiente_id=pid,
            payload={'transactions': [{'amount': '1000.00'}]},
            ok=False,
        )
        guardado = alm.cx.execute('SELECT payload FROM bitacora').fetchone()[0]
        assert json.loads(guardado)['transactions'][0]['amount'] == '1000.00'

    def test_un_payload_que_no_es_json_no_tumba_la_anotacion(self, alm):
        """Anotar es lo ultimo que se hace en el camino del error: si falla,
        se pierde justo el rastro que se necesitaba."""
        alm.anotar('raro', payload=object(), ok=False)
        assert alm.contar_por_tabla('bitacora') == 1

    def test_sin_payload_queda_nulo(self, alm):
        alm.anotar('inicio', ok=True)
        assert alm.cx.execute('SELECT payload FROM bitacora').fetchone()[0] is None
