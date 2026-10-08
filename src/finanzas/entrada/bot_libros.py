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

En la pregunta de la categoria hay dos salidas mas:

  una nueva       la propone la IA mirando la descripcion, o la escribe ella.
                   Se crea en ESE libro con un toque (aplicacion/categorias).
  🤝 prestamo      la plata sale y vuelve: el jefe le pide que le pase plata y
                   se la devuelve. Es un traslado a la cuenta «Préstamos» del
                   libro, no un gasto (aplicacion/prestamos).
"""

from __future__ import annotations

import html
import time
from typing import Any

from finanzas.adaptadores import actual, firefly, ia, telegram
from finanzas.adaptadores.almacen import Almacen
from finanzas.aplicacion import (
    categorias,
    interprete,
    libros,
    prestamos,
    publicador,
    publicador_actual,
    ruteo,
)
from finanzas.dominio import contado as _contado
from finanzas.dominio import dinero as _dinero
from finanzas.dominio import fechas

MAX_CATEGORIAS = 12
EMOJI = {'firefly': '📒', 'actual': '💅'}

# Las sugerencias de un movimiento son una lista de textos, y los botones
# viajan con el indice. Cuando la lista no es de categorias -- los grupos de
# Actual, las personas de un prestamo -- empieza con una marca, para que un
# boton viejo de categoria no tome un grupo o un nombre como categoria.
MARCA_GRUPO = '__grupo__'
MARCA_PERSONA = '__persona__'
# Por nombre: ruff lo toma por un «+» disfrazado si va pegado en el texto.
MAS = '\N{HEAVY PLUS SIGN}'

AYUDA = (
    '<b>Cómo registrar algo</b>\n\n'
    'Cuéntamelo como se lo dirías a alguien, escrito o en una nota de voz:\n'
    '«45 mil de esmaltes con la Nu»\n'
    '«ayer pagué 120 mil de arriendo del local en efectivo»\n'
    '«me pagaron 80 mil por unas uñas»\n'
    '«le presté 200 mil a mi jefe»\n\n'
    'Las alertas de Bancolombia te llegan solas.\n\n'
    'Siempre te pregunto a qué libro va antes de guardar nada. Si ninguna '
    'categoría le queda, puedes crear una desde ahí.\n\n'
    '/pendientes — lo que falta por contestar\n'
    '/prestamos — quién te debe y a quién le debes'
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
    if prestamos.es_prestamo(p):
        lineas.append(f'🤝 Préstamo con <b>{_e(p["prestamo_con"])}</b>')
    return '\n'.join(lineas)


def _enviar(
    cx: Any, chat: Any, p: Any, texto: str, botones: Any, tipo: str = 'categoria'
) -> None:
    msg = telegram.enviar(chat, texto, botones)
    if msg and msg.get('message_id'):
        _a(cx).guardar_mensaje(str(chat), msg['message_id'], p['id'], tipo)
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
        # Una devolucion que cuadra, o un prestamo que ella misma conto, no es
        # una venta: no se pregunta por Agendapro.
        de_prestamo = p['prestamo_con'] or any(
            exacto for *_, exacto in prestamos.devoluciones_posibles(alm, p)
        )
        if (
            lb['tipo'] == 'actual'
            and float(p['valor']) > 0
            and not de_prestamo
            and _preguntar_si_es_venta(cx, p, chat, lb)
        ):
            return None
        return _preguntar_categoria(cx, p, chat)
    if p['pregunta'] == 'existencia':
        return _preguntar_parecido(cx, p, chat)
    if p['decidido_por'] == 'espera_agendapro':
        return None
    return _publicar(cx, p['id'], chat)


def _es_una_venta(lb: Any, p: Any) -> bool:
    """Plata que entra al estudio como «Servicios»: es una venta, y las ventas
    las escribe Agendapro. Si el bot tambien la escribiera, contaria doble en
    cuanto Agendapro la suba (pasó con Angel y Verónica)."""
    return bool(
        lb
        and lb['tipo'] == 'actual'
        and float(p['valor']) > 0
        and p['categoria'] == 'Servicios'
        and not p['pago_libro_id']
        and not prestamos.es_prestamo(p)
    )


def _la_escribe_agendapro(cx: Any, p: Any, chat: Any) -> None:
    """Una venta no se escribe: se enlaza con la de Agendapro si ya esta, y si
    no, se espera a que ella la registre alla."""
    alm = _a(cx)
    lb = alm.libro(p['libro_id'])
    try:
        venta = publicador_actual.venta_de_agendapro(
            libros.cliente(lb), p, alm.ventas_ya_enlazadas(p)
        )
    except (libros.LibroNoDisponible, actual.ApiError):
        venta = None
    if venta:
        publicador_actual.enlazar_con(cx, p, venta, 'venta_agendapro')
        telegram.enviar(
            chat,
            '✅ Es la venta de Agendapro: no agregué nada.\n'
            + _la_venta(venta)
            + '\n\n'
            + describir(cx, alm.pendiente(p['id'])),
        )
        return
    alm.actualizar_pendiente(p['id'], pregunta=None, decidido_por='espera_agendapro')
    cx.commit()
    telegram.enviar(
        chat,
        '💈 Las ventas las escribe Agendapro, así no se cuentan dos veces. '
        'Si no está registrada, regístrala allá; cuando aparezca, la enlazo.\n'
        + describir(cx, alm.pendiente(p['id'])),
    )


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
    # Pagado con la plata del otro libro: la Nu pagando insumos del estudio,
    # o la tarjeta del estudio pagando algo personal. Botones aparte, nunca
    # preseleccionados.
    for tipo, gasto, paga in ruteo.cruces(alm, p):
        if tipo == ruteo.APORTE:
            etiqueta, dato = f'{_nombre(gasto)} (lo pagué yo)', 'lp'
        else:
            etiqueta, dato = f'{_nombre(gasto)} (lo pagó {paga["nombre"]})', 'lq'
        if gasto['id'] == p['sugerido_libro_id']:
            etiqueta += ' ✓'
            marcado = gasto['id']
        botones.append([(etiqueta, f'{dato}:{p["id"]}:{gasto["id"]}')])
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
    # Las nuevas que ya eligio y todavia no estan en el libro (en seco no se
    # crean). En Actual solo con su grupo: sin grupo no hay como crearla.
    for r in _a(cx).categorias_por_crear(lb['id'], ingreso):
        if categorias.ya_existe(r['categoria'], nombres):
            continue
        if isinstance(cliente, actual.Cliente) and not r['grupo']:
            continue
        nombres.append(r['categoria'])
    # La que ya contesto para este comercio en este libro, primero.
    regla = _a(cx).regla_de_libro(p['usuario_id'], ruteo.clave_de(p))
    if regla and regla['libro_id'] == lb['id'] and regla['categoria'] in nombres:
        nombres.remove(regla['categoria'])
        nombres.insert(0, regla['categoria'])
    return nombres


def _grupo_por_crear(cx: Any, p: Any, nombre: str) -> str | None:
    """El grupo de Actual de una categoria que se eligio como nueva y todavia
    no existe. Elegirla otra vez tiene que llevar su grupo: sin el, al
    publicar no hay donde crearla."""
    for r in _a(cx).categorias_por_crear(p['libro_id'], float(p['valor']) > 0):
        if r['categoria'] == nombre:
            return r['grupo']
    return None


def _aprendida(cx: Any, p: Any, lb: Any) -> bool:
    regla = _a(cx).regla_de_libro(p['usuario_id'], ruteo.clave_de(p))
    return bool(regla and regla['libro_id'] == lb['id'] and regla['categoria'])


def _botones_de_categorias(
    pid: int, cats: list[str], marcada: str | None = None, desde: int = 0
) -> list[list[tuple[str, str]]]:
    """Los botones de categoria. `desde` es donde empiezan `cats` dentro de las
    sugerencias guardadas.

    La marcada se DIBUJA primero pero conserva su indice: la lista guardada no
    se reordena, porque un boton de una pregunta anterior del mismo movimiento
    apunta por indice, y reordenar lo haria caer en otra categoria.
    """
    orden = list(range(min(len(cats), MAX_CATEGORIAS)))
    if marcada in cats:
        m = cats.index(marcada)
        orden = [m, *(i for i in orden if i != m)]
    botones, fila = [], []
    for i in orden:
        c = cats[i]
        fila.append((c + (' ✓' if c == marcada else ''), f'kc:{pid}:{desde + i}'))
        if len(fila) == 2:
            botones.append(fila)
            fila = []
    if fila:
        botones.append(fila)
    return botones


def _preguntar_categoria(cx: Any, p: Any, chat: Any) -> None:
    alm = _a(cx)
    lb = alm.libro(p['libro_id'])
    try:
        cats = _categorias(cx, p, lb)
    except (libros.LibroNoDisponible, actual.ApiError, firefly.ApiError) as ex:
        telegram.enviar(
            chat,
            f'No pude leer las categorías de {_e(lb["nombre"])}: {_e(str(ex)[:150])}',
        )
        return

    # Lo que propone la IA solo si no hay nada aprendido: lo que ella ya
    # contesto para este comercio pesa mas que cualquier adivinanza.
    marcada, nueva, razon = None, None, ''
    if not _aprendida(cx, p, lb) and not p['prestamo_con']:
        prop = categorias.proponer(p, cats, lb['nombre'])
        marcada, nueva, razon = prop['existente'], prop['nueva'], prop['razon']
    alm.guardar_sugerencias(p['id'], [*cats, nueva] if nueva else cats)

    botones: list[list[tuple[str, str]]] = []
    # Un gasto del estudio pagado con su plata es un aporte, no un prestamo:
    # ahi no se ofrece.
    con_prestamo = not p['pago_libro_id']
    # Un prestamo va primero cuando hay algo que lo diga: lo conto asi, o el
    # monto cuadra con lo que alguien le debe. Igual es un boton: nunca se
    # asume.
    if p['prestamo_con'] and con_prestamo:
        botones.append([(f'🤝 Préstamo con {p["prestamo_con"]} ✓', f'ks:{p["id"]}:0')])
    for j, (persona, saldo, exacto) in enumerate(
        prestamos.devoluciones_posibles(alm, p) if con_prestamo else []
    ):
        que = 'te debe' if saldo > 0 else 'le debes'
        botones.append(
            [
                (
                    f'🤝 Devolución · {persona} ({que} {_dinero.formatear(abs(saldo), "COP")})'
                    + (' ✓' if exacto else ''),
                    f'kd:{p["id"]}:{j}',
                )
            ]
        )
    botones += _botones_de_categorias(p['id'], cats, marcada)
    if nueva:
        botones.append([(f'{MAS} Nueva: «{nueva}»', f'kn:{p["id"]}:{len(cats)}')])
    if con_prestamo:
        botones.append([('🤝 Préstamo o devolución', f'kl:{p["id"]}:0')])
    botones.append(
        [('✏️ Escribirla', f'kt:{p["id"]}:0'), ('↩️ Otro libro', f'lr:{p["id"]}:0')]
    )
    pie = '\n\n<b>¿Qué categoría?</b>'
    if razon and (marcada or nueva):
        pie += f'\n<i>{_e(razon)}</i>'
    _enviar(cx, chat, p, describir(cx, p) + pie, botones)


def _la_venta(venta: dict[str, Any]) -> str:
    """La venta de Agendapro como la escribio el CRM: la clienta y el servicio
    van primero en la nota («Monica Jaramillo · Tradicional pies · Venta 1129 ·
    transferencia»); las que subieron antes de eso solo dicen «Venta N»."""
    return f'💈 <b>{_e(venta.get("notes") or "Venta")}</b> · {_e(venta.get("date"))}'


def _quien(venta: dict[str, Any]) -> str:
    """El boton de una venta: clienta y servicio sin el metodo de pago, que en
    un boton no cabe y no ayuda a reconocerla."""
    partes = [
        x
        for x in (venta.get('notes') or 'Venta').split(' · ')
        if x not in ('transferencia', 'efectivo', 'otro')
    ]
    fecha = str(venta.get('date') or '')[5:]
    return f'💈 {" · ".join(partes)[:40]} · {_plata(venta.get("amount", 0) / 100)} · {fecha}'


def _puede_esperar(p: Any) -> bool:
    """Esperar a Agendapro solo tiene sentido con una transferencia reciente y
    una sola vez: si ya se espero y no aparecio, ofrecerlo de nuevo es el
    bucle en el que ella contestaba lo mismo cada pocos minutos."""
    f = fechas.a_fecha(p['fecha'])
    return (
        p['decidido_por'] != 'espera_vencida'
        and f is not None
        and (fechas.hoy() - f).days < DIAS_DE_ESPERA
    )


def _preguntar_si_es_venta(cx: Any, p: Any, chat: Any, lb: Any) -> bool:
    """Una transferencia que entra al estudio casi siempre es una clienta, y
    Agendapro ya la subio como venta. Se pregunta antes que la categoria.

    Con una venta igual, se propone esa. Sin ella, se muestran las ventas de
    esos dias para que ella elija: la clienta pudo pagar distinto de lo
    cobrado, la venta pudo quedar otro dia, o pudo haber dos del mismo valor.
    """
    alm = _a(cx)
    try:
        cliente = libros.cliente(lb)
        ya = alm.ventas_ya_enlazadas(p)
        venta = publicador_actual.venta_de_agendapro(cliente, p, ya)
        cerca = [] if venta else publicador_actual.ventas_cerca(cliente, p, ya)
    except (libros.LibroNoDisponible, actual.ApiError):
        return False
    if venta:
        texto = (
            describir(cx, p)
            + '\n\nEn Agendapro hay una venta igual:\n'
            + _la_venta(venta)
            + '\n\n<b>¿Es esa venta?</b>'
            '\n<i>Si es, no agrego nada: ya está en el libro.</i>'
        )
        botones = [
            [('✅ Sí, es esa venta', f'va:{p["id"]}:1')],
            [('No, es otra cosa', f'va:{p["id"]}:0')],
        ]
    else:
        alm.guardar_sugerencias(p['id'], [str(t['id']) for t in cerca])
        texto = describir(cx, p) + '\n\n<b>¿Es el pago de una clienta?</b>'
        if cerca:
            texto += (
                '\n<i>No hay una venta igual en Agendapro. Si es una de estas, '
                'tócala y la enlazo:</i>'
            )
        botones = [[(_quien(t), f'vc:{p["id"]}:{i}')] for i, t in enumerate(cerca)]
        if _puede_esperar(p):
            texto += (
                '\n<i>Agendapro sube las ventas en la noche; si todavía no está, '
                'espero a que aparezca y la enlazo, para no contarla dos veces.</i>'
            )
            botones.append([('⏳ Sí, pero todavía no está', f'va:{p["id"]}:2')])
        botones.append([('📝 No está en Agendapro', f'va:{p["id"]}:0')])
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
    p = _a(cx).pendiente(pendiente_id)
    if p is not None and _es_una_venta(_a(cx).libro(p['libro_id']), p):
        return _la_escribe_agendapro(cx, p, chat)
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
    if p['pago_libro_id'] and accion == 'creado':
        paga = _a(cx).libro(p['pago_libro_id'])
        telegram.enviar(
            chat,
            f'✅ Guardado en {donde}{cat}, como aporte tuyo, y el cargo en '
            f'{_nombre(paga)} · {_e(p["cuenta_pago"])}\n' + describir(cx, p),
        )
        return
    if prestamos.es_prestamo(p) and accion in ('creado', 'seco', 'ya_estaba'):
        saldo = prestamos.saldo_de(_a(cx), p) or 0
        plata = _dinero.formatear(abs(saldo), 'COP')
        quien = _e(p['prestamo_con'])
        if abs(saldo) < 1:
            cuenta = f'Con {quien} quedaron a paz y salvo.'
        elif saldo > 0:
            cuenta = f'{quien} te debe <b>{plata}</b>.'
        else:
            cuenta = f'Le debes a {quien} <b>{plata}</b>.'
        primero = (
            f'🧪 En prueba: lo guardaría en {donde} como préstamo. Todavía no escribo en ese libro.'
            if accion == 'seco'
            else f'✅ Guardado en {donde} como préstamo, no como gasto.'
        )
        telegram.enviar(chat, f'{primero}\n{cuenta}\n' + describir(cx, p))
        return
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


def toque_aporte(t: Any) -> None:
    """«Es del estudio, lo pague yo»."""
    p = _es_suyo(t.cx, t)
    if not p:
        return
    try:
        p = ruteo.elegir_aporte(_a(t.cx), t.pid, t.idx)
    except ValueError:
        t.aviso('esa opción ya no está')
        return
    t.aviso('aporte tuyo')
    t.reemplazar(
        describir(t.cx, p) + '\n<i>Lo pagaste tú: queda como aporte al estudio.</i>'
    )
    siguiente(t.cx, t.pid, t.chat)


def toque_pago_duena(t: Any) -> None:
    """«Fue personal, lo pago el estudio»."""
    p = _es_suyo(t.cx, t)
    if not p:
        return
    try:
        p = ruteo.elegir_pago_duena(_a(t.cx), t.pid, t.idx)
    except ValueError:
        t.aviso('esa opción ya no está')
        return
    t.aviso('pago a la dueña')
    t.reemplazar(
        describir(t.cx, p)
        + f'\n<i>Lo pagó el estudio por ti: queda como {_e(p["categoria"])}.</i>'
    )
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
        pago_libro_id=None,
        cuenta_pago=None,
        categoria_grupo=None,
    )
    t.cx.commit()
    t.aviso('elige otra vez')
    siguiente(t.cx, t.pid, t.chat)


def toque_categoria(t: Any) -> None:
    p = _es_suyo(t.cx, t)
    if not p:
        return
    cats = _a(t.cx).sugerencias(t.pid)
    if (
        t.idx >= len(cats)
        or not p['libro_id']
        or (cats and cats[0] in (MARCA_GRUPO, MARCA_PERSONA))
    ):
        t.aviso('esa opción ya no está')
        return
    grupo = _grupo_por_crear(t.cx, p, cats[t.idx])
    ruteo.elegir_categoria(_a(t.cx), t.pid, cats[t.idx])
    _a(t.cx).actualizar_pendiente(t.pid, categoria_grupo=grupo)
    t.cx.commit()
    t.aviso(cats[t.idx])
    t.reemplazar(describir(t.cx, _a(t.cx).pendiente(t.pid)))
    _publicar(t.cx, t.pid, t.chat)


def _crear_categoria(cx: Any, pid: int, chat: Any, nombre: str) -> Any:
    """La categoria nueva, en el libro del movimiento. En Actual, con mas de
    un grupo posible, primero pregunta en cual va."""
    alm = _a(cx)
    p = alm.pendiente(pid)
    lb = alm.libro(p['libro_id'])
    cliente = libros.cliente(lb)
    if isinstance(cliente, actual.Cliente):
        grupos = cliente.grupos(float(p['valor']) > 0)
        if not grupos:
            telegram.enviar(
                chat, f'{_e(lb["nombre"])} no tiene grupos para esa categoría.'
            )
            return None
        if len(grupos) > 1:
            alm.guardar_sugerencias(
                pid, [MARCA_GRUPO, nombre, *(g['id'] for g in grupos)]
            )
            botones = [[(g['name'], f'kg:{pid}:{j}')] for j, g in enumerate(grupos)]
            _enviar(
                cx,
                chat,
                p,
                describir(cx, p)
                + f'\n\n¿En qué grupo de {_e(lb["nombre"])} va <b>{_e(nombre)}</b>?',
                botones,
            )
            return None
        return _con_la_nueva(cx, pid, chat, nombre, grupos[0]['id'])
    return _con_la_nueva(cx, pid, chat, nombre, None)


def _con_la_nueva(cx: Any, pid: int, chat: Any, nombre: str, grupo: str | None) -> None:
    """Queda con la categoria nueva y se publica. En Firefly se crea al
    publicar; en Actual el publicador la crea en `grupo` junto con el
    movimiento. En seco no se crea en ninguno."""
    if not _abierto(cx, pid, chat):
        return
    ruteo.elegir_categoria(_a(cx), pid, nombre)
    _a(cx).actualizar_pendiente(pid, categoria_grupo=grupo)
    cx.commit()
    _publicar(cx, pid, chat)


def toque_nueva_categoria(t: Any) -> None:
    """Toco «Nueva: X» o «Crear X», los botones de una categoria nueva."""
    p = _es_suyo(t.cx, t)
    if not p:
        return
    sug = _a(t.cx).sugerencias(t.pid)
    if t.idx >= len(sug) or not p['libro_id'] or sug[0] in (MARCA_GRUPO, MARCA_PERSONA):
        t.aviso('esa opción ya no está')
        return
    nombre = sug[t.idx]
    lb = _a(t.cx).libro(p['libro_id'])
    try:
        existentes = _categorias(t.cx, p, lb)
        ya = categorias.ya_existe(nombre, existentes)
        t.aviso(f'nueva: {nombre}' if not ya else nombre)
        t.reemplazar(describir(t.cx, p))
        if ya:
            grupo = _grupo_por_crear(t.cx, p, ya)
            ruteo.elegir_categoria(_a(t.cx), t.pid, ya)
            _a(t.cx).actualizar_pendiente(t.pid, categoria_grupo=grupo)
            t.cx.commit()
            _publicar(t.cx, t.pid, t.chat)
            return
        _crear_categoria(t.cx, t.pid, t.chat, nombre)
    except (libros.LibroNoDisponible, actual.ApiError, firefly.ApiError) as ex:
        telegram.enviar(
            t.chat,
            f'No pude crear la categoría en {_e(lb["nombre"])}: {_e(str(ex)[:150])}',
        )


def toque_grupo(t: Any) -> None:
    """Eligio el grupo de Actual donde va la categoria nueva."""
    p = _es_suyo(t.cx, t)
    if not p:
        return
    sug = _a(t.cx).sugerencias(t.pid)
    if len(sug) < 3 or sug[0] != MARCA_GRUPO or t.idx >= len(sug) - 2:
        t.aviso('esa opción ya no está')
        return
    nombre, grupo = sug[1], sug[2 + t.idx]
    t.aviso(nombre)
    t.reemplazar(describir(t.cx, p))
    _con_la_nueva(t.cx, t.pid, t.chat, nombre, grupo)


# ---------------------------------------------------------------- prestamos


def toque_prestamo(t: Any) -> None:
    """«🤝 Préstamo o devolución»: con quien."""
    p = _es_suyo(t.cx, t)
    if not p or not p['libro_id']:
        return
    alm = _a(t.cx)
    personas = [
        persona for persona, _ in prestamos.saldos(alm, p['usuario_id'], p['libro_id'])
    ]
    dicho = prestamos.nombre(p['prestamo_con'])
    if dicho and dicho.lower() not in {x.lower() for x in personas}:
        personas.insert(0, dicho)
    alm.guardar_sugerencias(t.pid, [MARCA_PERSONA, *personas])
    t.aviso('¿con quién?')
    t.reemplazar(describir(t.cx, p))
    botones = [[(f'👤 {x}', f'kw:{t.pid}:{j}')] for j, x in enumerate(personas)]
    botones.append([('↩️ No es un préstamo', f'kv:{t.pid}:0')])
    entra = float(p['valor']) > 0
    pregunta = (
        '¿Quién te la pasó o te la devolvió?'
        if entra
        else '¿A quién se la prestaste o le devolviste?'
    )
    _enviar(
        t.cx,
        t.chat,
        p,
        describir(t.cx, p)
        + f'\n\n<b>{pregunta}</b>\n<i>Escríbeme el nombre, o toca uno.</i>',
        botones,
        tipo='prestamo',
    )
    _esperar(t.chat, t.pid, 'prestamo')


# Lo que se le acaba de pedir por escrito en cada chat: el nombre de quien le
# debe, o el de una categoria. En Telegram casi nadie desliza para responder:
# escribe y ya. Sin esto, «mi jefe» suelto se leia como un movimiento nuevo,
# contestaba la ayuda y el prestamo se quedaba sin hacer. En memoria, como el
# hilo del asesor: si el proceso se reinicia, basta con tocar el boton otra vez.
ESPERANDO: dict[str, tuple[int, str, float]] = {}
MINUTOS_DE_ESPERA = 30


def _esperar(chat: Any, pid: int, tipo: str) -> None:
    ESPERANDO[str(chat)] = (pid, tipo, time.time())


def respuesta_sin_responder(cx: Any, chat: Any, texto: str) -> bool:
    """Si `texto`, escrito sin responder a ningun mensaje, es lo que se le
    acaba de pedir, lo atiende y devuelve True. Un texto con monto es un
    movimiento nuevo, no un nombre: ese sigue su camino."""
    pid, tipo, cuando = ESPERANDO.get(str(chat), (0, '', 0.0))
    if not pid or time.time() - cuando > MINUTOS_DE_ESPERA * 60:
        return False
    if not texto or texto.startswith('/') or _contado.monto_dicho(texto) is not None:
        return False
    ESPERANDO.pop(str(chat), None)
    if tipo == 'prestamo':
        respuesta_de_prestamo(cx, chat, pid, texto)
    else:
        _respuesta_escrita(cx, chat, pid, texto)
    return True


def _abierto(cx: Any, pid: int, chat: Any) -> bool:
    """Un boton viejo no cambia un movimiento que ya esta en su libro."""
    p = _a(cx).pendiente(pid)
    if p['estado'] in ('nuevo', 'error') and not p['firefly_id']:
        return True
    telegram.enviar(
        chat, 'Ese movimiento ya está guardado; ya no lo cambio desde aquí.'
    )
    return False


def _es_prestamo_con(cx: Any, pid: int, chat: Any, persona: str | None) -> None:
    persona = prestamos.nombre(persona)
    if not persona:
        telegram.enviar(chat, 'No entendí el nombre. Escríbeme cómo se llama.')
        _esperar(chat, pid, 'prestamo')
        return
    # Si ya estaba en el libro como gasto o ingreso (el camino de Juan publica
    # antes de preguntar), se saca para volver a escribirlo como traslado.
    try:
        prestamos.sacar_del_libro(_a(cx), pid)
    except prestamos.NoSePuedeRehacer as ex:
        telegram.enviar(chat, f'No lo puedo volver préstamo: {_e(ex)}.')
        return
    except (libros.LibroNoDisponible, actual.ApiError, firefly.ApiError) as ex:
        telegram.enviar(chat, f'No pude sacarlo del libro: {_e(str(ex)[:150])}')
        return
    prestamos.elegir(_a(cx), pid, persona)
    _publicar(cx, pid, chat)


def toque_persona(t: Any) -> None:
    p = _es_suyo(t.cx, t)
    if not p:
        return
    sug = _a(t.cx).sugerencias(t.pid)
    if not sug or sug[0] != MARCA_PERSONA or t.idx >= len(sug) - 1:
        t.aviso('esa opción ya no está')
        return
    t.aviso(sug[1 + t.idx])
    t.reemplazar(describir(t.cx, p))
    _es_prestamo_con(t.cx, t.pid, t.chat, sug[1 + t.idx])


def toque_devolucion(t: Any) -> None:
    """Una de las devoluciones que se ofrecieron en la pregunta de categoria."""
    p = _es_suyo(t.cx, t)
    if not p:
        return
    posibles = prestamos.devoluciones_posibles(_a(t.cx), p)
    if t.idx >= len(posibles):
        t.aviso('esa opción ya no está')
        return
    persona = posibles[t.idx][0]
    t.aviso(persona)
    t.reemplazar(describir(t.cx, p))
    _es_prestamo_con(t.cx, t.pid, t.chat, persona)


def toque_prestamo_dicho(t: Any) -> None:
    """Confirmo el prestamo que conto en el chat."""
    p = _es_suyo(t.cx, t)
    if not p or not p['prestamo_con']:
        t.aviso('esa opción ya no está')
        return
    t.aviso(p['prestamo_con'])
    t.reemplazar(describir(t.cx, p))
    _es_prestamo_con(t.cx, t.pid, t.chat, p['prestamo_con'])


def toque_no_es_prestamo(t: Any) -> None:
    p = _es_suyo(t.cx, t)
    if not p:
        return
    _a(t.cx).actualizar_pendiente(t.pid, prestamo_con=None)
    t.cx.commit()
    t.aviso('entonces, la categoría')
    t.reemplazar(describir(t.cx, _a(t.cx).pendiente(t.pid)))
    _preguntar_categoria(t.cx, _a(t.cx).pendiente(t.pid), t.chat)


def texto_de_prestamos(cx: Any, usuario_id: int) -> str:
    alm = _a(cx)
    lineas = []
    for lb in alm.libros_de(usuario_id):
        for persona, saldo in prestamos.saldos(alm, usuario_id, lb['id']):
            plata = _dinero.formatear(abs(saldo), 'COP')
            que = f'te debe <b>{plata}</b>' if saldo > 0 else f'le debes <b>{plata}</b>'
            lineas.append(f'{_nombre(lb)} · {_e(persona)} {que}')
    if not lineas:
        return 'No hay préstamos abiertos: nadie te debe y no le debes a nadie. ✅'
    return '<b>Préstamos abiertos</b>\n' + '\n'.join(lineas)


def toque_escribir_categoria(t: Any) -> None:
    if not _es_suyo(t.cx, t):
        return
    t.aviso('escríbela')
    msg = telegram.enviar(t.chat, 'Escríbeme el nombre de la categoría:')
    if msg and msg.get('message_id'):
        _a(t.cx).guardar_mensaje(str(t.chat), msg['message_id'], t.pid)
    _esperar(t.chat, t.pid, 'categoria')


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
    venta = publicador_actual.venta_de_agendapro(
        libros.cliente(lb), p, alm.ventas_ya_enlazadas(p)
    )
    if venta:
        publicador_actual.enlazar_con(t.cx, p, venta, 'venta_agendapro')
        alm.actualizar_pendiente(t.pid, categoria='Servicios')
        t.cx.commit()
        t.aviso('enlazada')
        t.reemplazar(
            '✅ Es la venta de Agendapro: no agregué nada.\n'
            + _la_venta(venta)
            + '\n\n'
            + describir(t.cx, alm.pendiente(t.pid))
        )
        return None
    if not _puede_esperar(p):
        # Un boton viejo: ya se espero, o la transferencia es de hace dias.
        t.aviso('ya no espero: elige la venta')
        return _preguntar_categoria(t.cx, p, t.chat)
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


def toque_venta_cercana(t: Any) -> None:
    """Eligio una de las ventas de esos dias: se enlaza aunque el monto o la
    fecha no sean iguales, y se le dice si el monto no cuadra."""
    p = _es_suyo(t.cx, t)
    if not p:
        return
    alm = _a(t.cx)
    ids = alm.sugerencias(t.pid)
    if t.idx >= len(ids):
        t.aviso('esa opción ya no está')
        return
    lb = alm.libro(p['libro_id'])
    venta = next(
        (
            v
            for v in publicador_actual.ventas_cerca(
                libros.cliente(lb), p, alm.ventas_ya_enlazadas(p), cuantas=50
            )
            if str(v['id']) == ids[t.idx]
        ),
        None,
    )
    if venta is None:
        t.aviso('esa venta ya no está libre')
        return _preguntar_categoria(t.cx, p, t.chat)
    publicador_actual.enlazar_con(t.cx, p, venta, 'venta_agendapro')
    alm.actualizar_pendiente(t.pid, categoria='Servicios')
    t.cx.commit()
    t.aviso('enlazada')
    ojo = ''
    if venta.get('amount') != actual.centavos(p['valor']):
        ojo = (
            f'\n⚠️ La venta dice {_plata(venta["amount"] / 100)} y llegaron '
            f'{_plata(float(p["valor"]))}: corrígela en Agendapro si no cuadra.'
        )
    t.reemplazar(
        '✅ Es la venta de Agendapro: no agregué nada.\n'
        + _la_venta(venta)
        + ojo
        + '\n\n'
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
    'kn': toque_nueva_categoria,
    'kg': toque_grupo,
    'kl': toque_prestamo,
    'kw': toque_persona,
    'kd': toque_devolucion,
    'ks': toque_prestamo_dicho,
    'kv': toque_no_es_prestamo,
    'km': toque_medio,
    'ld': toque_destino,
    'lp': toque_aporte,
    'lq': toque_pago_duena,
    'lr': toque_otro_libro,
    'kc': toque_categoria,
    'kt': toque_escribir_categoria,
    'va': toque_venta,
    'vc': toque_venta_cercana,
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
    if texto and not respondiendo and respuesta_sin_responder(cx, chat, texto):
        return None
    if texto and respondiendo:
        preguntado = alm.pregunta_de_mensaje(str(chat), respondiendo)
        if preguntado:
            ESPERANDO.pop(str(chat), None)
        if preguntado and preguntado[1] == 'prestamo':
            return respuesta_de_prestamo(cx, chat, preguntado[0], texto)
        if preguntado:
            return _respuesta_escrita(cx, chat, preguntado[0], texto)

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
    if nombre == '/prestamos':
        telegram.enviar(chat, texto_de_prestamos(cx, u['id']))
        return
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
    exacta = categorias.ya_existe(texto, cats)
    if exacta:
        grupo = _grupo_por_crear(cx, p, exacta)
        ruteo.elegir_categoria(_a(cx), pid, exacta)
        _a(cx).actualizar_pendiente(pid, categoria_grupo=grupo)
        cx.commit()
        return _publicar(cx, pid, chat)

    # No se llama asi ninguna. Antes se tomaba la mas parecida sin decir nada,
    # o se contestaba «no es una categoria» y ahi moria. Ahora se ofrecen las
    # parecidas y, si lo escrito sirve de nombre, crearla.
    hallados = [c for _, c, _ in interprete.buscar_categoria(texto, cats)][:4]
    nueva = categorias.nombre_valido(texto)
    if not hallados and not nueva:
        telegram.enviar(
            chat,
            f'«{_e(texto)}» no es una categoría de {_e(lb["nombre"])}, y es muy '
            'largo para crear una. Escríbeme solo el nombre, por ejemplo «Mascotas».',
        )
        _esperar(chat, pid, 'categoria')
        return
    # Al final de las que ya se ofrecieron, por lo mismo que en
    # `_botones_de_categorias`: los botones viejos siguen valiendo.
    antes = _a(cx).sugerencias(pid)
    if antes and antes[0] in (MARCA_GRUPO, MARCA_PERSONA):
        antes = []
    _a(cx).guardar_sugerencias(pid, [*antes, *hallados, *([nueva] if nueva else [])])
    botones = _botones_de_categorias(pid, hallados, desde=len(antes))
    if nueva:
        botones.append(
            [
                (
                    f'{MAS} Crear «{nueva}» en {lb["nombre"]}',
                    f'kn:{pid}:{len(antes) + len(hallados)}',
                )
            ]
        )
    texto_pregunta = f'«{_e(texto)}» no es una categoría de {_e(lb["nombre"])}.'
    if hallados:
        texto_pregunta += (
            ' ¿Es alguna de estas, o la creo?' if nueva else ' ¿Es alguna de estas?'
        )
    _enviar(cx, chat, p, describir(cx, p) + '\n\n' + texto_pregunta, botones)


def respuesta_de_prestamo(cx: Any, chat: Any, pid: int, texto: str) -> None:
    p = _a(cx).pendiente(pid)
    if p is None or p['usuario_id'] != _a(cx).usuario_por_chat(chat)['id']:
        return
    if not p['libro_id']:
        telegram.enviar(chat, 'Primero dime a qué libro va.')
        return
    _es_prestamo_con(cx, pid, chat, texto)


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
    prestamo = prestamos.nombre(d.get('prestamo'))
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
        prestamo=prestamo,
    )
    pid, nuevo = ruteo.crear_desde_chat(alm, u['id'], chat, mensaje_id, c)
    if not nuevo:
        return
    siguiente(cx, pid, chat)


# ------------------------------------------------- las ventas que se esperan

DIAS_DE_ESPERA = 4


def _espera_desde(p: Any) -> Any:
    """Desde cuando se espera: la transferencia, o el dia en que ella dijo
    «espera» si fue despues. Contar solo desde la transferencia hacia que una
    vieja venciera en la pasada siguiente y se preguntara otra vez de una."""
    f = fechas.a_fecha(p['fecha'])
    dijo = fechas.a_fecha(str(p['actualizado_en'] or '')[:10])
    return max(d for d in (f, dijo) if d) if (f or dijo) else None


def revisar_ventas_en_espera(cx: Any) -> int:
    """Las transferencias que la persona dijo que eran de una clienta y que
    Agendapro todavia no habia subido. Se enlazan cuando aparecen; si en unos
    dias no aparecen, se le pregunta UNA vez mas, ya sin la opcion de esperar.
    Devuelve cuantas enlazo."""
    alm = _a(cx)
    enlazadas = 0
    for p in alm.en_espera_de_agendapro():
        lb = alm.libro(p['libro_id'])
        chat = p['telegram_chat_id']
        try:
            venta = publicador_actual.venta_de_agendapro(
                libros.cliente(lb), p, alm.ventas_ya_enlazadas(p)
            )
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
                    + _la_venta(venta)
                    + '\n\n'
                    + describir(cx, alm.pendiente(p['id'])),
                )
            continue
        desde = _espera_desde(p)
        if desde and (fechas.hoy() - desde).days >= DIAS_DE_ESPERA and chat:
            alm.actualizar_pendiente(
                p['id'], decidido_por='espera_vencida', pregunta='categoria'
            )
            cx.commit()
            telegram.enviar(
                chat,
                f'⏳ Esperé {DIAS_DE_ESPERA} días y no apareció una venta igual en Agendapro.',
            )
            siguiente(cx, p['id'], chat)
    return enlazadas
