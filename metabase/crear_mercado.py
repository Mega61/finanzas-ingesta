"""Crea el dashboard "Mercado" con sus tarjetas.

Se puede correr varias veces: actualiza las tarjetas que ya existen y
reemplaza el contenido del dashboard en vez de duplicarlo.

  python metabase/crear_mercado.py

Que responde el dashboard, en este orden:

  1. cuanto me gaste          los cuatro numeros de arriba y la serie
  2. en que se fue            categoria por categoria, comida y hogar aparte
  3. que compre               los productos de cada categoria, con unidades
  4. que se salio de lo normal el mes contra su propio promedio

Tres decisiones que valen mas que las tarjetas:

**Tecnologia y electrodomesticos NO entran.** Se mira `finanzas.v_canasta`, que
los deja por fuera. El reloj de 1,2 millones y la licuadora no son la compra
del super; mezclarlos hacia que un mes normal pareciera el doble, y ademas casi
todos entraron por redencion de puntos. Hay una tarjeta al final que los lista
para que el numero se pueda rastrear, pero fuera de los totales.

**Comida y hogar van separados y con el mismo tratamiento.** Cada bloque tiene
su grafico de categorias y su tabla de productos. Son la misma pregunta hecha
dos veces, y antes solo se podia contestar para comida.

**TODAS las tarjetas responden al filtro de fecha.** La CTE `sel` saca el mes
de la seleccion y cada serie se calcula RELATIVA a el: parado en marzo la serie
va de abril del ano pasado a marzo, y el comparativo dice "marzo contra el
promedio de los seis meses anteriores a marzo".

Sobre los graficos, aprendido a golpes imprimiendo el dashboard:

  - Un grafico SIN `graph.dimensions` y `graph.metrics` sale sin etiquetas de
    eje. Se ven las barras y no se sabe de que son. Van siempre, en todos.
  - `graph.show_values` pone el numero encima de la barra. En un dashboard que
    se imprime es la diferencia entre leerlo y adivinarlo.
  - `pivot` NO sirve con SQL nativo: Metabase responde "Las tablas dinamicas
    solo se admiten para preguntas creadas en el constructor de consultas".
    La matriz mes x grupo va como tabla normal, con una columna por bloque.
  - `smartscalar` (Trend) necesita una columna de FECHA de verdad, no un texto
    'YYYY-MM', y toma la ultima fila: las consultas terminan en ORDER BY mes.
"""

import io
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

AQUI = os.path.dirname(os.path.abspath(__file__))
env = {}
for line in open(os.path.join(AQUI, '..', '.metabase.env'), encoding='utf-8'):
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, v = line.split('=', 1)
        env[k] = v.strip().strip("'").strip('"')
BASE, KEY = env['METABASE_URL'], env['METABASE_KEY']

DB = 2
COLECCION = 5
CAMPO = 873  # finanzas.v_compra.fecha, que ya es la fecha del cobro
# Un dashboard mensual arranca parado en un mes completo: `thismonth` el dia 1
# sale casi vacio y parece que algo se rompio. En Metabase el mes pasado se
# escribe `past1months` — `previousmonth` NO existe y rompe todas las tarjetas.
DEFECTO = 'past1months'


