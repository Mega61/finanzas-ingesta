# -*- coding: utf-8 -*-
"""El bot de Telegram: pregunta lo que el clasificador no supo, y aprende.

    python bot.py escuchar        # long polling, es lo que corre en el server
    python bot.py preguntar       # manda las preguntas pendientes y sale
    python bot.py resumen         # manda el resumen diario y sale

Cada respuesta se guarda como regla, asi que un comercio se pregunta UNA vez
en la vida. Despues de contestar, el movimiento se publica en Firefly de una
(la politica es que todo entra y se confirma hablando).
"""
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import clasificador  # noqa: E402
import config  # noqa: E402
import db  # noqa: E402
import firefly  # noqa: E402
import publicador  # noqa: E402
import telegram  # noqa: E402

# Cuantas categorias ofrecer como botones antes de pedir texto libre.
MAX_BOTONES = 8
# Cuantas preguntas mandar de una, para no ahogar el chat.
MAX_PREGUNTAS = 6


# ------------------------------------------------------------------ formato

def _plata(v, moneda='COP'):
    signo = '-' if v < 0 else '+'
    if moneda == 'USD':
        return f"{signo}US${abs(v):,.2f}"
    return f"{signo}${abs(v):,.0f}".replace(',', '.')


def describir(p):
    partes = [f"<b>{_plata(p['valor'], p['moneda'])}</b>"]
    partes.append(f"{p['fecha']}" + (f" {p['hora']}" if p['hora'] else ''))
    if p['contraparte']:
        partes.append(f"<b>{p['contraparte']}</b>")
    if p['cuenta_firefly']:
        flecha = '→' if p['valor'] < 0 else '←'
        partes.append(f"{p['cuenta_firefly']} {flecha}")
    return '\n'.join(partes)


# --------------------------------------------------------------- sugerencias

def _categorias_firefly():
    try:
        return sorted(c['attributes']['name']
                      for c in firefly.get_all('/api/v1/categories'))
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
    filas = cx.execute("""SELECT categoria, count(*) n FROM reglas
                          WHERE categoria IS NOT NULL AND categoria <> ''
                            AND direccion = ?
                          GROUP BY categoria ORDER BY n DESC""",
                       (direccion,)).fetchall()
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
            fila.append((c, f"c:{p['id']}:{i}"))
            if len(fila) == 2:
                botones.append(fila); fila = []
        if fila:
            botones.append(fila)
        botones.append([('✏️ Otra categoria', f"t:{p['id']}:0")])
        botones.append([('🚫 No es un movimiento', f"x:{p['id']}:0")])

        texto = ("¿Qué categoría es esto?\n\n" + describir(p) +
                 f"\n\n<i>{p['descripcion'] or ''}</i>")
        try:
            msg = telegram.enviar(chat, texto, botones)
            db.marcar_preguntado(cx, p['id'])
            _guardar_mensaje(cx, chat, msg['message_id'], p['id'])
            # se guardan las sugerencias para poder resolver el indice despues
            _guardar_sugerencias(cx, p['id'], sug)
            mandadas += 1
        except telegram.TelegramError as ex:
            print(f"  no pude preguntar por #{p['id']}: {ex}")
    cx.commit()
    return mandadas


def _preguntar_fantasma(cx, p, chat):
    """El extracto cerro y este cargo no aparecio: casi seguro fue una
    preautorizacion que nunca se cobro. Nunca se borra sin preguntar."""
    texto = ("👻 <b>Esto no apareció en el extracto</b>\n\n" + describir(p) +
             "\n\nEl extracto de esa tarjeta ya cerró y este cargo no está. "
             "Suele pasar con Uber: preautoriza el precio estimado y después "
             "cobra la tarifa real.\n\n¿Lo borro de Firefly?")
    botones = [
        [('🗑 Sí, bórralo', f"d:{p['id']}:0"),
         ('✅ No, es real', f"k:{p['id']}:0")],
    ]
    try:
        msg = telegram.enviar(chat, texto, botones)
        db.marcar_preguntado(cx, p['id'])
        _guardar_mensaje(cx, chat, msg['message_id'], p['id'])
        return True
    except telegram.TelegramError as ex:
        print(f"  no pude preguntar fantasma #{p['id']}: {ex}")
        return False


