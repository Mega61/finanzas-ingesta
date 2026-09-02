"""Simula una conversacion con el bot, sin tocar Firefly ni Telegram.

    python herramientas/simular_chat.py "cual fue la ultima?" "y la anterior"
    python herramientas/simular_chat.py --sin-ia "cambia la ultima a Mercado"
    python herramientas/simular_chat.py --guion casos.txt

Cada argumento es un mensaje, y varios en un comando son UNA conversacion con
memoria. Hay tres formas:

    es el costo del domicilio    un mensaje normal
    !mv:1441:0                   toca ese boton
    >9002 es una galleta         RESPONDE al mensaje 9002

El numero de mensaje lo imprime el propio simulador debajo de cada respuesta.
El `!` solo cuenta si lo que sigue tiene forma de callback, asi que «!!!» se
manda como texto.

Existe para poder buscar bugs sin escribirle al bot de verdad. Telegram y
Firefly son dobles: se ve lo que el bot RESPONDERIA y lo que ESCRIBIRIA, pero
no se manda ni se guarda nada. Gemini SI es real, porque justamente lo que hay
que probar es si entiende.

Los datos son los de un dia real: cinco movimientos recientes, tres con
pregunta abierta, y dos productos de supermercado sin clasificar. Las
respuestas del bot salen con `BOT>` y las escrituras con `>> FIREFLY`.

Salida con `--json` para poder revisarla en bloque.
"""

import argparse
import json
import re
import sqlite3
import types
from pathlib import Path

from finanzas.adaptadores import db, ia
from finanzas.adaptadores.almacen import Almacen
from finanzas.aplicacion import movimientos, presupuestos
from finanzas.entrada import bot

CHAT = '555'

# Un dia real: los movimientos que de verdad estaban en Firefly.
MOVIMIENTOS = [
    # id, monto, comercio, categoria, presupuesto, etiquetas
    ('1459', -185500, 'KAYBU SAS', 'Regalos', 'Vivir', ['sin-confirmar']),
    ('1458', -197800, 'MERCADO PAGO LIMITAD', 'Compras', 'Antojos', ['sin-confirmar']),
    ('1457', -194800, 'MERCADO PAGO LIMITAD', 'Compras', 'Antojos', ['sin-confirmar']),
    ('1456', -21040, 'Tienda D1', 'Mercado', 'Esencial', ['sin-confirmar']),
    ('1455', -212000, 'Etre', 'Compras Casa', 'Antojos', ['sin-confirmar']),
    ('1451', 113943, 'RAPPICARD BLACK', 'Abono', None, []),
    ('1441', -151495, 'Tierragro', 'Gato', 'Esencial', ['sin-confirmar']),
    ('1440', -457000, 'Zona Fit', 'Suplementos', None, ['sin-confirmar']),
    ('1439', -63000, 'Google', 'GBS Infra', None, ['sin-confirmar']),
    ('1438', -33467, 'Uber', 'Transporte Aplicacion', 'Vivir', []),
]

# Los que el bot te pregunto y siguen abiertos.
ABIERTAS = [
    ('1441', 'MERCADO PAGO*TIERRAG', -151495, 'Gato'),
    ('1440', 'MERCADO PAGO*ZONAFIT', -457000, 'Suplementos'),
    ('1439', 'GOOGLE *Workspace_go', -63000, 'GBS Infra'),
]

# Productos de factura sin clasificar.
PRODUCTOS = [
    ('890900608', '9900001', 'FLETES GRAVADO'),
    ('890900608', '1234567', 'GTA RELLENA CHOC BIS'),
]

CATEGORIAS = [
    'Abono', 'Articulos Personales', 'Cafe', 'Comida de calle', 'Compras',
    'Compras Casa', 'Compras Tecnologia', 'Cuidado personal', 'Domicilio',
    'Facturas', 'GBS Infra', 'Gato', 'Gimnasio', 'Juegos', 'Mecato', 'Mercado',
    'Moto', 'Regalos', 'Restaurante', 'Ropa', 'Salidas', 'Salud', 'Suplementos',
    'Telefono', 'Transporte Aplicacion', 'Viajes',
]
PRESUPUESTOS = ['Esencial', 'Vivir', 'Antojos', 'Imprevistos Esenciales',
                'Costos Financieros']
ETIQUETAS = ['Didi', 'Alimentos', 'Cena', 'Uber', 'Almuerzo', 'No alimentos',
             'parqueadero', 'Gasolina', 'Ropa', 'reembolsable']


class Registro:
    """Lo que el bot dijo y lo que habria escrito."""

    def __init__(self):
        self.dijo: list[dict] = []
        self.escribio: list[dict] = []
        self.avisos: list[str] = []
        self.borrados: list[str] = []
        self.errores: list[str] = []


