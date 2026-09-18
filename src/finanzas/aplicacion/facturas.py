"""Facturas de supermercado: del XML crudo al dashboard de mercado.

El recorrido completo, y por que cada paso esta separado:

    correo (Graph)  ->  facturas_crudas   el XML tal cual, nunca se borra
                    ->  facturas          cabecera + lineas parseadas
                    ->  catalogo          (nit, codigo) -> grupo + categoria
                    ->  Postgres          lo que lee Metabase

`parsear` y `clasificar` son pasos aparte a proposito: el catalogo cambia
cuando contestas una pregunta por Telegram, y entonces hay que reclasificar
sin volver a parsear. Y como la categoria vive en `catalogo` y no copiada en
la linea, corregir un producto arregla toda su historia de una.

Esto NO toca Firefly. Una factura de supermercado no es un movimiento
bancario: el movimiento ya entro por la alerta del banco. La factura dice QUE
compraste, no que pagaste — cruzarlas duplicaria el gasto.
"""

from __future__ import annotations

import io
import zipfile

from finanzas.adaptadores import db
from finanzas.aplicacion import catalogo as cat
from finanzas.parsers import factura_dian as fd


def xml_de_zip(crudo: bytes) -> list[tuple[str, str]]:
    """Los XML de adentro de un ZIP, como (nombre, texto)."""
    try:
        z = zipfile.ZipFile(io.BytesIO(crudo))
    except zipfile.BadZipFile:
        return []
    return [
        (n, z.read(n).decode('utf-8', 'replace'))
        for n in z.namelist()
        if n.lower().endswith('.xml')
    ]


def parsear(cx, limite: int = 500, desde: str | None = None) -> dict:
    """facturas_crudas -> facturas + lineas. Devuelve el conteo.

    `desde` descarta lo anterior a esa fecha sin marcarlo como error: el
    archivo del buzon llega hasta 2023 y para el dashboard solo interesa
    2025 en adelante.
    """
    n_ok = n_rep = n_viejas = n_mal = 0
    for fila in db.facturas_sin_parsear(cx, limite):
        try:
            f = fd.parsear(fila['xml'])
        except Exception:
            # Se marca parseada igual: un XML roto no se arregla solo, y
            # dejarlo pendiente hace que cada pasada lo reintente para siempre.
            db.factura_marcar_parseada(cx, fila['id'])
            n_mal += 1
            continue
        if desde and f.fecha < desde:
            db.factura_marcar_parseada(cx, fila['id'])
            n_viejas += 1
            continue
        if db.factura_guardar(cx, fila['id'], f, f.lineas):
            n_ok += 1
        else:
            n_rep += 1
        db.factura_marcar_parseada(cx, fila['id'])
    return {'nuevas': n_ok, 'repetidas': n_rep, 'viejas': n_viejas, 'ilegibles': n_mal}


def clasificar(cx, solo_nuevos: bool = True) -> dict:
    """Pone cada producto de las lineas en el catalogo.

    Con `solo_nuevos` no vuelve a mirar los que ya tienen clasificacion; en
    False reclasifica todo, que es lo que hay que hacer cuando cambian las
    reglas. Ni en un caso ni en el otro se pisa un `origen = 'usuario'`.
    """
    filas = db.productos_de_lineas(cx)

    n_nuevos = n_sin = 0
    for r in filas:
        if solo_nuevos and db.catalogo_ver(cx, r['nit'], r['codigo']):
            continue
        tipo, grupo, categoria, origen = cat.clasificar(
            r['nit'], r['codigo'], r['descripcion'] or '', r['iva']
        )
        db.catalogo_upsert(
            cx,
            r['nit'],
            r['codigo'],
            r['descripcion'],
            tipo,
            grupo,
            categoria,
            origen,
        )
        n_nuevos += 1
        if grupo == 'Sin clasificar':
            n_sin += 1
    return {'clasificados': n_nuevos, 'sin_resolver': n_sin}


def responder(cx, nit: str, codigo: str, grupo: str, categoria: str) -> None:
    """La respuesta del bot. Queda como 'usuario' y ya nada la pisa.

    Como la vista de Metabase lee la categoria del catalogo con un JOIN,
    contestar una vez reescribe todas las compras pasadas de ese producto.
    """
    db.catalogo_responder(cx, nit, codigo, cat.tipo_de(grupo), grupo, categoria)


