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
from datetime import date, timedelta

import pytest

from finanzas.adaptadores import actual, db, firefly
from finanzas.adaptadores.almacen import Almacen
from finanzas.aplicacion import personas, publicador, ruteo
from finanzas.dominio import fechas
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
            {'id': 'acc-aportes', 'name': 'Aportes en especie', 'closed': False},
        ]
        self.categorias = [
            {'id': 'cat-ins', 'name': 'Insumos', 'is_income': False, 'hidden': False},
            {'id': 'cat-otros-g', 'name': 'Otros', 'is_income': False, 'hidden': False},
            {'id': 'cat-serv', 'name': 'Servicios', 'is_income': True, 'hidden': False},
            {'id': 'cat-otros-i', 'name': 'Otros', 'is_income': True, 'hidden': False},
            {
                'id': 'cat-aporte',
                'name': 'Aportes de la dueña',
                'is_income': True,
                'hidden': False,
            },
            {
                'id': 'cat-sal',
                'name': 'Salario Dueña',
                'is_income': False,
                'hidden': False,
            },
        ]
        self.grupos = [
            {'id': 'g-costos', 'name': 'Costos Directos', 'is_income': False},
            {'id': 'g-fijos', 'name': 'Gastos Fijos', 'is_income': False},
            {'id': 'g-ingresos', 'name': 'Ingresos', 'is_income': True},
        ]
        self.tx: list[dict] = []
        self.creadas: list[dict] = []
        self.categorias_creadas: list[dict] = []
        self.cuentas_creadas: list[dict] = []

    def call(self, metodo, ruta, cuerpo=None):
        if ruta == '/accounts' and metodo == 'POST':
            c = {
                'id': f'acc-{len(self.cuentas) + 1}',
                'name': cuerpo['account']['name'],
                'closed': False,
                'offbudget': cuerpo['account']['offbudget'],
            }
            self.cuentas.append(c)
            self.cuentas_creadas.append(c)
            return c['id']
        if ruta == '/accounts':
            return self.cuentas
        if ruta == '/categorygroups':
            return self.grupos
        if ruta == '/categories' and metodo == 'POST':
            grupo = next(
                g for g in self.grupos if g['id'] == cuerpo['category']['group_id']
            )
            c = {
                'id': f'cat-nueva-{len(self.categorias_creadas) + 1}',
                'name': cuerpo['category']['name'],
                'is_income': grupo['is_income'],
                'group_id': grupo['id'],
                'hidden': False,
            }
            self.categorias.append(c)
            self.categorias_creadas.append(c)
            return c['id']
        if ruta == '/categories':
            return self.categorias
        if ruta == '/payees':
            # Actual crea el «payee» del traslado con cada cuenta.
            return [
                {'id': f'pay-{c["id"]}', 'transfer_acct': c['id']} for c in self.cuentas
            ]
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
        self.cuerpos = []
        self.cuentas = []
        self.falla_al_crear = 0
        # Con esto, lo publicado aparece al listar: el anti-duplicado lo ve.
        self.devolver_publicadas = False

    def call(self, metodo, ruta, payload=None, conexion=None):
        self.llamadas.append((metodo, ruta, conexion.token if conexion else None))
        self.cuerpos.append((metodo, ruta, payload))
        if ruta == '/api/v1/accounts' and metodo == 'POST':
            self.cuentas.append(payload)
            return {'data': {'id': f'cuenta-{len(self.cuentas)}'}}
        if ruta.startswith('/api/v1/accounts') and metodo == 'GET':
            datos = [
                {'id': f'cuenta-{i + 1}', 'attributes': {**c, 'active': True}}
                for i, c in enumerate(self.cuentas)
            ]
            return {'data': datos, 'meta': {'pagination': {'total_pages': 1}}}
        if (
            self.devolver_publicadas
            and ruta.startswith('/api/v1/transactions')
            and metodo == 'GET'
        ):
            datos = [
                {'id': str(900 + i), 'attributes': {'transactions': [t]}}
                for i, t in enumerate(self.transacciones())
            ]
            return {'data': datos, 'meta': {'pagination': {'total_pages': 1}}}
        if ruta.startswith('/api/v1/categories'):
            datos = [
                {'attributes': {'name': n}}
                for n in ('Comida', 'Cuidado personal', 'Transporte')
            ]
            return {'data': datos, 'meta': {'pagination': {'total_pages': 1}}}
        if metodo == 'POST':
            if self.falla_al_crear:
                self.falla_al_crear -= 1
                raise firefly.ApiError(500, 'Firefly se cayo')
            return {'data': {'id': '901'}}
        return {'data': [], 'meta': {'pagination': {'total_pages': 1}}}

    def creadas(self):
        return [(tok, r) for m, r, tok in self.llamadas if m == 'POST']

    def transacciones(self):
        return [
            c['transactions'][0]
            for m, r, c in self.cuerpos
            if m == 'POST' and r == '/api/v1/transactions'
        ]


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
    monkeypatch.setattr(bot_libros, 'ESPERANDO', {})

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
        directos = [b[0] for b in tg.botones() if b[1].startswith('ld:')]
        assert len(directos) == 1 and 'Golden Beauty' in directos[0]
        # «Personal» solo aparece como lo que es: algo suyo que pago el estudio.
        cruzados = [b for b in tg.botones() if b[1].startswith('lq:')]
        assert [b[0] for b in cruzados] == ['📒 Personal (lo pagó Golden Beauty)']

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