def _preguntar_monto(cx, p, chat):
    """Hubo varios candidatos en el extracto y no se puede saber cual es. No se
    toca el monto solo: eso fue lo que encadeno correcciones equivocadas."""
    texto = ("💰 <b>No sé cuál cargo es</b>\n\n" + describir(p) +
             "\n\nEn el extracto hay varios cargos del mismo comercio en esos "
             "días y no puedo saber cuál corresponde. Lo dejo como está.\n\n"
             "Si el monto está mal, escríbeme el correcto respondiendo a este "
             "mensaje.")
    botones = [[('✅ Déjalo así', f"k:{p['id']}:0")]]
    try:
        msg = telegram.enviar(chat, texto, botones)
        db.marcar_preguntado(cx, p['id'])
        _guardar_mensaje(cx, chat, msg['message_id'], p['id'])
        return True
    except telegram.TelegramError as ex:
        print(f"  no pude preguntar monto #{p['id']}: {ex}")
        return False


def _asegurar_tabla_sug(cx):
    cx.execute("""CREATE TABLE IF NOT EXISTS sugerencias (
                    pendiente_id INTEGER PRIMARY KEY
                        REFERENCES pendientes(id) ON DELETE CASCADE,
                    opciones     TEXT NOT NULL)""")


def _guardar_sugerencias(cx, pendiente_id, sug):
    _asegurar_tabla_sug(cx)
    cx.execute("INSERT OR REPLACE INTO sugerencias (pendiente_id, opciones) "
               "VALUES (?, ?)", (pendiente_id, '\n'.join(sug)))


def _leer_sugerencias(cx, pendiente_id):
    _asegurar_tabla_sug(cx)
    r = cx.execute('SELECT opciones FROM sugerencias WHERE pendiente_id = ?',
                   (pendiente_id,)).fetchone()
    return r['opciones'].split('\n') if r else []


# ------------------------------------------------------------------ resolver

def _presupuestos_de(cx, pendiente_id):
    """Los presupuestos entre los que hay que elegir para este movimiento."""
    import presupuestos
    p = cx.execute('SELECT * FROM pendientes WHERE id = ?',
                   (pendiente_id,)).fetchone()
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
        fila.append((n, f"b:{pendiente_id}:{i}"))
        if len(fila) == 2:
            botones.append(fila); fila = []
    if fila:
        botones.append(fila)
    telegram.enviar(chat,
                    "¿A qué presupuesto va?\n\n"
                    f"<b>{pr['categoria']}</b> la has puesto en varios.",
                    botones)


