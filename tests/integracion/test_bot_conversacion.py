"""Contestar hablando: la IA lee primero, pregunta de vuelta y aprende.

El pedido fue explicito: que al responderle un mensaje al bot, la IA
interprete, pregunte de vuelta lo que le falte y aprenda. Y con dos reglas:
nada se escribe en un libro sin confirmar con un toque, y una categoria nueva
solo se crea con ese toque.

Gemini es un doble que devuelve lo que diria; por debajo corre todo lo demas
de verdad: la validacion, el recorrido de libros, Actual y Firefly falsos.
"""

from __future__ import annotations

import pytest
from test_bot_libros import (  # mismo mundo: Mariana con sus dos libros
    ELLA,
    _alerta,
    _libro,
    _texto,
    _toque,
    mundo,  # noqa: F401  (fixture)
)

from finanzas.adaptadores import ia
from finanzas.aplicacion import conversacion
from finanzas.entrada import bot, bot_conversacion, bot_libros


class GeminiFalso:
    """Devuelve, en orden, lo que se le encola. Guarda con que lo llamaron."""

    def __init__(self):
        self.respuestas: list[dict] = []
        self.llamadas: list[dict] = []

    def __call__(self, movimiento, hilo, catalogos, *a, **k):
        self.llamadas.append(
            {'movimiento': movimiento, 'hilo': list(hilo), 'catalogos': catalogos, **k}
        )
        if not self.respuestas:
            raise ia.SinIA('nada encolado')
        r = self.respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        return {'siempre': False, 'confianza': 0.9, 'razon': 'porque si', **r}


@pytest.fixture
def gemini(mundo, monkeypatch):
    g = GeminiFalso()
    # bot_conversacion le habla a Telegram por su cuenta: sin esto la prueba
    # le escribe al Telegram de verdad.
    monkeypatch.setattr(bot_conversacion, 'telegram', mundo[1])
    monkeypatch.setattr(ia, 'disponible', lambda: True)
    monkeypatch.setattr(ia, 'entender_respuesta', g)
    # La propuesta de categoria de la pregunta de botones: aqui no importa.
    monkeypatch.setattr(
        ia, 'proponer_categoria', lambda *a, **k: {'existente': None, 'nueva': None}
    )
    return g


def _transferencia(alm, ella, eid='c1', **ev):
    """Sale de la cuenta de ahorros, que existe en los dos libros: se pregunta
    a cual va."""
    base = {
        'tipo': 'transferencia_salida',
        'valor': -200000.0,
        'instrumento': '5788',
        'clase_instrumento': 'cuenta',
        'contraparte': '3225458640',
        'descripcion': 'TRANSF A CUENTA 3225458640',
    }
    return _alerta(alm, ella, eid, **{**base, **ev})


def _pregunta_de(alm, tg, pid):
    """Hace la pregunta del bot y devuelve el id del mensaje."""
    bot_libros.siguiente(alm.cx, pid, ELLA)
    return tg.n


def _botones(tg):
    return {b[0]: b[1] for b in tg.botones()}