def _sin_html(t):
    return re.sub(r'<[^>]+>', '', t or '')


# Las que Telegram acepta en modo HTML. Cualquier otra cosa entre < > hace que
# rechace el mensaje COMPLETO y el bot se queda mudo.
_PERMITIDAS = ('b', 'i', 'u', 's', 'a', 'code', 'pre', 'tg-spoiler', 'blockquote')


def _html_malo(texto):
    """Por que Telegram rechazaria este mensaje, o None si esta bien.

    El simulador limpiaba el marcado antes de imprimir, asi que este bug --
    el peor de los de mudez -- era invisible con la herramienta.
    """
    pila = []
    for m in re.finditer(r'<(/?)([^\s>/]*)([^>]*)>', texto or ''):
        cierra, nombre = m.group(1), m.group(2).lower()
        if nombre not in _PERMITIDAS:
            return f'etiqueta que Telegram no conoce: <{cierra}{nombre}>'
        if cierra:
            if not pila or pila.pop() != nombre:
                return f'</{nombre}> sin abrir'
        elif nombre != 'br':
            pila.append(nombre)
    if pila:
        return f'<{pila[-1]}> sin cerrar'
    # Un < suelto que no forma etiqueta: «Cafe & Bar <3» es el caso real.
    suelto = re.sub(r'<[^>]*>', '', texto or '')
    if '<' in suelto:
        return 'hay un « < » sin escapar (¿un nombre con <3?)'
    return None