def aplicar_respuesta(cx, pendiente_id, categoria=None, descartar=False,
                      presupuesto=None, comercio=None):
    """Guarda la respuesta, crea la regla, y publica en Firefly."""
    p = cx.execute('SELECT * FROM pendientes WHERE id = ?',
                   (pendiente_id,)).fetchone()
    if p is None:
        return None, 'ese movimiento ya no existe'

    if descartar:
        db.pendiente_actualizar(cx, pendiente_id, estado='descartado',
                                pregunta=None, decidido_por='usuario')
        cx.commit()
        return p, 'descartado, no entra a Firefly'

    clave = clasificador.normalizar(p['contraparte'] or p['descripcion'])
    if clave:
        db.regla_guardar(cx, p['usuario_id'], clave, categoria=categoria,
                         presupuesto=presupuesto, cuenta_firefly=comercio,
                         origen='usuario',
                         direccion='ingreso' if float(p['valor']) > 0 else 'gasto')
    campos = {'categoria': categoria, 'pregunta': None, 'confianza': 1.0,
              'decidido_por': 'usuario'}
    if presupuesto:
        campos['presupuesto'] = presupuesto
    if comercio:
        campos['cuenta_destino'] = comercio
    db.pendiente_actualizar(cx, pendiente_id, **campos)
    cx.commit()

    p = cx.execute('SELECT * FROM pendientes WHERE id = ?',
                   (pendiente_id,)).fetchone()
    aviso = ''
    if p['presupuesto']:
        try:
            import presupuestos
            r = presupuestos.revienta(p['presupuesto'], p['valor'])
            if r:
                aviso = ("\n\n⚠️ Esto revienta "
                         f"<b>{r['nombre']}</b>: queda en {r['pct']:.0f}% "
                         f"({_plata(r['despues'])} de {_plata(r['limite'])}), "
                         f"o sea {_plata(r['exceso'])} de más.")
        except Exception:
            pass

    if p['estado'] in ('nuevo', 'error'):
        idx = publicador.IndiceFirefly(desde=str(p['fecha']), hasta=str(p['fecha']))
        accion, detalle = publicador.publicar_uno(cx, p, idx=idx, dry_run=False)
        return p, f"{categoria} · {accion} {detalle}{aviso}"
    return p, f"{categoria} · guardado{aviso}"


# ---------------------------------------------------------------- comandos

def _version_texto():
    """Que codigo esta corriendo. Sirve para saber si el contenedor quedo con
    una imagen vieja, que es facil que pase y dificil de ver de otra forma."""
    sha = config.get('GIT_SHA', 'desconocido')
    fecha = config.get('BUILD_FECHA', 'desconocida')
    import interprete
    import ia
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


AYUDA = (
    "<b>Qué hago</b>\n\n"
    "Leo las alertas de Bancolombia de tu correo, saco los movimientos y los "
    "meto a Firefly. Cuando no sé qué categoría poner, te pregunto acá.\n\n"
    "Cada respuesta queda aprendida: un comercio se pregunta <b>una sola vez</b>.\n\n"
    "<b>Comandos</b>\n"
    "/pendientes — lo que falta por clasificar\n"
    "/resumen — cómo va la conciliación\n"
    "/sinconfirmar — lo que está en Firefly sin confirmar\n"
)


def cmd_resumen(cx, chat):
    filas = db.resumen(cx)
    if not filas:
        telegram.enviar(chat, "Todo al día. No hay nada abierto. ✅")
        return
    lineas = ["<b>Cómo va</b>", '']
    total_preg = 0
    for f in filas:
        etq = f['estado'] if f['pregunta'] == 'nada' else f"{f['estado']} · falta {f['pregunta']}"
        lineas.append(f"{etq}: <b>{f['n']}</b>")
        if f['pregunta'] != 'nada':
            total_preg += f['n']
    sc = cx.execute("""SELECT count(*) n, sum(valor) t FROM pendientes
                       WHERE estado = 'publicado' AND visto_en IS NULL""").fetchone()
    if sc and sc['n']:
        lineas += ['', f"En Firefly sin confirmar: <b>{sc['n']}</b> "
                       f"({_plata(sc['t'] or 0)})"]
    if total_preg:
        lineas += ['', f"Tengo <b>{total_preg}</b> por preguntarte. /pendientes"]
    telegram.enviar(chat, '\n'.join(lineas))


def cmd_sinconfirmar(cx, chat):
    filas = cx.execute("""SELECT * FROM pendientes
                          WHERE estado = 'publicado' AND visto_en IS NULL
                          ORDER BY fecha DESC LIMIT 20""").fetchall()
    if not filas:
        telegram.enviar(chat, "No hay nada sin confirmar. ✅")
        return
    lineas = ["<b>En Firefly, sin confirmar contra extracto</b>", '']
    for p in filas:
        lineas.append(f"{p['fecha']} {_plata(p['valor'], p['moneda'])} "
                      f"— {(p['contraparte'] or '')[:28]}")
    sosp = cx.execute('SELECT count(*) n FROM v_sospechosos').fetchone()
    if sosp and sosp['n']:
        lineas += ['', f"⚠️ {sosp['n']} llevan más de 45 días sin aparecer en "
                       f"ningún extracto. Pueden ser preautorizaciones que nunca "
                       f"se cobraron."]
    telegram.enviar(chat, '\n'.join(lineas))