# --------------------------------------------------------------- exportar
# Postgres no tiene credenciales en el contenedor: el stack solo lleva las de
# Firefly, Graph, Telegram y Gemini. Asi que la sincronizacion sale como CSV y
# se carga con metabase/cargar_csv.py, que es el camino que ya usa
# finanzas.movimientos. Si algun dia se agrega el DSN de Postgres al stack,
# esto se reemplaza por un UPSERT y no cambia nada mas.

CSV_FACTURA = (
    'cufe',
    'nit',
    'proveedor',
    'numero',
    'tipo',
    'signo',
    'fecha',
    'hora',
    'sede',
    'moneda',
    'subtotal',
    'descuento',
    'total',
    'medios_pago',
    'puntos_redimidos',
    'ahorro',
    'pagada_con_puntos',
)
CSV_LINEA = (
    'cufe',
    'n',
    'nit',
    'codigo',
    'descripcion',
    'cantidad',
    'unidad',
    'precio_unitario',
    'descuento',
    'iva_pct',
    'total',
    'signo',
    'fecha',
)
CSV_PRODUCTO = (
    'nit',
    'codigo',
    'descripcion',
    'tipo',
    'grupo',
    'categoria',
    'origen',
)


def _fila_factura(r):
    return [
        r['cufe'],
        r['nit'],
        r['proveedor'],
        r['numero'],
        r['tipo'],
        r['signo'],
        r['fecha'],
        r['hora'],
        r['sede'],
        r['moneda'],
        r['subtotal'],
        r['descuento'],
        r['total'],
        r['medios_pago'],
        r['puntos_redimidos'],
        r['ahorro'],
        'true' if r['pagada_con_puntos'] else 'false',
    ]


def _fila_linea(r):
    return [r[c] for c in CSV_LINEA]


def _fila_producto(r):
    return [r[c] for c in CSV_PRODUCTO]


# Las tres tablas del dashboard, definidas UNA vez. De aqui salen tanto los
# CSV como la carga directa a Postgres: si vivieran por separado, arreglar una
# columna en un camino y no en el otro daria dos verdades distintas, que es
# justo lo que veniamos sufriendo.
CONJUNTOS = (
    ('factura.csv', 'finanzas.factura', CSV_FACTURA, 'facturas_todas', _fila_factura),
    (
        'factura_linea.csv',
        'finanzas.factura_linea',
        CSV_LINEA,
        'lineas_todas',
        _fila_linea,
    ),
    (
        'producto.csv',
        'finanzas.producto',
        CSV_PRODUCTO,
        'catalogo_todo',
        _fila_producto,
    ),
)


def exportar(cx, carpeta: str) -> dict:
    """Deja los tres CSV que carga metabase/cargar_csv.py."""
    import csv
    import os

    os.makedirs(carpeta, exist_ok=True)
    cuenta = {}
    for nombre, _tabla, columnas, consulta, adaptar in CONJUNTOS:
        ruta = os.path.join(carpeta, nombre)
        with open(ruta, 'w', newline='', encoding='utf-8') as fh:
            w = csv.writer(fh)
            w.writerow(columnas)
            n = 0
            for r in getattr(db, consulta)(cx):
                w.writerow(adaptar(r))
                n += 1
        cuenta[nombre] = n
    return cuenta


def cargar_a_postgres(cx, ensayo: bool = False) -> dict:
    """Las tres tablas a Postgres, cada una en su propia transaccion.

    Con `ensayo` no escribe: solo dice cuantas filas hay hoy alla y cuantas
    irian, que es lo que se quiere ver antes de tocar la base de Firefly.

    Cada tabla va aparte a proposito. Son independientes entre si, y si
    `producto` falla no hay razon para perder tambien la carga de `factura`.
    """
    from finanzas.adaptadores import postgres

    if not postgres.disponible():
        return {'omitido': 'sin POSTGRES_DSN'}

    cuenta = {}
    for _nombre, tabla, columnas, consulta, adaptar in CONJUNTOS:
        filas = [adaptar(r) for r in getattr(db, consulta)(cx)]
        if ensayo:
            cuenta[tabla] = {'ahora': postgres.conteo(tabla), 'irian': len(filas)}
        else:
            cuenta[tabla] = postgres.cargar(tabla, columnas, filas)
    return cuenta
