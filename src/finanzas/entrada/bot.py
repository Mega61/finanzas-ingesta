"""El bot de Telegram: pregunta lo que el clasificador no supo, y aprende.

    finanzas bot escuchar         # long polling, es lo que corre en el server
    finanzas bot preguntar        # manda las preguntas pendientes y sale
    finanzas bot resumen          # manda el resumen diario y sale

Cada respuesta se guarda como regla, asi que un comercio se pregunta UNA vez
en la vida. Despues de contestar, el movimiento se publica en Firefly de una
(la politica es que todo entra y se confirma hablando).
"""

import contextlib
import sys
import time
import traceback

from finanzas import config
from finanzas.adaptadores import db, firefly, ia, telegram
from finanzas.adaptadores.almacen import Almacen
from finanzas.aplicacion import (
    asesor,
    clasificador,
    interprete,
    movimientos,
    presupuestos,
    publicador,
)
from finanzas.dominio import dinero as _dinero
from finanzas.dominio import intencion


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
        partes.append(f'<b>{p["contraparte"]}</b>')
    if p['cuenta_firefly']:
        flecha = '→' if p['valor'] < 0 else '←'
        partes.append(f'{p["cuenta_firefly"]} {flecha}')
    return '\n'.join(partes)


# --------------------------------------------------------------- sugerencias


def _categorias_firefly():
    try:
        return sorted(
            c['attributes']['name'] for c in firefly.get_all('/api/v1/categories')
        )
    except Exception:
        return []