# --------------------------------------------------------------- el bucle

def manejar_update(cx, u):
    if 'callback_query' in u:
        cq = u['callback_query']
        dato = cq.get('data') or ''
        chat = cq['message']['chat']['id']
        mid = cq['message']['message_id']
        try:
            accion, pid, idx = dato.split(':')
            pid, idx = int(pid), int(idx)
        except ValueError:
            telegram.responder_callback(cq['id'], 'no entendí ese botón')
            return

        if accion == 'c':
            sug = _leer_sugerencias(cx, pid)
            cat = sug[idx] if idx < len(sug) else None
            if not cat:
                telegram.responder_callback(cq['id'], 'esa opción ya no está')
                return
            p, detalle = aplicar_respuesta(cx, pid, categoria=cat)
            telegram.responder_callback(cq['id'], f'listo: {cat}')
            if p is not None:
                telegram.editar(chat, mid,
                                f"✅ <b>{cat}</b>\n{describir(p)}\n<i>{detalle}</i>")
        elif accion == 'x':
            p, detalle = aplicar_respuesta(cx, pid, descartar=True)
            telegram.responder_callback(cq['id'], 'descartado')
            if p is not None:
                telegram.editar(chat, mid, f"🚫 Descartado\n{describir(p)}")
        elif accion == 'd':
            # borrar el fantasma de Firefly
            p = cx.execute('SELECT * FROM pendientes WHERE id = ?', (pid,)).fetchone()
            if p is None:
                telegram.responder_callback(cq['id'], 'ya no existe')
                return
            try:
                if p['firefly_id']:
                    firefly.borrar(p['firefly_id'])
                db.pendiente_actualizar(cx, pid, estado='fantasma', pregunta=None,
                                        decidido_por='usuario')
                db.bitacora(cx, 'borrar', usuario_id=p['usuario_id'],
                            pendiente_id=pid, firefly_id=p['firefly_id'],
                            respuesta='fantasma confirmado por el usuario')
                cx.commit()
                telegram.responder_callback(cq['id'], 'borrado')
                telegram.editar(chat, mid, f"🗑 Borrado de Firefly\n{describir(p)}")
            except firefly.ApiError as ex:
                telegram.responder_callback(cq['id'], 'no pude borrarlo')
                telegram.enviar(chat, f"No pude borrarlo: {str(ex)[:200]}")
        elif accion == 'k':
            # dejarlo como esta: es real, o el monto se queda
            p = cx.execute('SELECT * FROM pendientes WHERE id = ?', (pid,)).fetchone()
            db.pendiente_actualizar(cx, pid, estado='confirmado', pregunta=None,
                                    decidido_por='usuario')
            cx.commit()
            telegram.responder_callback(cq['id'], 'listo, lo dejo')
            if p is not None:
                telegram.editar(chat, mid, f"✅ Confirmado\n{describir(p)}")
        elif accion == 'a':
            # aceptar la propuesta que salio de interpretar el texto libre
            pr = _leer_propuesta(cx, pid)
            if pr is None:
                telegram.responder_callback(cq['id'], 'esa propuesta ya no esta')
                return
            if pr['pedir_presupuesto']:
                _preguntar_presupuesto(cx, pid, chat, pr)
                telegram.responder_callback(cq['id'], 'falta el presupuesto')
                return
            p, detalle = aplicar_respuesta(cx, pid, categoria=pr['categoria'],
                                           presupuesto=pr['presupuesto'],
                                           comercio=pr['comercio'])
            telegram.responder_callback(cq['id'], 'listo')
            if p is not None:
                telegram.editar(chat, mid,
                                f"✅ <b>{pr['categoria']}</b>\n{describir(p)}\n"
                                f"<i>{detalle}</i>")
        elif accion == 'b':
            # eleccion de presupuesto
            pr = _leer_propuesta(cx, pid)
            nombres = _presupuestos_de(cx, pid)
            elegido = nombres[idx] if idx < len(nombres) else None
            if not elegido:
                telegram.responder_callback(cq['id'], 'esa opcion ya no esta')
                return
            p, detalle = aplicar_respuesta(
                cx, pid, categoria=(pr['categoria'] if pr else None),
                presupuesto=elegido, comercio=(pr['comercio'] if pr else None))
            telegram.responder_callback(cq['id'], elegido)
            if p is not None:
                telegram.editar(chat, mid, f"✅ <b>{elegido}</b>\n{describir(p)}\n"
                                           f"<i>{detalle}</i>")
        elif accion == 't':
            cx.execute("INSERT OR REPLACE INTO sugerencias (pendiente_id, opciones) "
                       "VALUES (?, ?)", (pid, '__ESPERANDO_TEXTO__'))
            cx.commit()
            telegram.responder_callback(cq['id'], 'escribe la categoría')
            telegram.enviar(chat, f"Escribe la categoría para el movimiento "
                                  f"#{pid}, respondiendo a este mensaje:")
        return

    msg = u.get('message')
    if not msg:
        return
    chat = msg['chat']['id']
    texto = (msg.get('text') or '').strip()

    if texto.startswith('/start'):
        # vincula el chat con el usuario si todavia no lo esta
        u1 = cx.execute('SELECT id FROM usuarios WHERE telegram_chat_id IS NULL '
                        'ORDER BY id LIMIT 1').fetchone()
        if u1:
            cx.execute('UPDATE usuarios SET telegram_chat_id = ? WHERE id = ?',
                       (str(chat), u1['id']))
            cx.commit()
        telegram.enviar(chat, AYUDA)
    elif texto.startswith('/ayuda'):
        telegram.enviar(chat, AYUDA)
    elif texto.startswith('/pendientes'):
        # a proposito usa la lista completa: si el usuario lo pide, se le
        # muestra aunque ya se le hubiera preguntado hoy
        abiertos = db.pendientes_abiertos(cx, limite=MAX_PREGUNTAS)
        if not abiertos:
            telegram.enviar(chat, "No tengo nada por preguntarte. ✅")
        else:
            for p in abiertos:
                db.pendiente_actualizar(cx, p['id'], preguntado_en=None)
            cx.commit()
            preguntar_pendientes(cx)
    elif texto.startswith('/resumen'):
        cmd_resumen(cx, chat)
    elif texto.startswith('/sinconfirmar'):
        cmd_sinconfirmar(cx, chat)
    elif texto.startswith('/version'):
        telegram.enviar(chat, _version_texto())
    elif texto.startswith('/presupuestos'):
        import presupuestos
        telegram.enviar(chat, "<b>Presupuestos del mes</b>\n\n"
                              + presupuestos.formatear())
    elif texto:
        rp = (msg.get('reply_to_message') or {}).get('message_id')
        _texto_libre(cx, chat, texto, respondiendo_a=rp)


