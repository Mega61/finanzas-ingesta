"""Que el bot resuelva solo, sin devolverle la pregunta al usuario.

El reclamo era exacto: «me esta preguntando por 3 transacciones y yo le digo
algo y me dice que a cual de los mensajes anteriores me refiero, no se esta
usando el asesor». Las dos cosas eran ciertas, y las dos estaban en
`_texto_libre`:

- con varias preguntas abiertas se rendia y pedia «responde al mensaje»
- el asesor solo se consultaba cuando NO habia ninguna pregunta abierta, o sea
  casi nunca

Aqui se verifica el comportamiento nuevo con los tres movimientos reales que
tenia abiertos.
"""

from __future__ import annotations

import sqlite3

import pytest

from finanzas.adaptadores import db
from finanzas.adaptadores.almacen import Almacen
from finanzas.entrada import bot

ESQUEMA = db.ESQUEMA

# Los tres que el bot le estaba preguntando a la vez.
LOS_TRES = (
    ('MERCADO PAGO*TIERRAG', -151495.0, 'Gato'),
    ('MERCADO PAGO*ZONAFIT', -457000.0, 'Gimnasio'),
    ('BOLD CO ONLINE RTFE', -212000.0, None),
)


class TelegramFalso:
    class TelegramError(Exception):
        pass

    def __init__(self):
        self.enviados: list[str] = []
        self.editados: list[str] = []
        self.avisos: list[str] = []
        self.botones: list[list] = []
        self._id = 2000

    def enviar(self, chat, texto, botones=None, modo='HTML'):
        self.enviados.append(texto)
        if botones:
            self.botones.append(botones)
        self._id += 1
        return {'message_id': self._id}

    def editar(self, chat, message_id, texto, modo='HTML'):
        self.editados.append(texto)
        return {'message_id': message_id}

    def responder_callback(self, cq_id, texto=None, alerta=False):
        self.avisos.append(texto or '')

    def obtener_updates(self, *a, **k):
        return []


@pytest.fixture
def entorno(monkeypatch):
    """Base en memoria, Telegram y Firefly de mentiras, y los tres abiertos."""
    cx = sqlite3.connect(':memory:')
    cx.row_factory = sqlite3.Row
    cx.execute('PRAGMA foreign_keys = ON')
    alm = Almacen(cx)
    alm.inicializar(ESQUEMA)

    tg = TelegramFalso()
    monkeypatch.setattr(bot, 'telegram', tg)

    # La IA se apaga a proposito: estas pruebas cubren el camino de RESPALDO
    # por patrones. El camino del plan del modelo tiene sus propias pruebas,
    # con `entender_orden` simulado. Sin esto, cada prueba llamaria a Gemini.
    monkeypatch.setattr(bot.ia, 'disponible', lambda: False)

    # Nada sale a la red. Lo que se prueba aqui es el RUTEO: a que movimiento
    # va el texto y si va al asesor. La interpretacion en si tiene sus propias
    # pruebas, y dejarla real hacia que esto tardara minutos llamando a Gemini.
    consultas: list[str] = []
    monkeypatch.setattr(
        bot, '_consultar_asesor', lambda cx, chat, txt: consultas.append(txt)
    )

    def interpretar_falso(cx, usuario_id, pendiente, txt, cat=None):
        """Entiende lo justo para las frases de las pruebas."""
        bajo = txt.lower()
        for clave, categoria in (
            ('gata', 'Gato'),
            ('gym', 'Gimnasio'),
            ('gimnasio', 'Gimnasio'),
            ('casa', 'Hogar'),
            ('etre', 'Hogar'),
        ):
            if clave in bajo:
                return {
                    'categoria': categoria,
                    'presupuesto': 'Esencial',
                    'comercio': None,
                    'confianza': 0.9,
                    'razon': f'dijiste {clave}',
                    'fuente': 'heuristica',
                    'pedir_presupuesto': False,
                }
        return {
            'categoria': None,
            'presupuesto': None,
            'comercio': None,
            'confianza': 0.0,
            'razon': '',
            'fuente': 'nada',
            'pedir_presupuesto': False,
        }

    monkeypatch.setattr(bot.interprete, 'interpretar', interpretar_falso)
    monkeypatch.setattr(
        bot.interprete,
        'catalogo',
        lambda cx, uid: {
            'categorias': ['Gato', 'Gimnasio', 'Hogar'],
            'presupuestos': ['Esencial'],
            'comercios': [],
            'mapa': {},
        },
    )
    monkeypatch.setattr(bot.interprete, 'buscar_categoria', lambda txt, cats: [])
    # ya estan publicados: la correccion va por actualizar_split
    monkeypatch.setattr(bot.firefly, 'actualizar_split', lambda tx_id, **campos: True)
    monkeypatch.setattr(bot.presupuestos, 'revienta', lambda *a, **k: None)

    uid = alm.guardar_usuario('Juan', 'https://f', 'tok', '555')
    bid = alm.guardar_buzon(uid, 'graph', 'j@e.com')
    cid, _ = alm.guardar_correo(bid, '<m>', 'b', 'A', '2026-09-01', 'x')

    ids = {}
    for i, (nombre, valor, categoria) in enumerate(LOS_TRES):
        pid, _ = alm.crear_pendiente(
            correo_id=cid,
            usuario_id=uid,
            tipo='compra_tarjeta',
            fecha='2026-09-01',
            valor=valor,
            contraparte=nombre,
            descripcion=nombre,
            categoria=categoria,
            estado='publicado',
            pregunta='categoria',
            external_id=f'bc-{i}',
        )
        ids[nombre] = pid
    cx.commit()
    # el orden de `abiertas_del_chat` es por preguntado_en DESC: el ultimo
    # preguntado es el de BOLD, que es el caso del reclamo
    for nombre, _, _ in LOS_TRES:
        alm.marcar_preguntado(ids[nombre])
    return alm, tg, ids, consultas, uid