class TestLeePrimeroYConfirma:
    def test_contestar_el_libro_y_la_categoria_de_una_vez(self, mundo, gemini):
        alm, tg, ff, act, _juan, ella = mundo
        pid = _transferencia(alm, ella)
        mid = _pregunta_de(alm, tg, pid)
        gemini.respuestas.append(
            {
                'accion': 'proponer',
                'libro': 'Golden Beauty',
                'categoria': 'Insumos',
                'nota': 'Cuenta de la proveedora de esmaltes',
                'siempre': True,
            }
        )

        _texto(alm, 'eso es del estudio, le pagué los esmaltes', responde_a=mid)

        texto = tg.ultimo()[1]
        assert 'Golden Beauty' in texto and 'Insumos' in texto
        assert 'proveedora de esmaltes' in texto, 'dice lo que va a recordar'
        assert act.creadas == [] and ff.creadas() == [], 'nada sin el toque'
        assert alm.pendiente(pid)['libro_id'] is None

        _toque(alm, f'ic:{pid}:1')

        (creada,) = act.creadas
        assert creada['category'] == 'cat-ins'
        estudio = _libro(alm, ella, 'estudio')
        regla = alm.regla_de_libro(ella, conversacion.clave_de(alm.pendiente(pid)))
        assert (regla['libro_id'], regla['categoria']) == (estudio, 'Insumos')
        assert alm.nota(ella, '3225458640') == 'Cuenta de la proveedora de esmaltes'
        assert alm.turnos(pid) == [], 'el hilo se cierra al confirmar'

    def test_solo_esta_vez_no_deja_regla_pero_si_la_nota(self, mundo, gemini):
        alm, tg, _ff, act, _juan, ella = mundo
        pid = _transferencia(alm, ella)
        mid = _pregunta_de(alm, tg, pid)
        gemini.respuestas.append(
            {
                'accion': 'proponer',
                'libro': 'Golden Beauty',
                'categoria': 'Otros',
                'nota': 'Cuenta de la hermana',
            }
        )
        _texto(alm, 'esta vez fue algo del local', responde_a=mid)
        assert '✅ Sí, solo esta vez ✓' in _botones(tg), 'marca lo que entendio'

        _toque(alm, f'ic:{pid}:0')

        assert len(act.creadas) == 1
        assert alm.regla_de_libro(ella, '3225458640') is None
        assert alm.nota(ella, '3225458640') == 'Cuenta de la hermana'

    def test_una_categoria_nueva_solo_con_el_toque(self, mundo, gemini):
        alm, tg, _ff, act, _juan, ella = mundo
        pid = _transferencia(alm, ella)
        mid = _pregunta_de(alm, tg, pid)
        gemini.respuestas.append(
            {
                'accion': 'proponer',
                'libro': 'Golden Beauty',
                'categoria_nueva': 'arriendo local',
            }
        )
        _texto(alm, 'es el arriendo del local', responde_a=mid)
        assert 'Arriendo local' in tg.ultimo()[1]
        assert act.categorias_creadas == []

        _toque(alm, f'ic:{pid}:1')
        # El estudio tiene dos grupos de gasto: pregunta en cual, como siempre.
        assert '¿En qué grupo' in tg.ultimo()[1]