# Historial de conversacion por chat, en memoria. Se pierde al reiniciar y esta
# bien: el asesor arma el contexto financiero de cero en cada pregunta, el
# historial solo sirve para que se entiendan los "y si mejor...".
HISTORIAL = {}
MAX_HISTORIAL = 10


def _asegurar_tabla_preguntas(cx):
    """Que mensaje de Telegram corresponde a que movimiento."""
    cx.execute("""CREATE TABLE IF NOT EXISTS preguntas_enviadas (
                    chat_id      TEXT NOT NULL,
                    mensaje_id   INTEGER NOT NULL,
                    pendiente_id INTEGER NOT NULL
                        REFERENCES pendientes(id) ON DELETE CASCADE,
                    PRIMARY KEY (chat_id, mensaje_id))""")


def _guardar_mensaje(cx, chat, mensaje_id, pendiente_id):
    _asegurar_tabla_preguntas(cx)
    cx.execute("""INSERT OR REPLACE INTO preguntas_enviadas
                  (chat_id, mensaje_id, pendiente_id) VALUES (?, ?, ?)""",
               (str(chat), int(mensaje_id), int(pendiente_id)))
    cx.commit()


def _pendiente_de_mensaje(cx, chat, mensaje_id):
    _asegurar_tabla_preguntas(cx)
    r = cx.execute("""SELECT pendiente_id FROM preguntas_enviadas
                      WHERE chat_id = ? AND mensaje_id = ?""",
                   (str(chat), int(mensaje_id))).fetchone()
    return r['pendiente_id'] if r else None


