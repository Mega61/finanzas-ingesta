"""Acceso a la base de la cola. SQLite, en el volumen de datos.

Todo pasa por aqui para que el resto del codigo no sepa de SQL.
"""
import os
import sqlite3

import config

ESQUEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'esquema.sql')


def ruta():
    return config.ruta_datos('finanzas.db')


def conectar():
    cx = sqlite3.connect(ruta(), timeout=30)
    cx.row_factory = sqlite3.Row
    cx.execute('PRAGMA foreign_keys = ON')
    return cx


def inicializar(cx=None):
    """Crea el esquema si no existe. Es idempotente."""
    propio = cx is None
    cx = cx or conectar()
    with open(ESQUEMA, encoding='utf-8') as fh:
        cx.executescript(fh.read())
    cx.commit()
    if propio:
        cx.close()


# ------------------------------------------------------------------ usuarios

def usuario_upsert(cx, nombre, firefly_url, firefly_token, telegram_chat_id=None):
    """Un usuario por nombre. El token se guarda tal cual: la base vive en un
    volumen del servidor, no en el repo. Cifrarlo aqui daria una falsa
    sensacion de seguridad porque la llave tendria que estar al lado."""
    fila = cx.execute('SELECT id FROM usuarios WHERE nombre = ?', (nombre,)).fetchone()
    if fila:
        cx.execute("""UPDATE usuarios SET firefly_url = ?, firefly_token_enc = ?,
                      telegram_chat_id = COALESCE(?, telegram_chat_id)
                      WHERE id = ?""",
                   (firefly_url, firefly_token, telegram_chat_id, fila['id']))
        cx.commit()
        return fila['id']
    cur = cx.execute("""INSERT INTO usuarios (nombre, firefly_url, firefly_token_enc,
                        telegram_chat_id) VALUES (?, ?, ?, ?)""",
                     (nombre, firefly_url, firefly_token, telegram_chat_id))
    cx.commit()
    return cur.lastrowid


def buzon_upsert(cx, usuario_id, proveedor, direccion, secreto=None, imap_host=None):
    fila = cx.execute('SELECT id FROM buzones WHERE usuario_id = ? AND direccion = ?',
                      (usuario_id, direccion)).fetchone()
    if fila:
        return fila['id']
    cur = cx.execute("""INSERT INTO buzones (usuario_id, proveedor, direccion,
                        secreto_enc, imap_host) VALUES (?, ?, ?, ?, ?)""",
                     (usuario_id, proveedor, direccion, secreto, imap_host))
    cx.commit()
    return cur.lastrowid


def buzon_guardar_delta(cx, buzon_id, delta_link):
    cx.execute("""UPDATE buzones SET delta_link = ?, ultimo_sync = datetime('now'),
                  ultimo_error = NULL WHERE id = ?""", (delta_link, buzon_id))
    cx.commit()


def buzon_error(cx, buzon_id, mensaje):
    cx.execute('UPDATE buzones SET ultimo_error = ? WHERE id = ?',
               (str(mensaje)[:500], buzon_id))
    cx.commit()


# -------------------------------------------------------------- correo crudo