def _mensaje(texto, responde_a=None):
    m = {'message_id': 9, 'chat': {'id': '555'}, 'text': texto}
    if responde_a:
        m['reply_to_message'] = {'message_id': responde_a}
    return {'message': m}


def _resueltos(alm):
    return {
        r['contraparte']: r['categoria']
        for r in alm.cx.execute('SELECT contraparte, categoria FROM pendientes')
    }


class TestYaNoSeRinde:
    def test_no_vuelve_a_preguntar_a_cual_me_refiero(self, entorno):
        """El reclamo textual. Con tres abiertas y un texto que nombra un
        comercio, el bot resuelve — no pide que le respondas al mensaje."""
        alm, tg, _ids, _, _ = entorno
        bot.manejar_update(alm.cx, _mensaje('fue la comida de la gata en tierragro'))
        todo = ' '.join(tg.enviados)
        assert 'no sé a' not in todo
        assert 'mantén presionado' not in todo
        assert 'TIERRAG' in todo, 'tiene que decir a cual lo aplico'

    def test_lo_aplica_al_que_nombraste_y_no_a_otro(self, entorno):
        alm, _tg, _ids, _, _ = entorno
        bot.manejar_update(alm.cx, _mensaje('zonafit es el gimnasio'))
        cats = _resueltos(alm)
        assert cats['MERCADO PAGO*ZONAFIT'] is not None
        # los otros dos siguen como estaban: no se toco el equivocado
        assert cats['BOLD CO ONLINE RTFE'] is None

    def test_el_monto_tambien_identifica(self, entorno):
        """«los 212 mil» no nombra ningun comercio, pero solo un movimiento
        vale eso."""
        alm, tg, _ids, _, _ = entorno
        bot.manejar_update(alm.cx, _mensaje('los 212 mil fueron cosas para la casa'))
        assert 'BOLD' in ' '.join(tg.enviados)

    def test_sin_ninguna_senal_lo_aplica_al_ultimo_y_lo_dice(self, entorno):
        """«era Etre, venden cosas para la casa» no nombra nada que este en los
        movimientos. Se aplica al ultimo preguntado —lo que asumiria cualquiera—
        pero DICIENDOLO, y con botones para moverlo."""
        alm, tg, _ids, _, _ = entorno
        bot.manejar_update(
            alm.cx, _mensaje('era Etre, una empresa que vende cosas para la casa')
        )
        todo = ' '.join(tg.enviados)
        assert 'ultimo que te pregunte' in todo
        assert tg.botones, 'tiene que ofrecer los otros dos'

    def test_los_botones_permiten_moverlo_de_un_toque(self, entorno):
        alm, tg, _ids, _, _ = entorno
        bot.manejar_update(alm.cx, _mensaje('era Etre, venden cosas para la casa'))
        # el callback de los botones apunta a los OTROS movimientos
        datos = [d for fila in tg.botones[0] for _, d in fila]
        assert datos, 'sin botones no hay como corregir'
        assert all(d.startswith('m:') for d in datos)


