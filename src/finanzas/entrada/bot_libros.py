"""El bot para quien lleva varios libros: pregunta a donde va, y lo registra.

Es la conversacion de una persona con su Firefly personal y el Actual de su
negocio, sobre una misma cuenta de ahorros. La regla la puso el usuario y no
tiene excepciones: NADA llega a un libro hasta que ella dice a cual va. Lo que
se sabe -- la tarjeta, lo que dijo en el audio, donde fue la vez pasada --
preselecciona un boton y nada mas.

Dos formas de que entre un movimiento:

  la alerta del banco   llega por correo, la ingesta la parsea, y el bot
                        pregunta a que libro va
  lo que ella cuenta    «45 mil de esmaltes con la Nu», escrito o en una nota
                        de voz. Nu no manda correos: es la unica forma.

Y un solo recorrido para las dos, en `siguiente`:

  medio -> destino -> (venta de Agendapro?) -> categoria -> publicar
"""

from __future__ import annotations

import html
from typing import Any

from finanzas.adaptadores import actual, firefly, ia, telegram
from finanzas.adaptadores.almacen import Almacen
from finanzas.aplicacion import interprete, libros, publicador, publicador_actual, ruteo
from finanzas.dominio import contado as _contado
from finanzas.dominio import dinero as _dinero
from finanzas.dominio import fechas

MAX_CATEGORIAS = 12
EMOJI = {'firefly': '📒', 'actual': '💅'}

AYUDA = (
    '<b>Cómo registrar algo</b>\n\n'
    'Cuéntamelo como se lo dirías a alguien, escrito o en una nota de voz:\n'
    '«45 mil de esmaltes con la Nu»\n'
    '«ayer pagué 120 mil de arriendo del local en efectivo»\n'
    '«me pagaron 80 mil por unas uñas»\n\n'
    'Las alertas de Bancolombia te llegan solas.\n\n'
    'Siempre te pregunto a qué libro va antes de guardar nada.\n\n'
    '/pendientes — lo que falta por contestar'
)


def _a(cx: Any) -> Almacen:
    return Almacen(cx)


def _e(v: Any) -> str:
    return html.escape(str(v if v is not None else ''), quote=False)


def _plata(v: float) -> str:
    return _dinero.formatear(v, 'COP', con_signo=True)


def _nombre(libro: Any) -> str:
    return f'{EMOJI.get(libro["tipo"], "📘")} {libro["nombre"]}'


def describir(cx: Any, p: Any) -> str:
    origen = '🎙️ Lo que contaste' if p['origen'] == 'chat' else '💳 Alerta del banco'
    medio = f' · {_e(p["instrumento"])}' if p['instrumento'] else ''
    lineas = [f'{origen}{medio}', f'<b>{_plata(p["valor"])}</b> · {_e(p["fecha"])}']
    if p['contraparte']:
        lineas.append(f'<b>{_e(p["contraparte"])}</b>')
    if p['descripcion'] and p['descripcion'] != p['contraparte']:
        lineas.append(f'<i>{_e(p["descripcion"][:160])}</i>')
    lb = _a(cx).libro(p['libro_id']) if p['libro_id'] else None
    if lb:
        cuenta = f' · {_e(p["cuenta_firefly"])}' if p['cuenta_firefly'] else ''
        lineas.append(f'→ {_nombre(lb)}{cuenta}')
    return '\n'.join(lineas)


def _enviar(cx: Any, chat: Any, p: Any, texto: str, botones: Any) -> None:
    msg = telegram.enviar(chat, texto, botones)
    if msg and msg.get('message_id'):
        _a(cx).guardar_mensaje(str(chat), msg['message_id'], p['id'])
    _a(cx).marcar_preguntado(p['id'])


# ------------------------------------------------------------- el recorrido


def siguiente(cx: Any, pendiente_id: int, chat: Any) -> None:
    """Hace la pregunta que falta, o publica si ya no falta nada."""
    alm = _a(cx)
    p = alm.pendiente(pendiente_id)
    if p is None or p['estado'] not in ('nuevo', 'error'):
        return
    if not p['instrumento'] and p['origen'] == 'chat':
        return _preguntar_medio(cx, p, chat)
    if not p['libro_id']:
        return _preguntar_destino(cx, p, chat)
    if p['pregunta'] == 'categoria':
        lb = alm.libro(p['libro_id'])
        if (
            lb['tipo'] == 'actual'
            and float(p['valor']) > 0
            and _preguntar_si_es_venta(cx, p, chat, lb)
        ):
            return None
        return _preguntar_categoria(cx, p, chat)
    if p['pregunta'] == 'existencia':
        return _preguntar_parecido(cx, p, chat)
    if p['decidido_por'] == 'espera_agendapro':
        return None
    return _publicar(cx, p['id'], chat)


