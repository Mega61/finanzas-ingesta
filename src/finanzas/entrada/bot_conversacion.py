"""El lado de Telegram de contestar hablando, comun a los dos bots.

La IA lee la respuesta (aplicacion/conversacion) y aqui se hace lo que
decidio: mandar la pregunta de vuelta, o mostrar lo que entendio con los
botones para confirmar. Lo que se escribe en el libro lo hace cada bot al
confirmar, porque cada uno publica distinto: Juan en su Firefly, y quien
lleva varios libros por su recorrido de destino y categoria.

Nada se escribe sin un toque. Los dos botones de confirmar dicen tambien si
se aprende: «y para la proxima» guarda la regla de esa contraparte; «solo esta
vez» no, para que un gasto raro no pise lo de siempre.
"""

from __future__ import annotations

import html
import time
from typing import Any

from finanzas.adaptadores import telegram
from finanzas.adaptadores.almacen import Almacen
from finanzas.aplicacion import conversacion
from finanzas.dominio import dinero as _dinero

# El movimiento del que se viene hablando en cada chat. En Telegram casi nadie
# desliza para responder: escribe y ya. Sin esto, contestar la pregunta de
# vuelta sin responder al mensaje se leia como un mensaje nuevo. En memoria,
# como `bot_libros.ESPERANDO`: el hilo en si esta en la base, y si el proceso
# se reinicia basta con responder al mensaje.
HILO: dict[str, tuple[int, float]] = {}
MINUTOS_DE_HILO = 30
# Por nombre: ruff lo toma por un «+» disfrazado si va pegado en el texto.
MAS = '\N{HEAVY PLUS SIGN}'


def _e(v: Any) -> str:
    return html.escape(str(v if v is not None else ''), quote=False)


def _a(cx: Any) -> Almacen:
    return Almacen(cx)


def hilo_abierto(chat: Any, texto: str) -> int | None:
    """El movimiento del que se viene hablando, si es reciente y el texto
    puede ser parte de la conversacion."""
    pid, cuando = HILO.get(str(chat), (0, 0.0))
    if not pid or time.time() - cuando > MINUTOS_DE_HILO * 60:
        return None
    if not texto or texto.startswith('/'):
        return None
    return pid


def soltar(chat: Any) -> None:
    HILO.pop(str(chat), None)


def _seguir(chat: Any, pid: int) -> None:
    HILO[str(chat)] = (pid, time.time())


def conversar(
    cx: Any,
    chat: Any,
    p: Any,
    texto: str,
    libros: list[dict[str, Any]],
    presupuestos: list[str] | None = None,
    comercios: list[str] | None = None,
    con_prestamos: bool = False,
) -> bool:
    """Atiende `texto` como respuesta sobre `p`. False si no le toca: sin IA,
    si fallo, si el mensaje no habla de este movimiento, o si ya pregunto
    demasiado. En esos casos sigue el camino de siempre de cada bot."""
    alm = _a(cx)
    lectura = conversacion.entender(
        alm, p, texto, libros, presupuestos, comercios, con_prestamos
    )
    if lectura is None or lectura['accion'] == 'nada':
        return False
    if lectura['accion'] == 'rendirse':
        soltar(chat)
        telegram.enviar(chat, 'No te estoy entendiendo 😅 Mejor con los botones:')
        return False
    if lectura['accion'] == 'preguntar':
        _preguntar(cx, chat, p, lectura['pregunta'])
        return True
    _proponer(cx, chat, p, lectura)
    return True


def _encabezado(p: Any) -> str:
    plata = _dinero.formatear(p['valor'], p['moneda'] or 'COP', con_signo=True)
    return f'<i>{_e(p["contraparte"] or p["descripcion"])} · {plata} · {_e(p["fecha"])}</i>'


def _preguntar(cx: Any, chat: Any, p: Any, pregunta: str) -> None:
    alm = _a(cx)
    msg = telegram.enviar(chat, f'💬 {_e(pregunta)}\n{_encabezado(p)}')
    if msg and msg.get('message_id'):
        alm.guardar_mensaje(str(chat), msg['message_id'], p['id'], 'conversacion')
    alm.agregar_turno(p['id'], 'bot', pregunta)
    _seguir(chat, p['id'])


