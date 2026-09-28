"""La configuracion de las demas personas: que se valide, y que se aplique.

Esta configuracion decide quien ve que libros. Un error aqui no da una
excepcion rara: da una persona viendo la plata de otra. Por eso lo dudoso se
rechaza al arrancar en vez de adivinarse.
"""

from __future__ import annotations

import json
import sqlite3
import tomllib

import pytest

from finanzas.adaptadores import db
from finanzas.adaptadores.almacen import Almacen
from finanzas.aplicacion import personas
from finanzas.entrada import demonio

EJEMPLO = tomllib.loads(
    """
[[persona]]
nombre = "Mariana"
telegram = "777"

  [[persona.libro]]
  clave = "personal"
  tipo = "firefly"
  url = "https://ff/"
  secreto = "FIREFLY_TOKEN_NOVIA"

  [[persona.libro]]
  clave = "estudio"
  nombre = "Golden Beauty"
  tipo = "actual"
  url = "http://actual-api:5007"
  secreto = "ACTUAL_API_KEY"
  ajustes = { sync_id = "abc" }

  [[persona.buzon]]
  tipo = "imap"
  direccion = "ella@gmail.com"
  secreto = "GMAIL_APP_PASSWORD_NOVIA"

  [[persona.instrumento]]
  clave = "5788"
  clase = "cuenta"
  cuentas = { personal = "Ahorros *5788", estudio = "Bancolombia" }

  [[persona.instrumento]]
  clave = "NU"
  clase = "tarjeta"
  alias = "nu, NuBank"
  cuentas = { personal = "Nu" }
"""
)


def _alm():
    cx = sqlite3.connect(':memory:')
    cx.row_factory = sqlite3.Row
    cx.execute('PRAGMA foreign_keys = ON')
    a = Almacen(cx)
    a.inicializar(db.ESQUEMA, db.MIGRACIONES)
    return a


def _con(**cambios):
    """El ejemplo con un cambio en la persona, el primer libro o lo que sea."""
    d = json.loads(json.dumps(EJEMPLO))
    for ruta, valor in cambios.items():
        *camino, hoja = ruta.split('__')
        nodo = d['persona'][0]
        for c in camino:
            nodo = nodo[c][0] if isinstance(nodo[c], list) else nodo[c]
        nodo[hoja] = valor
    return d


class TestInterpretar:
    def test_el_ejemplo_se_entiende(self):
        (p,) = personas.interpretar(EJEMPLO)
        assert p.nombre == 'Mariana' and p.telegram == '777'
        assert [lb.clave for lb in p.libros] == ['personal', 'estudio']
        assert p.libros[0].url == 'https://ff', 'sin la barra final'
        assert p.libros[1].ajustes == {'sync_id': 'abc'}
        assert p.buzones[0].facturas is False, 'las facturas son de Juan'
        nu = p.instrumentos[1]
        assert (nu.clave, nu.alias) == ('nu', ('nu', 'nubank'))

    def test_un_secreto_pegado_se_rechaza(self):
        """`secreto` es el NOMBRE de la variable. Si alguien pega el token, se
        rechaza: terminaria en la base y en los logs."""
        with pytest.raises(personas.ConfiguracionInvalida, match='NOMBRE'):
            personas.interpretar(_con(libro__secreto='1|a8f7Hk2mQ9...'))

    def test_una_cuenta_en_un_libro_que_no_existe_se_rechaza(self):
        d = _con()
        d['persona'][0]['instrumento'][0]['cuentas']['otro'] = 'X'
        with pytest.raises(personas.ConfiguracionInvalida, match='no existen'):
            personas.interpretar(d)

    def test_juan_no_se_configura_aqui(self):
        with pytest.raises(personas.ConfiguracionInvalida, match='Juan'):
            personas.interpretar(_con(nombre='Juan'))

    def test_sin_libros_no_sirve(self):
        d = _con()
        d['persona'][0]['libro'] = []
        with pytest.raises(personas.ConfiguracionInvalida, match='al menos un libro'):
            personas.interpretar(d)

    def test_un_tipo_de_libro_desconocido_se_rechaza(self):
        with pytest.raises(personas.ConfiguracionInvalida, match='tipo'):
            personas.interpretar(_con(libro__tipo='excel'))

    def test_las_variables_se_expanden(self, monkeypatch):
        monkeypatch.setenv('URL_DE_PRUEBA', 'https://de-la-variable')
        (p,) = personas.interpretar(_con(libro__url='${URL_DE_PRUEBA}'))
        assert p.libros[0].url == 'https://de-la-variable'

    def test_una_variable_que_falta_se_dice(self):
        with pytest.raises(personas.ConfiguracionInvalida, match='NO_EXISTE_ESTA'):
            personas.interpretar(_con(libro__url='${NO_EXISTE_ESTA}'))

    def test_sin_chat_todavia_la_persona_existe_pero_sin_bot(self):
        (p,) = personas.interpretar(_con(telegram='${NO_EXISTE_ESTE_CHAT}'))
        assert p.telegram is None