def preguntar(cx: Any, p: Any, chat: Any) -> bool:
    """Lo que usa la ingesta para las preguntas abiertas de esta persona."""
    try:
        siguiente(cx, p['id'], chat)
    except telegram.TelegramError as ex:
        print(f'  no pude preguntar por #{p["id"]}: {ex}')
        return False
    return True


# ------------------------------------------------------------- las preguntas


def _preguntar_medio(cx: Any, p: Any, chat: Any) -> None:
    claves = list(
        dict.fromkeys(i['clave'] for i in _a(cx).instrumentos_de(p['usuario_id']))
    )
    botones = [
        [(c.title() if not c.isdigit() else f'*{c}', f'km:{p["id"]}:{i}')]
        for i, c in enumerate(claves)
    ]
    _a(cx).guardar_sugerencias(p['id'], claves)
    botones.append([('🚫 Cancelar', f'x:{p["id"]}:0')])
    _enviar(cx, chat, p, describir(cx, p) + '\n\n¿Con qué pagaste?', botones)


def _preguntar_destino(cx: Any, p: Any, chat: Any) -> None:
    alm = _a(cx)
    posibles = ruteo.libros_posibles(alm, p)
    marcado = ruteo.sugerencia(alm, p, posibles)
    botones = [
        [
            (
                _nombre(lb) + (' ✓' if lb['id'] == marcado else ''),
                f'ld:{p["id"]}:{lb["id"]}',
            )
        ]
        for lb in posibles
    ]
    botones.append([('🚫 No es un movimiento', f'x:{p["id"]}:0')])
    pie = '\n\n<b>¿A qué libro va?</b>'
    if marcado:
        pie += '\n<i>✓ es lo que creo, pero tú decides.</i>'
    _enviar(cx, chat, p, describir(cx, p) + pie, botones)


def _categorias(cx: Any, p: Any, lb: Any) -> list[str]:
    ingreso = float(p['valor']) > 0
    cliente = libros.cliente(lb)
    if isinstance(cliente, actual.Cliente):
        nombres = [c['name'] for c in cliente.categorias(ingreso)]
    else:
        nombres = sorted(
            c['attributes']['name'] for c in cliente.get_all('/api/v1/categories')
        )
    # La que ya contesto para este comercio en este libro, primero.
    regla = _a(cx).regla_de_libro(p['usuario_id'], ruteo.clave_de(p))
    if regla and regla['libro_id'] == lb['id'] and regla['categoria'] in nombres:
        nombres.remove(regla['categoria'])
        nombres.insert(0, regla['categoria'])
    return nombres


def _preguntar_categoria(cx: Any, p: Any, chat: Any) -> None:
    lb = _a(cx).libro(p['libro_id'])
    try:
        cats = _categorias(cx, p, lb)
    except (libros.LibroNoDisponible, actual.ApiError, firefly.ApiError) as ex:
        telegram.enviar(
            chat,
            f'No pude leer las categorías de {_e(lb["nombre"])}: {_e(str(ex)[:150])}',
        )
        return
    _a(cx).guardar_sugerencias(p['id'], cats)
    botones, fila = [], []
    for i, c in enumerate(cats[:MAX_CATEGORIAS]):
        fila.append((c, f'kc:{p["id"]}:{i}'))
        if len(fila) == 2:
            botones.append(fila)
            fila = []
    if fila:
        botones.append(fila)
    botones.append(
        [('✏️ Escribirla', f'kt:{p["id"]}:0'), ('↩️ Otro libro', f'lr:{p["id"]}:0')]
    )
    _enviar(cx, chat, p, describir(cx, p) + '\n\n<b>¿Qué categoría?</b>', botones)


