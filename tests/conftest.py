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