def sugerir_categorias(cx, usuario_id, p, todas):
    """Las categorias mas probables primero.

    Filtra por direccion: a una nomina que ENTRA no tiene sentido ofrecerle
    'Mecato' ni 'Salidas'. Sin esto la primera prueba real ofrecio justo eso.
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
        for c in todas:
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
        botones.append([('✏️ Otra categoria', f't:{p["id"]}:0')])
        botones.append([('🚫 No es un movimiento', f'x:{p["id"]}:0')])

        texto = (
            '¿Qué categoría es esto?\n\n'
            + describir(p)
            + f'\n\n<i>{p["descripcion"] or ""}</i>'
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
            f'— {(p["contraparte"] or "")[:28]}'
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

    def aviso(self, texto):
        """El globito de confirmacion sobre el boton."""
        telegram.responder_callback(self.cq_id, texto)

    def reemplazar(self, texto):
        """Cambia el mensaje de la pregunta por el resultado, para que el chat
        no quede lleno de preguntas ya resueltas."""
        telegram.editar(self.chat, self.mid, texto)

    def resolver(self, etiqueta, **respuesta):
        """El camino comun: aplicar la respuesta y reescribir el mensaje."""
        p, detalle = aplicar_respuesta(self.cx, self.pid, **respuesta)
        self.aviso(etiqueta)
        if p is not None:
            self.reemplazar(f'✅ <b>{etiqueta}</b>\n{describir(p)}\n<i>{detalle}</i>')


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
    t.reemplazar(f'Movido: «{txt[:60]}»')
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
        for c in _categorias_firefly():
            if c not in usadas:
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
    lineas += [movimientos.describir(m) for m in movs]
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


def _menu_movimiento(cx, chat, tx_id, mensaje_id=None):
    """El menu de un movimiento de Firefly: categoria, confirmar o borrar."""
    m = movimientos.uno(str(tx_id))
    if m is None:
        telegram.enviar(chat, 'Ese movimiento ya no existe en Firefly.')
        return
    cats = _categorias_para(cx, m['valor'])
    botones = _botonera([(c, f'mc:{tx_id}:{i}') for i, c in enumerate(cats)])
    ultima = [('✏️ otra cosa', f'mt:{tx_id}:0')]
    if movimientos.SIN_CONFIRMAR in m['etiquetas']:
        ultima.append(('✅ está bien', f'mk:{tx_id}:0'))
    botones.append(ultima)
    botones.append([('🗑 borrar', f'mx:{tx_id}:0')])

    texto = (
        f'<b>{movimientos.describir(m)}</b>\n'
        f'<i>{m["origen"]} → {m["destino"]}</i>\n\n'
        f'¿Qué le cambio?'
    )
    if mensaje_id:
        telegram.editar(chat, mensaje_id, texto)
        telegram.enviar(chat, 'Elige:', botones)
    else:
        telegram.enviar(chat, texto, botones)


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
        f'✅ <i>{que}</i>\n<b>{movimientos.describir(m)}</b>',
        [[('✏️ otra vez', f'mv:{tx_id}:0')]],
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
        f'Vas a BORRAR de Firefly:\n<b>{movimientos.describir(m)}</b>\n\n'
        f'No se puede deshacer.',
        [[('🗑 sí, bórralo', f'mB:{t.pid}:0'), ('cancelar', f'mv:{t.pid}:0')]],
    )


def _toque_borrar_confirmado(t):
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


def _editar_por_texto(cx, chat, texto, tx_id=None):
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
    elif ed.la_ultima:
        objetivo = movs[0]
    else:
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
            f'<b>{movimientos.describir(objetivo)}</b>\n\nNo se puede deshacer.',
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

    if not cambios:
        telegram.enviar(
            chat,
            f'Entendí que hablas de:\n'
            f'<b>{movimientos.describir(objetivo)}</b>\n\n'
            f'Pero no entendí qué cambiarle.',
        )
        _menu_movimiento(cx, chat, objetivo['id'])
        return

    _a(cx).cerrar_edicion(chat)
    _aplicar_edicion(cx, chat, str(objetivo['id']), cambios)


# La letra que viaja en callback_data. Es de un caracter porque el limite son
# 64 bytes contando el id del pendiente y el indice de la opcion.
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
    'mk': _toque_confirmar_movimiento,
    'mx': _toque_borrar_movimiento,
    'mB': _toque_borrar_confirmado,
    'mt': _toque_texto_movimiento,
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
}


def manejar_update(cx, u):
    """Reparte un update de Telegram. Solo enruta: la logica vive en los
    manejadores, que se pueden probar uno por uno."""
    if 'callback_query' in u:
        cq = u['callback_query']
        try:
            toque = Toque(cx, cq)
        except (ValueError, KeyError):
            telegram.responder_callback(cq['id'], 'no entendí ese botón')
            return
        manejador = TOQUES.get(toque.accion)
        if manejador is None:
            toque.aviso('no entendí ese botón')
            return
        manejador(toque)
        return

    msg = u.get('message')
    if not msg:
        return
    chat = msg['chat']['id']
    texto = (msg.get('text') or '').strip()
    if not texto:
        return

    if texto.startswith('/'):
        # Telegram manda /comando@nombre_del_bot en los grupos.
        nombre = texto.split()[0].split('@')[0]
        manejador = COMANDOS.get(nombre)
        if manejador is None:
            telegram.enviar(chat, f'No conozco {nombre}.\n\n{AYUDA}')
            return
        manejador(cx, chat, texto)
        return

    rp = (msg.get('reply_to_message') or {}).get('message_id')
    _texto_libre(cx, chat, texto, respondiendo_a=rp)


# Historial de conversacion por chat, en memoria. Se pierde al reiniciar y esta
# bien: el asesor arma el contexto financiero de cero en cada pregunta, el
# historial solo sirve para que se entiendan los "y si mejor...".
HISTORIAL = {}
MAX_HISTORIAL = 10


def _guardar_mensaje(cx, chat, mensaje_id, pendiente_id):
    _a(cx).guardar_mensaje(chat, mensaje_id, pendiente_id)


def _guardar_texto_en_espera(cx, chat, txt):
    _a(cx).guardar_texto_en_espera(chat, txt)


def _texto_en_espera(cx, chat):
    return _a(cx).texto_en_espera(chat)


def _pendiente_de_mensaje(cx, chat, mensaje_id):
    return _a(cx).pendiente_de_mensaje(chat, mensaje_id)


def abiertas_del_chat(cx, chat):
    return _a(cx).pendientes_abiertos_de_chat(chat)


def _texto_libre(cx, chat, texto, respondiendo_a=None):
    """Un mensaje escrito a mano y que hay que resolver solo.

    Antes esto tenia dos modos y los dos molestaban. Primero tomaba la pregunta
    MAS RECIENTE, asi que contestar la tercera de seis resolvia la sexta y la
    categoria caia en el movimiento equivocado. Se arreglo pasando a NO adivinar
    —«responde al mensaje de la que quieras contestar»— y eso es igual de malo
    por el otro lado: escribes «era Etre, venden cosas para la casa» y el bot te
    dice que no sabe de que le hablas. Y el asesor solo se consultaba cuando NO
    habia preguntas abiertas, o sea casi nunca.

    Ahora se LEE el mensaje, en este orden:

      1. Si respondiste a un mensaje concreto, es ese. No hay nada que pensar.
      2. Si pides un CAMBIO («cambia la ultima a Mercado», «borra la ultima»),
         va contra Firefly. Esto va antes del asesor, que no puede editar.
      3. Si es una consulta («¿me alcanza para...?»), va al asesor, haya o no
         preguntas abiertas.
      4. Si hay una sola pregunta abierta, es esa.
      5. Con varias, se puntua el texto contra cada una: el comercio que
         nombras, la categoria que implica, el monto. Si una gana claro, se
         resuelve y se dice CUAL.
      6. Si ninguna gana, se aplica a la ultima que se te pregunto —que es lo
         que un humano asumiria— diciendolo, y con botones para moverla de una.
    """
    # 1. respondio a un mensaje concreto
    if respondiendo_a:
        pid = _pendiente_de_mensaje(cx, chat, respondiendo_a)
        if pid:
            _responder_con_texto(cx, chat, pid, texto)
            return
        # ¿o al mensaje de «¿que le cambio?» de un movimiento de Firefly?
        ed = _a(cx).edicion_en_curso(chat)
        if ed and ed['mensaje_id'] == respondiendo_a:
            _editar_por_texto(cx, chat, texto, tx_id=ed['firefly_id'])
            return

    abiertas = abiertas_del_chat(cx, chat)

    # 2. una orden de cambio va contra Firefly, no contra la cola. Va ANTES del
    #    asesor: «cambia la ultima a Mercado» no lleva marcas de consulta, y el
    #    asesor no puede editar nada.
    if intencion.es_edicion(texto).pide_cambio:
        _editar_por_texto(cx, chat, texto)
        return

    # 3. una consulta va al asesor aunque haya cosas abiertas
    if intencion.es_para_el_asesor(texto):
        _consultar_asesor(cx, chat, texto)
        return

    if not abiertas:
        _consultar_asesor(cx, chat, texto)
        return

    # 4. una sola: no hay ambiguedad que resolver
    if len(abiertas) == 1:
        _responder_con_texto(cx, chat, abiertas[0]['id'], texto)
        return

    # 5. varias: leer el mensaje. La categoria que implica el texto se calcula
    #    UNA vez (es la misma para todos) y se pasa como senal mas.
    implicada = _categoria_implicada(cx, abiertas[0], texto)
    filas = [dict(m) for m in abiertas]
    coincidencias = intencion.a_que_movimiento(texto, filas, implicada)
    ganador = intencion.hay_un_ganador(coincidencias)

    if ganador:
        elegido = next(m for m in abiertas if m['id'] == ganador.id)
        telegram.enviar(
            chat,
            f'Entiendo que hablas del de <b>{_plata(elegido["valor"], elegido["moneda"])}'
            f'</b> en {(elegido["contraparte"] or "")[:28]}'
            f'\n<i>({", ".join(ganador.razones)})</i>',
        )
        _responder_con_texto(cx, chat, ganador.id, texto)
        return

    # 6. Nada en el texto senala a ninguna. Se aplica a la ultima preguntada,
    #    que es la que estabas mirando, y se dice: una respuesta aplicada en
    #    silencio al movimiento equivocado es lo unico inaceptable aqui.
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
    """Ultimo recurso: la ultima que se pregunto, diciendolo y con botones."""
    ultima = abiertas[0]
    _guardar_texto_en_espera(cx, chat, texto)

    lineas = [
        f'Lo tomo como respuesta al de '
        f'<b>{_plata(ultima["valor"], ultima["moneda"])}</b> en '
        f'{(ultima["contraparte"] or "")[:28]}, que es el ultimo que te pregunte.',
        '',
        'Si era otro, tocalo:',
    ]
    botones = []
    fila = []
    for p in abiertas[1:6]:
        etiqueta = f'{_plata(p["valor"], p["moneda"])} {(p["contraparte"] or "")[:14]}'
        fila.append((etiqueta, f'm:{p["id"]}:0'))
        if len(fila) == 2:
            botones.append(fila)
            fila = []
    if fila:
        botones.append(fila)

    telegram.enviar(chat, '\n'.join(lineas), botones or None)
    _responder_con_texto(cx, chat, ultima['id'], texto)


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
    sug = sugerir_categorias(cx, p['usuario_id'], p, todas=False)
    if not sug:
        telegram.enviar(
            chat,
            f'No entendí «{texto[:40]}» y no tengo categorías que sugerirte. '
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