def _preguntar_si_es_venta(cx: Any, p: Any, chat: Any, lb: Any) -> bool:
    """Una transferencia que entra al estudio casi siempre es una clienta, y
    Agendapro ya la subio como venta. Se pregunta antes que la categoria."""
    try:
        cliente = libros.cliente(lb)
        venta = publicador_actual.venta_de_agendapro(cliente, p)
    except (libros.LibroNoDisponible, actual.ApiError):
        return False
    if venta:
        texto = (
            describir(cx, p)
            + f'\n\nEn Agendapro hay una venta igual: <b>{_e(venta.get("notes") or "venta")}</b>'
            f' del {_e(venta.get("date"))}.\n\n<b>¿Es esa venta?</b>'
            '\n<i>Si es, no agrego nada: ya está en el libro.</i>'
        )
        botones = [
            [('✅ Sí, es esa venta', f'va:{p["id"]}:1')],
            [('No, es otra cosa', f'va:{p["id"]}:0')],
        ]
    else:
        texto = (
            describir(cx, p)
            + '\n\n¿Es el pago de una clienta?\n<i>Agendapro sube las ventas en la noche; '
            'si es una, espero a que aparezca y la enlazo, para no contarla dos veces.</i>'
        )
        botones = [
            [('💈 Sí, es de una clienta', f'va:{p["id"]}:2')],
            [('No, es otra cosa', f'va:{p["id"]}:0')],
        ]
    _enviar(cx, chat, p, texto, botones)
    return True


def _preguntar_parecido(cx: Any, p: Any, chat: Any) -> None:
    lb = _a(cx).libro(p['libro_id'])
    accion, t = publicador.publicar_en_su_libro(cx, p['id'])
    if accion != 'parecido':
        _a(cx).actualizar_pendiente(p['id'], pregunta=None)
        cx.commit()
        _contar(cx, p['id'], chat, accion, t)
        return
    texto = (
        describir(cx, p) + f'\n\nEn {_e(lb["nombre"])} ya hay uno muy parecido: '
        f'<b>{_plata((t.get("amount") or 0) / 100)}</b> el {_e(t.get("date"))}'
        + (f' — <i>{_e(t.get("notes"))}</i>' if t.get('notes') else '')
        + '\n\n<b>¿Es el mismo?</b>'
    )
    botones = [
        [('✅ Sí, es el mismo', f'kp:{p["id"]}:1')],
        [('No, son dos distintos', f'kp:{p["id"]}:0')],
    ]
    _enviar(cx, chat, p, texto, botones)


# ---------------------------------------------------------------- publicar


def _publicar(
    cx: Any, pendiente_id: int, chat: Any, aunque_se_parezca: bool = False
) -> None:
    accion, detalle = publicador.publicar_en_su_libro(
        cx, pendiente_id, aunque_se_parezca
    )
    if accion == 'parecido':
        _a(cx).actualizar_pendiente(pendiente_id, pregunta='existencia')
        cx.commit()
        return _preguntar_parecido(cx, _a(cx).pendiente(pendiente_id), chat)
    return _contar(cx, pendiente_id, chat, accion, detalle)


def _contar(cx: Any, pendiente_id: int, chat: Any, accion: str, detalle: Any) -> None:
    p = _a(cx).pendiente(pendiente_id)
    lb = _a(cx).libro(p['libro_id']) if p['libro_id'] else None
    donde = f'{_nombre(lb)}' if lb else ''
    cat = f' · {_e(p["categoria"])}' if p['categoria'] else ''
    textos = {
        'creado': f'✅ Guardado en {donde}{cat}',
        'ya_estaba': f'✅ Ya estaba en {donde}',
        'seco': f'🧪 En prueba: lo guardaría en {donde}{cat}. Todavía no escribo en ese libro.',
        'duplicado': f'↪️ No lo agregué: ya había uno igual en {donde}.',
    }
    texto = textos.get(accion) or f'⚠️ No lo pude guardar: {_e(str(detalle)[:200])}'
    telegram.enviar(chat, texto + '\n' + describir(cx, p))


# ------------------------------------------------------------------ toques


def _es_suyo(cx: Any, t: Any) -> Any:
    p = _a(cx).pendiente(t.pid)
    u = _a(cx).usuario_por_chat(t.chat)
    if p is None or u is None or p['usuario_id'] != u['id']:
        t.aviso('eso no es tuyo')
        return None
    return p