def call(method, path, payload=None):
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={
            'x-api-key': KEY,
            'Content-Type': 'application/json',
            'User-Agent': 'curl/8.0 metabase-admin',
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        print('HTTP', e.code, '->', e.read().decode('utf-8', 'replace')[:900])
        raise


def consulta(sql):
    tags = {}
    # Filtro opcional por categoria: lo llena el click en el grafico de
    # categorias, para que la tabla de productos muestre SOLO esa categoria.
    # Es un tag de texto y no de dimension porque va como `= {{categoria}}`
    # dentro de un [[...]], que Metabase borra entero cuando el valor no viene.
    if '{{categoria}}' in sql:
        tags['categoria'] = {
            'type': 'text',
            'name': 'categoria',
            'id': str(uuid.uuid4()),
            'display-name': 'Categoría',
            'required': False,
        }
    return {
        'lib/type': 'mbql/query',
        'database': DB,
        'stages': [
            {
                'lib/type': 'mbql.stage/native',
                'native': sql,
                'template-tags': {
                    **tags,
                    'mes': {
                        'dimension': ['field', {'lib/uuid': str(uuid.uuid4())}, CAMPO],
                        'type': 'dimension',
                        'widget-type': 'date/all-options',
                        'name': 'mes',
                        'id': str(uuid.uuid4()),
                        'display-name': 'Mes',
                        'default': DEFECTO,
                    }
                },
            }
        ],
    }


ANCLA = """WITH sel AS (
  SELECT COALESCE(
    (SELECT date_trunc('month', MAX(fecha)) FROM finanzas.v_compra WHERE {{mes}}),
    (SELECT date_trunc('month', MAX(fecha)) FROM finanzas.v_compra)
  ) AS m
)"""


def plata(*cols):
    return {
        f'["name","{c}"]': {
            'number_style': 'currency',
            'currency': 'COP',
            'currency_style': 'symbol',
            'decimals': 0,
        }
        for c in cols
    }


def grafico(dim, metricas, tipo=None, apilado=None, titulo_x=None, titulo_y=None):
    """Los ajustes que hacen que un grafico se pueda LEER.

    Sin `graph.dimensions` y `graph.metrics` Metabase dibuja las barras sin
    etiquetas de eje: se ve la forma y no se sabe de que es. Con
    `graph.show_values` el numero queda encima de la barra, que es lo unico que
    sirve cuando el dashboard se imprime.
    """
    v = {
        'graph.dimensions': [dim] if isinstance(dim, str) else list(dim),
        'graph.metrics': list(metricas),
        'graph.show_values': True,
    }
    if apilado:
        v['stackable.stack_type'] = apilado
    if tipo:
        v['series_settings'] = tipo
    if titulo_x:
        v['graph.x_axis.title_text'] = titulo_x
    if titulo_y:
        v['graph.y_axis.title_text'] = titulo_y
    return v


TREND = {'scalar.field': 'gasto', 'scalar.comparisons': [{'type': 'previousPeriod'}]}


def kpi(clave, nombre, filtro, pos):
    """Un numero grande con su cambio contra el mes anterior."""
    return (
        clave,
        nombre,
        'smartscalar',
        ANCLA
        + f"""
SELECT date_trunc('month', v.fecha)::date AS mes,
       ROUND(SUM(v.valor_pagado))         AS gasto
FROM finanzas.v_canasta v, sel
WHERE {filtro}
  AND v.fecha >= sel.m - interval '12 months'
  AND v.fecha < sel.m + interval '1 month'
GROUP BY 1
ORDER BY 1
""",
        TREND,
        pos,
    )


def por_categoria(clave, nombre, bloque, pos):
    """Cuanto se fue en cada categoria del bloque, en el mes elegido."""
    return (
        clave,
        nombre,
        'row',
        ANCLA
        + f"""
SELECT v.categoria, ROUND(SUM(v.valor_pagado)) AS gasto
FROM finanzas.v_canasta v, sel
WHERE v.bloque = '{bloque}'
  AND date_trunc('month', v.fecha) = sel.m
GROUP BY 1
HAVING SUM(v.valor_pagado) > 0
ORDER BY 2 DESC
""",
        {**grafico('categoria', ['gasto'], titulo_y='Categoría', titulo_x='Gasto'),
         'column_settings': plata('gasto')},
        pos,
    )


def productos(clave, nombre, bloque, pos):
    """Que se compro, con unidades. Ordenado por categoria para que los
    productos queden pegados a su categoria y se lea como un recibo."""
    return (
        clave,
        nombre,
        'table',
        ANCLA
        + f"""
SELECT v.categoria,
       v.producto,
       ROUND(SUM(v.cantidad), 2)  AS unidades,
       COUNT(*)                   AS veces,
       ROUND(SUM(v.valor_pagado)) AS gasto
FROM finanzas.v_canasta v, sel
WHERE v.bloque = '{bloque}'
  AND date_trunc('month', v.fecha) = sel.m
  [[AND v.categoria = {{{{categoria}}}}]]
GROUP BY 1, 2
ORDER BY v.categoria, gasto DESC
""",
        {'column_settings': plata('gasto')},
        pos,
    )


TARJETAS = [
    # --------------------------------------------------- 1. cuanto me gaste
    kpi('kpi_total', 'Gasto del mes', 'TRUE', (0, 0, 6, 4)),
    kpi('kpi_comida', 'Comida', "v.bloque = 'Comida'", (0, 6, 6, 4)),
    kpi(
        'kpi_hogar',
        'Hogar, aseo y cuidado',
        "v.bloque = 'Hogar y cuidado'",
        (0, 12, 6, 4),
    ),
    (
        'kpi_ahorro',
        'Ahorro por descuentos',
        'smartscalar',
        ANCLA
        + """
SELECT date_trunc('month', f.fecha)::date AS mes,
       ROUND(SUM(f.ahorro))               AS gasto
FROM finanzas.v_factura f, sel
WHERE f.fecha >= sel.m - interval '12 months'
  AND f.fecha < sel.m + interval '1 month'
GROUP BY 1
ORDER BY 1
""",
        TREND,
        (0, 18, 6, 4),
    ),
    (
        'serie',
        'Gasto mes a mes: comida y hogar',
        'combo',
        ANCLA
        + """
-- Las barras son el gasto del mes partido en dos; la linea punteada es el
-- promedio de los tres meses anteriores. Un mes por encima de su propia linea
-- es un mes caro de verdad, no uno que se ve alto al lado del anterior.
, mensual AS (
  SELECT date_trunc('month', v.fecha)::date AS mes,
         SUM(v.valor_pagado) FILTER (WHERE v.bloque = 'Comida')          AS comida,
         SUM(v.valor_pagado) FILTER (WHERE v.bloque = 'Hogar y cuidado') AS hogar,
         SUM(v.valor_pagado)                                            AS total
  FROM finanzas.v_canasta v, sel
  WHERE v.fecha >= sel.m - interval '14 months'
    AND v.fecha < sel.m + interval '1 month'
  GROUP BY 1
)
SELECT mes,
       ROUND(COALESCE(comida, 0)) AS comida,
       ROUND(COALESCE(hogar, 0))  AS hogar,
       ROUND(AVG(total) OVER (ORDER BY mes
             ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING)) AS promedio_3m
FROM mensual, sel
WHERE mes > sel.m - interval '12 months'
ORDER BY mes
""",
        grafico(
            'mes',
            ['comida', 'hogar', 'promedio_3m'],
            tipo={
                'comida': {'display': 'bar'},
                'hogar': {'display': 'bar'},
                'promedio_3m': {'display': 'line', 'line.style': 'dashed'},
            },
            apilado='stacked',
            titulo_x='Mes',
            titulo_y='Gasto',
        ),
        (4, 0, 14, 7),
    ),
    (
        'resumen',
        'Resumen mensual',
        'table',
        ANCLA
        + """
-- Esto era una tabla dinamica (pivot) y no funcionaba: Metabase solo las
-- admite en preguntas del constructor, no en SQL nativo. Como tabla normal
-- con una columna por bloque dice lo mismo y ademas se imprime.
SELECT to_char(date_trunc('month', v.fecha), 'YYYY-MM')                  AS mes,
       ROUND(SUM(v.valor_pagado) FILTER (WHERE v.bloque = 'Comida'))     AS comida,
       ROUND(SUM(v.valor_pagado) FILTER (WHERE v.bloque = 'Hogar y cuidado'))
                                                                        AS hogar,
       ROUND(SUM(v.valor_pagado))                                        AS total,
       COUNT(DISTINCT v.cufe)                                            AS compras
FROM finanzas.v_canasta v, sel
WHERE v.fecha >= sel.m - interval '11 months'
  AND v.fecha < sel.m + interval '1 month'
GROUP BY 1
ORDER BY 1 DESC
""",
        {'column_settings': plata('comida', 'hogar', 'total')},
        (4, 14, 10, 7),
    ),
    # ---------------------------------------- 2 y 3. comida: en que y que
    por_categoria('cat_comida', 'Comida: en qué se fue', 'Comida', (11, 0, 12, 7)),
    productos('prod_comida', 'Comida: qué compraste', 'Comida', (11, 12, 12, 7)),
    # ------------------------------- 5. lo mismo para lo que no se come
    por_categoria(
        'cat_hogar',
        'Hogar y cuidado: en qué se fue',
        'Hogar y cuidado',
        (18, 0, 12, 7),
    ),
    productos(
        'prod_hogar',
        'Hogar y cuidado: qué compraste',
        'Hogar y cuidado',
        (18, 12, 12, 7),
    ),
    # ------------------------------------------- 3. el balance de la canasta
    (
        'balance',
        'Balance de la canasta: cuánto pesa cada cosa',
        'table',
        ANCLA
        + """
-- Cuanto pesa cada categoria en el mes y como se compara con su propio
-- promedio de los seis meses anteriores. Es la tabla que dice si este mes se
-- fue en carne, en mecato o en aseo.
, act AS (
  SELECT v.bloque, v.categoria,
         SUM(v.valor_pagado)     AS gasto,
         SUM(v.cantidad)         AS unidades,
         COUNT(DISTINCT v.codigo) AS productos
  FROM finanzas.v_canasta v, sel
  WHERE date_trunc('month', v.fecha) = sel.m
  GROUP BY 1, 2
), prev AS (
  SELECT v.categoria, SUM(v.valor_pagado) / 6.0 AS gasto
  FROM finanzas.v_canasta v, sel
  WHERE date_trunc('month', v.fecha) < sel.m
    AND date_trunc('month', v.fecha) >= sel.m - interval '6 months'
  GROUP BY 1
)
SELECT a.bloque,
       a.categoria,
       a.productos,
       ROUND(a.unidades, 1)                                    AS unidades,
       ROUND(a.gasto)                                          AS gasto,
       ROUND(100.0 * a.gasto / SUM(a.gasto) OVER (), 1)        AS pct_del_mes,
       ROUND(COALESCE(p.gasto, 0))                             AS promedio_6m,
       ROUND(a.gasto - COALESCE(p.gasto, 0))                   AS diferencia
FROM act a
LEFT JOIN prev p ON p.categoria = a.categoria
ORDER BY a.gasto DESC
""",
        {'column_settings': plata('gasto', 'promedio_6m', 'diferencia')},
        (25, 0, 14, 8),
    ),
    (
        'normalidad',
        'Qué se salió de lo normal este mes',
        'bar',
        ANCLA
        + """
, act AS (
  SELECT v.categoria, SUM(v.valor_pagado) AS v
  FROM finanzas.v_canasta v, sel
  WHERE date_trunc('month', v.fecha) = sel.m
  GROUP BY 1
), prev AS (
  SELECT v.categoria, SUM(v.valor_pagado) / 6.0 AS v
  FROM finanzas.v_canasta v, sel
  WHERE date_trunc('month', v.fecha) < sel.m
    AND date_trunc('month', v.fecha) >= sel.m - interval '6 months'
  GROUP BY 1
)
SELECT COALESCE(a.categoria, p.categoria) AS categoria,
       ROUND(COALESCE(a.v, 0))            AS este_mes,
       ROUND(COALESCE(p.v, 0))            AS promedio_6m
FROM act a
FULL OUTER JOIN prev p ON p.categoria = a.categoria
ORDER BY 2 DESC
LIMIT 12
""",
        grafico(
            'categoria',
            ['este_mes', 'promedio_6m'],
            titulo_x='Categoría',
            titulo_y='Gasto',
        ),
        (25, 14, 10, 8),
    ),
    # ------------------------------------------------------- 4. control
    (
        'donde',
        'Dónde compraste',
        'row',
        ANCLA
        + """
SELECT v.cadena || COALESCE(' - ' || v.sede, '') AS donde,
       ROUND(SUM(v.valor_pagado))                AS gasto
FROM finanzas.v_canasta v, sel
WHERE date_trunc('month', v.fecha) = sel.m
GROUP BY 1
ORDER BY 2 DESC
""",
        {**grafico('donde', ['gasto'], titulo_y='Almacén', titulo_x='Gasto'),
         'column_settings': plata('gasto')},
        (33, 0, 8, 6),
    ),
    (
        'excluido',
        'Excluido del dashboard: tecnología y electrodomésticos',
        'table',
        ANCLA
        + """
-- Fuera de todos los totales de arriba, a proposito: no son la compra del
-- super. Se listan para que la plata se pueda rastrear y no parezca que
-- desaparecio.
SELECT v.fecha::date              AS fecha,
       v.producto,
       v.grupo,
       ROUND(SUM(v.valor_pagado)) AS gasto,
       BOOL_OR(v.pagada_con_puntos) AS pago_con_puntos
FROM finanzas.v_compra v, sel
WHERE v.tipo = 'No consumible'
  AND v.fecha >= sel.m - interval '11 months'
  AND v.fecha < sel.m + interval '1 month'
GROUP BY 1, 2, 3
ORDER BY gasto DESC
""",
        {'column_settings': plata('gasto')},
        (33, 8, 8, 6),
    ),
    (
        'pendientes',
        'Pendientes: sin clasificar y sin cuadrar',
        'table',
        ANCLA
        + """
-- Las dos colas de trabajo en una sola tarjeta.
--   producto  lo que el bot de Telegram tiene que preguntar
--   factura   compras sin movimiento en Firefly, casi siempre porque se
--             pagaron con bono, con puntos o con tarjeta del almacen
SELECT 'producto' AS que,
       v.producto AS detalle,
       ROUND(SUM(v.valor_pagado)) AS gasto,
       MAX(v.fecha)::date         AS ultima_vez
FROM finanzas.v_compra v, sel
WHERE v.tipo = 'Sin clasificar' AND v.fecha < sel.m + interval '1 month'
GROUP BY 1, 2
UNION ALL
SELECT 'factura',
       f.numero || ' (' || COALESCE(NULLIF(f.medios_pago, ''), 'sin dato') || ')',
       ROUND(f.total),
       f.fecha_factura::date
FROM finanzas.v_factura f, sel
WHERE NOT f.cuadrada_con_firefly
  AND f.fecha >= sel.m - interval '2 months'
  AND f.fecha < sel.m + interval '1 month'
ORDER BY 3 DESC
""",
        {'column_settings': plata('gasto')},
        (33, 16, 8, 6),
    ),
]


def cards_existentes():
    return {
        c['name']: c['id']
        for c in call('GET', '/api/card')
        if c.get('collection_id') == COLECCION and not c.get('archived')
    }


def buscar_dashboard(nombre):
    for d in call('GET', '/api/dashboard'):
        if d['name'] == nombre and not d.get('archived'):
            return d['id']
    return None


def main():
    ya = cards_existentes()
    creadas = {}
    for clave, nombre, display, sql, viz, _pos in TARJETAS:
        cuerpo = {
            'name': nombre,
            'dataset_query': consulta(sql.strip()),
            'display': display,
            'visualization_settings': viz,
            'collection_id': COLECCION,
        }
        if nombre in ya:
            card = call('PUT', f'/api/card/{ya[nombre]}', cuerpo)
            verbo = 'actualizada'
        else:
            card = call('POST', '/api/card', cuerpo)
            verbo = 'creada    '
        creadas[clave] = card['id']
        print(f'  card {card["id"]:>4}  {verbo}  {nombre}')

    dash_id = buscar_dashboard('Mercado')
    if dash_id is None:
        dash_id = call(
            'POST',
            '/api/dashboard',
            {
                'name': 'Mercado',
                'collection_id': COLECCION,
                'description': 'La compra del supermercado, mes a mes: cuanto, '
                'en que y que. Sin tecnologia ni electrodomesticos.',
            },
        )['id']
        print(f'\n  dashboard creado: {dash_id}')

    pid = 'f_mercado'
    cid = 'f_categoria'
    parametros = [
        {
            'name': 'Mes',
            'slug': 'mes',
            'id': pid,
            'type': 'date/all-options',
            'sectionId': 'date',
            'default': DEFECTO,
        },
        # Se llena tocando una barra del grafico de categorias. Sin valor, el
        # [[...]] de la consulta desaparece y las tablas muestran todo.
        {
            'name': 'Categoría',
            'slug': 'categoria',
            'id': cid,
            'type': 'string/=',
            'sectionId': 'string',
        },
    ]

    # Tocar una categoria filtra la tabla de productos de ese bloque: es lo que
    # hace que "en que se fue" y "que compraste" sean la misma pregunta y no
    # dos tarjetas sueltas al lado.
    CLICK = {
        'cat_comida': 'prod_comida',
        'cat_hogar': 'prod_hogar',
    }
    USA_CATEGORIA = set(CLICK.values())
    dashcards = []
    for i, (clave, _n, _d, _sql, viz, pos) in enumerate(TARJETAS):
        card_id = creadas[clave]
        mapeos = [
            {
                'parameter_id': pid,
                'card_id': card_id,
                'target': ['dimension', ['template-tag', 'mes'], {'stage-number': 0}],
            }
        ]
        if clave in USA_CATEGORIA:
            mapeos.append(
                {
                    'parameter_id': cid,
                    'card_id': card_id,
                    'target': ['variable', ['template-tag', 'categoria']],
                }
            )
        ajustes = dict(viz)
        if clave in CLICK:
            ajustes['click_behavior'] = {
                'type': 'crossfilter',
                'parameterMapping': {
                    cid: {
                        'id': cid,
                        'source': {
                            'type': 'column',
                            'id': 'categoria',
                            'name': 'categoria',
                        },
                        'target': {'type': 'parameter', 'id': cid},
                    }
                },
            }
        dashcards.append(
            {
                'id': -(i + 1),
                'card_id': card_id,
                'row': pos[0],
                'col': pos[1],
                'size_x': pos[2],
                'size_y': pos[3],
                'series': [],
                'visualization_settings': ajustes,
                'parameter_mappings': mapeos,
            }
        )

    call(
        'PUT',
        f'/api/dashboard/{dash_id}',
        {'parameters': parametros, 'dashcards': dashcards},
    )

    d = call('GET', f'/api/dashboard/{dash_id}')
    print(f'\n  "{d["name"]}" con {len(d["dashcards"])} tarjetas:')
    for dc in sorted(d['dashcards'], key=lambda x: (x['row'], x['col'])):
        c = dc.get('card') or {}
        print(
            f'    r{dc["row"]:<3} c{dc["col"]:<3} {dc["size_x"]}x{dc["size_y"]:<3} '
            f'{c.get("display", ""):<12} {c.get("name", "")[:48]}'
        )
    print(f'\n  {BASE}/dashboard/{dash_id}')


if __name__ == '__main__':
    main()
