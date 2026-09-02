"""Pruebas del almacen contra una base real, en memoria.

Son de integracion porque tocan SQLite de verdad, pero corren en milisegundos:
la base es `:memory:` y el esquema se crea en cada prueba. Asi se verifican las
cosas que solo se rompen contra un motor real — restricciones, ON CONFLICT,
CASCADE — sin depender de nada de afuera.
"""

from __future__ import annotations

import sqlite3

import pytest

from finanzas.adaptadores import db
from finanzas.adaptadores.almacen import Almacen

# La ruta la sabe db, que es su dueño: recalcularla aqui es lo que se
# rompio cuando el esquema paso a ser un dato del paquete.
ESQUEMA = db.ESQUEMA


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
    return alm.guardar_usuario('Juan', 'https://firefly.ejemplo', 'token', '999')


@pytest.fixture
def buzon(alm: Almacen, usuario: int) -> int:
    return alm.guardar_buzon(usuario, 'graph', 'juan@ejemplo.com')


@pytest.fixture
def correo(alm: Almacen, buzon: int) -> int:
    cid, _ = alm.guardar_correo(
        buzon,
        '<m1@banco>',
        'banco',
        'Alerta',
        '2026-09-01',
        'Compraste COP1.000,00 ...',
    )
    return cid


class TestEsquema:
    def test_crea_todas_las_tablas(self, alm: Almacen):
        tablas = {
            r[0]
            for r in alm.cx.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {
            'usuarios',
            'buzones',
            'correos_crudos',
            'pendientes',
            'reglas',
            'bitacora',
            'sugerencias',
            'propuestas',
            'preguntas_enviadas',
        } <= tablas

    def test_las_tres_tablas_de_conversacion_estan_en_el_esquema(self, alm: Almacen):
        """Antes bot.py las creaba a mano en tiempo de ejecucion, asi que el
        esquema no era la fuente de verdad."""
        for t in ('sugerencias', 'propuestas', 'preguntas_enviadas'):
            assert alm.contar_por_tabla(t) == 0

    def test_inicializar_es_idempotente(self, alm: Almacen):
        alm.inicializar(ESQUEMA)
        alm.inicializar(ESQUEMA)

    def test_crea_las_vistas(self, alm: Almacen):
        vistas = {
            r[0]
            for r in alm.cx.execute("SELECT name FROM sqlite_master WHERE type='view'")
        }
        assert vistas == {
            'v_por_preguntar',
            'v_abiertos',
            'v_sin_conciliar',
            'v_sospechosos',
            'v_catalogo_por_preguntar',
        }


class TestIdempotencia:
    def test_el_mismo_external_id_no_crea_dos(self, alm, usuario, correo):
        args = {
            'correo_id': correo,
            'usuario_id': usuario,
            'tipo': 'compra_tarjeta',
            'fecha': '2026-09-01',
            'valor': -1000.0,
            'external_id': 'bc-abc',
        }
        id1, nuevo1 = alm.crear_pendiente(**args)
        id2, nuevo2 = alm.crear_pendiente(**args)
        assert nuevo1 is True and nuevo2 is False
        assert id1 == id2, 'devuelve el que ya estaba, no crea otro'
        assert alm.contar_por_tabla('pendientes') == 1

    def test_el_mismo_correo_no_se_guarda_dos_veces(self, alm, buzon):
        a, n1 = alm.guardar_correo(buzon, '<x@banco>', 'b', 's', 'f', 'cuerpo')
        b, n2 = alm.guardar_correo(buzon, '<x@banco>', 'b', 's', 'f', 'otro')
        assert (n1, n2) == (True, False)
        assert a == b


class TestLaCola:
    def test_solo_se_publica_lo_que_tiene_cuenta(self, alm, usuario, correo):
        """pendientes_por_publicar exige cuenta_firefly: sin cuenta no se puede
        escribir en Firefly."""
        alm.crear_pendiente(
            correo_id=correo,
            usuario_id=usuario,
            tipo='c',
            fecha='2026-09-01',
            valor=-1.0,
            external_id='a',
            cuenta_firefly='VISA',
        )
        alm.crear_pendiente(
            correo_id=correo,
            usuario_id=usuario,
            tipo='c',
            fecha='2026-09-01',
            valor=-2.0,
            external_id='b',
        )
        alm.cx.commit()
        assert len(alm.pendientes_por_publicar()) == 1

    def test_no_repite_la_pregunta_el_mismo_dia(self, alm, usuario, correo):
        """La vista excluye lo preguntado hace menos de un dia. Sin eso el
        servicio repetia la misma pregunta cada 15 minutos."""
        pid, _ = alm.crear_pendiente(
            correo_id=correo,
            usuario_id=usuario,
            tipo='c',
            fecha='2026-09-01',
            valor=-1.0,
            external_id='a',
            pregunta='categoria',
        )
        alm.cx.commit()
        assert alm.contar_por_preguntar() == 1
        alm.marcar_preguntado(pid)
        assert alm.contar_por_preguntar() == 0

    def test_un_publicado_con_pregunta_abierta_si_aparece(self, alm, usuario, correo):
        """estado y pregunta son independientes: un movimiento puede estar en
        Firefly y con pregunta abierta al mismo tiempo, y ese es el caso normal."""
        alm.crear_pendiente(
            correo_id=correo,
            usuario_id=usuario,
            tipo='c',
            fecha='2026-09-01',
            valor=-1.0,
            external_id='a',
            estado='publicado',
            pregunta='categoria',
        )
        alm.cx.commit()
        assert alm.contar_por_preguntar() == 1

    def test_descartar_anteriores_a_la_marca(self, alm, usuario, correo):
        alm.crear_pendiente(
            correo_id=correo,
            usuario_id=usuario,
            tipo='c',
            fecha='2026-07-15',
            valor=-1.0,
            external_id='viejo',
            pregunta='categoria',
        )
        alm.crear_pendiente(
            correo_id=correo,
            usuario_id=usuario,
            tipo='c',
            fecha='2026-09-01',
            valor=-2.0,
            external_id='nuevo',
            pregunta='categoria',
        )
        alm.cx.commit()
        assert alm.descartar_anteriores_a('2026-09-01') == 1
        assert alm.contar_por_preguntar() == 1

    def test_descartar_sin_fecha(self, alm, usuario, correo):
        """Sin fecha no se puede ubicar en el tiempo ni emparejar con un
        extracto, y antes se quedaban reintentando en cada pasada."""
        alm.crear_pendiente(
            correo_id=correo,
            usuario_id=usuario,
            tipo='c',
            valor=-1.0,
            external_id='sf',
            pregunta='categoria',
        )
        alm.cx.commit()
        assert alm.descartar_sin_fecha() == 1
        assert alm.contar_por_preguntar() == 0

    def test_estado_invalido_se_rechaza(self, alm, usuario, correo):
        """El CHECK del esquema es la ultima defensa contra un typo."""
        with pytest.raises(sqlite3.IntegrityError):
            alm.crear_pendiente(
                correo_id=correo,
                usuario_id=usuario,
                tipo='c',
                fecha='2026-09-01',
                valor=-1.0,
                external_id='x',
                estado='inventado',
            )


class TestReglas:
    def test_guardar_es_upsert_y_no_borra_lo_que_ya_habia(self, alm, usuario):
        alm.guardar_regla(
            usuario, 'TIERRAGRO', categoria='Gato', presupuesto='Esencial'
        )
        # una segunda pasada sin presupuesto no debe borrarlo
        alm.guardar_regla(usuario, 'TIERRAGRO', categoria='Gato')
        r = alm.reglas(usuario)[0]
        assert r['categoria'] == 'Gato'
        assert r['presupuesto'] == 'Esencial', 'COALESCE conserva lo anterior'

    def test_aciertos_solo_sube(self, alm, usuario):
        """El contador de confirmaciones no puede bajar: es lo que decide si un
        comercio se publica sin preguntar."""
        alm.guardar_regla(usuario, 'EXITO', categoria='Mercado', aciertos=5)
        alm.guardar_regla(usuario, 'EXITO', categoria='Mercado', aciertos=1)
        assert alm.reglas(usuario)[0]['aciertos'] == 5

    def test_categorias_filtradas_por_direccion(self, alm, usuario):
        alm.guardar_regla(usuario, 'EXITO', categoria='Mercado', direccion='gasto')
        alm.guardar_regla(usuario, 'NOMINA', categoria='Salario', direccion='ingreso')
        gastos = [r['categoria'] for r in alm.categorias_usadas('gasto')]
        ingresos = [r['categoria'] for r in alm.categorias_usadas('ingreso')]
        assert gastos == ['Mercado']
        assert ingresos == ['Salario']

    def test_origen_invalido_se_rechaza(self, alm, usuario):
        with pytest.raises(sqlite3.IntegrityError):
            alm.guardar_regla(usuario, 'X', categoria='Y', origen='inventado')


class TestConversacion:
    def test_el_mensaje_apunta_al_movimiento_correcto(self, alm, usuario, correo):
        """Sin esto, contestar por texto resolvia la pregunta MAS RECIENTE en
        vez de la que se estaba contestando."""
        a, _ = alm.crear_pendiente(
            correo_id=correo,
            usuario_id=usuario,
            tipo='c',
            fecha='2026-09-01',
            valor=-1.0,
            external_id='a',
        )
        b, _ = alm.crear_pendiente(
            correo_id=correo,
            usuario_id=usuario,
            tipo='c',
            fecha='2026-09-01',
            valor=-2.0,
            external_id='b',
        )
        alm.cx.commit()
        alm.guardar_mensaje('999', 100, a)
        alm.guardar_mensaje('999', 101, b)
        assert alm.pendiente_de_mensaje('999', 100) == a
        assert alm.pendiente_de_mensaje('999', 101) == b
        assert alm.pendiente_de_mensaje('999', 999) is None

    def test_borrar_el_pendiente_arrastra_su_conversacion(self, alm, usuario, correo):
        pid, _ = alm.crear_pendiente(
            correo_id=correo,
            usuario_id=usuario,
            tipo='c',
            fecha='2026-09-01',
            valor=-1.0,
            external_id='a',
        )
        alm.cx.commit()
        alm.guardar_mensaje('999', 100, pid)
        alm.guardar_sugerencias(pid, ['Mercado', 'Gato'])
        alm.cx.execute('DELETE FROM pendientes WHERE id = ?', (pid,))
        alm.cx.commit()
        assert alm.contar_por_tabla('preguntas_enviadas') == 0
        assert alm.contar_por_tabla('sugerencias') == 0

    def test_sugerencias_conservan_el_orden(self, alm, usuario, correo):
        """El callback de Telegram aguanta 64 bytes, asi que viaja el INDICE de
        la opcion. Si el orden cambia, el usuario elige otra cosa."""
        pid, _ = alm.crear_pendiente(
            correo_id=correo,
            usuario_id=usuario,
            tipo='c',
            fecha='2026-09-01',
            valor=-1.0,
            external_id='a',
        )
        alm.cx.commit()
        opciones = ['Mercado', 'Gato', 'Restaurante', 'Salidas']
        alm.guardar_sugerencias(pid, opciones)
        assert alm.sugerencias(pid) == opciones


class TestSeguridad:
    @pytest.mark.parametrize(
        'malo', ['pendientes; DROP TABLE usuarios', 'no_existe', '', 'sqlite_master']
    )
    def test_contar_por_tabla_valida_contra_lista_blanca(self, alm, malo):
        """El nombre de una tabla no se puede parametrizar en SQL, hay que
        interpolarlo. Por eso va contra lista blanca."""
        with pytest.raises(ValueError):
            alm.contar_por_tabla(malo)


class TestVincularChat:
    def test_el_primer_start_ata_el_chat(self, alm):
        uid = alm.guardar_usuario('Juan', 'url', 'tok')
        assert alm.vincular_chat('12345') == uid
        assert alm.usuario_por_nombre('Juan')['telegram_chat_id'] == '12345'

    def test_no_roba_el_chat_de_otro(self, alm):
        alm.guardar_usuario('Juan', 'url', 'tok', '111')
        assert alm.vincular_chat('222') is None, 'ya todos tienen chat'
        assert alm.usuario_por_nombre('Juan')['telegram_chat_id'] == '111'
