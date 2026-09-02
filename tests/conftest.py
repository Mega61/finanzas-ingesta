"""Lo comun a todas las pruebas.

El bot solo atiende a los chat_id que estan en la configuracion, asi que sin
esto TODA prueba del bot se cae: el update entra y se ignora. Se autoriza el
'555' que usan los fixtures. Las pruebas de la autorizacion en si la vuelven a
tocar para probar los dos lados.
"""

from __future__ import annotations

import pytest

from finanzas.entrada import bot

CHAT_DE_PRUEBA = '555'


@pytest.fixture(autouse=True)
def _autoriza_el_chat_de_prueba(monkeypatch):
    monkeypatch.setattr(bot, 'chats_autorizados', lambda: {CHAT_DE_PRUEBA})


@pytest.fixture(autouse=True)
def _sin_memoria_de_la_prueba_anterior():
    """El bot recuerda cosas POR CHAT en diccionarios de modulo: el camino que
    se tomo, lo ultimo que se toco, el historial de la conversacion. Todas las
    pruebas usan el mismo chat, asi que sin limpiar esto una prueba cambia el
    ruteo de la siguiente y el resultado depende del orden en que corran.
    """
    for d in (bot.ULTIMO_CAMINO, bot.ULTIMO_TOCADO, bot.HISTORIAL):
        d.clear()
    yield
    for d in (bot.ULTIMO_CAMINO, bot.ULTIMO_TOCADO, bot.HISTORIAL):
        d.clear()