def abiertas_del_chat(cx, chat):
    return cx.execute(
        """SELECT p.* FROM pendientes p JOIN usuarios u ON u.id = p.usuario_id
           WHERE p.pregunta IS NOT NULL
             AND p.estado IN ('nuevo', 'publicado', 'error')
             AND u.telegram_chat_id = ?
           ORDER BY p.preguntado_en DESC, p.id DESC""", (str(chat),)).fetchall()


def _texto_libre(cx, chat, texto, respondiendo_a=None):
    """Un mensaje escrito a mano puede ser dos cosas muy distintas: la respuesta
    a una pregunta abierta, o una consulta al asesor.

    Y si es una respuesta, hay que saber A CUAL. Antes se tomaba la pregunta mas
    reciente, asi que contestar la tercera de seis resolvia la sexta: la
    respuesta se aplicaba al movimiento equivocado y el que creias contestado
    seguia preguntandose. Ahora se usa el "responder a" de Telegram.
    """
    # 1. ¿es una respuesta a una pregunta concreta?
    if respondiendo_a:
        pid = _pendiente_de_mensaje(cx, chat, respondiendo_a)
        if pid:
            _responder_con_texto(cx, chat, pid, texto)
            return

    abiertas = abiertas_del_chat(cx, chat)

    # 2. una sola pregunta abierta: no hay ambiguedad
    if len(abiertas) == 1:
        _responder_con_texto(cx, chat, abiertas[0]['id'], texto)
        return

    # 3. varias abiertas y no dijo a cual: NO se adivina. Adivinar aqui mete la
    #    categoria en el movimiento equivocado, que es peor que preguntar.
    if len(abiertas) > 1:
        lineas = [f"Tengo <b>{len(abiertas)}</b> preguntas abiertas y no sé a "
                  f"cuál me respondes.", '',
                  "Responde <b>al mensaje</b> de la que quieras contestar "
                  "(mantén presionado → Responder), o usa los botones.", '',
                  "Las abiertas:"]
        for p in abiertas[:6]:
            lineas.append(f"· {p['fecha']} {_plata(p['valor'], p['moneda'])} "
                          f"{(p['contraparte'] or '')[:24]}")
        telegram.enviar(chat, '\n'.join(lineas))
        return

    # 4. nada abierto: es para el asesor
    _consultar_asesor(cx, chat, texto)


def _responder_con_texto(cx, chat, pendiente_id, texto):
    """Interpreta 'fue la comida de la gata en tierragro' y propone."""
    import interprete

    p = cx.execute('SELECT * FROM pendientes WHERE id = ?',
                   (pendiente_id,)).fetchone()
    if p is None:
        telegram.enviar(chat, "Ese movimiento ya no existe.")
        return
    try:
        d = interprete.interpretar(cx, p['usuario_id'], p, texto)
    except Exception as ex:
        telegram.enviar(chat, f"No pude interpretar eso: {str(ex)[:200]}")
        return

    if not d['categoria']:
        telegram.enviar(chat, "No entendí a qué categoría va. Usa los botones "
                              "del mensaje anterior, o dime el nombre exacto "
                              "de la categoría.")
        return

    # se guarda la propuesta y se pide confirmacion: una interpretacion
    # equivocada aplicada en silencio es peor que un mensaje mas
    _guardar_propuesta(cx, pendiente_id, d)
    lineas = [describir(p), '',
              f"Entendí: <b>{d['categoria']}</b>"]
    if d['comercio']:
        lineas.append(f"comercio <b>{d['comercio']}</b>")
    if d['presupuesto']:
        lineas.append(f"presupuesto <b>{d['presupuesto']}</b>")
    lineas.append(f"\n<i>{d['razon']}</i>")
    if d['fuente'] == 'gemini':
        lineas.append("<i>(interpretado con IA)</i>")

    botones = [[('✅ Correcto', f"a:{pendiente_id}:0"),
                ('✏️ No, corrijo', f"t:{pendiente_id}:0")]]
    if d['pedir_presupuesto']:
        lineas.append("\n⚠️ Esa categoría cae en varios presupuestos. "
                      "Confirma y te pregunto cuál.")
    telegram.enviar(chat, '\n'.join(lineas), botones)


