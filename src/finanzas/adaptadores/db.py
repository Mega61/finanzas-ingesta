"""Compatibilidad. El SQL de verdad vive en finanzas.adaptadores.almacen.

Este modulo tenia 29 consultas propias, mientras otras 40 andaban repartidas en
seis archivos mas. Ahora todas viven en un solo lugar y esto es solo una capa
delgada, para no reescribir de golpe a todos los que ya importaban `db`.

A medida que los modulos se muevan a src/ pueden usar `Almacen` directamente y
esta capa se puede borrar.
"""

import sqlite3
from importlib import resources

from finanzas import config

# CAMPOS_PENDIENTE se reexporta: hay codigo que lo lee como db.CAMPOS_PENDIENTE.
from finanzas.adaptadores.almacen import CAMPOS_PENDIENTE, Almacen  # noqa: F401

# El esquema es un dato del paquete: se localiza con importlib.resources, no
# con dirname(__file__), que deja de servir si el paquete se instala en un zip.
ESQUEMA = resources.files('finanzas') / 'esquema.sql'


def ruta():
    return config.ruta_datos('finanzas.db')


def conectar():
    cx = sqlite3.connect(ruta(), timeout=30)
    cx.row_factory = sqlite3.Row
    cx.execute('PRAGMA foreign_keys = ON')
    return cx


def almacen(cx):
    """El almacen para una conexion ya abierta."""
    return Almacen(cx)


def inicializar(cx=None):
    propio = cx is None
    cx = cx or conectar()
    Almacen(cx).inicializar(ESQUEMA)
    if propio:
        cx.close()


# ------------------------------------------------------------------ usuarios


def usuario_upsert(cx, nombre, firefly_url, firefly_token, telegram_chat_id=None):
    return Almacen(cx).guardar_usuario(
        nombre, firefly_url, firefly_token, telegram_chat_id
    )


def buzon_upsert(cx, usuario_id, proveedor, direccion, secreto=None, imap_host=None):
    return Almacen(cx).guardar_buzon(
        usuario_id, proveedor, direccion, secreto, imap_host
    )


def buzon_guardar_delta(cx, buzon_id, delta_link=None):
    """El delta_link de Graph no se usa todavia; se conserva la firma."""
    Almacen(cx).marcar_sync(buzon_id)


def buzon_error(cx, buzon_id, mensaje):
    Almacen(cx).marcar_error_buzon(buzon_id, mensaje)


# -------------------------------------------------------------- correo crudo


def correo_guardar(cx, buzon_id, message_id, remitente, asunto, fecha_correo, cuerpo):
    return Almacen(cx).guardar_correo(
        buzon_id, message_id, remitente, asunto, fecha_correo, cuerpo
    )


def correos_sin_procesar(cx, limite=500):
    return Almacen(cx).correos_sin_procesar(limite)


def correo_marcar_procesado(cx, correo_id):
    Almacen(cx).marcar_correo_procesado(correo_id)


# ------------------------------------------------------------------- la cola


def pendiente_crear(cx, **kw):
    return Almacen(cx).crear_pendiente(**kw)


def pendiente_actualizar(cx, pendiente_id, **kw):
    Almacen(cx).actualizar_pendiente(pendiente_id, **kw)


def pendientes_por_publicar(cx, limite=200):
    return Almacen(cx).pendientes_por_publicar(limite)


def pendientes_por_preguntar(cx, usuario_id=None, limite=50):
    return Almacen(cx).pendientes_por_preguntar(limite)


def pendientes_abiertos(cx, usuario_id=None, limite=50):
    """Todo lo que tiene pregunta abierta, sin importar si ya se pregunto."""
    return Almacen(cx).pendientes_por_preguntar(limite)


def marcar_preguntado(cx, pendiente_id):
    Almacen(cx).marcar_preguntado(pendiente_id)


def resumen(cx, usuario_id=None):
    return Almacen(cx).resumen(usuario_id)


# -------------------------------------------------------------------- reglas


def regla_guardar(
    cx,
    usuario_id,
    patron,
    cuenta_firefly=None,
    categoria=None,
    presupuesto=None,
    etiquetas=None,
    origen='usuario',
    direccion=None,
    aciertos=None,
):
    Almacen(cx).guardar_regla(
        usuario_id,
        patron,
        cuenta_firefly,
        categoria,
        presupuesto,
        etiquetas,
        origen,
        direccion,
        aciertos,
    )


def regla_buscar(cx, usuario_id, comercio_normalizado):
    """La regla mas especifica que aplique. Se prefiere el patron mas largo:
    'UBER RIDES' le gana a 'UBER'."""
    filas = Almacen(cx).reglas(usuario_id)
    for r in sorted(filas, key=lambda x: -len(x['patron'])):
        if r['patron'] and r['patron'] in comercio_normalizado:
            return r
    return None


def regla_acierto(cx, regla_id):
    cx.execute('UPDATE reglas SET aciertos = aciertos + 1 WHERE id = ?', (regla_id,))


# ----------------------------------------------------------------- bitacora


def bitacora(
    cx,
    accion,
    usuario_id=None,
    pendiente_id=None,
    firefly_id=None,
    payload=None,
    respuesta=None,
    ok=True,
):
    Almacen(cx).anotar(
        accion, usuario_id, pendiente_id, firefly_id, payload, respuesta, ok
    )


# ------------------------------------------------------ facturas de mercado


def factura_cruda_guardar(cx, correo_id, archivo, xml):
    return Almacen(cx).guardar_factura_cruda(correo_id, archivo, xml)


def facturas_sin_parsear(cx, limite=500):
    return Almacen(cx).facturas_sin_parsear(limite)


def factura_marcar_parseada(cx, cruda_id):
    Almacen(cx).marcar_factura_parseada(cruda_id)


def factura_guardar(cx, cruda_id, f, lineas):
    return Almacen(cx).guardar_factura(cruda_id, f, lineas)


def catalogo_ver(cx, nit, codigo):
    return Almacen(cx).catalogo_ver(nit, codigo)


def catalogo_upsert(cx, nit, codigo, descripcion, tipo, grupo, categoria, origen):
    Almacen(cx).catalogo_upsert(
        nit, codigo, descripcion, tipo, grupo, categoria, origen
    )


def catalogo_por_preguntar(cx, limite=5):
    return Almacen(cx).catalogo_por_preguntar(limite)


def catalogo_marcar_preguntado(cx, nit, codigo):
    Almacen(cx).catalogo_marcar_preguntado(nit, codigo)


def resumen_facturas(cx):
    return Almacen(cx).resumen_facturas()


def productos_de_lineas(cx):
    return Almacen(cx).productos_de_lineas()


def catalogo_responder(cx, nit, codigo, tipo, grupo, categoria):
    Almacen(cx).catalogo_responder(nit, codigo, tipo, grupo, categoria)


def facturas_todas(cx):
    return Almacen(cx).facturas_todas()


def lineas_todas(cx):
    return Almacen(cx).lineas_todas()


def catalogo_todo(cx):
    return Almacen(cx).catalogo_todo()


def primer_usuario(cx):
    return Almacen(cx).primer_usuario()


def catalogo_por_id(cx, cat_id):
    return Almacen(cx).catalogo_por_id(cat_id)


def catalogo_responder_id(cx, cat_id, tipo, grupo, categoria):
    Almacen(cx).catalogo_responder_id(cat_id, tipo, grupo, categoria)


def catalogo_marcar_preguntado_id(cx, cat_id):
    Almacen(cx).catalogo_marcar_preguntado_id(cat_id)