def toque_medio(t: Any) -> None:
    p = _es_suyo(t.cx, t)
    if not p:
        return
    claves = _a(t.cx).sugerencias(t.pid)
    if t.idx >= len(claves):
        t.aviso('esa opción ya no está')
        return
    clase = next(
        (
            i['clase']
            for i in _a(t.cx).instrumentos_de(p['usuario_id'])
            if i['clave'] == claves[t.idx]
        ),
        None,
    )
    _a(t.cx).actualizar_pendiente(
        t.pid, instrumento=claves[t.idx], clase_instrumento=clase
    )
    t.cx.commit()
    t.aviso(claves[t.idx])
    t.reemplazar(describir(t.cx, _a(t.cx).pendiente(t.pid)))
    siguiente(t.cx, t.pid, t.chat)


def toque_destino(t: Any) -> None:
    """Eligio el libro. `idx` es el id del libro: se valida que sea SUYO."""
    p = _es_suyo(t.cx, t)
    if not p:
        return
    try:
        p = ruteo.elegir_destino(_a(t.cx), t.pid, t.idx)
    except ruteo.SinCuenta:
        t.aviso('ese libro no tiene esa cuenta')
        telegram.enviar(
            t.chat,
            f'No tengo configurada la cuenta de <b>{_e(p["instrumento"] or "este medio")}</b> '
            'en ese libro. Dile a Juan que la agregue, o elige otro libro.',
        )
        return
    except ValueError:
        t.aviso('ese libro no es tuyo')
        return
    t.aviso(_a(t.cx).libro(p['libro_id'])['nombre'])
    t.reemplazar(describir(t.cx, p))
    siguiente(t.cx, t.pid, t.chat)


def toque_otro_libro(t: Any) -> None:
    """Volver a elegir el libro, mientras no se haya publicado."""
    p = _es_suyo(t.cx, t)
    if not p:
        return
    if p['estado'] not in ('nuevo', 'error') or p['firefly_id']:
        t.aviso('ya está guardado, ya no se puede mover')
        return
    _a(t.cx).actualizar_pendiente(
        t.pid,
        libro_id=None,
        destino_por=None,
        cuenta_firefly=None,
        cuenta_destino=None,
        categoria=None,
        pregunta='destino',
    )
    t.cx.commit()
    t.aviso('elige otra vez')
    siguiente(t.cx, t.pid, t.chat)


def toque_categoria(t: Any) -> None:
    p = _es_suyo(t.cx, t)
    if not p:
        return
    cats = _a(t.cx).sugerencias(t.pid)
    if t.idx >= len(cats) or not p['libro_id']:
        t.aviso('esa opción ya no está')
        return
    ruteo.elegir_categoria(_a(t.cx), t.pid, cats[t.idx])
    t.aviso(cats[t.idx])
    t.reemplazar(describir(t.cx, _a(t.cx).pendiente(t.pid)))
    _publicar(t.cx, t.pid, t.chat)


def toque_escribir_categoria(t: Any) -> None:
    if not _es_suyo(t.cx, t):
        return
    t.aviso('escríbela')
    msg = telegram.enviar(t.chat, 'Escribe la categoría, respondiendo a este mensaje:')
    if msg and msg.get('message_id'):
        _a(t.cx).guardar_mensaje(str(t.chat), msg['message_id'], t.pid)


def toque_venta(t: Any) -> None:
    """1 = es la venta de Agendapro que ya esta · 2 = es de una clienta, esperar
    a Agendapro · 0 = es otra cosa, preguntar la categoria."""
    p = _es_suyo(t.cx, t)
    if not p:
        return
    alm = _a(t.cx)
    if t.idx == 0:
        t.aviso('entonces, la categoría')
        return _preguntar_categoria(t.cx, p, t.chat)
    lb = alm.libro(p['libro_id'])
    venta = publicador_actual.venta_de_agendapro(libros.cliente(lb), p)
    if venta:
        publicador_actual.enlazar_con(t.cx, p, venta, 'venta_agendapro')
        alm.actualizar_pendiente(t.pid, categoria='Servicios')
        t.cx.commit()
        t.aviso('enlazada')
        t.reemplazar(
            '✅ Es la venta de Agendapro: no agregué nada.\n'
            + describir(t.cx, alm.pendiente(t.pid))
        )
        return None
    alm.actualizar_pendiente(
        t.pid, categoria='Servicios', pregunta=None, decidido_por='espera_agendapro'
    )
    t.cx.commit()
    t.aviso('espero a Agendapro')
    t.reemplazar(
        '⏳ Espero a que Agendapro la suba y la enlazo. Si en unos días no aparece, te pregunto.\n'
        + describir(t.cx, alm.pendiente(t.pid))
    )
    return None