def _guardar_propuesta(cx, pendiente_id, d):
    cx.execute("""CREATE TABLE IF NOT EXISTS propuestas (
                    pendiente_id INTEGER PRIMARY KEY
                        REFERENCES pendientes(id) ON DELETE CASCADE,
                    categoria TEXT, presupuesto TEXT, comercio TEXT,
                    pedir_presupuesto INTEGER DEFAULT 0)""")
    cx.execute("""INSERT OR REPLACE INTO propuestas
                  (pendiente_id, categoria, presupuesto, comercio, pedir_presupuesto)
                  VALUES (?, ?, ?, ?, ?)""",
               (pendiente_id, d['categoria'], d['presupuesto'], d['comercio'],
                1 if d['pedir_presupuesto'] else 0))
    cx.commit()


def _leer_propuesta(cx, pendiente_id):
    try:
        return cx.execute('SELECT * FROM propuestas WHERE pendiente_id = ?',
                          (pendiente_id,)).fetchone()
    except Exception:
        return None


def _consultar_asesor(cx, chat, texto):
    import asesor
    import ia

    if not ia.disponible():
        telegram.enviar(chat, "Para hablar conmigo necesito una GEMINI_API_KEY. "
                              "Mientras tanto: /pendientes, /resumen, "
                              "/presupuestos.")
        return
    hist = HISTORIAL.setdefault(str(chat), [])
    try:
        respuesta = asesor.preguntar(texto, historial=hist)
    except Exception as ex:
        telegram.enviar(chat, f"No pude responder: {str(ex)[:200]}")
        return
    hist.append(('usuario', texto))
    hist.append(('asesor', respuesta))
    del hist[:-MAX_HISTORIAL]
    telegram.enviar(chat, respuesta)


def escuchar(cx, una_vuelta=False):
    print(f"bot @{telegram.yo().get('username')} escuchando...")
    try:
        telegram.poner_comandos()
    except telegram.TelegramError:
        pass
    while True:
        try:
            for u in telegram.updates(espera=30):
                try:
                    manejar_update(cx, u)
                except Exception:
                    traceback.print_exc()
        except telegram.TelegramError as ex:
            print(f"telegram: {ex}")
            time.sleep(10)
        except KeyboardInterrupt:
            print("\nchao")
            return
        if una_vuelta:
            return


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    accion = argv[0] if argv else 'escuchar'
    db.inicializar()
    cx = db.conectar()
    _asegurar_tabla_sug(cx)
    try:
        if accion == 'escuchar':
            escuchar(cx)
        elif accion == 'una-vuelta':
            escuchar(cx, una_vuelta=True)
        elif accion == 'preguntar':
            n = preguntar_pendientes(cx)
            print(f"  {n} preguntas mandadas")
        elif accion == 'resumen':
            chat = config.get('TELEGRAM_CHAT_ID_JUAN')
            cmd_resumen(cx, chat)
            print("  resumen mandado")
        else:
            print(__doc__)
            return 2
        return 0
    finally:
        cx.close()


if __name__ == '__main__':
    sys.exit(main())
