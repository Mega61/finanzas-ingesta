"""El bot de Telegram: pregunta lo que el clasificador no supo, y aprende.

    finanzas bot escuchar         # long polling, es lo que corre en el server
    finanzas bot preguntar        # manda las preguntas pendientes y sale
    finanzas bot resumen          # manda el resumen diario y sale

Cada respuesta se guarda como regla, asi que un comercio se pregunta UNA vez
en la vida. Despues de contestar, el movimiento se publica en Firefly de una
(la politica es que todo entra y se confirma hablando).
"""

import contextlib
import html
import sys
import time
import traceback

from finanzas import config
from finanzas.adaptadores import db, firefly, ia, telegram
from finanzas.adaptadores.almacen import Almacen
from finanzas.aplicacion import (
    asesor,
    catalogo,
    clasificador,
    interprete,
    movimientos,
    presupuestos,
    publicador,
)
from finanzas.dominio import dinero as _dinero
from finanzas.dominio import intencion
from finanzas.dominio import texto as _texto_dom


def _a(cx):
    """El almacen para esta conexion. Todo el SQL vive alla."""
    return Almacen(cx)


# Cuantas categorias ofrecer como botones antes de pedir texto libre.
MAX_BOTONES = 8
# Cuantas preguntas mandar de una, para no ahogar el chat.
MAX_PREGUNTAS = 6


# ------------------------------------------------------------------ formato


def _plata(v, moneda='COP'):
    return _dinero.formatear(v, moneda, con_signo=True)


def describir(p):
    partes = [f'<b>{_plata(p["valor"], p["moneda"])}</b>']
    partes.append(f'{p["fecha"]}' + (f' {p["hora"]}' if p['hora'] else ''))
    if p['contraparte']:
        partes.append(f'<b>{_escapar(p["contraparte"])}</b>')
    if p['cuenta_firefly']:
        flecha = '→' if p['valor'] < 0 else '←'
        partes.append(f'{_escapar(p["cuenta_firefly"])} {flecha}')
    return '\n'.join(partes)


# --------------------------------------------------------------- sugerencias


def _categorias_firefly():
    try:
        return sorted(
            c['attributes']['name'] for c in firefly.get_all('/api/v1/categories')
        )
    except Exception:
        return []


def sugerir_categorias(cx, usuario_id, p, todas=None):
    """Las categorias mas probables primero.

    Filtra por direccion: a una nomina que ENTRA no tiene sentido ofrecerle
    'Mecato' ni 'Salidas'. Sin esto la primera prueba real ofrecio justo eso.

    `todas` es el relleno para cuando la historia no alcanza. Se pide sola si
    no la dan: antes era obligatoria y `_pedir_categoria_a_mano` le pasaba
    `todas=False`, asi que `for c in todas` reventaba con «'bool' object is not
    iterable» justo en el camino de rescate —el que corre cuando NO se entendio
    el mensaje—, y el usuario quedaba colgado sin botones.
    """
    direccion = 'ingreso' if float(p['valor']) > 0 else 'gasto'
    sug = []

    if p['categoria']:
        sug.append(p['categoria'])

    # las mas usadas en esa direccion
    filas = _a(cx).categorias_usadas(direccion)
    for r in filas:
        if len(sug) >= MAX_BOTONES:
            break
        if r['categoria'] not in sug:
            sug.append(r['categoria'])

    # si en esa direccion no hay historia suficiente, se completa con el resto
    if len(sug) < 4:
        for c in todas if todas is not None else _categorias_firefly():
            if len(sug) >= MAX_BOTONES:
                break
            if c not in sug:
                sug.append(c)
    return sug[:MAX_BOTONES]


# ---------------------------------------------------------------- preguntar


def preguntar_pendientes(cx, limite=MAX_PREGUNTAS):
    """Manda las preguntas abiertas. Devuelve cuantas mando."""
    filas = db.pendientes_por_preguntar(cx, limite=limite)
    if not filas:
        return 0
    todas = _categorias_firefly()
    mandadas = 0
    for p in filas:
        chat = p['telegram_chat_id']
        if not chat:
            continue

        # El conciliador tambien abre preguntas, y no son de categoria.
        if p['pregunta'] == 'existencia':
            if _preguntar_fantasma(cx, p, chat):
                mandadas += 1
            continue
        if p['pregunta'] == 'monto':
            if _preguntar_monto(cx, p, chat):
                mandadas += 1
            continue

        sug = sugerir_categorias(cx, p['usuario_id'], p, todas)
        # el indice va en el callback por el limite de 64 bytes
        botones = []
        fila = []
        for i, c in enumerate(sug):
            fila.append((c, f'c:{p["id"]}:{i}'))
            if len(fila) == 2:
                botones.append(fila)
                fila = []
        if fila:
            botones.append(fila)
        botones.append([('✏️ escribirlo', f't:{p["id"]}:0')])
        # Si ya esta en Firefly, se ofrece el menu completo: presupuesto,
        # etiquetas, comercio y las 71 categorias. Antes solo habia ocho
        # botones de categoria y no habia camino a nada mas.
        if p['firefly_id']:
            botones.append(
                [
                    ('📂 categorías', f'lc:{p["firefly_id"]}:0'),
                    ('💰 presupuesto', f'pb:{p["firefly_id"]}:0'),
                ]
            )
            botones.append(
                [
                    ('🏷 etiqueta', f'le:{p["firefly_id"]}:0'),
                    ('🏪 comercio', f'nc:{p["firefly_id"]}:0'),
                ]
            )
        botones.append([('🚫 No es un movimiento', f'x:{p["id"]}:0')])

        texto = (
            '¿Qué es esto?\n\n'
            + describir(p)
            + f'\n\n<i>{_escapar(p["descripcion"])}</i>'
            # Los botones son el atajo, no la unica via. Decirlo importa: sin
            # esto la unica forma visible de contestar eran ocho categorias de
            # setenta y una, y para todo lo demas —presupuesto, etiqueta,
            # nombre del comercio— no habia camino.
            + '\n\n<i>O escríbeme y ya: «fue ropa en Etre, antojos, '
            'etiqueta Ropa».</i>'
        )
        try:
            msg = telegram.enviar(chat, texto, botones)
            db.marcar_preguntado(cx, p['id'])
            _guardar_mensaje(cx, chat, msg['message_id'], p['id'])
            # se guardan las sugerencias para poder resolver el indice despues
            _guardar_sugerencias(cx, p['id'], sug)
            mandadas += 1
        except telegram.TelegramError as ex:
            print(f'  no pude preguntar por #{p["id"]}: {ex}')
    cx.commit()
    return mandadas


def _preguntar_fantasma(cx, p, chat):
    """El extracto cerro y este cargo no aparecio: casi seguro fue una
    preautorizacion que nunca se cobro. Nunca se borra sin preguntar."""
    texto = (
        '👻 <b>Esto no apareció en el extracto</b>\n\n'
        + describir(p)
        + '\n\nEl extracto de esa tarjeta ya cerró y este cargo no está. '
        'Suele pasar con Uber: preautoriza el precio estimado y después '
        'cobra la tarifa real.\n\n¿Lo borro de Firefly?'
    )
    botones = [
        [('🗑 Sí, bórralo', f'd:{p["id"]}:0'), ('✅ No, es real', f'k:{p["id"]}:0')],
    ]
    try:
        msg = telegram.enviar(chat, texto, botones)
        db.marcar_preguntado(cx, p['id'])
        _guardar_mensaje(cx, chat, msg['message_id'], p['id'])
    except telegram.TelegramError as ex:
        print(f'  no pude preguntar fantasma #{p["id"]}: {ex}')
        return False
    else:
        return True


def _preguntar_monto(cx, p, chat):
    """Hubo varios candidatos en el extracto y no se puede saber cual es. No se
    toca el monto solo: eso fue lo que encadeno correcciones equivocadas."""
    texto = (
        '💰 <b>No sé cuál cargo es</b>\n\n'
        + describir(p)
        + '\n\nEn el extracto hay varios cargos del mismo comercio en esos '
        'días y no puedo saber cuál corresponde. Lo dejo como está.\n\n'
        'Si el monto está mal, escríbeme el correcto respondiendo a este '
        'mensaje.'
    )
    botones = [[('✅ Déjalo así', f'k:{p["id"]}:0')]]
    try:
        msg = telegram.enviar(chat, texto, botones)
        db.marcar_preguntado(cx, p['id'])
        _guardar_mensaje(cx, chat, msg['message_id'], p['id'])
    except telegram.TelegramError as ex:
        print(f'  no pude preguntar monto #{p["id"]}: {ex}')
        return False
    else:
        return True


def _guardar_sugerencias(cx, pendiente_id, sug):
    _a(cx).guardar_sugerencias(pendiente_id, sug)


def _leer_sugerencias(cx, pendiente_id):
    return _a(cx).sugerencias(pendiente_id)


# ------------------------------------------------------------------ resolver


def _presupuestos_de(cx, pendiente_id):
    """Los presupuestos entre los que hay que elegir para este movimiento."""
    p = _a(cx).pendiente(pendiente_id)
    pr = _leer_propuesta(cx, pendiente_id)
    cat = (pr['categoria'] if pr else None) or (p['categoria'] if p else None)
    mapa = presupuestos.mapa_categoria()
    info = mapa.get(cat)
    if info and len(info['reparto']) > 1:
        # los que de verdad usaste con esta categoria, mas usados primero
        return [k for k, _ in sorted(info['reparto'].items(), key=lambda x: -x[1])]
    return presupuestos.nombres_activos()


def _preguntar_presupuesto(cx, pendiente_id, chat, pr):
    """Las 9 categorias donde el historico esta dividido son juicios reales:
    'Restaurante' entre Vivir y Antojos. No se asume la mayoria, se pregunta."""
    nombres = _presupuestos_de(cx, pendiente_id)
    botones, fila = [], []
    for i, n in enumerate(nombres):
        fila.append((n, f'b:{pendiente_id}:{i}'))
        if len(fila) == 2:
            botones.append(fila)
            fila = []
    if fila:
        botones.append(fila)
    telegram.enviar(
        chat,
        f'¿A qué presupuesto va?\n\n<b>{pr["categoria"]}</b> la has puesto en varios.',
        botones,
    )


def aplicar_respuesta(
    cx, pendiente_id, categoria=None, descartar=False, presupuesto=None, comercio=None
):
    """Guarda la respuesta, crea la regla, y publica en Firefly."""
    p = _a(cx).pendiente(pendiente_id)
    if p is None:
        return None, 'ese movimiento ya no existe'

    if descartar:
        db.pendiente_actualizar(
            cx, pendiente_id, estado='descartado', pregunta=None, decidido_por='usuario'
        )
        cx.commit()
        return p, 'descartado, no entra a Firefly'

    clave = clasificador.normalizar(p['contraparte'] or p['descripcion'])
    if clave:
        db.regla_guardar(
            cx,
            p['usuario_id'],
            clave,
            categoria=categoria,
            presupuesto=presupuesto,
            cuenta_firefly=comercio,
            origen='usuario',
            direccion='ingreso' if float(p['valor']) > 0 else 'gasto',
        )
    campos = {
        'categoria': categoria,
        'pregunta': None,
        'confianza': 1.0,
        'decidido_por': 'usuario',
    }
    if presupuesto:
        campos['presupuesto'] = presupuesto
    if comercio:
        campos['cuenta_destino'] = comercio
    db.pendiente_actualizar(cx, pendiente_id, **campos)
    cx.commit()

    p = _a(cx).pendiente(pendiente_id)
    aviso = ''
    if p['presupuesto']:
        try:
            r = presupuestos.revienta(p['presupuesto'], p['valor'])
            if r:
                aviso = (
                    '\n\n⚠️ Esto revienta '
                    f'<b>{r["nombre"]}</b>: queda en {r["pct"]:.0f}% '
                    f'({_plata(r["despues"])} de {_plata(r["limite"])}), '
                    f'o sea {_plata(r["exceso"])} de más.'
                )
        except Exception:
            pass

    if p['estado'] in ('nuevo', 'error'):
        idx = publicador.IndiceFirefly(desde=str(p['fecha']), hasta=str(p['fecha']))
        accion, detalle = publicador.publicar_uno(cx, p, idx=idx, dry_run=False)
        return p, f'{categoria} · {accion} {detalle}{aviso}'

    # Ya estaba en Firefly. Antes esto solo actualizaba la base local y Firefly
    # se quedaba con lo viejo: apretabas «Compras» y en Firefly seguia diciendo
    # «Inversion», corregias el comercio y seguia diciendo «Bold». La respuesta
    # se veia aplicada en el chat y no habia cambiado nada donde importa.
    detalle = _corregir_en_firefly(cx, p, categoria, presupuesto, comercio)
    return p, f'{categoria} · {detalle}{aviso}'


def _corregir_en_firefly(cx, p, categoria, presupuesto, comercio):
    """Empuja la correccion al movimiento que ya esta en Firefly."""
    if not p['firefly_id']:
        return 'guardado local (sin id de Firefly)'
    campos = {}
    if categoria:
        campos['category_name'] = categoria
    if presupuesto:
        campos['budget_name'] = presupuesto
    # El nombre del comercio es la cuenta de destino de un gasto y la de origen
    # de un ingreso.
    if comercio:
        campos['destination_name' if float(p['valor']) < 0 else 'source_name'] = (
            comercio
        )
    if not campos:
        return 'guardado'
    try:
        firefly.actualizar_split(str(p['firefly_id']), **campos)
    except firefly.ApiError as ex:
        db.bitacora(
            cx,
            'corregir',
            usuario_id=p['usuario_id'],
            pendiente_id=p['id'],
            firefly_id=p['firefly_id'],
            payload=campos,
            respuesta=str(ex),
            ok=False,
        )
        cx.commit()
        return f'⚠️ no pude actualizar Firefly: {str(ex)[:120]}'
    db.bitacora(
        cx,
        'corregir',
        usuario_id=p['usuario_id'],
        pendiente_id=p['id'],
        firefly_id=p['firefly_id'],
        payload=campos,
        ok=True,
    )
    cx.commit()
    return 'corregido en Firefly (' + ', '.join(sorted(campos)) + ')'