class TestElAsesorSeAlcanza:
    def test_una_consulta_llega_al_asesor_aunque_haya_preguntas_abiertas(self, entorno):
        """Este era el otro reclamo: con cosas abiertas, el asesor era
        inalcanzable."""
        alm, _tg, _ids, consultas, _ = entorno
        bot.manejar_update(alm.cx, _mensaje('¿me alcanza para una bici de 2 millones?'))
        assert consultas == ['¿me alcanza para una bici de 2 millones?']

    @pytest.mark.parametrize(
        'txt',
        [
            'deberia comprar un conjunto de ropa deportiva',
            'cuanto llevo gastado este mes',
            'como voy con los presupuestos',
            'vale la pena la suscripcion?',
        ],
    )
    def test_las_consultas_no_se_toman_como_respuesta(self, entorno, txt):
        alm, _tg, _ids, consultas, _ = entorno
        bot.manejar_update(alm.cx, _mensaje(txt))
        assert consultas == [txt]
        assert all(
            v is None or v in ('Gato', 'Gimnasio') for v in _resueltos(alm).values()
        ), 'no toco ningun movimiento'

    def test_una_respuesta_no_se_va_al_asesor(self, entorno):
        """El error caro es al reves: mandar una respuesta al asesor solo deja
        el movimiento sin resolver, pero interpretar una pregunta como respuesta
        le mete una categoria inventada a una compra."""
        alm, _tg, _ids, consultas, _ = entorno
        bot.manejar_update(alm.cx, _mensaje('fue la comida de la gata en tierragro'))
        assert consultas == []


class TestResponderAlMensajeSigueGanando:
    def test_si_responde_a_un_mensaje_concreto_se_respeta(self, entorno):
        """Cuando el usuario SI dice a cual, eso manda sobre cualquier
        puntaje."""
        alm, tg, ids, _, _ = entorno
        pid = ids['BOLD CO ONLINE RTFE']
        alm.guardar_mensaje('555', 4242, pid)
        # el texto nombra TIERRAGRO, pero responde al mensaje del de BOLD
        bot.manejar_update(
            alm.cx, _mensaje('fue la comida de la gata en tierragro', responde_a=4242)
        )
        assert 'TIERRAG' not in ' '.join(tg.enviados[:1] or [''])
        assert _resueltos(alm)['BOLD CO ONLINE RTFE'] is not None


