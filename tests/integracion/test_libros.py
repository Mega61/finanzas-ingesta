"""Los libros, y la regla de cero confianza sobre el destino.

La regla la puso el usuario, despues de ver que una misma cuenta de ahorros
recibe el salario de una persona y los pagos de las clientas de su negocio: un
movimiento no llega a NINGUN libro hasta saber con certeza a cual va. Estas
pruebas cuidan las tres capas que la sostienen -- la base, la consulta y el
publicador -- porque un camino nuevo que se olvide de preguntar tiene que
chocar con alguna.
"""

from __future__ import annotations

import sqlite3

import pytest

from finanzas.adaptadores import db, firefly
from finanzas.adaptadores.almacen import Almacen
from finanzas.aplicacion import libros, publicador
from finanzas.dominio import destino

ESQUEMA = db.ESQUEMA
MIGRACIONES = db.MIGRACIONES


def _base(migraciones=MIGRACIONES):
    cx = sqlite3.connect(':memory:')
    cx.row_factory = sqlite3.Row
    cx.execute('PRAGMA foreign_keys = ON')
    alm = Almacen(cx)
    alm.inicializar(ESQUEMA, migraciones)
    return alm


def _correo(alm, uid, mid='<m@banco>'):
    bid = alm.guardar_buzon(uid, 'graph', f'{uid}@ejemplo.com')
    cid, _ = alm.guardar_correo(bid, mid, 'banco', 'Alerta', '2026-09-01', 'x')
    return cid


def _mov(alm, uid, cid, eid, **extra):
    campos = {
        'correo_id': cid,
        'usuario_id': uid,
        'tipo': 'compra_tarjeta',
        'fecha': '2026-09-01',
        'valor': -50000.0,
        'external_id': eid,
        'cuenta_firefly': 'VISA',
        'contraparte': 'TIERRAGRO',
    }
    campos.update(extra)
    pid, _ = alm.crear_pendiente(**campos)
    alm.cx.commit()
    return pid


def _libro(alm, uid, clave='personal', tipo='firefly', secreto='FIREFLY_TOKEN', **kw):
    return alm.guardar_libro(
        uid, clave, clave.title(), tipo, 'https://ff.ejemplo', secreto, **kw
    )


# ------------------------------------------------------------- la migracion