class TestLeer:
    def test_el_json_de_portainer_gana(self, monkeypatch):
        monkeypatch.setattr(
            personas, '_texto_crudo', lambda: ('json', json.dumps(EJEMPLO))
        )
        assert [p.nombre for p in personas.leer()] == ['Mariana']

    def test_sin_configuracion_no_hay_nadie(self):
        assert personas.leer() == []

    def test_una_configuracion_rota_cierra_el_bot(self, monkeypatch):
        """Mejor mudo que abierto: si no se entiende, nadie nuevo entra."""
        monkeypatch.setattr(personas, '_texto_crudo', lambda: ('toml', '[[persona'))
        with pytest.raises(personas.ConfiguracionInvalida):
            personas.leer()
        assert personas.chats() == {}


class TestAplicar:
    def test_deja_todo_en_la_base(self):
        alm = _alm()
        personas.aplicar(alm, personas.interpretar(EJEMPLO))
        u = alm.usuario_por_nombre('Mariana')
        assert u['telegram_chat_id'] == '777'
        assert u['firefly_token_enc'] == '', 'el token no se guarda'
        libros = {lb['clave']: lb for lb in alm.libros_de(u['id'])}
        assert set(libros) == {'personal', 'estudio'}
        assert libros['estudio']['secreto_env'] == 'ACTUAL_API_KEY'
        assert json.loads(libros['estudio']['ajustes']) == {'sync_id': 'abc'}
        (bz,) = alm.cx.execute('SELECT * FROM buzones').fetchall()
        assert (bz['proveedor'], bz['secreto_env'], bz['facturas']) == (
            'imap',
            'GMAIL_APP_PASSWORD_NOVIA',
            0,
        )
        ins = {
            (i['clave'], i['libro_clave']): i['cuenta']
            for i in alm.instrumentos_de(u['id'])
        }
        assert ins == {
            ('5788', 'personal'): 'Ahorros *5788',
            ('5788', 'estudio'): 'Bancolombia',
            ('nu', 'personal'): 'Nu',
        }

    def test_aplicar_dos_veces_no_duplica(self):
        alm = _alm()
        gente = personas.interpretar(EJEMPLO)
        personas.aplicar(alm, gente)
        personas.aplicar(alm, gente)
        uid = alm.usuario_por_nombre('Mariana')['id']
        assert len(alm.libros_de(uid)) == 2
        assert len(alm.instrumentos_de(uid)) == 3
        assert alm.contar_por_tabla('buzones') == 1

    def test_un_libro_que_sale_de_la_configuracion_se_desactiva(self):
        """No se borra: sus movimientos lo siguen nombrando. Pero deja de ser
        un destino posible."""
        alm = _alm()
        personas.aplicar(alm, personas.interpretar(EJEMPLO))
        d = _con()
        d['persona'][0]['libro'] = d['persona'][0]['libro'][:1]
        d['persona'][0]['instrumento'][0]['cuentas'] = {'personal': 'Ahorros *5788'}
        personas.aplicar(alm, personas.interpretar(d))
        uid = alm.usuario_por_nombre('Mariana')['id']
        assert [lb['clave'] for lb in alm.libros_de(uid)] == ['personal']
        assert len(alm.libros_de(uid, activos=False)) == 2


class TestUnaConfiguracionRotaNoTumbaAJuan:
    def test_el_arranque_sigue_y_no_aplica_nada(self, monkeypatch, tmp_path):
        """Tumbar el arranque dejaba a Juan sin ingesta por un error en la
        configuracion de otra persona."""
        monkeypatch.setattr(db, 'ruta', lambda: str(tmp_path / 'f.db'))
        monkeypatch.setenv('FIREFLY_URL', 'https://ff')
        monkeypatch.setenv('FIREFLY_TOKEN', 't')
        monkeypatch.setattr(personas, '_texto_crudo', lambda: ('toml', '[[persona'))
        db.inicializar()
        cx = db.conectar()
        uid, _ = demonio.paso_asegurar_usuario(cx)
        assert uid
        assert demonio.PROBLEMA_CON_PERSONAS
        assert [u['nombre'] for u in Almacen(cx).usuarios()] == ['Juan']
        cx.close()

    def test_el_chat_de_juan_no_se_le_puede_dar_a_otra_persona(self, monkeypatch):
        monkeypatch.setenv('TELEGRAM_CHAT_ID_JUAN', '777')
        monkeypatch.setattr(
            personas, '_texto_crudo', lambda: ('json', json.dumps(EJEMPLO))
        )
        with pytest.raises(personas.ConfiguracionInvalida, match='mismo chat'):
            personas.leer_para_aplicar()
        assert personas.chats() == {}, 'y el bot no la atiende'