# ---------------------------------------------------------------- comandos


def _version_texto():
    """Que codigo esta corriendo. Sirve para saber si el contenedor quedo con
    una imagen vieja, que es facil que pase y dificil de ver de otra forma."""
    sha = config.get('GIT_SHA', 'desconocido')
    fecha = config.get('BUILD_FECHA', 'desconocida')
    ia_txt = ('Gemini ' + ia.MODELO) if ia.disponible() else 'sin API key'
    lineas = [
        '<b>Versión</b>',
        f'commit <code>{sha[:12]}</code>',
        f'construida {fecha[:19]}',
        '',
        f'IA: {ia_txt}',
        f'texto libre: {"sí" if hasattr(interprete, "interpretar") else "no"}',
        f'asesor: {"sí" if ia.disponible() else "necesita GEMINI_API_KEY"}',
    ]
    return '\n'.join(lineas)


# La descripcion de cada comando vive aqui, junto al nombre, y el texto de
# ayuda se arma de esta tabla. Antes estaban separados y la ayuda solo
# mencionaba tres de los siete: /presupuestos y /version existian sin que
# nadie los supiera. Una prueba verifica que las dos listas coincidan.
DESCRIPCIONES = (
    ('/pendientes', 'lo que falta por clasificar'),
    ('/resumen', 'cómo va la conciliación'),
    ('/sinconfirmar', 'lo que está en Firefly sin confirmar'),
    ('/ultimos', 'los últimos movimientos, tocables para cambiarlos'),
    ('/listo', 'ya están bien, no me preguntes más'),
    ('/productos', 'clasificar lo que compraste en el super'),
    ('/presupuestos', 'cómo van los cinco presupuestos del mes'),
    ('/version', 'qué código está corriendo'),
    ('/ayuda', 'esto'),
)

AYUDA = (
    '<b>Qué hago</b>\n\n'
    'Leo las alertas de Bancolombia de tu correo, saco los movimientos y los '
    'meto a Firefly. Cuando no sé qué categoría poner, te pregunto acá.\n\n'
    'Cada respuesta queda aprendida: un comercio se pregunta <b>una sola vez</b>.'
    '\n\nTambién me puedes escribir de corrido: «fue la comida de la gata en '
    'Tierragro», o preguntarme si me alcanza para algo.\n\n'
    '<b>Comandos</b>\n' + '\n'.join(f'{c} — {d}' for c, d in DESCRIPCIONES)
)


def cmd_resumen(cx, chat):
    filas = db.resumen(cx)
    if not filas:
        telegram.enviar(chat, 'Todo al día. No hay nada abierto. ✅')
        return
    lineas = ['<b>Cómo va</b>', '']
    total_preg = 0
    for f in filas:
        etq = (
            f['estado']
            if f['pregunta'] == 'nada'
            else f'{f["estado"]} · falta {f["pregunta"]}'
        )
        lineas.append(f'{etq}: <b>{f["n"]}</b>')
        if f['pregunta'] != 'nada':
            total_preg += f['n']
    sc = _a(cx).total_sin_confirmar()
    if sc and sc['n']:
        lineas += [
            '',
            f'En Firefly sin confirmar: <b>{sc["n"]}</b> ({_plata(sc["t"] or 0)})',
        ]
    if total_preg:
        lineas += ['', f'Tengo <b>{total_preg}</b> por preguntarte. /pendientes']
    telegram.enviar(chat, '\n'.join(lineas))


def cmd_sinconfirmar(cx, chat):
    filas = _a(cx).sin_confirmar(20)
    if not filas:
        telegram.enviar(chat, 'No hay nada sin confirmar. ✅')
        return
    lineas = ['<b>En Firefly, sin confirmar contra extracto</b>', '']
    for p in filas:
        lineas.append(
            f'{p["fecha"]} {_plata(p["valor"], p["moneda"])} '
            f'— {_escapar(p["contraparte"])[:28]}'
        )
    n_sosp = _a(cx).contar_sospechosos()
    if n_sosp:
        lineas += [
            '',
            f'⚠️ {n_sosp} llevan más de 45 días sin aparecer en '
            f'ningún extracto. Pueden ser preautorizaciones que nunca '
            f'se cobraron.',
        ]
    telegram.enviar(chat, '\n'.join(lineas))


# --------------------------------------------------------------- el bucle


class Toque:
    """Un toque de boton, ya desarmado. `data` viene como 'accion:pid:idx'
    porque callback_data solo aguanta 64 bytes: viaja el INDICE de la opcion,
    no su texto."""

    __slots__ = ('accion', 'chat', 'cq_id', 'cx', 'idx', 'mid', 'pid')

    def __init__(self, cx, cq):
        self.cx = cx
        self.cq_id = cq['id']
        self.chat = cq['message']['chat']['id']
        self.mid = cq['message']['message_id']
        self.accion, pid, idx = (cq.get('data') or '').split(':')
        self.pid, self.idx = int(pid), int(idx)
        # Un indice negativo no lo genera nunca el bot, asi que solo llega
        # forjado -- y era peligroso: los guardas eran de un solo lado
        # (`idx < len(lista)`), asi que `lista[-1]` devolvia el ULTIMO elemento
        # y el bot escribia una categoria, un presupuesto o una etiqueta que
        # nadie eligio, contestando «listo». Se rechaza aqui, una vez, en vez
        # de en los ocho sitios que indexan.
        if self.idx < 0:
            raise ValueError(f'indice negativo: {self.idx}')

    def aviso(self, texto):
        """El globito de confirmacion sobre el boton."""
        telegram.responder_callback(self.cq_id, texto)

    def reemplazar(self, texto):
        """Cambia el mensaje de la pregunta por el resultado, para que el chat
        no quede lleno de preguntas ya resueltas."""
        telegram.editar(self.chat, self.mid, texto)

    def resolver(self, etiqueta, **respuesta):
        """El camino comun: aplicar la respuesta y reescribir el mensaje.

        Deja los botones del menu completo en el resultado. Una categoria no es
        todo lo que lleva un movimiento, y antes, despues de elegirla, no
        quedaba ningun camino para el presupuesto, la etiqueta o el comercio.
        """
        p, detalle = aplicar_respuesta(self.cx, self.pid, **respuesta)
        self.aviso(etiqueta)
        if p is None:
            return
        self.reemplazar(f'✅ <b>{etiqueta}</b>\n{describir(p)}\n<i>{detalle}</i>')
        if p['firefly_id']:
            telegram.enviar(
                self.chat,
                '¿Algo más?',
                [
                    [
                        ('💰 presupuesto', f'pb:{p["firefly_id"]}:0'),
                        ('🏷 etiqueta', f'le:{p["firefly_id"]}:0'),
                    ],
                    [
                        ('🏪 comercio', f'nc:{p["firefly_id"]}:0'),
                        ('📂 otra categoría', f'lc:{p["firefly_id"]}:0'),
                    ],
                ],
            )


def _toque_categoria(t):
    """Eligio una de las categorias sugeridas."""
    sug = _leer_sugerencias(t.cx, t.pid)
    cat = sug[t.idx] if t.idx < len(sug) else None
    if not cat:
        t.aviso('esa opción ya no está')
        return
    t.resolver(cat, categoria=cat)


def _toque_descartar(t):
    p, _ = aplicar_respuesta(t.cx, t.pid, descartar=True)
    t.aviso('descartado')
    if p is not None:
        t.reemplazar(f'🚫 Descartado\n{describir(p)}')


def _toque_borrar_fantasma(t):
    """Confirmo que el movimiento nunca existio: se borra de Firefly."""
    p = _a(t.cx).pendiente(t.pid)
    if p is None:
        t.aviso('ya no existe')
        return
    try:
        if p['firefly_id']:
            firefly.borrar(p['firefly_id'])
        db.pendiente_actualizar(
            t.cx, t.pid, estado='fantasma', pregunta=None, decidido_por='usuario'
        )
        db.bitacora(
            t.cx,
            'borrar',
            usuario_id=p['usuario_id'],
            pendiente_id=t.pid,
            firefly_id=p['firefly_id'],
            respuesta='fantasma confirmado por el usuario',
        )
        t.cx.commit()
    except firefly.ApiError as ex:
        t.aviso('no pude borrarlo')
        telegram.enviar(t.chat, f'No pude borrarlo: {str(ex)[:200]}')
    else:
        t.aviso('borrado')
        t.reemplazar(f'🗑 Borrado de Firefly\n{describir(p)}')


def _toque_dejarlo(t):
    """Es real, o el monto se queda como esta."""
    p = _a(t.cx).pendiente(t.pid)
    db.pendiente_actualizar(
        t.cx, t.pid, estado='confirmado', pregunta=None, decidido_por='usuario'
    )
    t.cx.commit()
    t.aviso('listo, lo dejo')
    if p is not None:
        t.reemplazar(f'✅ Confirmado\n{describir(p)}')


def _toque_aceptar_propuesta(t):
    """Acepto lo que salio de interpretar su texto libre."""
    pr = _leer_propuesta(t.cx, t.pid)
    if pr is None:
        t.aviso('esa propuesta ya no esta')
        return
    if pr['pedir_presupuesto']:
        _preguntar_presupuesto(t.cx, t.pid, t.chat, pr)
        t.aviso('falta el presupuesto')
        return
    t.resolver(
        pr['categoria'],
        categoria=pr['categoria'],
        presupuesto=pr['presupuesto'],
        comercio=pr['comercio'],
    )


def _toque_presupuesto(t):
    pr = _leer_propuesta(t.cx, t.pid)
    nombres = _presupuestos_de(t.cx, t.pid)
    elegido = nombres[t.idx] if t.idx < len(nombres) else None
    if not elegido:
        t.aviso('esa opcion ya no esta')
        return
    t.resolver(
        elegido,
        categoria=(pr['categoria'] if pr else None),
        presupuesto=elegido,
        comercio=(pr['comercio'] if pr else None),
    )


def _toque_producto_en_espera(t):
    """«Era el producto»: aplica el texto que quedo en espera a ESE producto.

    El gemelo de `_toque_mover` para el otro mundo. Los dos mundos viven en el
    mismo chat y el texto no cabe en el callback, asi que queda guardado por
    chat y el boton solo dice a cual de los dos iba.
    """
    txt = _texto_en_espera(t.cx, t.chat)
    if not txt:
        t.aviso('ya no tengo ese mensaje, escribelo de nuevo')
        return
    t.aviso('es el producto, entonces')
    _responder_producto(t.cx, t.chat, t.pid, txt)


def _toque_mover(t):
    """«Era otro»: aplica el texto libre que quedo en espera a ESTE movimiento.

    El texto no viaja en el callback porque ahi solo caben 64 bytes; queda
    guardado por chat y se recupera aqui.
    """
    txt = _texto_en_espera(t.cx, t.chat)
    if not txt:
        t.aviso('ya no tengo ese mensaje, escribelo de nuevo')
        return
    t.aviso('lo muevo')
    t.reemplazar(f'Movido: «{_escapar(txt[:60])}»')
    _responder_con_texto(t.cx, t.chat, t.pid, txt)


def _toque_pedir_texto(t):
    """Antes esto guardaba un marcador '__ESPERANDO_TEXTO__' en las sugerencias,
    que nadie leia y que ademas pisaba las opciones reales: si despues tocabas
    un boton, el indice ya no apuntaba a nada. Lo que de verdad hace falta es
    atar ESTE mensaje al movimiento, para que la respuesta libre caiga en el
    correcto."""
    t.aviso('escribe la categoría')
    eco = telegram.enviar(
        t.chat,
        f'Escribe la categoría para el movimiento '
        f'#{t.pid}, respondiendo a este mensaje:',
    )
    if eco and eco.get('message_id'):
        _guardar_mensaje(t.cx, t.chat, eco['message_id'], t.pid)


# ------------------------------------------- movimientos ya registrados
#
# Todo lo de aqui abajo trabaja contra FIREFLY, no contra la cola. Es la mitad
# que faltaba: el bot solo sabia resolver preguntas abiertas, y una vez cerrada
# la unica forma de corregir algo era entrar a Firefly a mano.

CUANTOS_ULTIMOS = 10

# El salto de linea, como constante: los reemplazos automaticos sobre este
# archivo lo han partido tres veces.
SALTO = chr(10)


def _cambios_de_etiquetas_y_presupuesto(ed, texto):
    """Lo que la orden pide ademas de la categoria.

    El presupuesto se resuelve MIRANDO si el nombre es un presupuesto de
    verdad. «ponla en Gato» y «ponla en Antojos» se escriben igual: el que
    manda es de que lista sale el nombre, no la forma de la frase.
    """
    cambios = {}
    if ed.etiqueta_agregar:
        cambios['etiquetas'] = [ed.etiqueta_agregar]
    if ed.etiqueta_quitar:
        cambios['quitar_etiquetas'] = [ed.etiqueta_quitar]
    try:
        activos = presupuestos.nombres_activos()
    except Exception:
        return cambios
    normal = {_texto_dom.normalizar(b): b for b in activos}
    for palabra in _texto_dom.normalizar(texto).split():
        if palabra in normal:
            cambios['presupuesto'] = normal[palabra]
            break
    else:
        for b in activos:
            if _texto_dom.normalizar(b) in _texto_dom.normalizar(texto):
                cambios['presupuesto'] = b
                break
    return cambios