def correo_guardar(cx, buzon_id, message_id, remitente, asunto, fecha_correo, cuerpo):
    """Devuelve (id, era_nuevo). El UNIQUE(buzon, message_id) es el dedupe."""
    fila = cx.execute('SELECT id FROM correos_crudos WHERE buzon_id = ? AND message_id = ?',
                      (buzon_id, message_id)).fetchone()
    if fila:
        return fila['id'], False
    cur = cx.execute("""INSERT INTO correos_crudos
                        (buzon_id, message_id, remitente, asunto, fecha_correo, cuerpo)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                     (buzon_id, message_id, remitente, asunto, fecha_correo, cuerpo))
    cx.commit()
    return cur.lastrowid, True


def correos_sin_procesar(cx, limite=500):
    return cx.execute("""SELECT c.*, b.usuario_id FROM correos_crudos c
                         JOIN buzones b ON b.id = c.buzon_id
                         WHERE c.procesado_en IS NULL
                         ORDER BY c.fecha_correo LIMIT ?""", (limite,)).fetchall()


def correo_marcar_procesado(cx, correo_id):
    cx.execute("UPDATE correos_crudos SET procesado_en = datetime('now') WHERE id = ?",
               (correo_id,))


# ------------------------------------------------------------------ la cola

CAMPOS_PENDIENTE = (
    'correo_id', 'usuario_id', 'tipo', 'fecha', 'hora', 'moneda', 'valor',
    'instrumento', 'clase_instrumento', 'traslado_a', 'contraparte',
    'descripcion', 'plantilla', 'cuenta_firefly', 'cuenta_destino', 'categoria',
    'presupuesto', 'etiquetas', 'confianza', 'decidido_por', 'estado',
    'pregunta', 'external_id',
)


def pendiente_crear(cx, **kw):
    """Inserta un pendiente. Si el external_id ya existe, no hace nada y
    devuelve el id que ya estaba: eso es la idempotencia."""
    ext = kw.get('external_id')
    if ext:
        fila = cx.execute('SELECT id FROM pendientes WHERE external_id = ?',
                          (ext,)).fetchone()
        if fila:
            return fila['id'], False
    campos = [c for c in CAMPOS_PENDIENTE if c in kw]
    marcas = ', '.join('?' for _ in campos)
    cur = cx.execute(
        f"INSERT INTO pendientes ({', '.join(campos)}) VALUES ({marcas})",
        [kw[c] for c in campos])
    return cur.lastrowid, True


def pendiente_actualizar(cx, pendiente_id, **kw):
    if not kw:
        return
    sets = ', '.join(f'{k} = ?' for k in kw)
    cx.execute(f"UPDATE pendientes SET {sets}, actualizado_en = datetime('now') "
               f"WHERE id = ?", list(kw.values()) + [pendiente_id])


def pendientes_por_publicar(cx, limite=200):
    return cx.execute("""SELECT * FROM pendientes
                         WHERE estado IN ('nuevo', 'error')
                           AND cuenta_firefly IS NOT NULL
                         ORDER BY fecha, id LIMIT ?""", (limite,)).fetchall()


def marcar_preguntado(cx, pendiente_id):
    cx.execute("UPDATE pendientes SET preguntado_en = datetime('now') WHERE id = ?",
               (pendiente_id,))
    cx.commit()


def pendientes_abiertos(cx, usuario_id=None, limite=50):
    """Todo lo que tiene pregunta abierta, sin importar si ya se pregunto. Es
    lo que responde /pendientes cuando el usuario lo pide a proposito."""
    q = """SELECT p.*, u.telegram_chat_id FROM pendientes p
           JOIN usuarios u ON u.id = p.usuario_id
           WHERE p.pregunta IS NOT NULL
             AND p.estado IN ('nuevo', 'publicado', 'error') AND u.activo = 1"""
    if usuario_id:
        return cx.execute(q + ' AND p.usuario_id = ? ORDER BY p.fecha DESC LIMIT ?',
                          (usuario_id, limite)).fetchall()
    return cx.execute(q + ' ORDER BY p.fecha DESC LIMIT ?', (limite,)).fetchall()


def pendientes_por_preguntar(cx, usuario_id=None, limite=50):
    if usuario_id:
        return cx.execute("""SELECT * FROM v_por_preguntar WHERE usuario_id = ?
                             LIMIT ?""", (usuario_id, limite)).fetchall()
    return cx.execute('SELECT * FROM v_por_preguntar LIMIT ?', (limite,)).fetchall()


def resumen(cx, usuario_id=None):
    if usuario_id:
        return cx.execute('SELECT * FROM v_sin_conciliar WHERE usuario_id = ?',
                          (usuario_id,)).fetchall()
    return cx.execute('SELECT * FROM v_sin_conciliar').fetchall()


# ------------------------------------------------------------------- reglas

def regla_buscar(cx, usuario_id, comercio_normalizado):
    """La regla mas especifica que le aplique. Se prefiere el patron mas largo:
    'UBER RIDES' gana sobre 'UBER'."""
    filas = cx.execute("""SELECT * FROM reglas
                          WHERE (usuario_id = ? OR usuario_id IS NULL)
                            AND es_regex = 0
                          ORDER BY length(patron) DESC""", (usuario_id,)).fetchall()
    for r in filas:
        if r['patron'] and r['patron'] in comercio_normalizado:
            return r
    return None


def regla_guardar(cx, usuario_id, patron, cuenta_firefly=None, categoria=None,
                  presupuesto=None, etiquetas=None, origen='usuario',
                  direccion=None, aciertos=None):
    cx.execute("""INSERT INTO reglas (usuario_id, patron, cuenta_firefly, categoria,
                     presupuesto, etiquetas, origen, direccion, aciertos)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, 0))
                  ON CONFLICT (usuario_id, patron) DO UPDATE SET
                     cuenta_firefly = COALESCE(excluded.cuenta_firefly, cuenta_firefly),
                     categoria      = COALESCE(excluded.categoria, categoria),
                     presupuesto    = COALESCE(excluded.presupuesto, presupuesto),
                     etiquetas      = COALESCE(excluded.etiquetas, etiquetas),
                     direccion      = COALESCE(excluded.direccion, direccion),
                     aciertos       = MAX(reglas.aciertos, excluded.aciertos)""",
               (usuario_id, patron, cuenta_firefly, categoria, presupuesto,
                etiquetas, origen, direccion, aciertos))
    cx.commit()


def regla_acierto(cx, regla_id):
    cx.execute('UPDATE reglas SET aciertos = aciertos + 1 WHERE id = ?', (regla_id,))


# ----------------------------------------------------------------- bitacora

def bitacora(cx, accion, usuario_id=None, pendiente_id=None, firefly_id=None,
             payload=None, respuesta=None, ok=True):
    cx.execute("""INSERT INTO bitacora (usuario_id, pendiente_id, accion, firefly_id,
                     payload, respuesta, ok) VALUES (?, ?, ?, ?, ?, ?, ?)""",
               (usuario_id, pendiente_id, accion, firefly_id,
                str(payload)[:4000] if payload else None,
                str(respuesta)[:4000] if respuesta else None, 1 if ok else 0))
    cx.commit()


if __name__ == '__main__':
    inicializar()
    cx = conectar()
    tablas = [r[0] for r in cx.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print(f"base: {ruta()}")
    print(f"tablas: {', '.join(tablas)}")
    for t in tablas:
        n = cx.execute(f'SELECT count(*) FROM {t}').fetchone()[0]
        print(f"  {t:16} {n:6d}")