class TestMoverLaRespuesta:
    """El boton «era otro». Es lo que hace aceptable que el bot aplique por su
    cuenta: si se equivoca, un toque lo mueve."""

    def test_mueve_el_texto_al_movimiento_que_tocaste(self, entorno):
        alm, _tg, ids, _, _ = entorno
        bot.manejar_update(alm.cx, _mensaje('era Etre, venden cosas para la casa'))
        # se aplico al de BOLD (el ultimo preguntado)
        assert _resueltos(alm)['BOLD CO ONLINE RTFE'] is not None
        # ahora se toca el de TIERRAG
        otro = ids['MERCADO PAGO*TIERRAG']
        bot.manejar_update(
            alm.cx,
            {
                'callback_query': {
                    'id': 'cq',
                    'data': f'm:{otro}:0',
                    'message': {'message_id': 77, 'chat': {'id': '555'}},
                }
            },
        )
        assert _resueltos(alm)['MERCADO PAGO*TIERRAG'] == 'Hogar'

    def test_el_texto_sobrevive_al_callback(self, entorno):
        """No cabe en el callback —Telegram admite 64 bytes— asi que queda
        guardado por chat."""
        alm, _tg, _ids, _, _ = entorno
        bot.manejar_update(alm.cx, _mensaje('era Etre, venden cosas para la casa'))
        assert alm.texto_en_espera('555') == 'era Etre, venden cosas para la casa'

    def test_sin_texto_guardado_avisa_en_vez_de_fallar(self, entorno):
        """Un boton de un despliegue anterior puede llegar cuando ya no hay
        nada guardado."""
        alm, tg, ids, _, _ = entorno
        bot.manejar_update(
            alm.cx,
            {
                'callback_query': {
                    'id': 'cq',
                    'data': f'm:{ids["MERCADO PAGO*TIERRAG"]}:0',
                    'message': {'message_id': 77, 'chat': {'id': '555'}},
                }
            },
        )
        assert any('escribelo de nuevo' in a for a in tg.avisos)


class TestElHiloDeLaConversacion:
    """Preguntar y seguir preguntando, sin que el bot cambie de tema.

    La conversacion real que fallo:
        TU : cual fue la ultima transaccion?      -> bien, al asesor
        TU : y la anterior a esa                  -> le saco el movimiento de
                                                     Google Workspace con
                                                     botones de categoria
    """

    @pytest.mark.parametrize(
        'seguimiento',
        [
            'y la anterior a esa',
            'y la anterior?',
            'y antes de esa?',
            'la penultima',
            'y la otra',
            'cual mas',
        ],
    )
    def test_el_seguimiento_va_al_asesor_y_no_a_las_preguntas_abiertas(
        self, entorno, seguimiento
    ):
        alm, tg, _ids, consultas, _u = entorno
        bot.manejar_update(alm.cx, _mensaje('cual fue la ultima transaccion?'))
        bot.manejar_update(alm.cx, _mensaje(seguimiento))
        assert consultas == ['cual fue la ultima transaccion?', seguimiento]
        # y no toco ningun movimiento ni saco ninguna pregunta
        assert tg.enviados == []

    def test_una_respuesta_de_verdad_sigue_llegando_a_su_movimiento(self, entorno):
        """El arreglo no puede haber roto lo otro: describir una compra sigue
        siendo una respuesta, aunque acabe de hablar con el asesor."""
        alm, tg, _ids, consultas, _u = entorno
        bot.manejar_update(alm.cx, _mensaje('cual fue la ultima transaccion?'))
        bot.manejar_update(alm.cx, _mensaje('fue la comida de la gata en tierragro'))
        assert consultas == ['cual fue la ultima transaccion?']
        assert 'TIERRAG' in ' '.join(tg.enviados)

    def test_el_hilo_se_recuerda_por_chat(self, entorno):
        """El modo es del chat, no global."""
        alm, _tg, _ids, consultas, _u = entorno
        bot.manejar_update(alm.cx, _mensaje('cuanto llevo gastado'))
        assert bot._venia_del_asesor('555')
        assert not bot._venia_del_asesor('otro-chat')
        assert consultas == ['cuanto llevo gastado']

    def test_el_hilo_caduca(self, entorno, monkeypatch):
        """Pasado un rato, un «y la otra» ya no se sabe de que hablaba. Se
        decide solo por el texto, que para estas formas basta."""
        _alm, _tg, _ids, _c, _u = entorno
        bot._recordar_camino('555', 'asesor')
        assert bot._venia_del_asesor('555')
        # se envejece la marca en vez de parchar time.time(), que se llama a si
        # mismo dentro del lambda y se va en recursion
        camino, cuando = bot.ULTIMO_CAMINO['555']
        bot.ULTIMO_CAMINO['555'] = (camino, cuando - bot.MINUTOS_DE_HILO * 60 - 1)
        assert not bot._venia_del_asesor('555')