def _venta(id_, fecha, monto, notas):
    """Una venta como la sube el CRM a Actual."""
    return {
        'id': id_,
        'account': 'acc-banc',
        'date': str(fecha),
        'amount': int(monto * 100),
        'imported_id': f'agendapro-tx:{id_}',
        'notes': notas,
    }


class TestLaVentaDeAgendapro:
    def _transferencia(
        self, alm, ella, fecha=date(2026, 9, 23), valor=40000.0, eid='a9'
    ):
        return _alerta(
            alm,
            ella,
            eid,
            tipo='transferencia_entrada',
            valor=valor,
            instrumento='5788',
            clase_instrumento='cuenta',
            contraparte='JERONIMO RUIZ HENAO',
            fecha=fecha,
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
                'notes': 'Marisol Pérez · Semipermanente pies · Venta 1150 · transferencia',
            }
        )
        pid = self._transferencia(alm, ella)
        _toque(alm, f'ld:{pid}:{_libro(alm, ella, "estudio")}')
        pregunta = tg.ultimo()[1]
        assert '¿Es esa venta?' in pregunta
        assert 'Marisol Pérez · Semipermanente pies' in pregunta, 'se ve de quien es'
        _toque(alm, f'va:{pid}:1')
        confirmacion = tg.editados[-1][1]
        assert 'Es la venta de Agendapro' in confirmacion
        assert 'Marisol Pérez' in confirmacion, 'y la confirmacion dice cual enlazo'
        assert act.creadas == [], 'no se cuenta dos veces'
        p = alm.pendiente(pid)
        assert (p['estado'], p['firefly_id'], p['decidido_por']) == (
            'publicado',
            'ag-1',
            'venta_agendapro',
        )

    def test_si_todavia_no_esta_se_espera_y_no_se_publica(self, mundo):
        alm, tg, _ff, act, _juan, ella = mundo
        ayer = fechas.hoy() - timedelta(days=1)
        pid = self._transferencia(alm, ella, fecha=ayer)
        _toque(alm, f'ld:{pid}:{_libro(alm, ella, "estudio")}')
        _toque(alm, f'va:{pid}:2')
        publicador.publicar_pendientes(alm.cx, dry_run=False)
        assert act.creadas == []
        # esa noche Agendapro la sube, y la siguiente pasada la enlaza
        act.tx.append(
            {
                'id': 'ag-2',
                'account': 'acc-banc',
                'date': str(ayer),
                'amount': 4000000,
                'imported_id': 'agendapro-tx:9',
                'notes': 'Laura Gómez · Retoque · Venta 9 · transferencia',
            }
        )
        assert bot_libros.revisar_ventas_en_espera(alm.cx) == 1
        assert 'Laura Gómez · Retoque' in tg.ultimo()[1], 'el aviso dice de quien era'
        assert alm.pendiente(pid)['firefly_id'] == 'ag-2'
        assert act.creadas == []

    def test_sin_venta_igual_ella_elige_una_cercana(self, mundo):
        """Lo de Dayana: la venta esta en Agendapro, pero no igual (otro monto
        u otro dia). Antes solo podia decir «espera» y el bot volvia a
        preguntar; ahora la ve y la toca."""
        alm, tg, _ff, act, _juan, ella = mundo
        act.tx.append(
            _venta(
                'ag-d',
                date(2026, 9, 27),
                100000,
                'Dayana Vargas · Semipermanente pies y manos · Venta 1163 · transferencia',
            )
        )
        pid = self._transferencia(alm, ella, valor=105000.0)
        _toque(alm, f'ld:{pid}:{_libro(alm, ella, "estudio")}')
        botones = [b[0] for b in tg.botones()]
        assert any('Dayana Vargas' in b for b in botones), botones
        assert not any('todavía no está' in b for b in botones), 'vieja: no se espera'
        _toque(alm, f'vc:{pid}:0')
        p = alm.pendiente(pid)
        assert (p['estado'], p['firefly_id']) == ('publicado', 'ag-d')
        assert act.creadas == []
        assert 'llegaron' in tg.editados[-1][1], 'avisa que el monto no cuadra'

    def test_una_venta_ya_enlazada_no_se_vuelve_a_ofrecer(self, mundo):
        alm, tg, _ff, act, _juan, ella = mundo
        act.tx.append(
            _venta('ag-1', date(2026, 9, 23), 40000, 'Ana · Retoque · Venta 1')
        )
        estudio = _libro(alm, ella, 'estudio')
        primera = self._transferencia(alm, ella)
        _toque(alm, f'ld:{primera}:{estudio}')
        _toque(alm, f'va:{primera}:1')
        segunda = self._transferencia(alm, ella, eid='a10')
        _toque(alm, f'ld:{segunda}:{estudio}')
        assert '¿Es esa venta?' not in tg.ultimo()[1]
        assert not any('Ana' in b[0] for b in tg.botones())

    def test_la_espera_vencida_pregunta_una_vez_y_sin_volver_a_esperar(self, mundo):
        """El bucle: la espera contaba desde la transferencia, asi que una
        vieja vencia en la pasada siguiente, y la pregunta volvia a ofrecer
        «espera»."""
        alm, tg, _ff, _act, _juan, ella = mundo
        ayer = fechas.hoy() - timedelta(days=1)
        pid = self._transferencia(alm, ella, fecha=ayer)
        _toque(alm, f'ld:{pid}:{_libro(alm, ella, "estudio")}')
        _toque(alm, f'va:{pid}:2')
        enviados = len(tg.enviados)
        bot_libros.revisar_ventas_en_espera(alm.cx)
        assert len(tg.enviados) == enviados, 'recien dijo espera: no se pregunta'

        hace = fechas.hoy() - timedelta(days=bot_libros.DIAS_DE_ESPERA + 1)
        alm.cx.execute(
            'UPDATE pendientes SET fecha = ?, actualizado_en = ? WHERE id = ?',
            (str(hace), str(hace), pid),
        )
        bot_libros.revisar_ventas_en_espera(alm.cx)
        assert 'no apareció' in tg.enviados[-2][1]
        assert not any('todavía no está' in b[0] for b in tg.botones())
        # un boton viejo de «espera» tampoco la vuelve a meter en la espera
        _toque(alm, f'va:{pid}:2')
        assert alm.pendiente(pid)['decidido_por'] != 'espera_agendapro'
        assert alm.en_espera_de_agendapro() == []

    def test_una_venta_pagada_en_dos_transferencias(self, mundo):
        """Dayana pago su venta de 100.000 en dos: 30.000 y 70.000. La venta
        sigue ofreciendose hasta que lo enlazado la cubre."""
        alm, tg, _ff, act, _juan, ella = mundo
        act.tx.append(
            _venta(
                'ag-d', date(2026, 9, 25), 100000, 'Dayana Vargas · Semi · Venta 1163'
            )
        )
        estudio = _libro(alm, ella, 'estudio')
        for eid, valor in (('a1', 30000.0), ('a2', 70000.0)):
            pid = self._transferencia(alm, ella, valor=valor, eid=eid)
            _toque(alm, f'ld:{pid}:{estudio}')
            botones = [b[0] for b in tg.botones()]
            assert any('Dayana' in b for b in botones), (eid, botones)
            _toque(alm, f'vc:{pid}:0')
            assert alm.pendiente(pid)['firefly_id'] == 'ag-d'
        tercera = self._transferencia(alm, ella, valor=70000.0, eid='a3')
        _toque(alm, f'ld:{tercera}:{estudio}')
        assert not any('Dayana' in b[0] for b in tg.botones()), 'ya esta pagada'
        assert act.creadas == []

    def test_elegir_servicios_no_escribe_la_venta(self, mundo):
        """Angel y Veronica: la venta no estaba aun, ella dijo «otra cosa» y
        eligio Servicios, y el bot la escribio; esa noche Agendapro subio la
        suya y quedo doble. Ahora espera a Agendapro."""
        alm, tg, _ff, act, _juan, ella = mundo
        pid = self._transferencia(alm, ella)
        _toque(alm, f'ld:{pid}:{_libro(alm, ella, "estudio")}')
        _toque(alm, f'va:{pid}:0')
        _toque(alm, f'kc:{pid}:{[b[0] for b in tg.botones()].index("Servicios")}')
        assert act.creadas == [], 'las ventas las escribe Agendapro'
        assert alm.pendiente(pid)['decidido_por'] == 'espera_agendapro'
        assert 'Agendapro' in tg.ultimo()[1]


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