def _objetivos_del_lote(ed, movs):
    """Los movimientos a los que aplica una orden en plural, o lista vacia.

    «las ultimas 2» toma los dos mas recientes; si ademas dice una categoria
    («las ultimas 2 estan en compras») se filtra por ella, que es lo que hace
    que el numero y el filtro se confirmen entre si.
    """
    if not ed.cuantas and not ed.categoria_filtro:
        return []
    candidatos = movs
    if ed.categoria_filtro:
        objetivo = _texto_dom.normalizar(ed.categoria_filtro)
        filtrados = [
            m for m in movs if _texto_dom.normalizar(m['categoria']) == objetivo
        ]
        # Si el filtro no caza con nada, se ignora y manda el numero: puede que
        # haya dicho la categoria a la que QUIERE moverlas.
        if filtrados:
            candidatos = filtrados
        elif not ed.cuantas:
            # Ni filtro que cace ni numero: NO es un lote. Devolver todo hacia
            # que «ponle la categoria Chuches a la ultima» contestara «entendi
            # que hablas de 10 movimientos» cuando se pidio uno.
            return []
    if ed.cuantas:
        candidatos = candidatos[: ed.cuantas]
    return candidatos if len(candidatos) > 1 else []


def _aplicar_en_lote(cx, chat, texto, ed, objetivos):
    """Los mismos cambios en varios movimientos, reportando uno por uno."""
    cambios = {}
    try:
        cat = interprete.catalogo(cx, _usuario_de(cx, chat))
        hallazgos = interprete.buscar_categoria(texto, cat['categorias'])
        if hallazgos and not ed.categoria_filtro:
            cambios['categoria'] = hallazgos[0][1]
    except Exception:
        pass
    if ed.comercio:
        cambios['comercio'] = ed.comercio
    cambios.update(_cambios_de_etiquetas_y_presupuesto(ed, texto))

    if not cambios:
        # Si nombro una categoria que no existe, se dice: callarselo deja al
        # usuario creyendo que el problema fue otro.
        inventada = ''
        if ed.categoria_filtro:
            cats = movimientos.categorias()
            objetivo = _texto_dom.normalizar(ed.categoria_filtro)
            if not any(_texto_dom.normalizar(c) == objetivo for c in cats):
                inventada = (
                    f'No tengo una categoría «{_escapar(ed.categoria_filtro)}». '
                )
        telegram.enviar(
            chat,
            f'{inventada}Entendí que hablas de {len(objetivos)} movimientos, '
            f'pero no de qué cambiarles. Tócalos uno por uno:',
        )
        cmd_ultimos(cx, chat)
        return

    ids = [str(m['id']) for m in objetivos]
    resultados = movimientos.editar_varios(ids, **cambios)
    que = ', '.join(f'{k}: {v}' for k, v in cambios.items())
    lineas = [f'✅ <i>{que}</i>  en {len(objetivos)} movimientos:', '']
    for r in resultados:
        if r.get('error'):
            lineas.append(f'⚠️ #{r["id"]}: {r["error"]}')
        else:
            lineas.append(movimientos.describir_html(r['movimiento']))
    db.bitacora(
        cx,
        'editar_lote',
        usuario_id=_usuario_de(cx, chat),
        payload={'ids': ids, 'cambios': cambios},
        ok=all(not r.get('error') for r in resultados),
    )
    cx.commit()
    telegram.enviar(chat, SALTO.join(lineas))


def _usuario_de(cx, chat):
    u = _a(cx).usuario_por_chat(chat)
    return u['id'] if u else 1


def _para_puntuar(movs):
    """Los movimientos con la forma que espera `intencion.a_que_movimiento`.

    Ese modulo busca el nombre del comercio en `contraparte`; en Firefly esta en
    `destino` (o en la descripcion si no hay cuenta de destino).
    """
    return [
        {
            'id': int(m['id']),
            'valor': m['valor'],
            'contraparte': m['destino'] or m['descripcion'],
            'categoria': m['categoria'],
        }
        for m in movs
        if m['id']
    ]


def _categorias_para(cx, valor):
    """Las categorias a ofrecer, filtradas por direccion.

    No se guardan en ninguna tabla: se recalculan igual en el momento de
    mostrarlas y en el de recibir el toque, asi que el indice del boton sigue
    apuntando a lo mismo. Guardarlas exigiria una fila con clave ajena a
    `pendientes`, y aqui el objetivo es un id de Firefly.
    """
    direccion = 'ingreso' if valor > 0 else 'gasto'
    try:
        usadas = [
            r['categoria']
            for r in _a(cx).categorias_usadas(direccion)
            if r['categoria']
        ]
    except Exception:
        usadas = []
    # Sin reglas aprendidas —instalacion nueva, o una direccion que nunca se ha
    # usado— la lista queda vacia y el menu sale SIN ningun boton. Se cae a las
    # categorias que existan en Firefly, igual que las preguntas normales.
    if len(usadas) < 4:
        # Pero sin las que la historia dice que son de la OTRA direccion: el
        # relleno es alfabetico, asi que a un gasto le ofrecia «Abono» de
        # primero. Si nunca se ha usado en ningun lado no se puede saber, y
        # entra: inventar una regla ahi seria peor.
        try:
            otra = {
                r['categoria']
                for r in _a(cx).categorias_usadas(
                    'gasto' if direccion == 'ingreso' else 'ingreso'
                )
                if r['categoria']
            }
        except Exception:
            otra = set()
        propias = set(usadas)
        for c in _categorias_firefly():
            if c not in usadas and (c not in otra or c in propias):
                usadas.append(c)
    return usadas[:6]


def _botonera(pares, por_fila=2):
    botones, fila = [], []
    for par in pares:
        fila.append(par)
        if len(fila) == por_fila:
            botones.append(fila)
            fila = []
    if fila:
        botones.append(fila)
    return botones


def cmd_ultimos(cx, chat, texto=''):
    """Los ultimos movimientos, cada uno tocable para cambiarlo.

    /ultimos            los 10 mas recientes
    /ultimos tierragro  los que coincidan
    """
    partes = (texto or '').split()
    consulta = ' '.join(partes[1:]) if len(partes) > 1 else None
    try:
        movs = (
            movimientos.buscar(consulta, limite=CUANTOS_ULTIMOS)
            if consulta
            else movimientos.ultimos(limite=CUANTOS_ULTIMOS)
        )
    except Exception as ex:
        telegram.enviar(chat, f'No pude leer Firefly: {str(ex)[:150]}')
        return
    if not movs:
        cola = f' de «{consulta}»' if consulta else ''
        telegram.enviar(chat, f'No encontré movimientos{cola}.')
        return

    encabezado = f'<b>Movimientos{" · " + consulta if consulta else ""}</b>'
    lineas = [encabezado, '']
    lineas += [movimientos.describir_html(m) for m in movs]
    lineas.append('\nToca uno para cambiarlo.')
    telegram.enviar(
        chat,
        '\n'.join(lineas),
        _botonera(
            [
                (
                    f'{_plata(m["valor"])} {(m["destino"] or m["descripcion"])[:12]}',
                    f'mv:{m["id"]}:0',
                )
                for m in movs
            ]
        ),
    )


def cmd_listo(cx, chat, _texto=''):
    """«Ya estan bien, no me preguntes mas»: cierra todas las preguntas.

    Es lo que faltaba. El bot vuelve a preguntar cada 24h por lo que siga
    abierto, y no habia forma de decirle que ya estaba resuelto salvo contestar
    una por una. Con movimientos ya correctos en Firefly, eso es puro ruido.
    """
    alm = _a(cx)
    abiertas = alm.pendientes_abiertos_de_chat(chat)
    if not abiertas:
        telegram.enviar(chat, 'No tengo nada abierto. ✅')
        return

    # lo que Firefly ya sabe de cada uno, para adoptar su categoria
    try:
        en_firefly = {
            str(m['id']): m for m in movimientos.ultimos(limite=500, dias=120)
        }
    except Exception:
        en_firefly = {}

    adoptadas, sin_publicar = 0, 0
    for p in abiertas:
        ff = en_firefly.get(str(p['firefly_id'] or ''))
        if ff and ff['categoria'] and not p['categoria']:
            db.pendiente_actualizar(
                cx, p['id'], categoria=ff['categoria'], confianza=1.0
            )
            adoptadas += 1
        if p['estado'] in ('nuevo', 'error'):
            # nunca llego a Firefly: cerrar la pregunta sin mas lo dejaria en
            # el limbo, asi que se descarta explicitamente
            db.pendiente_actualizar(cx, p['id'], estado='descartado')
            sin_publicar += 1
    cerradas = alm.cerrar_preguntas_del_chat(chat, 'usuario_dijo_listo')

    lineas = [
        f'Listo. Cerré <b>{cerradas}</b> preguntas y no te vuelvo a preguntar '
        f'por ellas.'
    ]
    if adoptadas:
        lineas.append(f'De {adoptadas} tomé la categoría que ya tenían en Firefly.')
    if sin_publicar:
        lineas.append(
            f'{sin_publicar} no habían llegado a Firefly, así que las descarté.'
        )
    lineas.append('\nSi alguna quedó mal: /ultimos y la cambias.')
    telegram.enviar(chat, '\n'.join(lineas))


# Cuantas categorias por pagina en la lista completa. El menu ofrecia OCHO de
# setenta y una, y para las otras 63 no habia camino salvo escribir el nombre
# exacto.
POR_PAGINA = 10


def _menu_movimiento(cx, chat, tx_id, mensaje_id=None):
    """Todo lo que se le puede cambiar a un movimiento, en un solo sitio.

    Antes esto solo ofrecia categorias —y solo seis— y no habia forma de tocar
    el presupuesto, las etiquetas ni el nombre del comercio sin entrar a
    Firefly. El correo llega como «MERCADO PAGO*...» y el comercio de verdad
    hay que poderlo escribir.
    """
    m = movimientos.uno(str(tx_id))
    if m is None:
        telegram.enviar(chat, 'Ese movimiento ya no existe en Firefly.')
        return
    sug = _categorias_para(cx, m['valor'])
    botones = _botonera([(c, f'mc:{tx_id}:{i}') for i, c in enumerate(sug)])
    botones.append(
        [
            ('📂 categorías', f'lc:{tx_id}:0'),
            ('💰 presupuesto', f'pb:{tx_id}:0'),
        ]
    )
    botones.append(
        [
            ('🏷 etiqueta', f'le:{tx_id}:0'),
            ('🏪 comercio', f'nc:{tx_id}:0'),
        ]
    )
    ultima = [('✏️ escribirlo', f'mt:{tx_id}:0')]
    if movimientos.SIN_CONFIRMAR in m['etiquetas']:
        ultima.append(('✅ está bien', f'mk:{tx_id}:0'))
    botones.append(ultima)
    botones.append([('🗑 borrar', f'mx:{tx_id}:0')])

    texto = _ficha(m)
    if mensaje_id:
        telegram.editar(chat, mensaje_id, texto)
        telegram.enviar(chat, '¿Qué le cambio?', botones)
    else:
        telegram.enviar(chat, texto, botones)


def _ficha(m):
    """La tarjeta del movimiento con TODO lo que tiene puesto.

    Muestra presupuesto y etiquetas a proposito: sin verlos no habia forma de
    saber que faltaba.
    """
    lineas = [f'<b>{_plata(m["valor"], m["moneda"])}</b>  {m["fecha"]}']
    lineas.append(
        f'{_escapar(m["origen"])} → <b>{_escapar(m["destino"] or m["descripcion"])}</b>'
    )
    lineas.append(f'categoría: <b>{m["categoria"] or "—"}</b>')
    lineas.append(f'presupuesto: <b>{m["presupuesto"] or "—"}</b>')
    propias = [
        e
        for e in m['etiquetas']
        if e.lower() not in movimientos.ETIQUETAS_DE_MAQUINA
        and not e.lower().startswith('recon-')
    ]
    lineas.append(f'etiquetas: <b>{", ".join(propias) if propias else "—"}</b>')
    if movimientos.SIN_CONFIRMAR in m['etiquetas']:
        lineas.append('<i>sin confirmar contra extracto</i>')
    return '\n'.join(lineas)