def montar(reg, con_ia=True):
    """La base, Firefly y Telegram de mentiras. Devuelve la conexion."""
    cx = sqlite3.connect(':memory:')
    cx.row_factory = sqlite3.Row
    cx.execute('PRAGMA foreign_keys = ON')
    alm = Almacen(cx)
    alm.inicializar(db.ESQUEMA)
    uid = alm.guardar_usuario('Juan', 'https://f', 'tok', CHAT)
    bid = alm.guardar_buzon(uid, 'graph', 'j@e.com')
    cid, _ = alm.guardar_correo(bid, '<m>', 'banco', 'Alerta', '2026-09-02', 'x')

    # las preguntas abiertas
    for i, (ffid, nombre, valor, cat) in enumerate(ABIERTAS):
        pid, _ = alm.crear_pendiente(
            correo_id=cid, usuario_id=uid, tipo='compra_tarjeta',
            fecha='2026-09-02', valor=float(valor), moneda='COP',
            contraparte=nombre, descripcion=nombre, categoria=cat,
            cuenta_firefly='AMEX PLATINO', estado='publicado',
            pregunta='categoria', external_id=f'bc-sim-{i}')
        alm.actualizar_pendiente(pid, firefly_id=ffid)
        alm.marcar_preguntado(pid)

    for nit, cod, desc in PRODUCTOS:
        cx.execute(
            'INSERT INTO catalogo (nit, codigo, descripcion, grupo, categoria,'
            " tipo) VALUES (?, ?, ?, 'Sin clasificar', 'Sin clasificar',"
            " 'Sin clasificar')", (nit, cod, desc))
    cx.commit()

    # ------------------------------------------------------------- Firefly
    tx = {}
    for i, monto, comercio, cat, pres, etqs in MOVIMIENTOS:
        tx[i] = {
            'id': i, 'fecha': '2026-09-02', 'valor': float(monto), 'moneda': 'COP',
            'descripcion': comercio, 'categoria': cat, 'presupuesto': pres,
            'origen': 'AMEX PLATINO' if monto < 0 else comercio,
            'destino': comercio if monto < 0 else 'Bancolombia',
            'etiquetas': list(etqs), 'tipo': 'withdrawal' if monto < 0 else 'deposit',
            'notas': '', 'partes': 1,
        }

    def ultimos(limite=15, dias=35):
        return sorted(tx.values(), key=lambda m: -int(m['id']))[:limite]

    def editar(tx_id, **cambios):
        m = tx.get(str(tx_id))
        if m is None:
            raise ValueError(f'el movimiento {tx_id} no existe en Firefly')
        reg.escribio.append({'id': str(tx_id), 'cambios': dict(cambios)})
        if cambios.get('categoria'):
            m['categoria'] = cambios['categoria']
        if cambios.get('presupuesto'):
            m['presupuesto'] = cambios['presupuesto']
        if cambios.get('comercio'):
            m['destino'] = cambios['comercio']
        if cambios.get('descripcion'):
            m['descripcion'] = cambios['descripcion']
        for e in cambios.get('etiquetas') or []:
            if e not in m['etiquetas']:
                m['etiquetas'].append(e)
        for e in cambios.get('quitar_etiquetas') or []:
            if e in m['etiquetas']:
                m['etiquetas'].remove(e)
        return m

    def borrar(tx_id):
        # Firefly contesta 404 con un id que no existe. Devolver True siempre
        # hacia que un doble toque en «borrar» dijera «Borrado» dos veces.
        if str(tx_id) not in tx:
            raise ValueError(f'el movimiento {tx_id} no existe en Firefly')
        reg.borrados.append(str(tx_id))
        tx.pop(str(tx_id), None)
        return True

    movimientos.ultimos = ultimos
    movimientos.uno = lambda t: tx.get(str(t))
    movimientos.editar = editar
    movimientos.borrar = borrar
    movimientos.confirmar = lambda t: True
    movimientos.editar_varios = lambda ids, **c: [
        ({'id': str(i), 'movimiento': editar(i, **c)}
         if str(i) in tx else {'id': str(i), 'error': 'no existe'})
        for i in ids
    ]
    movimientos.buscar = lambda consulta=None, dias=35, limite=20, categoria=None: [
        m for m in ultimos(999)
        if not consulta
        or consulta.lower() in (m['destino'] or '').lower()
        or consulta.lower() in (m['categoria'] or '').lower()
    ][:limite]
    movimientos.categorias = lambda d=None: CATEGORIAS
    movimientos.etiquetas_mas_usadas = lambda limite=24: ETIQUETAS[:limite]
    presupuestos.nombres_activos = lambda: PRESUPUESTOS
    presupuestos.mapa_categoria = lambda **k: {
        'Mercado': {'presupuesto': 'Esencial', 'seguro': True, 'reparto': {}},
        'Gato': {'presupuesto': 'Esencial', 'seguro': True, 'reparto': {}},
    }
    presupuestos.estado = lambda cuando=None: [
        {'nombre': b, 'limite': 1_000_000, 'gastado': 400_000, 'queda': 600_000,
         'pct': 40.0} for b in PRESUPUESTOS
    ]
    presupuestos.revienta = lambda *a, **k: None
    # Registra: `aplicar_respuesta` publica por aqui, no por `movimientos`, y
    # sin esto esas escrituras eran INVISIBLES en la salida.
    def split_falso(tx_id, **campos):
        reg.escribio.append({'id': str(tx_id), 'cambios': dict(campos),
                             'via': 'split'})
        return True

    bot.firefly.actualizar_split = split_falso
    # Los comercios salen de aqui. Devolviendo [] siempre iban vacios, que es
    # justo el caso que ocultaba un esquema invalido.
    bot.firefly.get_all = lambda ruta: (
        [{'attributes': {'name': c}} for c in CATEGORIAS]
        if '/categories' in ruta
        else [
            {'attributes': {'name': m[2]}}
            for m in MOVIMIENTOS
        ]
        if '/accounts' in ruta
        else []
    )

    # ------------------------------------------------------------ Telegram
    class TelegramFalso:
        TelegramError = Exception
        _id = 9000

        def enviar(self, chat, texto, botones=None, modo='HTML'):
            TelegramFalso._id += 1
            reg.dijo.append({
                'texto': _sin_html(texto),
                'crudo': texto,
                'html_malo': _html_malo(texto),
                'botones': [[t for t, _ in fila] for fila in (botones or [])],
                'datos': [d for fila in (botones or []) for _, d in fila],
                'mensaje_id': TelegramFalso._id,
            })
            return {'message_id': TelegramFalso._id}

        def editar(self, chat, message_id, texto, modo='HTML'):
            reg.dijo.append({'texto': _sin_html(texto), 'crudo': texto,
                             'html_malo': _html_malo(texto), 'botones': [],
                             'datos': [], 'editado': message_id})
            return {'message_id': message_id}

        def responder_callback(self, cq_id, texto=None, alerta=False):
            reg.avisos.append(texto or '')

        def poner_comandos(self, comandos):
            return {}

    bot.telegram = TelegramFalso()
    bot.config = types.SimpleNamespace(
        get=lambda k, d=None: CHAT if 'CHAT_ID' in k else d)

    # El asesor de verdad necesita saldos de Firefly; se simula su respuesta
    # para que se pueda ver A DONDE fue el mensaje sin depender de la red.
    def asesor_falso(cx_, chat, txt):
        reg.dijo.append({'texto': f'[ASESOR] {txt}', 'botones': [], 'datos': [],
                         'html_malo': None})
        # El historial lo llena el asesor de verdad. Sin esto el plan del
        # modelo se probaba SIEMPRE sin historial de conversacion.
        hist = bot.HISTORIAL.setdefault(str(chat), [])
        hist.append(('usuario', txt))
        hist.append(('asesor', '[respuesta simulada del asesor]'))
        del hist[: -bot.MAX_HISTORIAL]

    bot._consultar_asesor = asesor_falso

    if not con_ia:
        ia.disponible = lambda: False
    return cx