# ------------------------------------------------------ pagado con la otra plata


class TestPagadoConLaPlataDelOtroLibro:
    """El estudio no se sostiene solo: la Nu, que es personal, le paga cosas."""

    def _nu_para_el_estudio(self, alm, tg, ella):
        _texto(alm, '65 mil de insumos con la nu')
        (p,) = alm.cx.execute(
            "SELECT * FROM pendientes WHERE origen = 'chat'"
        ).fetchall()
        aporte = [b for b in tg.botones() if b[1].startswith('lp:')]
        assert [b[0] for b in aporte] == ['💅 Golden Beauty (lo pagué yo)']
        _toque(alm, aporte[0][1])
        cats = [b[0] for b in tg.botones()]
        assert 'Insumos' in cats, 'se pregunta con las categorias del estudio'
        return p['id'], cats

    def test_un_gasto_del_estudio_con_la_nu_queda_en_los_dos_libros(self, mundo):
        alm, tg, ff, act, _juan, ella = mundo
        pid, cats = self._nu_para_el_estudio(alm, tg, ella)
        _toque(alm, f'kc:{pid}:{cats.index("Insumos")}')

        (tx,) = act.creadas
        assert (tx['account'], tx['amount']) == ('acc-aportes', 0), (
            'no mueve cuentas reales'
        )
        partes = {s['category']: s['amount'] for s in tx['subtransactions']}
        assert partes == {'cat-aporte': 6500000, 'cat-ins': -6500000}
        (creado,) = [x for x in ff.llamadas if x[0] == 'POST']
        assert creado[2] == 'tok-mariana'
        p = alm.pendiente(pid)
        assert (p['estado'], p['pago_libro_id']) == (
            'publicado',
            _libro(alm, ella, 'personal'),
        )
        assert 'como aporte tuyo' in tg.ultimo()[1]

    def test_si_firefly_falla_reintentar_no_duplica_actual(self, mundo):
        alm, tg, ff, act, _juan, ella = mundo
        ff.falla_al_crear = 1
        pid, cats = self._nu_para_el_estudio(alm, tg, ella)
        _toque(alm, f'kc:{pid}:{cats.index("Insumos")}')
        assert alm.pendiente(pid)['estado'] == 'error'
        assert len(act.creadas) == 1

        publicador.publicar_pendientes(alm.cx, dry_run=False)

        assert len(act.creadas) == 1, 'Actual ya lo tenia: no se escribe otra vez'
        assert len([x for x in ff.llamadas if x[0] == 'POST']) == 2
        assert alm.pendiente(pid)['estado'] == 'publicado'

    def test_algo_personal_con_la_tarjeta_del_estudio_es_un_pago_a_la_duena(
        self, mundo
    ):
        alm, tg, ff, act, _juan, ella = mundo
        pid = _alerta(alm, ella, 'a1', contraparte='FARMACIA', descripcion='FARMACIA')
        bot.preguntar_pendientes(alm.cx)
        (b,) = [b for b in tg.botones() if b[1].startswith('lq:')]
        _toque(alm, b[1])
        (tx,) = act.creadas
        assert (tx['account'], tx['category'], tx['amount']) == (
            'acc-tc',
            'cat-sal',
            -6500000,
        )
        assert ff.creadas() == [], 'en Firefly no se movio plata suya'
        assert alm.pendiente(pid)['estado'] == 'publicado'

    def test_el_libro_que_paga_no_puede_ser_de_otra_persona(self, mundo):
        alm, _tg, _ff, _act, juan, ella = mundo
        pid = _alerta(alm, ella, 'a1')
        with pytest.raises(sqlite3.IntegrityError, match='otra persona'):
            alm.actualizar_pendiente(pid, pago_libro_id=_libro(alm, juan, 'personal'))