def _proponer(cx: Any, chat: Any, p: Any, lec: dict[str, Any]) -> None:
    alm = _a(cx)
    conversacion.guardar(alm, p['id'], lec)
    alm.agregar_turno(p['id'], 'bot', conversacion.en_palabras(lec))
    pid = p['id']

    if lec['accion'] == 'descartar':
        lineas = ['🚫 Entiendo que <b>no fue un movimiento</b>.']
        botones = [
            [('🚫 Sí, descártalo', f'ic:{pid}:0')],
            [('✏️ No, te explico', f'ix:{pid}:0')],
        ]
    else:
        lineas = ['Entendí esto:']
        if lec['libro']:
            lineas.append(f'📘 <b>{_e(lec["libro"])}</b>')
        if lec['prestamo_con']:
            lineas.append(f'🤝 Préstamo con <b>{_e(lec["prestamo_con"])}</b>')
        if lec['categoria']:
            lineas.append(f'📂 <b>{_e(lec["categoria"])}</b>')
        if lec['categoria_nueva']:
            lineas.append(
                f'{MAS} Categoría nueva <b>{_e(lec["categoria_nueva"])}</b> '
                '<i>(la creo si confirmas)</i>'
            )
        if lec['presupuesto']:
            lineas.append(f'💰 {_e(lec["presupuesto"])}')
        if lec['comercio']:
            lineas.append(f'🏪 {_e(lec["comercio"])}')
        if not (lec['categoria'] or lec['categoria_nueva'] or lec['prestamo_con']):
            lineas.append('<i>La categoría te la pregunto después.</i>')
        if lec['nota']:
            lineas.append(f'📝 Me acuerdo: <i>{_e(lec["nota"])}</i>')
        siempre = '✅ Sí, y para la próxima' + (' ✓' if lec['siempre'] else '')
        una_vez = '✅ Sí, solo esta vez' + ('' if lec['siempre'] else ' ✓')
        botones = [
            [(siempre, f'ic:{pid}:1')],
            [(una_vez, f'ic:{pid}:0')],
            [('✏️ No, te explico', f'ix:{pid}:0')],
        ]
    if lec['razon']:
        lineas.append(f'\n<i>{_e(lec["razon"])}</i>')
    lineas.append(_encabezado(p))
    msg = telegram.enviar(chat, '\n'.join(lineas), botones)
    if msg and msg.get('message_id'):
        alm.guardar_mensaje(str(chat), msg['message_id'], pid, 'conversacion')
    _seguir(chat, pid)


# ------------------------------------------------------------------ toques


def confirmada(t: Any) -> tuple[Any, dict[str, Any]] | None:
    """El movimiento y la lectura que se le mostro, si siguen en pie. Quien
    toca ya se verifico en el despacho: `ic` es un toque de pendiente."""
    alm = _a(t.cx)
    p = alm.pendiente(t.pid)
    lec = conversacion.leer(alm, t.pid) if p is not None else None
    if lec is None:
        t.aviso('esa opción ya no está')
        return None
    return p, lec


def cerrar(t: Any, p: Any, lec: dict[str, Any]) -> None:
    conversacion.cerrar(_a(t.cx), p, lec)
    soltar(t.chat)


def toque_corrijo(t: Any) -> None:
    """«✏️ No, te explico»: el hilo sigue abierto y lo que escriba se vuelve a
    leer con todo lo que ya se hablo."""
    p = _a(t.cx).pendiente(t.pid)
    if p is None:
        t.aviso('ese movimiento ya no existe')
        return
    t.aviso('dime')
    msg = telegram.enviar(
        t.chat, f'¿Qué cambio? Cuéntame y lo vuelvo a leer.\n{_encabezado(p)}'
    )
    if msg and msg.get('message_id'):
        _a(t.cx).guardar_mensaje(str(t.chat), msg['message_id'], t.pid, 'conversacion')
    _seguir(t.chat, t.pid)


TOQUES = {'ix': toque_corrijo}