class TestPreguntaDeVuelta:
    def test_pregunta_y_sigue_el_hilo_sin_responder_al_mensaje(self, mundo, gemini):
        alm, tg, _ff, _act, _juan, ella = mundo
        pid = _transferencia(alm, ella)
        mid = _pregunta_de(alm, tg, pid)
        gemini.respuestas += [
            {'accion': 'preguntar', 'pregunta': '¿De quién es esa cuenta?'},
            {
                'accion': 'proponer',
                'libro': 'Personal',
                'prestamo_con': 'mi hermana',
                'nota': 'Cuenta de la hermana',
            },
        ]

        _texto(alm, 'le pasé plata', responde_a=mid)
        assert '¿De quién es esa cuenta?' in tg.ultimo()[1]

        # Escribe sin deslizar para responder: sigue siendo de este movimiento.
        _texto(alm, 'de mi hermana, me la devuelve el viernes', mid=51)

        segunda = gemini.llamadas[1]['hilo']
        assert segunda == [
            ('persona', 'le pasé plata'),
            ('bot', '¿De quién es esa cuenta?'),
            ('persona', 'de mi hermana, me la devuelve el viernes'),
        ]
        assert 'Préstamo con <b>Hermana</b>' in tg.ultimo()[1]

    def test_responder_a_la_pregunta_de_vuelta_tambien_cae_en_el_hilo(
        self, mundo, gemini
    ):
        alm, tg, *_rest, ella = mundo
        pid = _transferencia(alm, ella)
        mid = _pregunta_de(alm, tg, pid)
        gemini.respuestas += [
            {'accion': 'preguntar', 'pregunta': '¿Para qué fue?'},
            {'accion': 'proponer', 'libro': 'Personal', 'categoria': 'Comida'},
        ]
        _texto(alm, 'ni idea', responde_a=mid)
        bot_conversacion.HILO.clear()  # como si el proceso se hubiera reiniciado
        _texto(alm, 'un almuerzo', mid=51, responde_a=tg.n)
        assert len(gemini.llamadas) == 2
        assert 'Comida' in tg.ultimo()[1]

    def test_se_rinde_despues_de_tres_preguntas(self, mundo, gemini):
        alm, tg, *_rest, ella = mundo
        pid = _transferencia(alm, ella)
        mid = _pregunta_de(alm, tg, pid)
        gemini.respuestas += [
            {'accion': 'preguntar', 'pregunta': f'¿Pregunta {i}?'} for i in range(4)
        ]
        _texto(alm, 'mmm', responde_a=mid)
        for i in range(3):
            _texto(alm, f'no sé {i}', mid=60 + i)
        enviados = [t for _c, t, _b in tg.enviados]
        assert any('Mejor con los botones' in t for t in enviados)
        assert not any('¿Pregunta 3?' in t for t in enviados)

    def test_lo_que_aprendio_se_lo_da_la_proxima_vez(self, mundo, gemini):
        alm, tg, *_rest, ella = mundo
        alm.guardar_nota(ella, '3225458640', 'Cuenta de la hermana')
        pid = _transferencia(alm, ella)
        mid = _pregunta_de(alm, tg, pid)
        gemini.respuestas.append({'accion': 'preguntar', 'pregunta': '¿Préstamo?'})
        _texto(alm, 'lo de siempre', responde_a=mid)
        assert gemini.llamadas[0]['nota'] == 'Cuenta de la hermana'


class TestNoInventa:
    def test_una_categoria_de_otro_libro_no_pasa(self, mundo, gemini):
        """«Comida» es del Firefly personal: en el estudio no existe."""
        alm, tg, *_rest, ella = mundo
        pid = _transferencia(alm, ella)
        mid = _pregunta_de(alm, tg, pid)
        gemini.respuestas.append(
            {'accion': 'proponer', 'libro': 'Golden Beauty', 'categoria': 'Comida'}
        )
        _texto(alm, 'comida', responde_a=mid)
        assert '💬' in tg.ultimo()[1], 'sin nada valido que proponer, pregunta'
        assert conversacion.leer(alm, pid) is None

    def test_sin_libro_la_categoria_dice_cual_es(self, mundo, gemini):
        """«Insumos» solo existe en el estudio: ese es el libro."""
        alm, tg, *_rest, ella = mundo
        pid = _transferencia(alm, ella)
        mid = _pregunta_de(alm, tg, pid)
        gemini.respuestas.append({'accion': 'proponer', 'categoria': 'Insumos'})
        _texto(alm, 'insumos', responde_a=mid)
        lec = conversacion.leer(alm, pid)
        assert lec['libro_id'] == _libro(alm, ella, 'estudio')

    def test_un_boton_viejo_no_aplica_nada(self, mundo, gemini):
        alm, _tg, _ff, act, *_rest = mundo
        _toque(alm, 'ic:999:1')
        assert act.creadas == []


