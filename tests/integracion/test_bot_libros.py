"""La conversacion de quien lleva dos libros, de punta a punta.

Mariana lleva su Firefly personal y el Actual de su estudio sobre una misma
cuenta de ahorros. Estas pruebas cuidan la regla que puso el usuario: nada
llega a un libro hasta que ella dice a cual va, y nunca al libro de otro.

Actual y Firefly son dobles que contestan HTTP: por debajo corre el adaptador
de verdad.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.parse
from datetime import date

import pytest

from finanzas.adaptadores import actual, db, firefly
from finanzas.adaptadores.almacen import Almacen
from finanzas.aplicacion import personas, publicador, ruteo
from finanzas.entrada import bot, bot_libros

JUAN, ELLA = '555', '777'

CONFIG = {
    'persona': [
        {
            'nombre': 'Mariana',
            'telegram': ELLA,
            'libro': [
                {
                    'clave': 'personal',
                    'nombre': 'Personal',
                    'tipo': 'firefly',
                    'url': 'https://ff',
                    'secreto': 'FIREFLY_TOKEN_NOVIA',
                    'en_serio': True,
                },
                {
                    'clave': 'estudio',
                    'nombre': 'Golden Beauty',
                    'tipo': 'actual',
                    'url': 'http://actual-api:5007',
                    'secreto': 'ACTUAL_API_KEY',
                    'en_serio': True,
                    'ajustes': {'sync_id': 'gbs'},
                },
            ],
            'instrumento': [
                {
                    'clave': '5788',
                    'clase': 'cuenta',
                    'cuentas': {'personal': 'Ahorros *5788', 'estudio': 'Bancolombia'},
                },
                {
                    'clave': '9919',
                    'clase': 'tarjeta',
                    'cuentas': {'estudio': 'T.Cred *9919'},
                },
                {
                    'clave': 'nu',
                    'clase': 'tarjeta',
                    'alias': 'nu, nubank',
                    'cuentas': {'personal': 'Nu'},
                },
            ],
        }
    ]
}


class ActualFalso:
    """El puente de Actual, contestando lo que contesta el de verdad."""

    def __init__(self):
        self.cuentas = [
            {'id': 'acc-banc', 'name': 'Bancolombia', 'closed': False},
            {'id': 'acc-tc', 'name': 'T.Cred *9919', 'closed': False},
        ]
        self.categorias = [
            {'id': 'cat-ins', 'name': 'Insumos', 'is_income': False, 'hidden': False},
            {'id': 'cat-otros-g', 'name': 'Otros', 'is_income': False, 'hidden': False},
            {'id': 'cat-serv', 'name': 'Servicios', 'is_income': True, 'hidden': False},
            {'id': 'cat-otros-i', 'name': 'Otros', 'is_income': True, 'hidden': False},
        ]
        self.tx: list[dict] = []
        self.creadas: list[dict] = []

    def call(self, metodo, ruta, cuerpo=None):
        if ruta == '/accounts':
            return self.cuentas
        if ruta == '/categories':
            return self.categorias
        if ruta == '/payees':
            return [{'id': 'pay-tc', 'transfer_acct': 'acc-tc'}]
        if ruta.startswith('/accounts/') and metodo == 'GET':
            cuenta = ruta.split('/')[2]
            q = urllib.parse.parse_qs(ruta.split('?', 1)[1])
            d, h = q['since_date'][0], q['until_date'][0]
            return [
                t for t in self.tx if t['account'] == cuenta and d <= t['date'] <= h
            ]
        if ruta.startswith('/accounts/') and metodo == 'POST':
            t = {**cuerpo['transaction'], 'account': ruta.split('/')[2]}
            t['id'] = f'tx-{len(self.tx) + 1}'
            self.tx.append(t)
            self.creadas.append(t)
            return {'message': 'ok'}
        raise AssertionError(f'ruta inesperada {metodo} {ruta}')


class FireflyFalso:
    def __init__(self):
        self.llamadas = []

    def call(self, metodo, ruta, payload=None, conexion=None):
        self.llamadas.append((metodo, ruta, conexion.token if conexion else None))
        if ruta.startswith('/api/v1/categories'):
            datos = [
                {'attributes': {'name': n}}
                for n in ('Comida', 'Cuidado personal', 'Transporte')
            ]
            return {'data': datos, 'meta': {'pagination': {'total_pages': 1}}}
        if metodo == 'POST':
            return {'data': {'id': '901'}}
        return {'data': [], 'meta': {'pagination': {'total_pages': 1}}}

    def creadas(self):
        return [(tok, r) for m, r, tok in self.llamadas if m == 'POST']


class TelegramFalso:
    class TelegramError(Exception):
        pass

    def __init__(self):
        self.enviados: list[tuple[str, str, list]] = []
        self.editados: list[tuple[str, str]] = []
        self.avisos: list[str] = []
        self.n = 1000

    def enviar(self, chat, texto, botones=None, modo='HTML'):
        self.n += 1
        self.enviados.append((str(chat), texto, botones or []))
        return {'message_id': self.n}

    def editar(self, chat, mid, texto, botones=None, modo='HTML'):
        self.editados.append((str(chat), texto))

    def responder_callback(self, cq_id, texto=None, alerta=False):
        self.avisos.append(texto or '')

    def ultimo(self):
        return self.enviados[-1]

    def botones(self):
        return [b for fila in self.ultimo()[2] for b in fila]


@pytest.fixture
def mundo(monkeypatch):
    cx = sqlite3.connect(':memory:')
    cx.row_factory = sqlite3.Row
    cx.execute('PRAGMA foreign_keys = ON')
    alm = Almacen(cx)
    alm.inicializar(db.ESQUEMA, db.MIGRACIONES)

    juan = alm.guardar_usuario('Juan', 'https://ff', '', JUAN)
    alm.guardar_libro(
        juan,
        'personal',
        'Personal',
        'firefly',
        'https://ff',
        'FIREFLY_TOKEN',
        en_serio=True,
    )
    personas.aplicar(alm, personas.interpretar(CONFIG))
    ella = alm.usuario_por_nombre('Mariana')['id']

    monkeypatch.setenv('FIREFLY_TOKEN', 'tok-juan')
    monkeypatch.setenv('FIREFLY_TOKEN_NOVIA', 'tok-mariana')
    monkeypatch.setenv('ACTUAL_API_KEY', 'llave')
    monkeypatch.setattr(bot, 'chats_autorizados', lambda: {JUAN, ELLA})

    tg = TelegramFalso()
    monkeypatch.setattr(bot, 'telegram', tg)
    monkeypatch.setattr(bot_libros, 'telegram', tg)
    monkeypatch.setattr(bot.ia, 'disponible', lambda: False)

    ff = FireflyFalso()
    monkeypatch.setattr(firefly, 'call', ff.call)
    act = ActualFalso()
    monkeypatch.setattr(
        actual.Cliente, '_call', lambda self, m, r, c=None: act.call(m, r, c)
    )
    return alm, tg, ff, act, juan, ella


def _alerta(alm, uid, eid, **ev):
    bid = alm.guardar_buzon(uid, 'imap', f'{uid}@gmail.com')
    cid, _ = alm.guardar_correo(bid, f'<{eid}>', 'banco', 'Alerta', '2026-09-22', 'x')
    base = {
        'tipo': 'compra_tarjeta',
        'fecha': date(2026, 9, 22),
        'hora': '14:14',
        'moneda': 'COP',
        'valor': -65000.0,
        'instrumento': '9919',
        'clase_instrumento': 'tarjeta',
        'traslado_a': None,
        'contraparte': 'EPY*MARYURI ARENGAS',
        'descripcion': 'EPY*MARYURI ARENGAS',
        'plantilla': 'compra_tcred',
    }
    base.update(ev)
    correo = {'id': cid, 'usuario_id': uid}
    pid, _ = ruteo.crear_desde_alerta(alm, correo, type('Ev', (), base), eid)
    alm.cx.commit()
    return pid


def _toque(alm, dato, chat=ELLA):
    bot.manejar_update(
        alm.cx,
        {
            'callback_query': {
                'id': 'q',
                'data': dato,
                'message': {'message_id': 1, 'chat': {'id': chat}},
            }
        },
    )


def _texto(alm, texto, chat=ELLA, mid=50, responde_a=None):
    m = {'message_id': mid, 'chat': {'id': chat}, 'text': texto}
    if responde_a:
        m['reply_to_message'] = {'message_id': responde_a}
    bot.manejar_update(alm.cx, {'message': m})


def _libro(alm, uid, clave):
    return next(lb['id'] for lb in alm.libros_de(uid) if lb['clave'] == clave)


# --------------------------------------------------------- la alerta del banco


class TestLaAlertaPreguntaElLibroAntesDeTodo:
    def test_nace_sin_libro_y_no_se_publica(self, mundo):
        alm, _tg, ff, act, _juan, ella = mundo
        pid = _alerta(alm, ella, 'a1')
        p = alm.pendiente(pid)
        assert (p['libro_id'], p['pregunta']) == (None, 'destino')
        assert publicador.publicar_pendientes(alm.cx, dry_run=False) == {}
        assert act.creadas == [] and ff.creadas() == []

    def test_solo_ofrece_los_libros_donde_la_tarjeta_tiene_cuenta(self, mundo):
        """La *9919 solo existe en el estudio: el Firefly personal ni aparece."""
        alm, tg, _ff, _act, _juan, ella = mundo
        _alerta(alm, ella, 'a1')
        bot.preguntar_pendientes(alm.cx)
        chat, texto, _ = tg.ultimo()
        assert chat == ELLA and '¿A qué libro va?' in texto
        nombres = [b[0] for b in tg.botones()]
        assert any('Golden Beauty' in n for n in nombres)
        assert not any('Personal' in n for n in nombres)

    def test_la_cuenta_de_ahorros_ofrece_los_dos(self, mundo):
        alm, tg, *_rest, ella = mundo
        _alerta(alm, ella, 'a1', instrumento='5788', clase_instrumento='cuenta')
        bot.preguntar_pendientes(alm.cx)
        nombres = ' '.join(b[0] for b in tg.botones())
        assert 'Golden Beauty' in nombres and 'Personal' in nombres

    def test_elegir_el_libro_y_la_categoria_la_publica_ahi(self, mundo):
        alm, tg, ff, act, _juan, ella = mundo
        pid = _alerta(alm, ella, 'a1')
        estudio = _libro(alm, ella, 'estudio')

        _toque(alm, f'ld:{pid}:{estudio}')
        assert '¿Qué categoría?' in tg.ultimo()[1]
        cats = [b[0] for b in tg.botones()]
        assert 'Insumos' in cats and 'Servicios' not in cats, (
            'un gasto: solo categorias de gasto'
        )

        _toque(alm, f'kc:{pid}:{cats.index("Insumos")}')

        (creada,) = act.creadas
        assert creada['account'] == 'acc-tc'
        assert creada['amount'] == -6500000
        assert creada['category'] == 'cat-ins'
        assert creada['imported_id'] == 'a1'
        assert ff.creadas() == [], 'nada en Firefly'
        p = alm.pendiente(pid)
        assert (p['estado'], p['firefly_id'], p['destino_por']) == (
            'publicado',
            'tx-1',
            'usuario',
        )
        assert '✅ Guardado en' in tg.ultimo()[1]

    def test_lo_que_contesto_se_aprende_para_ese_libro(self, mundo):
        alm, tg, _ff, act, _juan, ella = mundo
        estudio = _libro(alm, ella, 'estudio')
        pid = _alerta(alm, ella, 'a1')
        _toque(alm, f'ld:{pid}:{estudio}')
        _toque(alm, f'kc:{pid}:{[b[0] for b in tg.botones()].index("Insumos")}')

        otro = _alerta(alm, ella, 'a2', fecha=date(2026, 9, 25), valor=-30000.0)
        bot.preguntar_pendientes(alm.cx)
        assert '✓' in ' '.join(b[0] for b in tg.botones()), 'el estudio viene marcado'
        assert alm.pendiente(otro)['libro_id'] is None, 'marcado no es elegido'
        _toque(alm, f'ld:{otro}:{estudio}')
        # la categoria ya la sabe para ESE libro: no vuelve a preguntar
        assert len(act.creadas) == 2 and act.creadas[1]['category'] == 'cat-ins'


class TestLaVentaDeAgendapro:
    def _transferencia(self, alm, ella):
        return _alerta(
            alm,
            ella,
            'a9',
            tipo='transferencia_entrada',
            valor=40000.0,
            instrumento='5788',
            clase_instrumento='cuenta',
            contraparte='JERONIMO RUIZ HENAO',
            fecha=date(2026, 9, 23),
        )

    def test_si_ya_esta_en_agendapro_se_enlaza_sin_escribir(self, mundo):
        alm, tg, _ff, act, _juan, ella = mundo
        act.tx.append(
            {
                'id': 'ag-1',
                'account': 'acc-banc',
                'date': '2026-09-23',
                'amount': 4000000,
                'imported_id': 'agendapro-tx:51226519',
                'notes': 'Venta 1150 · transferencia',
            }
        )
        pid = self._transferencia(alm, ella)
        _toque(alm, f'ld:{pid}:{_libro(alm, ella, "estudio")}')
        assert '¿Es esa venta?' in tg.ultimo()[1]
        _toque(alm, f'va:{pid}:1')
        assert act.creadas == [], 'no se cuenta dos veces'
        p = alm.pendiente(pid)
        assert (p['estado'], p['firefly_id'], p['decidido_por']) == (
            'publicado',
            'ag-1',
            'venta_agendapro',
        )

    def test_si_todavia_no_esta_se_espera_y_no_se_publica(self, mundo):
        alm, _tg, _ff, act, _juan, ella = mundo
        pid = self._transferencia(alm, ella)
        _toque(alm, f'ld:{pid}:{_libro(alm, ella, "estudio")}')
        _toque(alm, f'va:{pid}:2')
        publicador.publicar_pendientes(alm.cx, dry_run=False)
        assert act.creadas == []
        # esa noche Agendapro la sube, y la siguiente pasada la enlaza
        act.tx.append(
            {
                'id': 'ag-2',
                'account': 'acc-banc',
                'date': '2026-09-23',
                'amount': 4000000,
                'imported_id': 'agendapro-tx:9',
                'notes': 'Venta',
            }
        )
        assert bot_libros.revisar_ventas_en_espera(alm.cx) == 1
        assert alm.pendiente(pid)['firefly_id'] == 'ag-2'
        assert act.creadas == []


class TestLoParecido:
    def test_pregunta_si_es_el_mismo_y_no_duplica(self, mundo):
        """Ella ya lo habia anotado a mano: el bot pregunta, no duplica ni
        descarta solo."""
        alm, tg, _ff, act, _juan, ella = mundo
        act.tx.append(
            {
                'id': 'mano',
                'account': 'acc-tc',
                'date': '2026-09-22',
                'amount': -6500000,
                'imported_id': None,
                'notes': 'uñas y wypall',
            }
        )
        pid = _alerta(alm, ella, 'a1')
        _toque(alm, f'ld:{pid}:{_libro(alm, ella, "estudio")}')
        _toque(alm, f'kc:{pid}:{[b[0] for b in tg.botones()].index("Insumos")}')
        assert '¿Es el mismo?' in tg.ultimo()[1]
        _toque(alm, f'kp:{pid}:1')
        assert act.creadas == []
        assert alm.pendiente(pid)['firefly_id'] == 'mano'

    def test_si_son_dos_se_guarda_igual(self, mundo):
        alm, tg, _ff, act, _juan, ella = mundo
        act.tx.append(
            {
                'id': 'mano',
                'account': 'acc-tc',
                'date': '2026-09-22',
                'amount': -6500000,
                'imported_id': None,
            }
        )
        pid = _alerta(alm, ella, 'a1')
        _toque(alm, f'ld:{pid}:{_libro(alm, ella, "estudio")}')
        _toque(alm, f'kc:{pid}:{[b[0] for b in tg.botones()].index("Insumos")}')
        _toque(alm, f'kp:{pid}:0')
        assert len(act.creadas) == 1


# ------------------------------------------------------- lo que ella cuenta


class TestLoQueElleCuenta:
    def test_un_gasto_escrito_se_registra_en_su_firefly(self, mundo):
        """Nu no manda correos. Ella lo cuenta, elige, y queda en SU Firefly,
        con SU token."""
        alm, tg, ff, act, _juan, ella = mundo
        _texto(alm, '45 mil de esmaltes con la nu')
        (p,) = [
            r for r in alm.cx.execute("SELECT * FROM pendientes WHERE origen = 'chat'")
        ]
        assert (p['valor'], p['instrumento'], p['libro_id']) == (-45000.0, 'nu', None)
        assert '¿A qué libro va?' in tg.ultimo()[1]
        _toque(alm, f'ld:{p["id"]}:{_libro(alm, ella, "personal")}')
        cats = [b[0] for b in tg.botones()]
        _toque(alm, f'kc:{p["id"]}:{cats.index("Cuidado personal")}')
        assert ff.creadas() == [('tok-mariana', '/api/v1/transactions')]
        assert act.creadas == []
        assert alm.pendiente(p['id'])['estado'] == 'publicado'

    def test_el_mismo_mensaje_dos_veces_no_crea_dos(self, mundo):
        """Telegram puede reenviar un update. El external_id sale del mensaje."""
        alm, *_ = mundo
        _texto(alm, '45 mil de esmaltes con la nu', mid=70)
        _texto(alm, '45 mil de esmaltes con la nu', mid=70)
        assert (
            alm.cx.execute(
                "SELECT count(*) FROM pendientes WHERE origen = 'chat'"
            ).fetchone()[0]
            == 1
        )

    def test_sin_medio_pregunta_con_que_pago(self, mundo):
        alm, tg, *_ = mundo
        _texto(alm, '20 mil de almuerzo')
        assert '¿Con qué pagaste?' in tg.ultimo()[1]

    def test_una_nota_de_voz_se_entiende_y_pregunta(self, mundo, monkeypatch):
        alm, tg, _ff, _act, _juan, ella = mundo
        monkeypatch.setattr(
            bot_libros.telegram, 'descargar', lambda fid: b'OggS...', raising=False
        )
        monkeypatch.setattr(bot_libros.ia, 'disponible', lambda: True)
        oido = {}

        def entender(hoy, medios, libros, texto=None, audio=None, tipo_audio=None):
            oido.update(audio=audio, medios=medios, libros=libros)
            return {
                'es_movimiento': True,
                'direccion': 'gasto',
                'monto': 120000,
                'comercio': '',
                'descripcion': 'arriendo del local',
                'fecha': '2026-09-26',
                'medio': '5788',
                'libro': 'estudio',
                'transcripcion': 'ayer pague 120 mil del arriendo del local',
            }

        monkeypatch.setattr(bot_libros.ia, 'entender_contado', entender)
        bot.manejar_update(
            alm.cx,
            {
                'message': {
                    'message_id': 80,
                    'chat': {'id': ELLA},
                    'voice': {'file_id': 'F', 'mime_type': 'audio/ogg'},
                }
            },
        )
        assert oido['audio'] == b'OggS...'
        assert set(oido['medios']) == {'5788', '9919', 'nu'}
        (p,) = alm.cx.execute(
            "SELECT * FROM pendientes WHERE origen = 'chat'"
        ).fetchall()
        assert (p['valor'], p['libro_id']) == (-120000.0, None), (
            'lo que dijo solo sugiere'
        )
        assert p['sugerido_libro_id'] == _libro(alm, ella, 'estudio')
        assert '✓' in ' '.join(b[0] for b in tg.botones() if 'Golden' in b[0])

    def test_sin_monto_lo_pide(self, mundo):
        alm, tg, *_ = mundo
        _texto(alm, 'esmaltes con la nu')
        assert 'Cuéntamelo' in tg.ultimo()[1]
        assert alm.contar_por_tabla('pendientes') == 0


# ------------------------------------------------------------ la frontera


class TestNadieTocaLoDeOtro:
    def test_juan_no_puede_elegir_el_libro_de_un_movimiento_de_ella(self, mundo):
        alm, tg, _ff, _act, _juan, ella = mundo
        pid = _alerta(alm, ella, 'a1')
        _toque(alm, f'ld:{pid}:{_libro(alm, ella, "estudio")}', chat=JUAN)
        assert tg.avisos[-1] == 'eso no es tuyo'
        assert alm.pendiente(pid)['libro_id'] is None

    def test_ella_no_puede_mandar_su_movimiento_al_libro_de_juan(self, mundo):
        alm, tg, _ff, _act, juan, ella = mundo
        pid = _alerta(alm, ella, 'a1', instrumento='5788', clase_instrumento='cuenta')
        de_juan = _libro(alm, juan, 'personal')
        _toque(alm, f'ld:{pid}:{de_juan}')
        assert tg.avisos[-1] == 'ese libro no es tuyo'
        assert alm.pendiente(pid)['libro_id'] is None

    def test_sus_comandos_no_muestran_lo_de_juan(self, mundo):
        alm, tg, ff, *_ = mundo
        _texto(alm, '/ultimos')
        assert 'Cómo registrar algo' in tg.ultimo()[1]
        assert not any(tok == 'tok-juan' for _m, _r, tok in ff.llamadas)

    def test_un_libro_en_seco_no_escribe(self, mundo):
        alm, tg, _ff, act, _juan, ella = mundo
        estudio = _libro(alm, ella, 'estudio')
        alm.cx.execute('UPDATE libros SET en_serio = 0 WHERE id = ?', (estudio,))
        pid = _alerta(alm, ella, 'a1')
        _toque(alm, f'ld:{pid}:{estudio}')
        _toque(alm, f'kc:{pid}:{[b[0] for b in tg.botones()].index("Insumos")}')
        assert act.creadas == []
        assert 'En prueba' in tg.ultimo()[1]
        assert alm.pendiente(pid)['estado'] == 'nuevo'


def test_la_configuracion_de_prueba_es_valida():
    assert json.dumps(CONFIG) and personas.interpretar(CONFIG)