def toque_parecido(t: Any) -> None:
    """1 = es el mismo que ya esta: se enlaza · 0 = son dos: se guarda igual."""
    p = _es_suyo(t.cx, t)
    if not p:
        return
    if t.idx == 1:
        accion, candidato = publicador.publicar_en_su_libro(t.cx, t.pid)
        if accion == 'parecido':
            publicador_actual.enlazar_con(t.cx, p, candidato, 'mismo_que_ya_estaba')
            t.aviso('listo')
            t.reemplazar(
                '✅ Era el mismo: no agregué nada.\n'
                + describir(t.cx, _a(t.cx).pendiente(t.pid))
            )
            return
        _contar(t.cx, t.pid, t.chat, accion, candidato)
        return
    _a(t.cx).actualizar_pendiente(t.pid, pregunta=None)
    t.cx.commit()
    t.aviso('lo guardo')
    _publicar(t.cx, t.pid, t.chat, aunque_se_parezca=True)


TOQUES = {
    'km': toque_medio,
    'ld': toque_destino,
    'lr': toque_otro_libro,
    'kc': toque_categoria,
    'kt': toque_escribir_categoria,
    'va': toque_venta,
    'kp': toque_parecido,
}


# ---------------------------------------------------------------- mensajes


def manejar_mensaje(cx: Any, chat: Any, msg: dict[str, Any]) -> None:
    """Texto o nota de voz de alguien con varios libros."""
    alm = _a(cx)
    u = alm.usuario_por_chat(chat)
    if u is None:
        return
    texto = (msg.get('text') or msg.get('caption') or '').strip()

    if texto.startswith('/'):
        return _comando(cx, chat, u, texto)

    respondiendo = (msg.get('reply_to_message') or {}).get('message_id')
    if texto and respondiendo:
        pid = alm.pendiente_de_mensaje(str(chat), respondiendo)
        if pid:
            return _respuesta_escrita(cx, chat, pid, texto)

    audio = msg.get('voice') or msg.get('audio')
    if audio:
        try:
            datos = telegram.descargar(audio['file_id'])
        except telegram.TelegramError as ex:
            telegram.enviar(chat, f'No pude bajar el audio: {_e(ex)}')
            return None
        return _registrar(
            cx,
            chat,
            u,
            msg['message_id'],
            audio=datos,
            tipo=audio.get('mime_type') or 'audio/ogg',
        )

    if texto:
        return _registrar(cx, chat, u, msg['message_id'], texto=texto)
    telegram.enviar(chat, AYUDA)
    return None


def _comando(cx: Any, chat: Any, u: Any, texto: str) -> None:
    nombre = texto.split(maxsplit=1)[0].split('@', maxsplit=1)[0]
    if nombre == '/pendientes':
        abiertos = [
            p
            for p in _a(cx).pendientes_abiertos_de_chat(str(chat))
            if p['estado'] in ('nuevo', 'error')
        ]
        if not abiertos:
            telegram.enviar(chat, 'No tengo nada por preguntarte. ✅')
            return
        telegram.enviar(chat, f'Tengo <b>{len(abiertos)}</b> por contestar:')
        for p in abiertos[:6]:
            siguiente(cx, p['id'], chat)
        return
    telegram.enviar(chat, AYUDA)


def _respuesta_escrita(cx: Any, chat: Any, pid: int, texto: str) -> None:
    p = _a(cx).pendiente(pid)
    if p is None or p['usuario_id'] != _a(cx).usuario_por_chat(chat)['id']:
        return
    if p['pregunta'] != 'categoria' or not p['libro_id']:
        telegram.enviar(chat, 'Para eso usa los botones del mensaje, por favor.')
        return
    lb = _a(cx).libro(p['libro_id'])
    cats = _categorias(cx, p, lb)
    hallados = interprete.buscar_categoria(texto, cats)
    if not hallados:
        telegram.enviar(
            chat,
            f'«{_e(texto)}» no es una categoría de {_e(lb["nombre"])}. '
            'Estas son las que hay: ' + ', '.join(_e(c) for c in cats),
        )
        return
    ruteo.elegir_categoria(_a(cx), pid, hallados[0][1])
    _publicar(cx, pid, chat)