def test_si_dijo_el_estudio_no_se_marca_el_personal(mundo):
    """«65 mil de insumos con la nu para el salon»: la Nu solo tiene cuenta en
    lo personal, pero ella dijo el salon. Se marca el boton del aporte, nunca
    el personal."""
    alm, tg, _ff, _act, _juan, ella = mundo
    alm.crear_pendiente(
        usuario_id=ella,
        origen='chat',
        referencia='777:1',
        external_id='tg-x',
        tipo='gasto_contado',
        fecha='2026-09-27',
        valor=-65000.0,
        instrumento='nu',
        sugerido_libro_id=_libro(alm, ella, 'estudio'),
        pregunta='destino',
    )
    alm.cx.commit()
    bot.preguntar_pendientes(alm.cx)
    marcados = [b[0] for b in tg.botones() if '✓' in b[0]]
    assert marcados == ['💅 Golden Beauty (lo pagué yo) ✓']


# ------------------------------------------------------- categorias nuevas


def _dato(tg, empieza):
    """El callback del boton cuyo texto empieza asi."""
    return next(b[1] for b in tg.botones() if b[0].startswith(empieza))


class TestCategoriaNueva:
    """Una cuenta nueva arranca con pocas categorias. Antes lo escrito que no
    existia moria en «no es una categoria»; ahora se puede crear."""

    def _hasta_la_categoria(self, alm, tg, ella, libro, **ev):
        pid = _alerta(alm, ella, 'a1', **ev)
        _toque(alm, f'ld:{pid}:{_libro(alm, ella, libro)}')
        assert '¿Qué categoría?' in tg.ultimo()[1]
        return pid

    def test_escrita_en_firefly_se_crea_al_publicar(self, mundo):
        alm, tg, ff, _act, _juan, ella = mundo
        pid = self._hasta_la_categoria(
            alm, tg, ella, 'personal', instrumento='5788', clase_instrumento='cuenta'
        )
        _toque(alm, f'kt:{pid}:0')
        _texto(alm, 'mascotas', responde_a=tg.n)
        assert 'no es una categoría de Personal' in tg.ultimo()[1]
        _toque(alm, _dato(tg, '\N{HEAVY PLUS SIGN} Crear «Mascotas»'))
        (tx,) = ff.transacciones()
        assert tx['category_name'] == 'Mascotas'
        assert alm.pendiente(pid)['estado'] == 'publicado'

    def test_en_actual_pregunta_el_grupo_y_la_crea_ahi(self, mundo):
        alm, tg, _ff, act, _juan, ella = mundo
        pid = self._hasta_la_categoria(alm, tg, ella, 'estudio')
        _toque(alm, f'kt:{pid}:0')
        _texto(alm, 'Lavandería', responde_a=tg.n)
        _toque(alm, _dato(tg, '\N{HEAVY PLUS SIGN} Crear'))
        assert 'grupo' in tg.ultimo()[1]
        assert act.categorias_creadas == [], 'todavia no: falta el grupo'
        _toque(alm, _dato(tg, 'Gastos Fijos'))
        (nueva,) = act.categorias_creadas
        assert (nueva['name'], nueva['group_id']) == ('Lavandería', 'g-fijos')
        (tx,) = act.creadas
        assert tx['category'] == nueva['id']

    def test_escrita_sin_responder_al_mensaje_sirve(self, mundo):
        alm, tg, ff, _act, _juan, ella = mundo
        pid = self._hasta_la_categoria(
            alm, tg, ella, 'personal', instrumento='5788', clase_instrumento='cuenta'
        )
        _toque(alm, f'kt:{pid}:0')
        _texto(alm, 'mascotas', mid=60)
        assert 'no es una categoría de Personal' in tg.ultimo()[1]
        _toque(alm, _dato(tg, '\N{HEAVY PLUS SIGN} Crear «Mascotas»'))
        (tx,) = ff.transacciones()
        assert tx['category_name'] == 'Mascotas'

    def test_en_seco_la_nueva_vuelve_a_salir_con_su_grupo(self, mundo):
        """En seco no se crea, y antes tampoco volvia a salir en la lista: con
        cada movimiento habia que crearla otra vez."""
        alm, tg, _ff, act, _juan, ella = mundo
        alm.cx.execute("UPDATE libros SET en_serio = 0 WHERE clave = 'estudio'")
        pid = self._hasta_la_categoria(alm, tg, ella, 'estudio')
        _toque(alm, f'kt:{pid}:0')
        _texto(alm, 'Lavandería', mid=60)
        _toque(alm, _dato(tg, '\N{HEAVY PLUS SIGN} Crear'))
        _toque(alm, _dato(tg, 'Gastos Fijos'))

        otro = _alerta(
            alm, ella, 'a2', valor=-12000.0, contraparte='OTRA', descripcion='OTRA'
        )
        _toque(alm, f'ld:{otro}:{_libro(alm, ella, "estudio")}')
        _toque(alm, _dato(tg, 'Lavandería'))
        p = alm.pendiente(otro)
        assert (p['categoria'], p['categoria_grupo']) == ('Lavandería', 'g-fijos')
        assert act.categorias_creadas == []

        # ya en serio, se crea una sola vez para los dos
        alm.cx.execute("UPDATE libros SET en_serio = 1 WHERE clave = 'estudio'")
        alm.cx.commit()
        for x in (pid, otro):
            publicador.publicar_en_su_libro(alm.cx, x)
        assert [c['name'] for c in act.categorias_creadas] == ['Lavandería']
        assert len(act.creadas) == 2

    def test_un_libro_en_seco_no_crea_la_categoria(self, mundo):
        alm, tg, _ff, act, _juan, ella = mundo
        alm.cx.execute("UPDATE libros SET en_serio = 0 WHERE clave = 'estudio'")
        pid = self._hasta_la_categoria(alm, tg, ella, 'estudio')
        _toque(alm, f'kt:{pid}:0')
        _texto(alm, 'Lavandería', responde_a=tg.n)
        _toque(alm, _dato(tg, '\N{HEAVY PLUS SIGN} Crear'))
        _toque(alm, _dato(tg, 'Gastos Fijos'))
        assert act.categorias_creadas == [] and act.creadas == []
        assert 'En prueba' in tg.ultimo()[1]
        p = alm.pendiente(pid)
        assert (p['categoria'], p['categoria_grupo']) == ('Lavandería', 'g-fijos'), (
            'queda lista para crearla cuando el libro vaya en serio'
        )

    def test_la_ia_la_propone_mirando_la_descripcion(self, mundo, monkeypatch):
        alm, tg, ff, _act, _juan, ella = mundo
        monkeypatch.setattr(bot_libros.ia, 'disponible', lambda: True)
        visto = {}

        def proponer(mov, existentes, libro=''):
            visto.update(mov=mov, existentes=list(existentes))
            return {
                'existente': None,
                'nueva': 'Mascotas',
                'razon': 'es una veterinaria',
            }

        monkeypatch.setattr(bot_libros.ia, 'proponer_categoria', proponer)
        pid = self._hasta_la_categoria(
            alm,
            tg,
            ella,
            'personal',
            instrumento='5788',
            clase_instrumento='cuenta',
            contraparte='VETERINARIA HUELLITAS',
        )
        assert visto['mov']['contraparte'] == 'VETERINARIA HUELLITAS'
        assert 'Comida' in visto['existentes']
        assert 'es una veterinaria' in tg.ultimo()[1]
        _toque(alm, _dato(tg, '\N{HEAVY PLUS SIGN} Nueva: «Mascotas»'))
        assert ff.transacciones()[0]['category_name'] == 'Mascotas'
        assert alm.pendiente(pid)['estado'] == 'publicado'

    def test_si_la_ia_marca_una_existente_no_se_mueve_su_indice(
        self, mundo, monkeypatch
    ):
        """La marcada se dibuja primero, pero su boton sigue apuntando a ella:
        un boton de una pregunta anterior no cae en otra categoria."""
        alm, tg, ff, _act, _juan, ella = mundo
        monkeypatch.setattr(bot_libros.ia, 'disponible', lambda: True)
        monkeypatch.setattr(
            bot_libros.ia,
            'proponer_categoria',
            lambda *a, **k: {'existente': 'Transporte', 'nueva': None, 'razon': 'r'},
        )
        pid = self._hasta_la_categoria(
            alm, tg, ella, 'personal', instrumento='5788', clase_instrumento='cuenta'
        )
        primero = tg.botones()[0]
        assert primero[0] == 'Transporte ✓'
        assert alm.sugerencias(pid)[int(primero[1].split(':')[2])] == 'Transporte'
        assert alm.sugerencias(pid)[0] == 'Comida', 'la lista guardada no se reordena'
        _toque(alm, primero[1])
        assert ff.transacciones()[0]['category_name'] == 'Transporte'

    def test_escribir_una_que_existe_la_usa_sin_preguntar(self, mundo):
        alm, tg, ff, _act, _juan, ella = mundo
        pid = self._hasta_la_categoria(
            alm, tg, ella, 'personal', instrumento='5788', clase_instrumento='cuenta'
        )
        _toque(alm, f'kt:{pid}:0')
        _texto(alm, 'transporte', responde_a=tg.n)
        assert ff.transacciones()[0]['category_name'] == 'Transporte'

    def test_una_frase_larga_no_se_vuelve_categoria(self, mundo):
        alm, tg, ff, _act, _juan, ella = mundo
        pid = self._hasta_la_categoria(
            alm, tg, ella, 'personal', instrumento='5788', clase_instrumento='cuenta'
        )
        _toque(alm, f'kt:{pid}:0')
        _texto(alm, 'fue lo del regalo de cumpleaños de la vecina', responde_a=tg.n)
        assert not any('Crear' in b[0] for b in tg.botones())
        assert ff.transacciones() == []