class TestLaMigracion:
    def test_lo_que_ya_habia_queda_en_su_unico_libro(self, tmp_path):
        """La base de produccion ya tiene a Juan y su cola. Al migrar, todo lo
        suyo queda en su libro personal, con destino 'unico': no habia otro."""
        vacia = tmp_path / 'sin_migraciones'
        vacia.mkdir()
        alm = _base(vacia)
        assert alm.version() == 0
        uid = alm.guardar_usuario('Juan', 'https://ff.juan', 'tok', '555')
        cid = _correo(alm, uid)
        _mov(alm, uid, cid, 'a', estado='publicado')
        alm.actualizar_pendiente(_mov(alm, uid, cid, 'b'), firefly_id='99')
        _mov(alm, uid, cid, 'c', estado='nuevo')

        assert alm.migrar(MIGRACIONES) == [1]

        assert alm.version() == 1
        (libro,) = alm.libros_de(uid)
        assert (libro['clave'], libro['tipo'], libro['url']) == (
            'personal',
            'firefly',
            'https://ff.juan',
        )
        assert libro['secreto_env'] == 'FIREFLY_TOKEN', 'el nombre, no el token'
        assert libro['en_serio'] == 1, 'Juan ya publicaba de verdad'
        filas = alm.cx.execute(
            'SELECT libro_id, destino_por FROM pendientes'
        ).fetchall()
        assert {tuple(f) for f in filas} == {(libro['id'], 'unico')}

    def test_migrar_dos_veces_no_hace_nada(self):
        alm = _base()
        assert alm.version() == 1
        assert alm.inicializar(ESQUEMA, MIGRACIONES) == []
        assert alm.version() == 1

    def test_una_migracion_que_falla_no_deja_nada_a_medias(self, tmp_path):
        """Cada migracion va en su transaccion con el cambio de version. Si
        fallara a mitad y la version avanzara igual, la base creeria estar al
        dia con la mitad de los cambios."""
        carpeta = tmp_path / 'migraciones'
        carpeta.mkdir()
        (carpeta / '001_libros.sql').write_text(
            (MIGRACIONES / '001_libros.sql').read_text(encoding='utf-8')
        )
        (carpeta / '002_rota.sql').write_text(
            'CREATE TABLE a_medias (x INTEGER);\nESTO NO ES SQL;\n'
        )
        alm = _base(tmp_path / 'ninguna')
        with pytest.raises(sqlite3.Error):
            alm.migrar(carpeta)
        assert alm.version() == 1, 'la 001 si quedo; la 002 no'
        tablas = {
            r[0]
            for r in alm.cx.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert 'a_medias' not in tablas

    def test_dos_migraciones_con_el_mismo_numero_no_se_aplican(self, tmp_path):
        carpeta = tmp_path / 'm'
        carpeta.mkdir()
        (carpeta / '001_a.sql').write_text('SELECT 1;')
        (carpeta / '001_b.sql').write_text('SELECT 1;')
        with pytest.raises(ValueError):
            Almacen.migraciones_de(carpeta)


class TestElRespaldo:
    def test_se_respalda_una_base_con_datos_antes_de_migrar(
        self, tmp_path, monkeypatch
    ):
        ruta = tmp_path / 'finanzas.db'
        monkeypatch.setattr(db, 'ruta', lambda: str(ruta))
        cx = db.conectar()
        vacia = tmp_path / 'vacia'
        vacia.mkdir()
        alm = Almacen(cx)
        alm.inicializar(ESQUEMA, vacia)
        alm.guardar_usuario('Juan', 'https://ff', 'tok')

        db.inicializar(cx)

        (copia,) = tmp_path.glob('finanzas.db.antes-de-v1-*')
        vieja = sqlite3.connect(copia)
        assert vieja.execute('PRAGMA user_version').fetchone()[0] == 0
        assert vieja.execute('SELECT nombre FROM usuarios').fetchone()[0] == 'Juan'
        vieja.close()
        assert Almacen(cx).version() == 1
        cx.close()

    def test_una_base_nueva_no_se_respalda(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db, 'ruta', lambda: str(tmp_path / 'finanzas.db'))
        db.inicializar()
        assert not list(tmp_path.glob('*.antes-de-*'))


# ------------------------------------------------------------------ la regla


class TestLaBaseNoDejaPublicarSinDestino:
    def test_no_nace_publicado_sin_libro(self):
        alm = _base()
        uid = alm.guardar_usuario('Juan', 'u', 't')
        cid = _correo(alm, uid)
        with pytest.raises(sqlite3.IntegrityError, match='destino sin confirmar'):
            _mov(alm, uid, cid, 'a', estado='publicado')

    def test_no_pasa_a_publicado_sin_libro(self):
        alm = _base()
        uid = alm.guardar_usuario('Juan', 'u', 't')
        pid = _mov(alm, uid, _correo(alm, uid), 'a')
        with pytest.raises(sqlite3.IntegrityError, match='destino sin confirmar'):
            alm.actualizar_pendiente(pid, estado='publicado')

    def test_no_recibe_un_id_del_libro_sin_libro(self):
        """firefly_id es la prueba de que ya se escribio en un libro."""
        alm = _base()
        uid = alm.guardar_usuario('Juan', 'u', 't')
        pid = _mov(alm, uid, _correo(alm, uid), 'a')
        with pytest.raises(sqlite3.IntegrityError, match='destino sin confirmar'):
            alm.actualizar_pendiente(pid, firefly_id='123')

    def test_un_libro_sin_quien_lo_confirme_tampoco_sirve(self):
        alm = _base()
        uid = alm.guardar_usuario('Juan', 'u', 't')
        lid = _libro(alm, uid)
        with pytest.raises(sqlite3.IntegrityError, match='destino sin confirmar'):
            _mov(alm, uid, _correo(alm, uid), 'a', libro_id=lid, estado='publicado')

    def test_el_libro_no_puede_ser_de_otra_persona(self):
        """La mezcla que mas cuesta: la compra de una persona en los libros de
        la otra."""
        alm = _base()
        juan = alm.guardar_usuario('Juan', 'u', 't')
        ella = alm.guardar_usuario('Mariana', 'u', 't')
        de_ella = _libro(alm, ella)
        with pytest.raises(sqlite3.IntegrityError, match='de otra persona'):
            _mov(
                alm,
                juan,
                _correo(alm, juan),
                'a',
                libro_id=de_ella,
                destino_por='unico',
            )
        pid = _mov(alm, juan, _correo(alm, juan, '<otro>'), 'b')
        with pytest.raises(sqlite3.IntegrityError, match='de otra persona'):
            alm.actualizar_pendiente(pid, libro_id=de_ella, destino_por='usuario')

    def test_sin_libro_si_puede_esperar_y_descartarse(self):
        """Lo que NO esta en un libro se puede crear, preguntar y descartar."""
        alm = _base()
        uid = alm.guardar_usuario('Juan', 'u', 't')
        pid = _mov(alm, uid, _correo(alm, uid), 'a', pregunta='categoria')
        alm.actualizar_pendiente(pid, estado='descartado')
        assert alm.pendiente(pid)['estado'] == 'descartado'

    def test_la_cola_de_publicar_no_ofrece_lo_que_no_tiene_destino(self):
        alm = _base()
        uid = alm.guardar_usuario('Juan', 'u', 't')
        lid = _libro(alm, uid)
        cid = _correo(alm, uid)
        _mov(alm, uid, cid, 'sin')
        con = _mov(alm, uid, cid, 'con', libro_id=lid, destino_por='unico')
        assert [p['id'] for p in alm.pendientes_por_publicar()] == [con]


class TestCuandoElDestinoEsCierto:
    def test_con_un_solo_libro_es_cierto(self):
        alm = _base()
        uid = alm.guardar_usuario('Juan', 'u', 't')
        lid = _libro(alm, uid)
        assert libros.destino_seguro(alm, uid) == {
            'libro_id': lid,
            'destino_por': 'unico',
        }

    def test_con_dos_libros_hay_que_preguntar(self):
        """El caso de la segunda persona: su Firefly y el Actual del estudio.
        Ni la tarjeta ni el historico deciden: se pregunta."""
        alm = _base()
        uid = alm.guardar_usuario('Mariana', 'u', 't')
        _libro(alm, uid, 'personal')
        _libro(alm, uid, 'estudio', tipo='actual', secreto='ACTUAL_PASSWORD')
        assert libros.destino_seguro(alm, uid) == {}

    def test_sin_libros_hay_que_preguntar(self):
        alm = _base()
        uid = alm.guardar_usuario('Juan', 'u', 't')
        assert libros.destino_seguro(alm, uid) == {}

    def test_un_libro_inactivo_no_cuenta(self):
        alm = _base()
        uid = alm.guardar_usuario('Mariana', 'u', 't')
        lid = _libro(alm, uid, 'personal')
        otro = _libro(alm, uid, 'estudio')
        alm.cx.execute('UPDATE libros SET activo = 0 WHERE id = ?', (otro,))
        assert libros.destino_seguro(alm, uid)['libro_id'] == lid

    def test_elegir_solo_vale_entre_los_libros_propios(self):
        assert destino.elegido(3, [3, 4]) == destino.Destino(3, 'usuario')
        with pytest.raises(ValueError):
            destino.elegido(9, [3, 4])


# ---------------------------------------------------------------- publicar


class FireflyFalso:
    """Anota a QUE Firefly se le hablo: la URL y el token de cada llamada."""

    def __init__(self):
        self.llamadas = []

    def call(self, metodo, ruta, payload=None, conexion=None):
        self.llamadas.append((metodo, ruta, conexion))
        if metodo == 'POST':
            return {'data': {'id': str(len(self.llamadas))}}
        return {'data': [], 'meta': {'pagination': {'total_pages': 1}}}

    def creados(self):
        return [(c.url, c.token) for m, _r, c in self.llamadas if m == 'POST']


@pytest.fixture
def dos_personas(monkeypatch):
    alm = _base()
    ff = FireflyFalso()
    monkeypatch.setattr(firefly, 'call', ff.call)
    monkeypatch.setenv('FIREFLY_TOKEN', 'tok-juan')
    monkeypatch.setenv('FIREFLY_TOKEN_NOVIA', 'tok-mariana')
    juan = alm.guardar_usuario('Juan', 'u', 't')
    ella = alm.guardar_usuario('Mariana', 'u', 't')
    lj = alm.guardar_libro(
        juan,
        'personal',
        'Personal',
        'firefly',
        'https://ff',
        'FIREFLY_TOKEN',
        en_serio=True,
    )
    le = alm.guardar_libro(
        ella,
        'personal',
        'Personal',
        'firefly',
        'https://ff',
        'FIREFLY_TOKEN_NOVIA',
        en_serio=True,
    )
    return alm, ff, juan, ella, lj, le


class TestElPublicador:
    def test_cada_movimiento_va_con_el_token_de_su_libro(self, dos_personas):
        """Misma instancia de Firefly, dos usuarios: lo unico que separa una
        contabilidad de la otra es el token. Publicar con el equivocado mete la
        compra en la cuenta de la otra persona."""
        alm, ff, juan, ella, lj, le = dos_personas
        _mov(alm, juan, _correo(alm, juan), 'j', libro_id=lj, destino_por='unico')
        _mov(alm, ella, _correo(alm, ella), 'm', libro_id=le, destino_por='usuario')

        conteo = publicador.publicar_pendientes(alm.cx, dry_run=False)

        assert conteo == {'creado': 2}
        assert sorted(ff.creados()) == [
            ('https://ff', 'tok-juan'),
            ('https://ff', 'tok-mariana'),
        ]
        por_token = {c.token for _m, _r, c in ff.llamadas}
        assert por_token == {'tok-juan', 'tok-mariana'}, 'ni una llamada sin conexion'

    def test_sin_destino_no_se_publica_ni_se_consulta(self, dos_personas):
        alm, ff, _juan, ella, *_ = dos_personas
        pid = _mov(alm, ella, _correo(alm, ella), 'm')
        assert publicador.publicar_pendientes(alm.cx, dry_run=False) == {}
        assert ff.llamadas == []
        assert alm.pendiente(pid)['estado'] == 'nuevo'

    def test_publicar_uno_sin_destino_no_hace_nada(self, dos_personas):
        alm, ff, _juan, ella, *_ = dos_personas
        pid = _mov(alm, ella, _correo(alm, ella), 'm')
        accion, _ = publicador.publicar_uno(alm.cx, alm.pendiente(pid), dry_run=False)
        assert accion == 'sin_destino'
        assert ff.llamadas == []

    def test_con_el_libro_de_otro_no_publica(self, dos_personas):
        alm, ff, juan, _ella, lj, le = dos_personas
        pid = _mov(alm, juan, _correo(alm, juan), 'j', libro_id=lj, destino_por='unico')
        accion, _ = publicador.publicar_uno(
            alm.cx, alm.pendiente(pid), dry_run=False, libro=alm.libro(le)
        )
        assert accion == 'error'
        assert ff.creados() == []

    def test_un_libro_en_seco_no_escribe_aunque_el_proceso_si(self, dos_personas):
        """El libro nuevo puede probarse en seco mientras el de siempre publica."""
        alm, ff, juan, ella, lj, le = dos_personas
        alm.cx.execute('UPDATE libros SET en_serio = 0 WHERE id = ?', (le,))
        _mov(alm, juan, _correo(alm, juan), 'j', libro_id=lj, destino_por='unico')
        pid = _mov(
            alm, ella, _correo(alm, ella), 'm', libro_id=le, destino_por='usuario'
        )

        conteo = publicador.publicar_pendientes(alm.cx, dry_run=False)

        assert conteo == {'creado': 1, 'seco': 1}
        assert ff.creados() == [('https://ff', 'tok-juan')]
        assert alm.pendiente(pid)['estado'] == 'nuevo'

    def test_la_marca_de_agua_del_libro_gana(self, dos_personas):
        alm, ff, _juan, ella, _lj, le = dos_personas
        alm.cx.execute("UPDATE libros SET desde = '2026-10-01' WHERE id = ?", (le,))
        pid = _mov(
            alm, ella, _correo(alm, ella), 'm', libro_id=le, destino_por='usuario'
        )
        publicador.publicar_pendientes(alm.cx, desde='2026-01-01', dry_run=False)
        assert alm.pendiente(pid)['estado'] == 'descartado'
        assert ff.creados() == []

    def test_un_libro_sin_token_no_publica_y_lo_dice(self, dos_personas, monkeypatch):
        alm, ff, _juan, ella, _lj, le = dos_personas
        # No basta con quitarla del entorno: config.get cae al .env del repo,
        # que en desarrollo SI la tiene.
        monkeypatch.setattr(libros.config, 'get', lambda k, d=None: None)
        _mov(alm, ella, _correo(alm, ella), 'm', libro_id=le, destino_por='usuario')
        assert publicador.publicar_pendientes(alm.cx, dry_run=False) == {'sin_libro': 1}
        assert 'FIREFLY_TOKEN_NOVIA' in alm.libro(le)['ultimo_error']
        assert ff.llamadas == []

    def test_un_libro_de_actual_todavia_no_recibe_nada(self, dos_personas):
        alm, ff, _juan, ella, *_ = dos_personas
        est = _libro(alm, ella, 'estudio', tipo='actual', secreto='ACTUAL_PASSWORD')
        _mov(alm, ella, _correo(alm, ella), 'm', libro_id=est, destino_por='usuario')
        assert publicador.publicar_pendientes(alm.cx, dry_run=False) == {'sin_libro': 1}
        assert ff.llamadas == []


def test_el_token_no_sale_en_el_repr():
    c = firefly.Cliente(firefly.Conexion('https://ff', 'secreto-largo'))
    assert 'secreto-largo' not in repr(c)