def _registrar(
    cx: Any,
    chat: Any,
    u: Any,
    mensaje_id: int,
    texto: str | None = None,
    audio: bytes | None = None,
    tipo: str = 'audio/ogg',
) -> None:
    alm = _a(cx)
    instrumentos = alm.instrumentos_de(u['id'])
    medios = list(dict.fromkeys(i['clave'] for i in instrumentos))
    todos = alm.libros_de(u['id'])
    hoy = str(fechas.hoy())
    d: dict[str, Any] | None = None
    if ia.disponible():
        try:
            d = ia.entender_contado(
                hoy,
                medios,
                [(lb['clave'], lb['nombre']) for lb in todos],
                texto=texto,
                audio=audio,
                tipo_audio=tipo,
            )
        except ia.SinIA as ex:
            print(f'  no entendi lo contado con la IA: {ex}')
    if d is None:
        if audio is not None:
            telegram.enviar(chat, 'No pude entender el audio ahora. ¿Me lo escribes?')
            return
        monto = _contado.monto_dicho(texto)
        d = {
            'es_movimiento': monto is not None,
            'direccion': 'ingreso' if _contado.es_ingreso(texto) else 'gasto',
            'monto': monto or 0,
            'descripcion': texto,
            'comercio': '',
            'fecha': hoy,
            'medio': ruteo.instrumento_por_nombre(alm, u['id'], texto),
            'libro': None,
            'transcripcion': texto,
        }
    if not d.get('es_movimiento'):
        telegram.enviar(chat, AYUDA)
        return
    monto = float(d.get('monto') or 0)
    if monto <= 0:
        telegram.enviar(
            chat,
            'No entendí cuánto fue. Dímelo con el monto: «45 mil de esmaltes con la Nu».',
        )
        return
    fecha = fechas.a_fecha(d.get('fecha')) or fechas.hoy()
    if fecha > fechas.hoy():
        fecha = fechas.hoy()
    libro = next((lb['id'] for lb in todos if lb['clave'] == d.get('libro')), None)
    c = ruteo.Contado(
        valor=-monto if d.get('direccion') != 'ingreso' else monto,
        comercio=(d.get('comercio') or '').strip() or None,
        descripcion=(d.get('descripcion') or '').strip() or None,
        fecha=str(fecha),
        instrumento=d.get('medio') or ruteo.instrumento_por_nombre(alm, u['id'], texto),
        libro_sugerido=libro,
        transcripcion=d.get('transcripcion'),
    )
    pid, nuevo = ruteo.crear_desde_chat(alm, u['id'], chat, mensaje_id, c)
    if not nuevo:
        return
    siguiente(cx, pid, chat)


# ------------------------------------------------- las ventas que se esperan

DIAS_DE_ESPERA = 4


def revisar_ventas_en_espera(cx: Any) -> int:
    """Las transferencias que la persona dijo que eran de una clienta y que
    Agendapro todavia no habia subido. Se enlazan cuando aparecen; si en unos
    dias no aparecen, se le pregunta. Devuelve cuantas enlazo."""
    alm = _a(cx)
    enlazadas = 0
    for p in alm.en_espera_de_agendapro():
        lb = alm.libro(p['libro_id'])
        chat = p['telegram_chat_id']
        try:
            venta = publicador_actual.venta_de_agendapro(libros.cliente(lb), p)
        except (libros.LibroNoDisponible, actual.ApiError) as ex:
            print(f'  no pude revisar la venta de #{p["id"]}: {ex}')
            continue
        if venta:
            publicador_actual.enlazar_con(cx, p, venta, 'venta_agendapro')
            enlazadas += 1
            if chat:
                telegram.enviar(
                    chat,
                    '✅ Ya apareció en Agendapro, la enlacé.\n'
                    + describir(cx, alm.pendiente(p['id'])),
                )
            continue
        f = fechas.a_fecha(p['fecha'])
        if f and (fechas.hoy() - f).days >= DIAS_DE_ESPERA and chat:
            alm.actualizar_pendiente(
                p['id'], decidido_por='alerta', pregunta='categoria'
            )
            cx.commit()
            telegram.enviar(
                chat,
                f'⏳ Esta transferencia lleva {DIAS_DE_ESPERA} días y no aparece en Agendapro.',
            )
            siguiente(cx, p['id'], chat)
    return enlazadas