# ------------------------------------------------------------------ prestamos


class TestPrestamos:
    """El jefe le pide que le pase plata a una cuenta, y se la devuelve. No es
    gasto ni ingreso: es un traslado a «Préstamos», y el saldo es lo que le
    deben."""

    def _salida(self, alm, ella, eid='p1', valor=-300000.0, **ev):
        base = {
            'tipo': 'transferencia_salida',
            'valor': valor,
            'instrumento': '5788',
            'clase_instrumento': 'cuenta',
            'contraparte': 'CARLOS ANDRES MEJIA',
            'descripcion': 'Transferencia a CARLOS ANDRES MEJIA',
        }
        base.update(ev)
        return _alerta(alm, ella, eid, **base)

    def test_prestamo_y_devolucion_en_firefly(self, mundo):
        alm, tg, ff, act, _juan, ella = mundo
        personal = _libro(alm, ella, 'personal')
        pid = self._salida(alm, ella)
        _toque(alm, f'ld:{pid}:{personal}')
        _toque(alm, f'kl:{pid}:0')
        assert '¿A quién se la prestaste' in tg.ultimo()[1]
        _texto(alm, 'mi jefe', responde_a=tg.n)

        (tx,) = ff.transacciones()
        assert tx['type'] == 'transfer'
        assert (tx['source_name'], tx['destination_name']) == (
            'Ahorros *5788',
            'Préstamos',
        )
        assert 'category_name' not in tx, 'un prestamo no es un gasto'
        assert tx['description'].startswith('Préstamo · Jefe')
        (cuenta,) = ff.cuentas
        assert (cuenta['name'], cuenta['type']) == ('Préstamos', 'asset')
        assert 'Jefe te debe' in tg.ultimo()[1]
        assert act.creadas == []

        # a los dias se la devuelve
        vuelta = self._salida(
            alm,
            ella,
            'p2',
            valor=300000.0,
            tipo='transferencia_entrada',
            fecha=date(2026, 9, 24),
        )
        _toque(alm, f'ld:{vuelta}:{personal}')
        devolucion = tg.botones()[0]
        assert devolucion[0].startswith('🤝 Devolución · Jefe') and '✓' in devolucion[0]
        _toque(alm, devolucion[1])

        tx2 = ff.transacciones()[1]
        assert (tx2['type'], tx2['source_name'], tx2['destination_name']) == (
            'transfer',
            'Préstamos',
            'Ahorros *5788',
        )
        assert len(ff.cuentas) == 1, 'la cuenta no se crea dos veces'
        assert 'paz y salvo' in tg.ultimo()[1]
        _texto(alm, '/prestamos', mid=99)
        assert 'No hay préstamos abiertos' in tg.ultimo()[1]

    def test_en_actual_es_un_traslado_a_su_cuenta_de_prestamos(self, mundo):
        alm, tg, _ff, act, _juan, ella = mundo
        pid = self._salida(alm, ella)
        _toque(alm, f'ld:{pid}:{_libro(alm, ella, "estudio")}')
        _toque(alm, f'kl:{pid}:0')
        _texto(alm, 'Laura', responde_a=tg.n)
        (cuenta,) = act.cuentas_creadas
        assert (cuenta['name'], cuenta['offbudget']) == ('Préstamos', False)
        (tx,) = act.creadas
        assert (tx['account'], tx['amount']) == ('acc-banc', -30000000)
        assert tx['payee'] == f'pay-{cuenta["id"]}'
        assert 'category' not in tx

    def test_una_entrada_al_estudio_que_devuelve_un_prestamo_no_pregunta_agendapro(
        self, mundo
    ):
        alm, tg, _ff, _act, _juan, ella = mundo
        estudio = _libro(alm, ella, 'estudio')
        pid = self._salida(alm, ella)
        _toque(alm, f'ld:{pid}:{estudio}')
        _toque(alm, f'kl:{pid}:0')
        _texto(alm, 'Laura', responde_a=tg.n)
        vuelta = self._salida(
            alm, ella, 'p2', valor=300000.0, tipo='transferencia_entrada'
        )
        _toque(alm, f'ld:{vuelta}:{estudio}')
        assert '¿Qué categoría?' in tg.ultimo()[1], 'no es una venta'
        assert tg.botones()[0][0].startswith('🤝 Devolución · Laura')

    def test_una_devolucion_que_no_cuadra_no_va_marcada(self, mundo):
        alm, tg, _ff, _act, _juan, ella = mundo
        personal = _libro(alm, ella, 'personal')
        pid = self._salida(alm, ella)
        _toque(alm, f'ld:{pid}:{personal}')
        _toque(alm, f'kl:{pid}:0')
        _texto(alm, 'jefe', responde_a=tg.n)
        parcial = self._salida(
            alm, ella, 'p2', valor=100000.0, tipo='transferencia_entrada'
        )
        _toque(alm, f'ld:{parcial}:{personal}')
        (devolucion,) = [b for b in tg.botones() if 'Devolución' in b[0]]
        assert '✓' not in devolucion[0]
        _toque(alm, devolucion[1])
        assert 'Jefe te debe' in tg.ultimo()[1] and '200.000' in tg.ultimo()[1]

    def test_lo_que_cuenta_como_prestamo_se_preselecciona(self, mundo, monkeypatch):
        alm, tg, ff, _act, _juan, ella = mundo
        monkeypatch.setattr(bot_libros.ia, 'disponible', lambda: True)
        monkeypatch.setattr(
            bot_libros.ia,
            'entender_contado',
            lambda *a, **k: {
                'es_movimiento': True,
                'direccion': 'gasto',
                'monto': 200000,
                'comercio': '',
                'descripcion': 'préstamo al jefe',
                'fecha': '2026-09-26',
                'medio': '5788',
                'libro': 'no_dijo',
                'prestamo': 'mi jefe',
                'transcripcion': 'le presté 200 mil a mi jefe',
            },
        )
        _texto(alm, 'le presté 200 mil a mi jefe')
        (p,) = alm.cx.execute("SELECT * FROM pendientes WHERE origen = 'chat'")
        assert p['prestamo_con'] == 'Jefe' and p['cuenta_destino'] is None, (
            'lo que dijo solo sugiere'
        )
        _toque(alm, f'ld:{p["id"]}:{_libro(alm, ella, "personal")}')
        assert tg.botones()[0][0] == '🤝 Préstamo con Jefe ✓'
        assert ff.transacciones() == [], 'nada se publica sin el toque'
        _toque(alm, tg.botones()[0][1])
        assert ff.transacciones()[0]['destination_name'] == 'Préstamos'

    def test_elegir_una_categoria_quita_la_sugerencia_de_prestamo(
        self, mundo, monkeypatch
    ):
        alm, tg, ff, _act, _juan, ella = mundo
        pid = self._salida(alm, ella)
        alm.actualizar_pendiente(pid, prestamo_con='Jefe')
        alm.cx.commit()
        _toque(alm, f'ld:{pid}:{_libro(alm, ella, "personal")}')
        _toque(alm, _dato(tg, 'Comida'))
        tx = ff.transacciones()[0]
        assert (tx['type'], tx['category_name']) == ('withdrawal', 'Comida')
        assert alm.pendiente(pid)['prestamo_con'] is None

    def test_cambiar_de_libro_deshace_el_prestamo(self, mundo):
        alm, tg, _ff, _act, _juan, ella = mundo
        alm.cx.execute('UPDATE libros SET en_serio = 0')
        pid = self._salida(alm, ella)
        _toque(alm, f'ld:{pid}:{_libro(alm, ella, "personal")}')
        _toque(alm, f'kl:{pid}:0')
        _texto(alm, 'jefe', responde_a=tg.n)
        assert 'En prueba' in tg.ultimo()[1]
        _toque(alm, f'lr:{pid}:0')
        p = alm.pendiente(pid)
        assert (p['libro_id'], p['cuenta_destino']) == (None, None)
        assert alm.saldos_de_prestamos(ella, _libro(alm, ella, 'personal')) == []

    def test_el_nombre_sin_responder_al_mensaje_sirve(self, mundo):
        """Casi nadie desliza para responder: escribe el nombre y ya. Antes eso
        se leia como un movimiento nuevo, contestaba la ayuda y el prestamo
        nunca se hacia; en el primero ni siquiera hay un boton de persona."""
        alm, tg, ff, _act, _juan, ella = mundo
        pid = self._salida(alm, ella)
        _toque(alm, f'ld:{pid}:{_libro(alm, ella, "personal")}')
        _toque(alm, f'kl:{pid}:0')
        _texto(alm, 'mi jefe', mid=60)
        assert alm.pendiente(pid)['prestamo_con'] == 'Jefe'
        assert 'Jefe te debe' in tg.ultimo()[1]
        (tx,) = ff.transacciones()
        assert tx['type'] == 'transfer'

    def test_en_seco_el_prestamo_queda_y_cuenta_en_el_saldo(self, mundo):
        alm, tg, ff, _act, _juan, ella = mundo
        alm.cx.execute("UPDATE libros SET en_serio = 0 WHERE clave = 'personal'")
        pid = self._salida(alm, ella)
        _toque(alm, f'ld:{pid}:{_libro(alm, ella, "personal")}')
        _toque(alm, f'kl:{pid}:0')
        _texto(alm, 'jefe', mid=60)
        assert 'En prueba' in tg.ultimo()[1] and 'Jefe te debe' in tg.ultimo()[1]
        assert ff.transacciones() == []
        _texto(alm, '/prestamos', mid=61)
        assert 'Jefe te debe' in tg.ultimo()[1]

    def test_un_movimiento_con_monto_no_se_toma_por_el_nombre(self, mundo):
        alm, _tg, _ff, _act, _juan, ella = mundo
        pid = self._salida(alm, ella)
        _toque(alm, f'ld:{pid}:{_libro(alm, ella, "personal")}')
        _toque(alm, f'kl:{pid}:0')
        _texto(alm, '45 mil de esmaltes con la nu', mid=60)
        assert alm.pendiente(pid)['prestamo_con'] is None
        assert (
            alm.cx.execute(
                "SELECT count(*) FROM pendientes WHERE origen = 'chat'"
            ).fetchone()[0]
            == 1
        )

    def test_otro_toque_olvida_que_esperaba_el_nombre(self, mundo):
        alm, _tg, _ff, _act, _juan, ella = mundo
        pid = self._salida(alm, ella)
        _toque(alm, f'ld:{pid}:{_libro(alm, ella, "personal")}')
        _toque(alm, f'kl:{pid}:0')
        _toque(alm, f'kv:{pid}:0')
        _texto(alm, 'jefe', mid=60)
        assert alm.pendiente(pid)['prestamo_con'] is None

    def test_la_devolucion_del_mismo_dia_no_es_un_duplicado(self, mundo):
        """El anti-duplicado de Firefly compara monto y cuenta. La devolucion es
        el mismo monto por la misma cuenta, al reves: no es un duplicado."""
        alm, tg, ff, _act, _juan, ella = mundo
        ff.devolver_publicadas = True
        personal = _libro(alm, ella, 'personal')
        pid = self._salida(alm, ella)
        _toque(alm, f'ld:{pid}:{personal}')
        _toque(alm, f'kl:{pid}:0')
        _texto(alm, 'jefe', responde_a=tg.n)
        vuelta = self._salida(
            alm, ella, 'p2', valor=300000.0, tipo='transferencia_entrada'
        )
        _toque(alm, f'ld:{vuelta}:{personal}')
        _toque(alm, tg.botones()[0][1])
        assert len(ff.transacciones()) == 2
        assert alm.pendiente(vuelta)['estado'] == 'publicado'