def _pagina_de(items, pagina, plantilla_dato, volver_a):
    """Una botonera paginada. `plantilla_dato` recibe el indice ABSOLUTO."""
    total = max(1, (len(items) + POR_PAGINA - 1) // POR_PAGINA)
    pagina = max(0, min(pagina, total - 1))
    ini = pagina * POR_PAGINA
    trozo = list(enumerate(items))[ini : ini + POR_PAGINA]
    botones = _botonera([(c, plantilla_dato(i)) for i, c in trozo])
    nav = []
    if pagina > 0:
        nav.append(('◀', volver_a(pagina - 1)))
    nav.append((f'{pagina + 1}/{total}', volver_a(pagina)))
    if pagina < total - 1:
        nav.append(('▶', volver_a(pagina + 1)))
    botones.append(nav)
    return botones, pagina, total


def _toque_lista_categorias(t):
    """TODAS las categorias, paginadas. El indice del boton es absoluto sobre
    `movimientos.categorias()`.

    Va por `sc:` y no por `mc:` a proposito. Compartian prefijo y son dos
    listas distintas: `mc:` indexa las seis de `_categorias_para` (filtradas
    por direccion) y esta indexa las setenta y una. Resultado: de los botones
    de esta pantalla solo servian los seis primeros y los demas contestaban
    «esa opcion ya no esta». No se podia poner Mercado, Gato ni Ropa desde
    aqui.
    """
    todas = movimientos.categorias()
    botones, pagina, total = _pagina_de(
        todas, t.idx, lambda i: f'sc:{t.pid}:{i}', lambda p: f'lc:{t.pid}:{p}'
    )
    botones.append([('« volver', f'mv:{t.pid}:0')])
    t.aviso('')
    t.reemplazar(f'<b>Categorías</b>  ({len(todas)} en total)')
    telegram.enviar(t.chat, f'Página {pagina + 1} de {total}:', botones)


def _toque_menu_presupuesto(t):
    """Los presupuestos activos, y la opcion de fijarlo para la CATEGORIA."""
    m = movimientos.uno(str(t.pid))
    if m is None:
        t.aviso('ese movimiento ya no existe')
        return
    nombres = presupuestos.nombres_activos()
    botones = _botonera([(b, f'sb:{t.pid}:{i}') for i, b in enumerate(nombres)])
    if m['categoria']:
        botones.append(
            [
                (f'📌 {m["categoria"]} siempre va aquí', f'bp:{t.pid}:0'),
            ]
        )
    botones.append([('« volver', f'mv:{t.pid}:0')])
    t.aviso('')
    telegram.enviar(
        t.chat,
        f'<b>Presupuesto</b> para {_plata(m["valor"], m["moneda"])} en '
        f'{_escapar((m["destino"] or m["descripcion"])[:24])}\n'
        f'ahora: <b>{m["presupuesto"] or "—"}</b>',
        botones,
    )


def _toque_elegir_presupuesto(t):
    nombres = presupuestos.nombres_activos()
    if t.idx >= len(nombres):
        t.aviso('esa opción ya no está')
        return
    _aplicar_edicion(
        t.cx, t.chat, str(t.pid), {'presupuesto': nombres[t.idx]}, aviso=t.aviso
    )


def _toque_fijar_presupuesto_de_categoria(t):
    """«Compras siempre va en Antojos»: la decision queda guardada.

    El presupuesto se deducia del historico y solo cuando la categoria apuntaba
    al mismo el 80% de las veces. Las repartidas de verdad —Compras 7 a 2,
    Regalos 4 a 4— se quedaban SIN presupuesto para siempre y no habia forma de
    zanjarlo desde el chat.
    """
    m = movimientos.uno(str(t.pid))
    if m is None or not m['categoria'] or not m['presupuesto']:
        t.aviso('primero ponle categoría y presupuesto')
        return
    _a(t.cx).fijar_presupuesto_de_categoria(m['categoria'], m['presupuesto'])
    t.aviso('anotado')
    telegram.enviar(
        t.chat,
        f'📌 De ahora en adelante <b>{m["categoria"]}</b> va a '
        f'<b>{m["presupuesto"]}</b>, sin preguntar.\n'
        f'<i>Esto gana sobre lo que diga el histórico. Para cambiarlo, '
        f'vuelve a fijarlo con otro presupuesto.</i>',
    )


def _toque_menu_etiquetas(t):
    """Las etiquetas que de verdad usas, paginadas, mas la de escribirla."""
    todas = movimientos.etiquetas_mas_usadas()
    botones, pagina, total = _pagina_de(
        todas, t.idx, lambda i: f'se:{t.pid}:{i}', lambda p: f'le:{t.pid}:{p}'
    )
    botones.append([('✏️ otra etiqueta', f'ne:{t.pid}:0')])
    botones.append([('« volver', f'mv:{t.pid}:0')])
    t.aviso('')
    telegram.enviar(
        t.chat, f'<b>Etiquetas</b> — página {pagina + 1} de {total}', botones
    )


def _toque_elegir_etiqueta(t):
    todas = movimientos.etiquetas_mas_usadas()
    if t.idx >= len(todas):
        t.aviso('esa opción ya no está')
        return
    _aplicar_edicion(
        t.cx, t.chat, str(t.pid), {'etiquetas': [todas[t.idx]]}, aviso=t.aviso
    )


def _toque_pedir_etiqueta(t):
    t.aviso('escríbela')
    eco = telegram.enviar(
        t.chat,
        'Escribe la etiqueta, respondiendo a este mensaje.\n'
        '<i>Se agrega a las que ya tiene, no las reemplaza.</i>',
    )
    _a(t.cx).abrir_edicion(
        t.chat, str(t.pid), (eco or {}).get('message_id'), campo='etiquetas'
    )


def _toque_pedir_comercio(t):
    """El correo llega como «MERCADO PAGO*...» y el comercio real hay que
    poderlo escribir."""
    m = movimientos.uno(str(t.pid))
    t.aviso('escríbelo')
    ahora = (m['destino'] or m['descripcion']) if m else '?'
    eco = telegram.enviar(
        t.chat,
        f'¿Cómo se llama el comercio? Responde a este mensaje.\n'
        f'ahora dice <b>{ahora}</b>\n'
        f'<i>El banco manda el nombre de la pasarela, no el del negocio.</i>',
    )
    _a(t.cx).abrir_edicion(
        t.chat, str(t.pid), (eco or {}).get('message_id'), campo='comercio'
    )


def _aplicar_edicion(cx, chat, tx_id, cambios, aviso=None):
    """Aplica los cambios en Firefly y lo reporta con el resultado releido."""
    try:
        m = movimientos.editar(str(tx_id), **cambios)
    except Exception as ex:
        if aviso:
            aviso('no pude')
        telegram.enviar(chat, f'No pude cambiarlo: {str(ex)[:180]}')
        return None
    db.bitacora(
        cx,
        'editar',
        usuario_id=_usuario_de(cx, chat),
        firefly_id=str(tx_id),
        payload=cambios,
        ok=True,
    )
    cx.commit()
    if aviso:
        aviso('listo')
    que = ', '.join(f'{k}: {v}' for k, v in cambios.items())
    telegram.enviar(
        chat,
        f'✅ <i>{que}</i>\n<b>{movimientos.describir_html(m)}</b>',
        [
            [
                ('💰 presupuesto', f'pb:{tx_id}:0'),
                ('🏷 etiqueta', f'le:{tx_id}:0'),
            ],
            [
                ('🏪 comercio', f'nc:{tx_id}:0'),
                ('⚙️ todo', f'mv:{tx_id}:0'),
            ],
        ],
    )
    return m


# ------------------------------------------------------ toques de movimiento


def _toque_ver_movimiento(t):
    t.aviso('')
    _menu_movimiento(t.cx, t.chat, t.pid, mensaje_id=t.mid)


def _toque_categoria_movimiento(t):
    m = movimientos.uno(str(t.pid))
    if m is None:
        t.aviso('ese movimiento ya no existe')
        return
    cats = _categorias_para(t.cx, m['valor'])
    cat = cats[t.idx] if t.idx < len(cats) else None
    if not cat:
        t.aviso('esa opción ya no está')
        return
    _aplicar_edicion(t.cx, t.chat, str(t.pid), {'categoria': cat}, aviso=t.aviso)


def _toque_categoria_de_la_lista(t):
    """El de la pantalla paginada: indexa TODAS las categorias."""
    if movimientos.uno(str(t.pid)) is None:
        t.aviso('ese movimiento ya no existe')
        return
    todas = movimientos.categorias()
    if t.idx >= len(todas):
        t.aviso('esa opción ya no está')
        return
    _aplicar_edicion(
        t.cx, t.chat, str(t.pid), {'categoria': todas[t.idx]}, aviso=t.aviso
    )


def _toque_confirmar_movimiento(t):
    """Le quita «sin-confirmar»: el movimiento queda cerrado sin esperar el
    extracto."""
    try:
        movimientos.confirmar(str(t.pid))
    except Exception as ex:
        t.aviso('no pude')
        telegram.enviar(t.chat, f'No pude confirmarlo: {str(ex)[:150]}')
        return
    t.aviso('confirmado')
    t.reemplazar('✅ Confirmado. Le quité la etiqueta «sin-confirmar».')


def _toque_borrar_movimiento(t):
    """Pide confirmacion: borrar de Firefly no se puede deshacer."""
    m = movimientos.uno(str(t.pid))
    if m is None:
        t.aviso('ya no existe')
        return
    t.aviso('¿seguro?')
    telegram.enviar(
        t.chat,
        f'Vas a BORRAR de Firefly:\n<b>{movimientos.describir_html(m)}</b>\n\n'
        f'No se puede deshacer.',
        [[('🗑 sí, bórralo', f'mB:{t.pid}:0'), ('cancelar', f'mv:{t.pid}:0')]],
    )


def _toque_borrar_confirmado(t):
    # Que siga existiendo. Un doble toque en el boton mandaba dos DELETE y
    # decia «Borrado de Firefly» las dos veces; el segundo es un 404 contra
    # Firefly de verdad.
    if movimientos.uno(str(t.pid)) is None:
        t.aviso('ese ya no existe')
        t.reemplazar('🗑 Ese movimiento ya no está en Firefly.')
        return
    try:
        movimientos.borrar(str(t.pid))
    except Exception as ex:
        t.aviso('no pude')
        telegram.enviar(t.chat, f'No pude borrarlo: {str(ex)[:150]}')
        return
    db.bitacora(
        t.cx,
        'borrar',
        usuario_id=_usuario_de(t.cx, t.chat),
        firefly_id=str(t.pid),
        respuesta='borrado desde el bot',
        ok=True,
    )
    t.cx.commit()
    t.aviso('borrado')
    t.reemplazar('🗑 Borrado de Firefly.')


def _toque_texto_movimiento(t):
    """Pide por escrito que cambiarle. La respuesta llega por texto libre y se
    ata a este movimiento con `edicion_en_curso`."""
    t.aviso('escríbelo')
    eco = telegram.enviar(
        t.chat,
        '¿Qué le cambio? Responde a este mensaje.\n'
        '<i>Por ejemplo: «es Mercado», «el comercio es Etre».</i>',
    )
    _a(t.cx).abrir_edicion(t.chat, str(t.pid), (eco or {}).get('message_id'))


# --------------------------------------------------------- editar por texto


def _editar_por_texto(cx, chat, texto, tx_id=None, campo=None):
    """«cambia la ultima a Mercado», «la de tierragro ponla en Gato».

    Encuentra el movimiento por lo que dice el texto —el comercio, el monto, o
    «la ultima»— y le aplica el cambio. Si no puede identificarlo, muestra los
    ultimos con botones en vez de rendirse.
    """
    ed = intencion.es_edicion(texto)
    try:
        movs = movimientos.ultimos(limite=CUANTOS_ULTIMOS)
    except Exception as ex:
        telegram.enviar(chat, f'No pude leer Firefly: {str(ex)[:150]}')
        return
    if not movs and tx_id is None:
        telegram.enviar(chat, 'No veo movimientos recientes que cambiar.')
        return

    objetivo = None
    if tx_id is not None:
        objetivo = movimientos.uno(str(tx_id))
        # Se pidio UN campo concreto: el texto es su valor, tal cual. Sin esto,
        # escribir «Ropa» despues de tocar «etiqueta» se interpretaba como una
        # categoria y la etiqueta nunca se ponia.
        if objetivo is not None and campo:
            valor = texto.strip()
            cambio = {'etiquetas': [valor]} if campo == 'etiquetas' else {campo: valor}
            _a(cx).cerrar_edicion(chat)
            _aplicar_edicion(cx, chat, str(tx_id), cambio)
            return
    # VARIOS a la vez: «las ultimas 2 estan en compras, agregales la etiqueta
    # Ropa». Todo este camino resolvia un solo objetivo, y una orden en plural
    # acababa aplicada a uno o a ninguno.
    objetivos = _objetivos_del_lote(ed, movs) if tx_id is None else []
    if objetivos:
        _aplicar_en_lote(cx, chat, texto, ed, objetivos)
        return

    if tx_id is None and ed.la_ultima:
        objetivo = movs[0]
    elif tx_id is None:
        g = intencion.hay_un_ganador(
            intencion.a_que_movimiento(texto, _para_puntuar(movs))
        )
        if g:
            objetivo = next((m for m in movs if int(m['id']) == g.id), None)

    if objetivo is None:
        telegram.enviar(chat, 'No supe a cuál te refieres. Toca el que quieras:')
        cmd_ultimos(cx, chat)
        return

    if ed.borrar:
        telegram.enviar(
            chat,
            f'Vas a BORRAR de Firefly:\n'
            f'<b>{movimientos.describir_html(objetivo)}</b>\n\nNo se puede deshacer.',
            [
                [
                    ('🗑 sí, bórralo', f'mB:{objetivo["id"]}:0'),
                    ('cancelar', f'mv:{objetivo["id"]}:0'),
                ]
            ],
        )
        return

    cambios = {}
    try:
        cat = interprete.catalogo(cx, _usuario_de(cx, chat))
        hallazgos = interprete.buscar_categoria(texto, cat['categorias'])
        if hallazgos:
            cambios['categoria'] = hallazgos[0][1]
    except Exception:
        pass
    if ed.comercio:
        cambios['comercio'] = ed.comercio
    if ed.monto is not None:
        cambios['monto'] = ed.monto
    cambios.update(_cambios_de_etiquetas_y_presupuesto(ed, texto))

    if not cambios:
        telegram.enviar(
            chat,
            f'Entendí que hablas de:\n'
            f'<b>{movimientos.describir_html(objetivo)}</b>\n\n'
            f'Pero no entendí qué cambiarle.',
        )
        _menu_movimiento(cx, chat, objetivo['id'])
        return

    _a(cx).cerrar_edicion(chat)
    _aplicar_edicion(cx, chat, str(objetivo['id']), cambios)


# La letra que viaja en callback_data. Es de un caracter porque el limite son
# 64 bytes contando el id del pendiente y el indice de la opcion.

# ------------------------------------------------ productos de supermercado
# Preguntar por PRODUCTO y no por linea de factura: 1.925 lineas se reducen a
# 683 productos distintos, y la categoria vive en el catalogo, no copiada en la
# linea. O sea que contestar una vez reescribe TODAS las compras pasadas de ese
# producto — las 30 veces que compraste tortillas desde 2025 — y no solo las
# que vengan despues.
#
# La cola va ordenada por plata, no por fecha de llegada: preguntar en orden de
# aparicion gasta los turnos en el producto que compraste una sola vez.

# Su propio cupo, aparte de MAX_PREGUNTAS. Sin esto una semana clasificando
# mercado tapa las alertas del banco, que son las que hay que mirar el mismo
# dia.
MAX_PRODUCTOS = 3


def _columna(fila, nombre, si_no_esta=None):
    """El valor de esa columna si la consulta la trajo.

    Las filas del catalogo vienen de dos consultas: una hace el join con
    `factura_lineas` y trae `gasto` y `compras`, y la otra -- la de buscar un
    producto por su id -- no. Indexar a ciegas revienta con «No item with that
    key» y deja el toque sin contestar.
    """
    try:
        valor = fila[nombre]
    except (IndexError, KeyError):
        return si_no_esta
    return si_no_esta if valor is None else valor


def _texto_producto(p):
    # Sin signo: en un movimiento el + o el - dice si entra o sale plata, pero
    # una compra de supermercado siempre sale, y "+$6.059" se lee al reves.
    plata = _dinero.formatear(_columna(p, 'gasto', 0), 'COP')
    veces = _columna(p, 'compras', 0)
    cuantas = 'una vez' if veces == 1 else f'{veces} veces'
    return (
        '🛒 <b>¿Qué es esto?</b>\n\n'
        f'<b>{_escapar(p["descripcion"] or p["codigo"])}</b>\n'
        f'comprado {cuantas} · {plata}\n\n'
        '<i>El nombre viene cortado por el almacén. '
        'Lo que respondas queda para siempre.</i>'
        + SALTO
        + '<i>O respóndeme a este mensaje y ya: «es el costo del domicilio».</i>'
    )


def preguntar_productos(cx, limite=MAX_PRODUCTOS):
    """Manda las preguntas de catalogo pendientes. Devuelve cuantas mando."""
    chat = config.get('TELEGRAM_CHAT_ID_JUAN')
    if not chat:
        return 0
    filas = _a(cx).catalogo_por_preguntar(limite)
    mandadas = 0
    for p in filas:
        try:
            _preguntar_producto_uno(cx, chat, p)
            mandadas += 1
        except telegram.TelegramError as ex:
            print(f'  no pude preguntar por el producto #{p["id"]}: {ex}')
    return mandadas


def _preguntar_producto_uno(cx, chat, p, encabezado=''):
    """La pregunta de UN producto, con los grupos en botones.

    Aparte para poder volver a preguntar cuando el modelo propone algo que no
    sirve -- un grupo que no existe, o con la confianza en el piso -- en vez de
    guardar cualquier cosa o quedarse callado.
    """
    botones = _botonera(
        [(g, f'fg:{p["id"]}:{i}') for i, g in enumerate(catalogo.GRUPOS)]
    )
    botones.append([('🤷 Saltar', f'fx:{p["id"]}:0')])
    texto = _texto_producto(p)
    if encabezado:
        texto = f'{encabezado}{SALTO}{SALTO}{texto}'
    msg = telegram.enviar(chat, texto, botones)
    db.catalogo_marcar_preguntado_id(cx, p['id'])
    # Se ata el mensaje al producto: asi contestar por escrito llega a donde
    # debe. Sin esto la unica via eran los botones, y una respuesta escrita
    # acababa en el camino de las transacciones.
    if msg and msg.get('message_id'):
        _a(cx).guardar_mensaje_producto(str(chat), msg['message_id'], p['id'])
    cx.commit()
    return msg


def _toque_producto_grupo(t):
    """Eligio el grupo. Si el grupo tiene una sola categoria, se cierra ya."""
    p = db.catalogo_por_id(t.cx, t.pid)
    if p is None:
        t.aviso('ese producto ya no está')
        return
    if t.idx >= len(catalogo.GRUPOS):
        t.aviso('esa opción ya no está')
        return
    grupo = catalogo.GRUPOS[t.idx]
    cats = catalogo.CATEGORIAS.get(grupo, ())
    if len(cats) <= 1:
        _guardar_producto(t, p, grupo, cats[0] if cats else grupo)
        return
    # El indice de grupo viaja junto con el de categoria porque el callback de
    # Telegram no aguanta mas de 64 bytes y no hay donde guardar el paso
    # intermedio sin inventar otra tabla.
    botones = _botonera(
        [(c, f'fc:{p["id"]}:{t.idx * 100 + i}') for i, c in enumerate(cats)]
    )
    botones.append([('⬅️ Otro grupo', f'fv:{p["id"]}:0')])
    telegram.editar(
        t.chat,
        t.mid,
        f'🛒 <b>{_escapar(p["descripcion"] or p["codigo"])}</b>\n'
        f'Grupo: <b>{grupo}</b>\n\n¿Qué tipo?',
        botones,
    )
    t.aviso(grupo)


def _toque_producto_categoria(t):
    p = db.catalogo_por_id(t.cx, t.pid)
    if p is None:
        t.aviso('ese producto ya no está')
        return
    gidx, cidx = divmod(t.idx, 100)
    if gidx >= len(catalogo.GRUPOS):
        t.aviso('esa opción ya no está')
        return
    grupo = catalogo.GRUPOS[gidx]
    cats = catalogo.CATEGORIAS.get(grupo, ())
    if cidx >= len(cats):
        t.aviso('esa opción ya no está')
        return
    _guardar_producto(t, p, grupo, cats[cidx])


def _toque_producto_volver(t):
    p = db.catalogo_por_id(t.cx, t.pid)
    if p is None:
        t.aviso('ese producto ya no está')
        return
    botones = _botonera(
        [(g, f'fg:{p["id"]}:{i}') for i, g in enumerate(catalogo.GRUPOS)]
    )
    botones.append([('🤷 Saltar', f'fx:{p["id"]}:0')])
    telegram.editar(t.chat, t.mid, _texto_producto_simple(p), botones)


def _texto_producto_simple(p):
    return (
        f'🛒 <b>¿Qué es esto?</b>\n\n<b>{_escapar(p["descripcion"] or p["codigo"])}</b>'
    )


def _toque_producto_saltar(t):
    """No se sabe. Queda marcado como preguntado y vuelve a la cola en 3 dias."""
    db.catalogo_marcar_preguntado_id(t.cx, t.pid)
    t.aviso('lo dejo para después')
    t.reemplazar('🤷 Lo dejo para después.')


def _guardar_producto(t, p, grupo, categoria):
    tipo = catalogo.tipo_de(grupo)
    db.catalogo_responder_id(t.cx, t.pid, tipo, grupo, categoria)
    n = p['veces'] or 0
    arrastre = f'\n<i>Se aplicó a las {n} compras anteriores.</i>' if n > 1 else ''
    t.aviso(f'{grupo} · {categoria}')
    t.reemplazar(
        f'✅ <b>{_escapar(p["descripcion"] or p["codigo"])}</b>\n'
        f'{tipo} · {grupo} · {categoria}{arrastre}'
    )


def cmd_productos(cx, chat, _texto=''):
    """Los productos que faltan por clasificar, con sus botones."""
    filas = _a(cx).catalogo_sin_clasificar(20)
    if not filas:
        telegram.enviar(chat, '✅ No hay productos sin clasificar.')
        return
    total = sum(f['gasto'] or 0 for f in filas)
    telegram.enviar(
        chat,
        f'🛒 <b>{len(filas)} productos sin clasificar</b> '
        f'({_dinero.formatear(total, "COP")})\n'
        'Te mando los más caros primero.',
    )
    # Manda las filas que ACABO de contar. Antes contaba con esta consulta
    # -- que ignora `preguntado_en` a proposito, porque el comando lo pide el
    # usuario -- y mandaba con `preguntar_productos`, que filtra los preguntados
    # hace menos de tres dias. Pedirlo dos veces anunciaba «2 productos sin
    # clasificar» y no mandaba ninguno: el bot se veia muerto, y si se perdian
    # los mensajes quedaba mudo tres dias.
    for f in filas[:MAX_PRODUCTOS]:
        try:
            _preguntar_producto_uno(cx, chat, f)
        except telegram.TelegramError as ex:
            print(f'  no pude preguntar por el producto #{f["id"]}: {ex}')


# --------------------------------------------------- entender con el modelo
#
# Este es el camino principal desde que existe. Antes el ruteo lo hacian
# expresiones regulares y a Gemini solo se le preguntaba «¿que categoria?»
# sobre UN movimiento, con el comercio restringido a un enum de los que ya
# existian —asi que ni podia proponer un nombre nuevo—. Todo lo demas —a cual
# te referias, si era pregunta o respuesta, las etiquetas, el plural, los
# presupuestos— salia de patrones que habia que ir parchando uno por uno.
#
# El modelo no era el cuello de botella: era que no se le estaba preguntando.

# Por debajo de esto no se aplica nada solo: se muestra lo que se entendio y se
# pide un toque. El modelo devuelve 0.9+ cuando la orden es inequivoca.
CONFIANZA_PARA_APLICAR = 0.75

# Cuantos movimientos se pueden tocar de un golpe sin confirmar. «las ultimas
# 2» o «las 3 ultimas» son ordenes precisas y pasan directo; «cambia todas a
# mercado» reescribia los diez, el ingreso incluido, sin preguntar nada.
MAXIMO_SIN_CONFIRMAR = 4


def _plan_de_ia(cx, chat, texto, abiertas):
    """Lo que el modelo entiende del mensaje, o None si no se puede usar."""
    if not ia.disponible():
        return None
    try:
        movs = movimientos.ultimos(limite=25)
        # Tambien los PRODUCTOS pendientes. Sin esto, contestar «es el costo de
        # domicilio» a la pregunta de «fletes gravado» caia en el camino de
        # editar transacciones y el bot ofrecia cambiarle la categoria a una
        # compra del banco: dos cosas distintas en el mismo chat.
        prods = _a(cx).productos_preguntados(chat)
        return ia.entender_orden(
            texto,
            movs,
            movimientos.categorias(),
            presupuestos.nombres_activos(),
            movimientos.etiquetas_mas_usadas(),
            abiertas=[dict(p) for p in abiertas],
            historial=HISTORIAL.get(str(chat)),
            productos=[dict(p) for p in prods],
            grupos_producto={g: list(catalogo.CATEGORIAS[g]) for g in catalogo.GRUPOS},
            # Cuales se acabaron de tocar, para que «no espera, era el mercado»
            # corrija ESE y no el de id mas alto.
            tocados=_ultimos_tocados(chat),
        )
    except Exception as ex:
        # Sin IA o con la llamada rota se cae al camino de patrones, que sigue
        # funcionando: peor, pero funcionando.
        print(f'  no pude entender con IA: {type(ex).__name__}: {str(ex)[:160]}')
        return None


def _cambios_del_plan(plan, texto=''):
    """Los campos de `movimientos.editar` que pide el plan.

    Las etiquetas se filtran contra lo que el usuario ESCRIBIO: el modelo le
    agregaba etiquetas que nadie pidio -- a «es lo de google, eso es del
    trabajo» le ponia `reembolsable` -- y una etiqueta es justo el dato que
    solo aporta el usuario.
    """
    cambios = {}
    if plan.get('categoria'):
        cambios['categoria'] = plan['categoria']
    if plan.get('presupuesto'):
        cambios['presupuesto'] = plan['presupuesto']
    if plan.get('comercio'):
        cambios['comercio'] = plan['comercio']
    if plan.get('etiquetas_agregar'):
        pedidas = (
            intencion.etiquetas_respaldadas(texto, plan['etiquetas_agregar'])
            if texto
            else plan['etiquetas_agregar']
        )
        if pedidas:
            cambios['etiquetas'] = pedidas
    if plan.get('etiquetas_quitar'):
        cambios['quitar_etiquetas'] = plan['etiquetas_quitar']
    return cambios


def _lotes_del_plan(plan, texto=''):
    """El plan como una lista de (ids, cambios).

    Un mensaje puede darle valores distintos a movimientos distintos: «la de
    tierragro ponla en gato y la de uber en salidas». Antes solo cabia UN juego
    de cambios para toda la lista, asi que ganaba el ultimo y las dos quedaban
    en lo mismo -- y el texto que se le mostraba decia lo correcto, que es lo
    peor de todo: parecia que habia funcionado.
    """
    lotes = []
    for lote in plan.get('lotes') or []:
        ids = [str(i) for i in (lote.get('movimientos') or [])]
        cambios = _cambios_del_plan(lote, texto)
        if ids and cambios:
            lotes.append((ids, cambios))
    if lotes:
        return lotes
    ids = [str(i) for i in (plan.get('movimientos') or [])]
    cambios = _cambios_del_plan(plan, texto)
    return [(ids, cambios)] if ids and cambios else []


def _cerrar_preguntas(cx, ids, cambios, abiertas):
    """Cierra las preguntas de esos movimientos. Devuelve cuantas."""
    por_firefly = {str(p['firefly_id']): p for p in abiertas if p['firefly_id']}
    cerradas = 0
    for i in ids:
        p = por_firefly.get(i)
        if p:
            db.pendiente_actualizar(
                cx,
                p['id'],
                pregunta=None,
                categoria=cambios.get('categoria') or p['categoria'],
                presupuesto=cambios.get('presupuesto') or p['presupuesto'],
                confianza=1.0,
                decidido_por='usuario',
            )
            cerradas += 1
    return cerradas


def _ejecutar_plan(cx, chat, texto, plan, abiertas, ya_confirmado=False):
    """Hace lo que el plan dice. Devuelve True si lo atendio.

    `ya_confirmado` es para el toque de «si, hazlo»: sin eso el umbral de
    confirmacion se vuelve a evaluar y el plan queda pidiendo permiso para
    siempre.
    """
    accion = plan.get('accion')
    confianza = float(plan.get('confianza') or 0)
    ids = [str(i) for i in (plan.get('movimientos') or [])]
    porque = plan.get('explicacion') or ''

    if accion in ('nada', None):
        return False

    if accion == 'consultar':
        _recordar_camino(chat, 'asesor')
        _consultar_asesor(cx, chat, texto)
        return True

    if accion == 'regla_presupuesto':
        cat, pres = plan.get('categoria'), plan.get('presupuesto')
        if not (cat and pres):
            return False
        _a(cx).fijar_presupuesto_de_categoria(cat, pres)
        telegram.enviar(
            chat,
            f'📌 Anotado: <b>{cat}</b> va a <b>{pres}</b>.\n'
            f'<i>{porque}</i>\n\n'
            f'Esto gana sobre lo que diga el histórico, así que la próxima '
            f'compra de esa categoría entra con presupuesto sola.\n'
            f'¿Se lo pongo también a las que ya están sin presupuesto?',
            [[('sí, a las de este mes', 'rp:0:0')]],
        )
        _recordar_camino(chat, 'edicion')
        return True

    if accion == 'clasificar_producto':
        return _clasificar_producto_del_plan(cx, chat, plan, confianza)

    if accion == 'borrar':
        if len(ids) != 1:
            return False
        m = movimientos.uno(ids[0])
        if m is None:
            return False
        telegram.enviar(
            chat,
            f'Vas a BORRAR de Firefly:\n<b>{movimientos.describir_html(m)}</b>\n\n'
            f'No se puede deshacer.',
            [[('🗑 sí, bórralo', f'mB:{ids[0]}:0'), ('cancelar', f'mv:{ids[0]}:0')]],
        )
        _recordar_camino(chat, 'edicion')
        return True

    if accion not in ('editar', 'responder'):
        return False

    lotes = _lotes_del_plan(plan, texto)
    if not lotes:
        return False

    # Pidio un PRESUPUESTO y el plan solo trae categoria: el presupuesto que
    # nombro no existe y el modelo lo reemplazo por la categoria parecida.
    # «ponla en el presupuesto Viajes Largos» le borraba a Uber su categoria
    # «Transporte Aplicacion» y la dejaba en «Viajes». Se avisa y no se toca.
    if intencion.pide_presupuesto(texto) and not any(
        c.get('presupuesto') for _g, c in lotes
    ):
        cuales = ', '.join(c['categoria'] for _g, c in lotes if c.get('categoria'))
        telegram.enviar(
            chat,
            f'No tengo un presupuesto con ese nombre. Los que hay: '
            f'<b>{", ".join(presupuestos.nombres_activos())}</b>.'
            + (
                f'{SALTO}{SALTO}<i>No le puse la categoría «{cuales}», que es '
                f'lo más parecido, porque eso te borraría la que ya tenía.</i>'
                if cuales
                else ''
            ),
        )
        _recordar_camino(chat, 'edicion')
        return True

    ids = [i for grupo, _c in lotes for i in grupo]

    # Confianza baja: se muestra lo que se entendio y se pide un toque. Aplicar
    # en silencio algo que no se entendio bien es lo unico inaceptable. Y una
    # edicion que toca MUCHOS movimientos de un golpe se confirma aunque la
    # confianza sea alta: «cambia todas a mercado» reescribia los diez -- el
    # ingreso incluido, que quedaba con categoria de gasto -- sin preguntar.
    masiva = not ya_confirmado and len(ids) > MAXIMO_SIN_CONFIRMAR
    if not ya_confirmado and (confianza < CONFIANZA_PARA_APLICAR or masiva):
        # Se guarda el plan EXACTO que se muestra. Al confirmar se ejecuta ese.
        _guardar_texto_en_espera(cx, chat, texto, plan)
        objetivo = ids[0]
        encabezado = (
            f'Eso toca <b>{len(ids)} movimientos</b>. Confírmame antes:'
            if masiva
            else 'Creo que quieres esto, pero no estoy seguro:'
        )
        telegram.enviar(
            chat,
            f'{encabezado}{SALTO}<i>{porque}</i>{SALTO}{SALTO}'
            f'{_resumen_de_lotes(lotes)}',
            [
                [('✅ sí, hazlo', f'ok:{objetivo}:0')],
                [('⚙️ mejor lo toco yo', f'mv:{objetivo}:0')],
            ],
        )
        _recordar_camino(chat, 'edicion')
        return True

    lineas = [f'✅ <i>{porque}</i>', '']
    cerradas, hubo_error = 0, False
    for grupo, cambios in lotes:
        for r in movimientos.editar_varios(grupo, **cambios):
            if r.get('error'):
                lineas.append(f'⚠️ #{r["id"]}: {r["error"]}')
                hubo_error = True
            else:
                lineas.append(movimientos.describir_html(r['movimiento']))
        # Si era la RESPUESTA a algo que el bot pregunto, tambien se cierra la
        # pregunta: si no, seguiria preguntando por algo ya resuelto.
        if accion == 'responder':
            cerradas += _cerrar_preguntas(cx, grupo, cambios, abiertas)
    if cerradas:
        lineas.append(f'{SALTO}<i>{cerradas} pregunta(s) cerradas.</i>')

    db.bitacora(
        cx,
        'plan_ia',
        usuario_id=_usuario_de(cx, chat),
        payload={'texto': texto, 'plan': plan, 'lotes': lotes},
        ok=not hubo_error,
    )
    cx.commit()
    _recordar_tocados(chat, ids)
    telegram.enviar(
        chat,
        SALTO.join(lineas),
        [[('✏️ no era eso', f'mv:{ids[0]}:0')]],
    )
    _recordar_camino(chat, 'edicion')
    # El mensaje ademas preguntaba algo. Antes esa parte se perdia en silencio:
    # se hacia el cambio y la pregunta no se contestaba nunca.
    if plan.get('consultar_tambien'):
        _recordar_camino(chat, 'asesor')
        _consultar_asesor(cx, chat, texto)
    return True


def _responder_producto(cx, chat, catalogo_id, texto):
    """Contestaste al mensaje de un producto concreto: no hay que adivinar cual.

    Se le pide al modelo solo el grupo y la categoria, con el producto ya
    fijado. Si no hay IA se cae a las reglas de palabras del catalogo, que para
    «es el costo de domicilio» aciertan.
    """
    p = _a(cx).catalogo_por_id(int(catalogo_id))
    if p is None:
        telegram.enviar(chat, 'Ese producto ya no está en la cola.')
        return
    grupos = {g: list(catalogo.CATEGORIAS[g]) for g in catalogo.GRUPOS}
    plan = None
    if ia.disponible():
        try:
            plan = ia.entender_orden(
                texto,
                [],
                movimientos.categorias(),
                presupuestos.nombres_activos(),
                productos=[{'id': catalogo_id, 'descripcion': p['descripcion']}],
                grupos_producto=grupos,
            )
        except Exception as ex:
            print(f'  no pude entender el producto: {str(ex)[:120]}')
    if plan and plan.get('producto_grupo'):
        plan['producto_id'] = str(catalogo_id)
        _clasificar_producto_del_plan(cx, chat, plan)
        return

    # Respaldo: las reglas de palabras del propio catalogo, aplicadas a lo que
    # el usuario escribio en vez de a la descripcion truncada del almacen. Sin
    # el nit ni el codigo a proposito: con ellos ganan los OVERRIDES y lo que
    # escribio el usuario no se mira. Ver `catalogo.clasificar_texto`.
    _tipo, grupo, cat, _fuente = catalogo.clasificar_texto(texto)
    if grupo == 'Sin clasificar':
        telegram.enviar(
            chat,
            f'No supe en qué grupo va «{_escapar(texto[:40])}». Usa los botones del '
            f'mensaje, que son los grupos que existen.',
        )
        return
    _clasificar_producto_del_plan(
        cx,
        chat,
        {
            'producto_id': str(catalogo_id),
            'producto_grupo': grupo,
            'producto_categoria': cat,
            'explicacion': f'por lo que escribiste: «{_escapar(texto[:40])}»',
        },
    )


def _clasificar_producto_del_plan(cx, chat, plan, confianza=1.0):
    """Guarda que ES un producto de supermercado, dicho en palabras.

    Antes la pregunta de producto solo aceptaba botones. Contestar «es el costo
    de domicilio» a «fletes gravado» no tenia a donde llegar, y el mensaje
    acababa en el camino de las transacciones ofreciendo cambiar la categoria
    de una compra del banco.
    """
    pid = plan.get('producto_id')
    grupo = plan.get('producto_grupo')
    cat = plan.get('producto_categoria')
    if not (pid and grupo):
        return False
    p = _a(cx).catalogo_por_id(int(pid))
    if p is None:
        telegram.enviar(chat, 'Ese producto ya no está en la cola.')
        return True

    # El GRUPO tiene que existir. Se validaba la categoria contra el grupo pero
    # nunca el grupo contra el catalogo: con uno inventado -- «Mercado», que es
    # el nombre de una categoria de MOVIMIENTOS y por eso el error mas probable
    # -- `validas` quedaba vacio y se guardaba `grupo=categoria=Mercado`. Y como
    # queda con origen='usuario', ninguna regla lo vuelve a pisar: el dashboard
    # se rompe en silencio y para siempre.
    if grupo not in catalogo.GRUPOS:
        _preguntar_producto_uno(
            cx, chat, p, f'No tengo un grupo que se llame «{grupo}». ¿Cuál es?'
        )
        _recordar_camino(chat, 'producto')
        return True

    # Que el par sea valido: el modelo puede cruzarlos. Y se DICE, porque
    # guardar otra categoria callado es lo mismo que mentir.
    validas = catalogo.CATEGORIAS.get(grupo) or ()
    nota = ''
    if cat not in validas:
        pedida, cat = cat, validas[0]
        if pedida:
            nota = (
                f'{SALTO}<i>«{pedida}» no es una categoría de {grupo}; '
                f'la dejé en «{cat}».</i>'
            )

    # Un producto no se guarda con la confianza en el piso. Es asimetrico a
    # proposito al reves de como estaba: editar una transaccion pedia 0.75 y se
    # puede deshacer; un producto no pedia nada y no lo vuelve a tocar ninguna
    # regla.
    if confianza < CONFIANZA_PARA_APLICAR:
        combinado = catalogo.GRUPOS.index(grupo) * 100 + list(validas).index(cat)
        msg = telegram.enviar(
            chat,
            f'¿<b>{_escapar(p["descripcion"] or p["codigo"])}</b> es '
            f'<b>{grupo} · {cat}</b>?{SALTO}'
            f'<i>{plan.get("explicacion") or ""}</i>',
            [
                [('✅ sí', f'fc:{pid}:{combinado}')],
                [('⚙️ otro grupo', f'fv:{pid}:0')],
            ],
        )
        if msg and msg.get('message_id'):
            _a(cx).guardar_mensaje_producto(str(chat), msg['message_id'], int(pid))
        cx.commit()
        _recordar_camino(chat, 'producto')
        return True

    tipo = catalogo.tipo_de(grupo)
    db.catalogo_responder_id(cx, int(pid), tipo, grupo, cat)
    # La consulta del catalogo trae 'veces' en un sitio y 'compras' en
    # otro; se acepta el que venga.
    veces = _columna(p, 'veces') or _columna(p, 'compras', 0)
    arrastre = (
        f'{SALTO}<i>Se aplicó a las {veces} compras anteriores.</i>'
        if (veces or 0) > 1
        else ''
    )
    telegram.enviar(
        chat,
        f'✅ <b>{_escapar(p["descripcion"] or p["codigo"])}</b>{SALTO}'
        f'{tipo} · {grupo} · {cat}{arrastre}{nota}{SALTO}'
        f'<i>{plan.get("explicacion") or ""}</i>',
    )
    _recordar_camino(chat, 'producto')
    return True


def _resumen_de_cambios(cambios):
    """Los cambios en lenguaje llano, para confirmarlos."""
    nombres = {
        'categoria': 'categoría',
        'presupuesto': 'presupuesto',
        'comercio': 'comercio',
        'etiquetas': 'agregar etiqueta',
        'quitar_etiquetas': 'quitar etiqueta',
        'monto': 'monto',
    }
    filas = []
    for k, v in cambios.items():
        valor = ', '.join(v) if isinstance(v, list) else v
        filas.append(f'· {nombres.get(k, k)}: <b>{valor}</b>')
    return SALTO.join(filas)


def _resumen_de_lotes(lotes):
    """Igual que arriba pero diciendo A QUE movimiento va cada cosa, que es lo
    que importa cuando el mensaje le pone valores distintos a cada uno."""
    if len(lotes) == 1:
        return _resumen_de_cambios(lotes[0][1])
    trozos = []
    for ids, cambios in lotes:
        cuales = ', '.join(f'#{i}' for i in ids)
        trozos.append(f'<b>{cuales}</b>{SALTO}{_resumen_de_cambios(cambios)}')
    return (SALTO + SALTO).join(trozos)


def _toque_confirmar_plan(t):
    """«si, hazlo» sobre un plan que el modelo no dio por seguro.

    Ejecuta el plan GUARDADO, el mismo que se le mostro. Antes le volvia a
    preguntar al modelo con el texto, y eso traia dos problemas: el plan nuevo
    podia ser otro -- se confirmaba «toca 8 movimientos» y se aplicaba otra
    cosa -- y como el umbral de confirmacion se recalcula sobre el plan, una
    orden de mas de cuatro movimientos no se aplicaba NUNCA: cada toque volvia
    a pedir confirmacion y quemaba una llamada al modelo.
    """
    txt = _texto_en_espera(t.cx, t.chat)
    if not txt:
        t.aviso('ya no tengo ese mensaje')
        return
    t.aviso('va')
    plan = _a(t.cx).plan_en_espera(t.chat)
    if not plan:
        # Solo se le vuelve a preguntar si no hay plan guardado: pasa con los
        # planes que quedaron en espera antes de que esto existiera.
        plan = _plan_de_ia(t.cx, t.chat, txt, abiertas_del_chat(t.cx, t.chat))
    if not plan:
        t.reemplazar('No pude entenderlo de nuevo. Tócalo y te lo cambio a mano.')
        return
    plan['confianza'] = 1.0
    _ejecutar_plan(
        t.cx, t.chat, txt, plan, abiertas_del_chat(t.cx, t.chat), ya_confirmado=True
    )


def _toque_presupuesto_a_los_viejos(t):
    """Aplica la regla recien fijada a los movimientos de este mes que estan sin
    presupuesto. Es lo que hace que fijarla no sea solo para el futuro."""
    fijados = _a(t.cx).presupuestos_fijados()
    if not fijados:
        t.aviso('no hay reglas puestas')
        return
    t.aviso('voy')
    arreglados = []
    for m in movimientos.ultimos(limite=300, dias=45):
        if m['presupuesto'] or not m['categoria'] or m['valor'] > 0:
            continue
        pres = fijados.get(m['categoria'])
        if not pres:
            continue
        try:
            movimientos.editar(str(m['id']), presupuesto=pres)
            arreglados.append(f'{movimientos.describir_html(m)} → {pres}')
        except Exception as ex:
            arreglados.append(f'⚠️ #{m["id"]}: {str(ex)[:80]}')
    if not arreglados:
        telegram.enviar(t.chat, 'No había ninguno sin presupuesto. ✅')
        return
    telegram.enviar(
        t.chat,
        f'Le puse presupuesto a {len(arreglados)}:' + SALTO + SALTO.join(arreglados),
    )


TOQUES = {
    'c': _toque_categoria,
    'x': _toque_descartar,
    'd': _toque_borrar_fantasma,
    'k': _toque_dejarlo,
    'a': _toque_aceptar_propuesta,
    'b': _toque_presupuesto,
    't': _toque_pedir_texto,
    'm': _toque_mover,
    # Los que empiezan por 'm' y siguen: trabajan contra un id de FIREFLY, no
    # contra un pendiente de la cola.
    'mv': _toque_ver_movimiento,
    'mc': _toque_categoria_movimiento,
    # 'sc' indexa TODAS las categorias; 'mc' solo las seis del menu. Ver la
    # nota en `_toque_lista_categorias`.
    'sc': _toque_categoria_de_la_lista,
    'mk': _toque_confirmar_movimiento,
    'mx': _toque_borrar_movimiento,
    'mB': _toque_borrar_confirmado,
    'mt': _toque_texto_movimiento,
    # El menu ampliado, tambien sobre un movimiento de Firefly. Antes solo se
    # podia cambiar la categoria, y solo entre seis de las setenta y una.
    'lc': _toque_lista_categorias,
    'pb': _toque_menu_presupuesto,
    'sb': _toque_elegir_presupuesto,
    'bp': _toque_fijar_presupuesto_de_categoria,
    'le': _toque_menu_etiquetas,
    'se': _toque_elegir_etiqueta,
    'ne': _toque_pedir_etiqueta,
    'nc': _toque_pedir_comercio,
    # El plan que propone el modelo cuando no esta seguro.
    'ok': _toque_confirmar_plan,
    'rp': _toque_presupuesto_a_los_viejos,
    # Los que empiezan por 'f' trabajan contra el CATALOGO de productos, que
    # es un tercer espacio de nombres: ni la cola de pendientes ni Firefly.
    'fg': _toque_producto_grupo,
    'fc': _toque_producto_categoria,
    'fv': _toque_producto_volver,
    'fp': _toque_producto_en_espera,
    'fx': _toque_producto_saltar,
}


def _cmd_start(cx, chat, _texto):
    _a(cx).vincular_chat(chat)
    telegram.enviar(chat, AYUDA)


def _cmd_ayuda(_cx, chat, _texto):
    telegram.enviar(chat, AYUDA)


def _cmd_pendientes(cx, chat, _texto):
    """A proposito usa la lista completa: si el usuario lo pide, se le muestra
    aunque ya se le hubiera preguntado hoy."""
    abiertos = db.pendientes_abiertos(cx, limite=MAX_PREGUNTAS)
    if not abiertos:
        telegram.enviar(chat, 'No tengo nada por preguntarte. ✅')
        return
    for p in abiertos:
        db.pendiente_actualizar(cx, p['id'], preguntado_en=None)
    cx.commit()
    preguntar_pendientes(cx)


def _cmd_resumen(cx, chat, _texto):
    cmd_resumen(cx, chat)


def _cmd_sinconfirmar(cx, chat, _texto):
    cmd_sinconfirmar(cx, chat)


def _cmd_version(_cx, chat, _texto):
    telegram.enviar(chat, _version_texto())


def _cmd_ultimos(cx, chat, texto):
    cmd_ultimos(cx, chat, texto)


def _cmd_productos(cx, chat, _texto):
    cmd_productos(cx, chat)


def _cmd_listo(cx, chat, texto):
    cmd_listo(cx, chat, texto)


def _cmd_presupuestos(_cx, chat, _texto):
    telegram.enviar(chat, '<b>Presupuestos del mes</b>\n\n' + presupuestos.formatear())


COMANDOS = {
    '/start': _cmd_start,
    '/ayuda': _cmd_ayuda,
    '/pendientes': _cmd_pendientes,
    '/resumen': _cmd_resumen,
    '/sinconfirmar': _cmd_sinconfirmar,
    '/version': _cmd_version,
    '/presupuestos': _cmd_presupuestos,
    '/ultimos': _cmd_ultimos,
    '/listo': _cmd_listo,
    '/productos': _cmd_productos,
}


def _escapar(valor):
    """Lo que viene del usuario o de Firefly, listo para meter en HTML.

    Un comercio que se llame «Cafe & Bar <3» rompe el HTML del mensaje, y
    Telegram rechaza el mensaje COMPLETO: el bot queda mudo sin decir por que.
    Peor cuando el nombre queda guardado en Firefly, porque desde ahi TODA
    pantalla que lo muestre -- incluidas las que servirian para corregirlo --
    queda irrenderizable.
    """
    return html.escape(str(valor if valor is not None else ''), quote=False)


def chats_autorizados():
    """Los chat_id que pueden usar el bot. Vacio = no hay ninguno configurado.

    Se lee en cada llamada y no se cachea: el conjunto sale del entorno y en
    las pruebas se cambia con monkeypatch.
    """
    return {
        str(config.get(clave))
        for clave in ('TELEGRAM_CHAT_ID_JUAN', 'TELEGRAM_CHAT_ID_NOVIA')
        if config.get(clave)
    }


def autorizado(chat):
    """Si ese chat puede hablarle al bot.

    El token de un bot de Telegram es un secreto, pero el NOMBRE del bot es
    publico: cualquiera que lo encuentre le puede escribir, y el bot atendia a
    quien fuera. Con un `/ultimos` se leian todos los movimientos y saldos, y
    con tres toques se borraba un movimiento de Firefly.

    Sin chats configurados NO se abre a todo el mundo: se cierra. Un despliegue
    a medio configurar tiene que quedar mudo, no publico.
    """
    permitidos = chats_autorizados()
    return bool(permitidos) and str(chat) in permitidos


def manejar_update(cx, u):
    """Reparte un update de Telegram. Solo enruta: la logica vive en los
    manejadores, que se pueden probar uno por uno."""
    if 'callback_query' in u:
        cq = u['callback_query']
        # De quien viene el toque. Se mira ANTES de armar el Toque: un callback
        # forjado desde otro chat borraba de Firefly.
        de_chat = ((cq.get('message') or {}).get('chat') or {}).get('id')
        if not autorizado(de_chat):
            print(f'  toque ignorado, chat no autorizado: {de_chat}')
            with contextlib.suppress(Exception):
                telegram.responder_callback(cq['id'], 'este bot no es tuyo')
            return
        try:
            toque = Toque(cx, cq)
        except (ValueError, KeyError):
            telegram.responder_callback(cq['id'], 'no entendí ese botón')
            return
        manejador = TOQUES.get(toque.accion)
        if manejador is None:
            toque.aviso('no entendí ese botón')
            return
        # El callback SIEMPRE se contesta: si el manejador revienta antes de
        # avisar, el botoncito se queda girando en Telegram para siempre y no
        # hay ninguna senal de que algo fallo.
        try:
            manejador(toque)
        except Exception as ex:
            print(f'  el toque {toque.accion} fallo: {type(ex).__name__}: {ex}')
            with contextlib.suppress(Exception):
                toque.aviso('algo se rompió, intenta de nuevo')
            raise
        return

    msg = u.get('message') or u.get('edited_message')
    if not msg:
        return
    # Con `.get`: llegan updates de encuestas, de miembros que entran y de
    # canales, y no todos traen `chat`.
    chat = (msg.get('chat') or {}).get('id')
    if not chat:
        return
    if not autorizado(chat):
        # Se registra el id: es exactamente el numero que hay que poner en
        # TELEGRAM_CHAT_ID_JUAN o _NOVIA para dar acceso.
        print(f'  mensaje ignorado, chat no autorizado: {chat}')
        return
    # El `caption` de una foto o un PDF tambien es texto que el usuario
    # escribio: mandar la foto de una factura con «esto fue mercado» dejaba al
    # bot completamente callado.
    texto = (msg.get('text') or msg.get('caption') or '').strip()
    if not texto:
        # Callarse ante un sticker o una nota de voz parece un bot roto. Ante un
        # mensaje vacio de verdad si hay que callarse.
        if any(k in msg for k in ('photo', 'document', 'voice', 'audio', 'video')):
            telegram.enviar(
                chat,
                'Por ahora solo entiendo texto. Si le pusiste descripción a eso, '
                'escríbemela y ya.',
            )
        return

    if texto.startswith('/'):
        # Telegram manda /comando@nombre_del_bot en los grupos.
        nombre = texto.split()[0].split('@')[0]
        manejador = COMANDOS.get(nombre)
        if manejador is None:
            telegram.enviar(chat, f'No conozco {_escapar(nombre)}.\n\n{AYUDA}')
            return
        manejador(cx, chat, texto)
        return

    rp = (msg.get('reply_to_message') or {}).get('message_id')
    _texto_libre(cx, chat, texto, respondiendo_a=rp)


# Historial de conversacion por chat, en memoria. Se pierde al reiniciar y esta
# bien: el asesor arma el contexto financiero de cero en cada pregunta, el
# historial solo sirve para que se entiendan los "y si mejor...".
HISTORIAL = {}
# Entradas, no turnos: cada turno son dos (lo que dijo el usuario y lo que
# contesto el bot). Con 10 se olvidaba a los cinco turnos, y una deliberacion
# -- «quiero comprar una bici de 2 millones», cinco preguntas, «y entonces la
# bici si o no» -- perdia el precio de la bici, que es justo la conversacion de
# varios turnos que tiene sentido tener.
MAX_HISTORIAL = 24


def _guardar_mensaje(cx, chat, mensaje_id, pendiente_id):
    _a(cx).guardar_mensaje(chat, mensaje_id, pendiente_id)


def _guardar_texto_en_espera(cx, chat, txt, plan=None):
    _a(cx).guardar_texto_en_espera(chat, txt, plan)


def _texto_en_espera(cx, chat):
    return _a(cx).texto_en_espera(chat)


def _pendiente_de_mensaje(cx, chat, mensaje_id):
    return _a(cx).pendiente_de_mensaje(chat, mensaje_id)


def abiertas_del_chat(cx, chat):
    return _a(cx).pendientes_abiertos_de_chat(chat)


# El ultimo camino que tomo cada chat, para no romper el hilo. En memoria, como
# el historial del asesor: si el proceso se reinicia se pierde la conversacion
# entera y da igual haber guardado el modo.
ULTIMO_CAMINO = {}
# Cuanto vale ese recuerdo. Pasado ese rato, un «y la otra» ya no se sabe de que
# hablaba y se decide solo por el texto.
MINUTOS_DE_HILO = 15


def _recordar_camino(chat, camino):
    ULTIMO_CAMINO[str(chat)] = (camino, time.time())


# Lo ultimo que se toco en ese chat. Sin esto, «no espera, era el mercado»
# se aplicaba al movimiento de id mas alto y no al que se acababa de cambiar:
# la correccion se le escribia a un movimiento que el usuario nunca menciono.
ULTIMO_TOCADO = {}


def _recordar_tocados(chat, ids):
    if ids:
        ULTIMO_TOCADO[str(chat)] = ([str(i) for i in ids], time.time())


def _ultimos_tocados(chat):
    """Los del ultimo cambio, si fue hace poco. Vacio si no."""
    ids, cuando = ULTIMO_TOCADO.get(str(chat), ([], 0))
    if ids and (time.time() - cuando) < MINUTOS_DE_HILO * 60:
        return ids
    return []


def _venia_del_asesor(chat):
    camino, cuando = ULTIMO_CAMINO.get(str(chat), (None, 0))
    return camino == 'asesor' and (time.time() - cuando) < MINUTOS_DE_HILO * 60


def _texto_libre(cx, chat, texto, respondiendo_a=None):
    """Un mensaje escrito a mano y que hay que resolver solo.

    El orden importa y cada paso esta donde esta por un fallo concreto:

      1. Si respondiste a un mensaje, es ese. No hay nada que pensar.
      2. Si pides un CAMBIO («cambia la ultima a Mercado»), va contra Firefly.
         Antes del asesor, que no puede editar.
      3. Si es una consulta («¿me alcanza?», «cual fue la ultima»), al asesor,
         haya o no preguntas abiertas.
      4. Si vienes hablando con el asesor y esto continua la conversacion («y la
         anterior a esa»), sigue con el asesor. Sin esto, ese mensaje caia en el
         camino de las respuestas y el bot sacaba un movimiento cualquiera con
         botones de categoria.
      5. Si el texto no implica NINGUNA categoria ni senala NINGUN movimiento,
         no es una respuesta. Al asesor. Esta es la garantia dura: sin ella,
         cualquier cosa que no se entienda acaba tratada como respuesta a la
         ultima pregunta abierta.
      6. Ya con la certeza de que parece una respuesta: una sola abierta es esa;
         con varias se puntua; y si ninguna gana, la ultima preguntada, pero
         diciendolo y con botones para moverla.
    """
    # 1. respondio a un mensaje concreto
    if respondiendo_a:
        pid = _pendiente_de_mensaje(cx, chat, respondiendo_a)
        if pid:
            _recordar_camino(chat, 'respuesta')
            _responder_con_texto(cx, chat, pid, texto)
            return
        # ¿O al mensaje de un PRODUCTO de supermercado? Son dos espacios
        # distintos y en el mismo chat: «fletes gravado» es una linea de
        # factura, no un cargo del banco.
        cat_id = _a(cx).producto_de_mensaje(chat, respondiendo_a)
        if cat_id:
            _recordar_camino(chat, 'producto')
            _responder_producto(cx, chat, cat_id, texto)
            return
        ed = _a(cx).edicion_en_curso(chat)
        if ed and ed['mensaje_id'] == respondiendo_a:
            _recordar_camino(chat, 'edicion')
            _editar_por_texto(
                cx, chat, texto, tx_id=ed['firefly_id'], campo=ed['campo']
            )
            return

    abiertas_ahora = abiertas_del_chat(cx, chat)

    # 2. QUE ENTIENDE EL MODELO. Este es el camino principal: se le da el
    #    mensaje, los movimientos recientes con su id y los catalogos, y
    #    devuelve un plan. Todo lo de abajo es el respaldo para cuando no hay
    #    API key o la llamada falla.
    plan = _plan_de_ia(cx, chat, texto, abiertas_ahora)
    if plan and _ejecutar_plan(cx, chat, texto, plan, abiertas_ahora):
        return

    # 3. respaldo por patrones: una orden de cambio va contra Firefly
    if intencion.es_edicion(texto).pide_cambio:
        _recordar_camino(chat, 'edicion')
        _editar_por_texto(cx, chat, texto)
        return

    # 4. una consulta explicita
    if intencion.es_para_el_asesor(texto):
        _recordar_camino(chat, 'asesor')
        _consultar_asesor(cx, chat, texto)
        return

    # 5. seguir el hilo: «y la anterior a esa» continua la conversacion. Ninguna
    #    de estas formas describe una compra, asi que no depende del modo.
    if intencion.es_seguimiento(texto):
        _recordar_camino(chat, 'asesor')
        _consultar_asesor(cx, chat, texto)
        return

    abiertas = abiertas_del_chat(cx, chat)
    if not abiertas:
        _recordar_camino(chat, 'asesor')
        _consultar_asesor(cx, chat, texto)
        return

    # 6. las senales se calculan UNA vez y se reusan abajo
    implicada = _categoria_implicada(cx, abiertas[0], texto)
    filas = [dict(m) for m in abiertas]
    coincidencias = intencion.a_que_movimiento(texto, filas, implicada)
    senala = any(c.senalado for c in coincidencias)

    # Si veniamos hablando con el asesor y esto no dice ninguna categoria ni
    # nombra ningun movimiento, sigue siendo conversacion. Se limita a ese caso
    # a proposito: como filtro general descartaba respuestas de verdad, porque
    # aqui solo corre la heuristica barata y no la interpretacion completa —
    # «era Etre, una empresa que vende cosas para la casa» no da categoria por
    # heuristica y si es una respuesta.
    if _venia_del_asesor(chat) and not implicada and not senala:
        _recordar_camino(chat, 'asesor')
        _consultar_asesor(cx, chat, texto)
        return

    # 6.5 HAY PRODUCTOS DE FACTURA ESPERANDO y el mensaje no nombra ninguna
    #     transaccion. No se puede adivinar de cual de los dos mundos habla.
    #     Adivinando, «es shampoo» acababa escrito en la compra de Google
    #     Workspace Y aprendido como regla permanente: «GOOGLE WORKSPACE GO ->
    #     Cuidado personal», o sea que el bot aprendia para siempre que Google
    #     Workspace es shampoo. Preguntar cuesta un mensaje; equivocarse cuesta
    #     una regla que envenena todo lo que entre despues.
    prods = _a(cx).productos_preguntados(chat)
    if prods and not senala:
        _guardar_texto_en_espera(cx, chat, texto)
        botones = [
            [(f'🛒 {(p["descripcion"] or p["codigo"])[:24]}', f'fp:{p["id"]}:0')]
            for p in prods[:3]
        ]
        botones.append(
            [
                (
                    f'💳 {(abiertas[0]["contraparte"] or "")[:20]}',
                    f'm:{abiertas[0]["id"]}:0',
                )
            ]
        )
        telegram.enviar(
            chat,
            f'¿«{_escapar(texto[:60])}» es sobre un producto del mercado o sobre una '
            f'transacción? No quiero apuntarle al equivocado.',
            botones,
        )
        _recordar_camino(chat, 'respuesta')
        return

    _recordar_camino(chat, 'respuesta')

    # 7. es una respuesta: a cual
    if len(abiertas) == 1:
        _responder_con_texto(cx, chat, abiertas[0]['id'], texto)
        return

    ganador = intencion.hay_un_ganador(coincidencias)
    if ganador:
        elegido = next(m for m in abiertas if m['id'] == ganador.id)
        telegram.enviar(
            chat,
            f'Entiendo que hablas del de '
            f'<b>{_plata(elegido["valor"], elegido["moneda"])}</b> en '
            f'{_escapar(elegido["contraparte"])[:28]}'
            f'\n<i>({", ".join(ganador.razones)})</i>',
        )
        _responder_con_texto(cx, chat, ganador.id, texto)
        return

    _aplicar_a_la_ultima(cx, chat, abiertas, texto)


def _categoria_implicada(cx, cualquiera, texto):
    """Que categoria sugiere el texto, sin gastar una peticion de IA.

    Se usa solo como senal para saber A CUAL movimiento apunta, asi que la
    heuristica basta: si se equivoca, el peor caso es que no desempate.
    """
    try:
        cat = interprete.catalogo(cx, cualquiera['usuario_id'])
        hallazgos = interprete.buscar_categoria(texto, cat['categorias'])
        return hallazgos[0][1] if hallazgos else None
    except Exception:
        return None


def _aplicar_a_la_ultima(cx, chat, abiertas, texto):
    """Varias preguntas abiertas y ninguna senal de a cual va: se PREGUNTA.

    Antes aplicaba a la ultima que se pregunto, diciendolo y con botones para
    moverla. El argumento era que lo malo no es aplicar sino aplicar en
    silencio, y para una sola pregunta abierta sigue siendo cierto. Con tres
    abiertas y cero senal, la probabilidad de acertar es una de tres: contestar
    «gym» con Zona Fit abierta acababa escrito en Google Workspace, con su
    presupuesto y todo. Ahi ya no es avisar, es apostar con los datos del
    usuario.

    El texto queda en espera, asi que el boton lo aplica sin tener que
    reescribirlo.
    """
    _guardar_texto_en_espera(cx, chat, texto)
    botones = []
    fila = []
    for p in abiertas[:6]:
        etiqueta = f'{_plata(p["valor"], p["moneda"])} {(p["contraparte"] or "")[:14]}'
        fila.append((etiqueta, f'm:{p["id"]}:0'))
        if len(fila) == 2:
            botones.append(fila)
            fila = []
    if fila:
        botones.append(fila)

    telegram.enviar(
        chat,
        f'«{_escapar(texto[:60])}» me sirve, pero tengo '
        f'<b>{len(abiertas)} preguntas abiertas</b> y no sé a cuál va. '
        f'¿A cuál?',
        botones or None,
    )


# Arriba de este umbral el bot APLICA y ofrece deshacer. Por debajo, propone y
# espera. El corte no es arbitrario: la heuristica da 0.85 cuando la senal es
# inequivoca (una palabra que solo aparece en una categoria) y 0.72 cuando es
# debil, y Gemini devuelve su propia confianza.
UMBRAL_APLICAR = 0.8


def _responder_con_texto(cx, chat, pendiente_id, texto):
    """Interpreta «fue la comida de la gata en tierragro» y resuelve.

    Antes SIEMPRE pedia confirmacion antes de aplicar, con el argumento de que
    una interpretacion equivocada aplicada en silencio es peor que un mensaje
    mas. El argumento estaba a medias: lo malo no es aplicar, es aplicar EN
    SILENCIO. Pedir permiso convertia cada respuesta en dos interacciones, para
    la enorme mayoria de casos en que la interpretacion era correcta.

    Ahora, cuando la confianza alcanza, aplica y muestra el boton para
    corregir. Cuando no alcanza, propone como antes.
    """
    p = _a(cx).pendiente(pendiente_id)
    if p is None:
        telegram.enviar(chat, 'Ese movimiento ya no existe.')
        return
    try:
        d = interprete.interpretar(cx, p['usuario_id'], p, texto)
    except Exception as ex:
        telegram.enviar(chat, f'No pude interpretar eso: {str(ex)[:200]}')
        return

    if not d['categoria']:
        _pedir_categoria_a_mano(cx, chat, p, texto)
        return

    # Falta el presupuesto y la categoria no lo decide sola: eso SI hay que
    # preguntarlo, porque el presupuesto es la mitad de la decision.
    if d['pedir_presupuesto']:
        _guardar_propuesta(cx, pendiente_id, d)
        _preguntar_presupuesto(cx, pendiente_id, chat, d)
        return

    if float(d['confianza'] or 0) >= UMBRAL_APLICAR:
        _guardar_propuesta(cx, pendiente_id, d)
        p2, detalle = aplicar_respuesta(
            cx,
            pendiente_id,
            categoria=d['categoria'],
            presupuesto=d['presupuesto'],
            comercio=d['comercio'],
        )
        if p2 is None:
            telegram.enviar(chat, detalle)
            return
        lineas = [f'✅ <b>{d["categoria"]}</b>', describir(p2)]
        if d['comercio']:
            lineas.append(f'comercio <b>{d["comercio"]}</b>')
        lineas.append(f'<i>{d["razon"]}</i>')
        lineas.append(f'<i>{detalle}</i>')
        telegram.enviar(
            chat,
            '\n'.join(lineas),
            [[('✏️ No, corrijo', f't:{pendiente_id}:0')]],
        )
        return

    # Confianza baja: se propone y se espera. Aqui el mensaje extra si se gana
    # el sueldo.
    _guardar_propuesta(cx, pendiente_id, d)
    lineas = [describir(p), '', f'Creo que es <b>{d["categoria"]}</b>']
    if d['comercio']:
        lineas.append(f'comercio <b>{d["comercio"]}</b>')
    if d['presupuesto']:
        lineas.append(f'presupuesto <b>{d["presupuesto"]}</b>')
    lineas.append(f'\n<i>{d["razon"]}</i>')
    if d['fuente'] == 'gemini':
        lineas.append('<i>(interpretado con IA)</i>')
    telegram.enviar(
        chat,
        '\n'.join(lineas),
        [
            [
                ('✅ Sí', f'a:{pendiente_id}:0'),
                ('✏️ No, corrijo', f't:{pendiente_id}:0'),
            ]
        ],
    )


def _pedir_categoria_a_mano(cx, chat, p, texto):
    """No se entendio nada del texto. En vez de dejar al usuario colgado, se le
    vuelven a ofrecer las categorias con botones."""
    sug = sugerir_categorias(cx, p['usuario_id'], p)
    if not sug:
        telegram.enviar(
            chat,
            f'No entendí «{_escapar(texto[:40])}» y no tengo categorías que sugerirte. '
            f'Dime el nombre exacto de la categoría.',
        )
        return
    _guardar_sugerencias(cx, p['id'], sug)
    botones, fila = [], []
    for i, c in enumerate(sug):
        fila.append((c, f'c:{p["id"]}:{i}'))
        if len(fila) == 2:
            botones.append(fila)
            fila = []
    if fila:
        botones.append(fila)
    telegram.enviar(
        chat,
        f'No supe qué categoría es «{texto[:40]}».\n{describir(p)}\n\n'
        f'¿Alguna de estas?',
        botones,
    )


def _guardar_propuesta(cx, pendiente_id, d):
    _a(cx).guardar_propuesta(
        pendiente_id,
        d['categoria'],
        d['presupuesto'],
        d['comercio'],
        d['pedir_presupuesto'],
    )


def _leer_propuesta(cx, pendiente_id):
    return _a(cx).propuesta(pendiente_id)


def _consultar_asesor(cx, chat, texto):

    if not ia.disponible():
        telegram.enviar(
            chat,
            'Para hablar conmigo necesito una GEMINI_API_KEY. '
            'Mientras tanto: /pendientes, /resumen, '
            '/presupuestos.',
        )
        return
    hist = HISTORIAL.setdefault(str(chat), [])
    try:
        respuesta = asesor.preguntar(texto, historial=hist)
    except Exception as ex:
        telegram.enviar(chat, f'No pude responder: {str(ex)[:200]}')
        return
    hist.append(('usuario', texto))
    hist.append(('asesor', respuesta))
    del hist[:-MAX_HISTORIAL]
    telegram.enviar(chat, respuesta)


def escuchar(cx, una_vuelta=False):
    print(f'bot @{telegram.yo().get("username")} escuchando...')
    with contextlib.suppress(telegram.TelegramError):
        telegram.poner_comandos(list(DESCRIPCIONES))
    while True:
        try:
            for u in telegram.updates(espera=30):
                try:
                    manejar_update(cx, u)
                except Exception:
                    traceback.print_exc()
        except telegram.TelegramError as ex:
            print(f'telegram: {ex}')
            time.sleep(10)
        except KeyboardInterrupt:
            print('\nchao')
            return
        if una_vuelta:
            return


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    accion = argv[0] if argv else 'escuchar'
    db.inicializar()
    cx = db.conectar()
    try:
        if accion == 'escuchar':
            escuchar(cx)
        elif accion == 'una-vuelta':
            escuchar(cx, una_vuelta=True)
        elif accion == 'preguntar':
            n = preguntar_pendientes(cx)
            print(f'  {n} preguntas mandadas')
        elif accion == 'resumen':
            chat = config.get('TELEGRAM_CHAT_ID_JUAN')
            cmd_resumen(cx, chat)
            print('  resumen mandado')
        else:
            print(__doc__)
            return 2
        return 0
    finally:
        cx.close()


if __name__ == '__main__':
    sys.exit(main())