class TestRespaldo:
    def test_si_gemini_falla_sigue_el_camino_de_antes(self, mundo, gemini):
        alm, tg, *_rest, ella = mundo
        pid = _transferencia(alm, ella)
        mid = _pregunta_de(alm, tg, pid)
        gemini.respuestas.append(ia.SinIA('Gemini HTTP 503'))
        _texto(alm, 'del estudio', responde_a=mid)
        assert 'usa los botones' in tg.ultimo()[1]
        assert alm.turnos(pid) == [], 'lo que nadie leyo no queda en el hilo'

    def test_si_no_habla_de_esto_no_lo_fuerza(self, mundo, gemini, monkeypatch):
        """Con el hilo abierto, «45 mil de esmaltes con la Nu» es un gasto
        nuevo y no la respuesta: el modelo dice `nada` y se registra aparte."""
        alm, tg, *_rest, ella = mundo
        pid = _transferencia(alm, ella)
        mid = _pregunta_de(alm, tg, pid)
        gemini.respuestas += [
            {'accion': 'preguntar', 'pregunta': '¿Para qué fue?'},
            {'accion': 'nada'},
        ]
        _texto(alm, 'mmm', responde_a=mid)
        llamado = []
        monkeypatch.setattr(
            bot_libros, '_registrar', lambda *a, **k: llamado.append(k.get('texto'))
        )
        _texto(alm, '45 mil de esmaltes con la Nu', mid=52)
        assert llamado == ['45 mil de esmaltes con la Nu']


# ------------------------------------------------------------- el de Juan


class TestElDeJuan:
    @pytest.fixture
    def juan(self, mundo, gemini, monkeypatch):
        alm, tg, _ff, _act, juan, _ella = mundo
        monkeypatch.setattr(
            bot.interprete,
            'catalogo',
            lambda cx, uid: {
                'categorias': ['Arriendo', 'Mercado', 'Transporte Aplicación'],
                'presupuestos': ['Esencial', 'Antojos'],
                'comercios': ['Doña Luz'],
                'mapa': {},
            },
        )
        aplicadas = []

        def aplicar(cx, pid, **k):
            aplicadas.append(k)
            return alm.pendiente(pid), 'listo'

        monkeypatch.setattr(bot, 'aplicar_respuesta', aplicar)
        libro = alm.libros_de(juan)[0]['id']
        bid = alm.guardar_buzon(juan, 'graph', 'juan@hotmail.com')
        cid, _ = alm.guardar_correo(bid, '<j1>', 'b', 'A', '2026-10-22', 'x')
        pid, _ = alm.crear_pendiente(
            correo_id=cid,
            usuario_id=juan,
            tipo='transferencia_salida',
            fecha='2026-10-22',
            valor=-220000.0,
            contraparte='3137570780',
            descripcion='TRANSF A CUENTA 3137570780',
            libro_id=libro,
            destino_por='unico',
            pregunta='categoria',
            external_id='j1',
        )
        alm.cx.commit()
        msg = tg.enviar('555', 'pregunta')
        alm.guardar_mensaje('555', msg['message_id'], pid)
        return alm, tg, pid, msg['message_id'], aplicadas

    def test_propone_y_aplica_al_confirmar(self, juan, gemini):
        alm, tg, pid, mid, aplicadas = juan
        gemini.respuestas.append(
            {
                'accion': 'proponer',
                'categoria': 'Arriendo',
                'presupuesto': 'Esencial',
                'comercio': 'Doña Luz',
                'nota': 'Cuenta de Doña Luz, la arrendadora',
                'siempre': True,
            }
        )
        _texto(alm, 'es el arriendo, la cuenta de doña luz', chat='555', responde_a=mid)
        assert 'Arriendo' in tg.ultimo()[1] and aplicadas == []

        _toque(alm, f'ic:{pid}:1', chat='555')

        assert aplicadas == [
            {
                'categoria': 'Arriendo',
                'presupuesto': 'Esencial',
                'comercio': 'Doña Luz',
                'aprender': True,
            }
        ]
        assert alm.nota(alm.pendiente(pid)['usuario_id'], '3137570780')

    def test_solo_esta_vez_no_aprende(self, juan, gemini):
        alm, _tg, pid, mid, aplicadas = juan
        gemini.respuestas.append({'accion': 'proponer', 'categoria': 'Mercado'})
        _texto(alm, 'mercado', chat='555', responde_a=mid)
        _toque(alm, f'ic:{pid}:0', chat='555')
        assert aplicadas[0]['aprender'] is False