class TestPrestamosDeJuan:
    """En el libro de Juan la alerta se publica apenas llega. Un prestamo se
    decide despues: lo escrito como gasto se borra y vuelve como traslado."""

    def _publicada(self, alm, juan, ff, eid='j1', valor=-500000.0, **ev):
        pid = _alerta(
            alm,
            juan,
            eid,
            valor=valor,
            tipo='transferencia_salida',
            instrumento='1234',
            clase_instrumento='cuenta',
            contraparte='PEDRO PEREZ',
            descripcion='Transferencia a PEDRO PEREZ',
            **ev,
        )
        alm.actualizar_pendiente(pid, cuenta_firefly='Ahorros Juan')
        alm.cx.commit()
        publicador.publicar_pendientes(alm.cx, dry_run=False)
        assert alm.pendiente(pid)['estado'] == 'publicado'
        return pid

    def test_un_gasto_ya_publicado_se_vuelve_prestamo(self, mundo):
        alm, tg, ff, _act, juan, _ella = mundo
        pid = self._publicada(alm, juan, ff)
        assert ff.transacciones()[0]['type'] == 'withdrawal'
        bot.preguntar_pendientes(alm.cx)
        chat, _texto_enviado, _ = tg.ultimo()
        assert chat == JUAN
        _toque(alm, _dato(tg, '🤝 préstamo'), chat=JUAN)
        _texto(alm, 'Pedro', chat=JUAN, responde_a=tg.n)

        borrados = [r for m, r, _ in ff.llamadas if m == 'DELETE']
        assert borrados == ['/api/v1/transactions/901']
        tx = ff.transacciones()[-1]
        assert (tx['type'], tx['source_name'], tx['destination_name']) == (
            'transfer',
            'Ahorros Juan',
            'Préstamos',
        )
        p = alm.pendiente(pid)
        assert (p['estado'], p['prestamo_con'], p['pregunta']) == (
            'publicado',
            'Pedro',
            None,
        )
        assert 'Pedro te debe' in tg.ultimo()[1]
        tokens = {tok for m, _r, tok in ff.llamadas if m in ('POST', 'DELETE')}
        assert tokens == {'tok-juan'}, 'todo en el Firefly de Juan'

        _texto(alm, '/prestamos', chat=JUAN, mid=98)
        assert 'Pedro' in tg.ultimo()[1]

    def test_la_devolucion_le_sale_marcada(self, mundo):
        alm, tg, ff, _act, juan, _ella = mundo
        pid = self._publicada(alm, juan, ff)
        bot.preguntar_pendientes(alm.cx)
        _toque(alm, _dato(tg, '🤝 préstamo'), chat=JUAN)
        _texto(alm, 'Pedro', chat=JUAN, responde_a=tg.n)
        assert alm.pendiente(pid)['prestamo_con'] == 'Pedro'

        self._publicada(alm, juan, ff, 'j2', valor=500000.0, fecha=date(2026, 9, 25))
        bot.preguntar_pendientes(alm.cx)
        primero = tg.botones()[0]
        assert primero[0].startswith('🤝 Devolución · Pedro') and '✓' in primero[0]
        _toque(alm, primero[1], chat=JUAN)
        assert 'paz y salvo' in tg.ultimo()[1]

    def test_juan_tambien_puede_escribir_el_nombre_sin_responder(self, mundo):
        alm, tg, ff, _act, juan, _ella = mundo
        pid = self._publicada(alm, juan, ff)
        bot.preguntar_pendientes(alm.cx)
        _toque(alm, _dato(tg, '🤝 préstamo'), chat=JUAN)
        _texto(alm, 'Pedro', chat=JUAN, mid=70)
        assert alm.pendiente(pid)['prestamo_con'] == 'Pedro'

    def test_ella_no_puede_tocar_el_prestamo_de_juan(self, mundo):
        alm, _tg, ff, _act, juan, _ella = mundo
        pid = self._publicada(alm, juan, ff)
        _toque(alm, f'kl:{pid}:0', chat=ELLA)
        assert not any(m == 'DELETE' for m, _r, _t in ff.llamadas)
