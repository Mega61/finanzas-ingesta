"""El resumen diario con graficas: la imagen, el texto y el envio por Telegram."""

from __future__ import annotations

import json
from datetime import date

from finanzas.adaptadores import graficos, telegram
from finanzas.aplicacion import plan as P
from finanzas.aplicacion import tablero
from finanzas.entrada import bot

HOY = date(2026, 10, 2)
ESTADO = [
    {'nombre': 'Esencial', 'limite': 4_640_000, 'gastado': 273_000},
    {'nombre': 'Vivir', 'limite': 400_000, 'gastado': 520_000},
    {'nombre': 'Sin tope', 'limite': None, 'gastado': 10_000},
]
PLAN = P.interpretar(
    {
        'cuenta': 'Bancolombia',
        'reserva': 'Nu',
        'colchon': 500_000,
        'compromiso': [
            {'concepto': 'Contrato nuevo', 'fecha': '2026-10-20', 'monto': 3_400_000}
        ],
        'tarjeta': [
            {
                'nombre': 'VISA BLACK',
                'corte': 15,
                'pago': 5,
                'sin_interes': {
                    'saldo': 4_715_181.68,
                    'al': '2026-10-02',
                    'cuota': 261_954.58,
                },
            }
        ],
    }
)
SALDOS = {'Bancolombia': 1_803_809, 'Nu': 21_694_319, 'VISA BLACK': -4_970_004}


def test_corto():
    assert graficos.corto(1_803_809) == '1,8M'
    assert graficos.corto(2_000_000) == '2M'
    assert graficos.corto(-450_000) == '-450k'
    assert graficos.corto(4_200_000, signo=True) == '+4,2M'


def test_la_imagen_es_un_png_con_y_sin_proyeccion():
    proy = P.proyectar(PLAN, HOY, SALDOS)
    con = graficos.tablero('Presupuestos', ESTADO, 2, 31, proy, 'Bancolombia')
    sin = graficos.tablero('Presupuestos', ESTADO, 2, 31)
    assert con.startswith(b'\x89PNG') and sin.startswith(b'\x89PNG')
    assert len(con) > len(sin)


def test_el_texto_avisa_cuanto_mover_y_cuando():
    proy = P.proyectar(PLAN, HOY, SALDOS)
    texto = tablero._texto(HOY, SALDOS, ESTADO, PLAN, proy)
    assert 'Bancolombia <b>$1.803.809</b>' in texto
    assert 'VISA 5M (4,7M al 0%)' in texto
    assert '⚠️ Vivir pasado por $120.000' in texto
    assert 'Contrato nuevo' in texto
    # 1,8M - 3,4M el 20-oct, y el 5-nov la VISA sin el celular (517k): -2,1M.
    # Para no bajar del colchon (500k) hay que pasar 2,6M antes del 20.
    assert 'baja a <b>-$2.112.968</b> el 5 nov' in texto
    assert 'Pasa <b>$2.612.968</b> de Nu antes del 20 oct' in texto
    assert len(texto) <= telegram.TOPE_PIE_DE_FOTO


def test_sin_faltante_dice_que_no_hay_que_mover_nada():
    saldos = {**SALDOS, 'Bancolombia': 9_000_000}
    proy = P.proyectar(PLAN, HOY, saldos)
    texto = tablero._texto(HOY, saldos, ESTADO, PLAN, proy)
    assert '✅ Bancolombia no baja de' in texto


class _Respuesta:
    def __init__(self, cuerpo):
        self._c = json.dumps(cuerpo).encode()

    def read(self):
        return self._c

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_la_foto_va_en_multipart_con_su_pie(monkeypatch):
    visto = {}

    def urlopen(req, timeout=None):
        visto['url'] = req.full_url
        visto['tipo'] = req.get_header('Content-type')
        visto['cuerpo'] = req.data
        return _Respuesta({'ok': True, 'result': {'message_id': 7}})

    monkeypatch.setattr(telegram, '_token', lambda: 'T')
    monkeypatch.setattr(telegram.urllib.request, 'urlopen', urlopen)
    r = telegram.enviar_foto(123, b'\x89PNGdatos', '<b>Así vas</b>')
    assert r == {'message_id': 7}
    assert visto['url'].endswith('/sendPhoto')
    assert visto['tipo'].startswith('multipart/form-data; boundary=')
    assert '<b>Así vas</b>'.encode() in visto['cuerpo']
    assert b'\x89PNGdatos' in visto['cuerpo']
    assert b'name="parse_mode"\r\n\r\nHTML' in visto['cuerpo']


def test_un_pie_largo_va_como_mensaje_aparte(monkeypatch):
    mandados = []
    monkeypatch.setattr(telegram, '_token', lambda: 'T')
    monkeypatch.setattr(
        telegram.urllib.request,
        'urlopen',
        lambda req, timeout=None: _Respuesta({'ok': True, 'result': {}}),
    )
    monkeypatch.setattr(
        telegram, 'enviar', lambda chat, texto, **k: mandados.append(texto)
    )
    largo = 'x' * (telegram.TOPE_PIE_DE_FOTO + 1)
    telegram.enviar_foto(1, b'png', largo)
    assert mandados == [largo]


def test_si_el_tablero_falla_llega_el_resumen_de_siempre(monkeypatch):
    mandados = []
    monkeypatch.setattr(bot, '_uid', lambda cx, chat: 1)
    monkeypatch.setattr(bot, '_es_juan', lambda cx, chat: True)
    monkeypatch.setattr(bot, 'cmd_tablero', lambda cx, chat: 1 / 0)
    monkeypatch.setattr(bot.db, 'resumen', lambda cx, uid: [])
    monkeypatch.setattr(
        bot.telegram, 'enviar', lambda chat, texto, *a, **k: mandados.append(texto)
    )
    bot.cmd_resumen(None, 5)
    assert mandados == ['Todo al día. No hay nada abierto. ✅']


def test_a_mariana_le_llega_la_cola(monkeypatch):
    llamado = []
    monkeypatch.setattr(bot, '_uid', lambda cx, chat: 2)
    monkeypatch.setattr(bot, '_es_juan', lambda cx, chat: False)
    monkeypatch.setattr(bot, 'cmd_tablero', lambda cx, chat: llamado.append('tablero'))
    monkeypatch.setattr(bot.db, 'resumen', lambda cx, uid: [])
    monkeypatch.setattr(bot.telegram, 'enviar', lambda *a, **k: llamado.append('cola'))
    bot.cmd_resumen(None, 6)
    assert llamado == ['cola']


def test_el_tablero_suma_las_preguntas_pendientes(monkeypatch):
    enviado = {}
    monkeypatch.setattr(
        bot.tablero, 'armar', lambda: tablero.Tablero(b'png', '<b>Así vas</b>')
    )
    monkeypatch.setattr(bot, '_uid', lambda cx, chat: 1)
    monkeypatch.setattr(
        bot.db,
        'resumen',
        lambda cx, uid: [
            {'estado': 'publicado', 'pregunta': 'categoria', 'n': 3},
            {'estado': 'publicado', 'pregunta': 'nada', 'n': 40},
        ],
    )
    monkeypatch.setattr(
        bot.telegram, 'enviar_foto', lambda chat, png, pie: enviado.update(pie=pie)
    )
    bot.cmd_tablero(None, 1)
    assert 'Tengo <b>3</b> por preguntarte' in enviado['pie']