def _update(msg):
    """El update de Telegram que corresponde a esa linea del guion.

      !accion:pid:idx   toca un boton
      >123 texto        responde AL MENSAJE 123 (es un camino aparte del bot)
      cualquier otra    un mensaje normal

    El `!` solo cuenta si lo que sigue tiene forma de callback: antes
    «!!!» -- que es algo que un usuario escribe -- se interpretaba como un
    boton y no habia forma de probarlo.
    """
    if re.match(r'^![a-zA-Z]{1,3}:-?\d+:-?\d+$', msg):
        return {
            'callback_query': {
                'id': 'q',
                'data': msg[1:],
                'message': {'message_id': 1, 'chat': {'id': CHAT}},
            }
        }
    m = re.match(r'^>(\d+)\s+(.*)$', msg, re.S)
    cuerpo = {'message_id': 5, 'chat': {'id': CHAT}, 'text': m.group(2) if m else msg}
    if m:
        cuerpo['reply_to_message'] = {'message_id': int(m.group(1))}
    return {'message': cuerpo}


def correr(mensajes, con_ia=True, como_json=False):
    reg = Registro()
    cx = montar(reg, con_ia=con_ia)
    salida = []
    for msg in mensajes:
        antes_dijo, antes_esc = len(reg.dijo), len(reg.escribio)
        antes_av = len(reg.avisos)
        paso = {'tu': msg}
        try:
            bot.manejar_update(cx, _update(msg))
        except Exception as ex:
            paso['EXCEPCION'] = f'{type(ex).__name__}: {ex}'
            reg.errores.append(paso['EXCEPCION'])
        nuevos = reg.dijo[antes_dijo:]
        paso['bot'] = [d['texto'] for d in nuevos]
        paso['botones'] = [d['datos'] for d in nuevos if d['datos']]
        paso['escribio'] = reg.escribio[antes_esc:]
        # Los avisos son los globitos de los botones. Sin mostrarlos, un toque
        # que solo contesta el globito salia como «no respondio nada», y eso
        # eran falsos positivos.
        paso['avisos'] = [a for a in reg.avisos[antes_av:] if a]
        paso['html_malo'] = [d['html_malo'] for d in nuevos if d.get('html_malo')]
        paso['ids'] = [d['mensaje_id'] for d in nuevos if d.get('mensaje_id')]
        salida.append(paso)

    if como_json:
        print(json.dumps({'pasos': salida, 'borrados': reg.borrados,
                          'errores': reg.errores, 'avisos': reg.avisos},
                         ensure_ascii=False, indent=1))
        return salida

    for paso in salida:
        print('=' * 78)
        print(f'TU > {paso["tu"]}')
        if paso.get('EXCEPCION'):
            print(f'  *** EXCEPCION: {paso["EXCEPCION"]}')
        for t in paso['bot']:
            print('BOT> ' + t.replace('\n', '\n     '))
        for b in paso['botones']:
            print('     botones: ' + ' '.join(b))
        for d in paso.get('ids') or []:
            print(f'     (mensaje #{d} — respóndele con «>{d} tu texto»)')
        for a in paso['avisos']:
            print(f'     (globito: {a})')
        for m in paso['html_malo']:
            print(f'  *** HTML QUE TELEGRAM RECHAZARIA: {m}')
        for e in paso['escribio']:
            via = f' [{e["via"]}]' if e.get('via') else ''
            print(f'  >> FIREFLY {e["id"]}{via}: {e["cambios"]}')
        # Un globito SI es una respuesta. Contarlo como silencio daba falsos
        # positivos en la mayoria de los botones invalidos.
        if not paso['bot'] and not paso['avisos'] and not paso.get('EXCEPCION'):
            print('  *** EL BOT NO RESPONDIO NADA')
        print()
    if reg.borrados:
        print(f'borrados de Firefly: {reg.borrados}')
    return salida


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='python herramientas/simular_chat.py',
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('mensajes', nargs='*', help='lo que le escribes al bot')
    ap.add_argument('--sin-ia', action='store_true',
                    help='apaga Gemini para probar el respaldo por patrones')
    ap.add_argument('--guion', help='archivo con un mensaje por linea')
    ap.add_argument('--json', action='store_true', help='salida en JSON')
    a = ap.parse_args(argv)

    mensajes = list(a.mensajes)
    if a.guion:
        mensajes += [
            ln.strip() for ln in Path(a.guion).read_text(encoding='utf-8').splitlines()
            if ln.strip() and not ln.startswith('#')
        ]
    if not mensajes:
        ap.error('dame al menos un mensaje, o un --guion')
    correr(mensajes, con_ia=not a.sin_ia, como_json=a.json)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
