-- Facturas electronicas de supermercado (DIAN UBL 2.1) -> mercado por producto.
--
-- Tres tablas y una vista. La regla que sostiene todo: la categoria vive en
-- `producto`, NUNCA copiada en `factura_linea`. Asi, corregir un producto
-- reescribe su historia completa — las 30 veces que compraste tortillas desde
-- 2025 cambian de categoria con un solo UPDATE. Si la categoria se copiara a
-- la linea, el sistema solo podria arreglar el futuro.

CREATE TABLE IF NOT EXISTS finanzas.factura (
  cufe              text PRIMARY KEY,      -- identidad DIAN: dedupe por aqui,
                                           -- no por Message-ID (hay reenvios)
  nit               text NOT NULL,
  proveedor         text,
  numero            text,
  tipo              text NOT NULL,         -- factura | nota_credito
  signo             smallint NOT NULL,     -- -1 en nota credito (devolucion)
  fecha             date NOT NULL,
  hora              text,
  sede              text,
  moneda            text NOT NULL DEFAULT 'COP',
  subtotal          numeric(14,2),
  descuento         numeric(14,2),
  total             numeric(14,2),         -- PayableAmount: el unico confiable
  -- Lo que Exito mete en cbc:Note y el UBL no modela.
  medios_pago       text,                  -- 'PAGO CON PUNTOS|TARJETA DEBITO'
  puntos_redimidos  numeric(14,2) DEFAULT 0,
  ahorro            numeric(14,2) DEFAULT 0,
  pagada_con_puntos boolean DEFAULT false
);

CREATE INDEX IF NOT EXISTS ix_factura_fecha ON finanzas.factura (fecha);

CREATE TABLE IF NOT EXISTS finanzas.factura_linea (
  cufe            text NOT NULL,
  n               integer NOT NULL,
  nit             text NOT NULL,
  codigo          text NOT NULL,
  descripcion     text,
  cantidad        numeric(12,3),
  unidad          text,
  precio_unitario numeric(14,2),
  descuento       numeric(14,2),   -- AllowanceCharge de linea: por esto
                                   -- precio * cantidad != total
  iva_pct         numeric(5,2),
  total           numeric(14,2),
  signo           smallint NOT NULL DEFAULT 1,
  fecha           date,
  PRIMARY KEY (cufe, n)
);

CREATE INDEX IF NOT EXISTS ix_linea_prod ON finanzas.factura_linea (nit, codigo);
CREATE INDEX IF NOT EXISTS ix_linea_fecha ON finanzas.factura_linea (fecha);

-- El catalogo. La llave es (nit, codigo) y no el codigo solo: Exito usa PLU
-- interno y D1/Supervaquita usan EAN, y los dos espacios chocan.
CREATE TABLE IF NOT EXISTS finanzas.producto (
  nit         text NOT NULL,
  codigo      text NOT NULL,
  descripcion text,             -- la mas larga vista; los almacenes truncan
  -- Consumible | No consumible. El corte de arriba del dashboard: lo que se
  -- acaba y se repone, contra la compra de una vez. Separar la comida del
  -- aseo no servia — los dos se acaban cada mes y pesan igual en el
  -- presupuesto. Lo que si cambia el mes es un televisor.
  tipo        text NOT NULL,
  grupo       text NOT NULL,    -- Alimentacion | Aseo y hogar |
                                -- Cuidado personal | Mascotas |
                                -- Comida preparada | Licores | Servicios |
                                -- Tecnologia | Electrodomesticos | Hogar
  categoria   text NOT NULL,    -- el detalle dentro del grupo
  origen      text,             -- iva8 | iva0 | override | palabra | usuario
  PRIMARY KEY (nit, codigo)
);

-- Una linea de compra ya categorizada. Es lo que leen todas las tarjetas.
--
-- Sobre los dos montos, porque la diferencia importa:
--
--   valor         LineExtensionAmount: el precio de la linea SIN IVA. Es lo
--                 que trae el XML y sirve para comparar precios entre meses.
--   valor_pagado  la parte del total de la factura que le toca a esta linea.
--                 Se prorratea con el peso de la linea ya con su IVA propio.
--
-- Hace falta porque las dos cifras no coinciden: las lineas suman 18,2M sin
-- IVA y las facturas cobran 20,2M. Sumar `valor` por categoria y llamarlo
-- "lo que gaste en mercado" se queda corto en un 10%. Y no basta con subirle
-- el IVA a cada linea: quedaba una diferencia de 105 mil pesos en 42 facturas
-- por redondeos, impuesto al consumo y cargos de cabecera. Prorratear cierra
-- la cuenta exacta contra lo que salio del bolsillo.
-- `fecha` es la fecha en que SE COBRO, no la del tiquete: si la factura tiene
-- movimiento en Firefly, manda Firefly. Se llama `fecha` a secas —y no
-- `fecha_cargo`— para que el filtro del dashboard y todas las tarjetas la usen
-- sin tocar nada. La del tiquete queda como `fecha_factura`.
-- Ver metabase/ddl/05_cargo_firefly.sql.
CREATE OR REPLACE VIEW finanzas.v_compra AS
WITH bruto AS (
  SELECT cufe, n,
         total * (1 + COALESCE(iva_pct, 0) / 100.0) AS con_iva
  FROM finanzas.factura_linea
), peso AS (
  SELECT cufe, n, con_iva,
         SUM(con_iva) OVER (PARTITION BY cufe) AS total_cufe
  FROM bruto
)
SELECT COALESCE(c.fecha_cargo, l.fecha)                       AS fecha,
       l.fecha                                                AS fecha_factura,
       date_trunc('month', COALESCE(c.fecha_cargo, l.fecha))::date AS mes,
       (c.firefly_id IS NOT NULL)                             AS cuadrada_con_firefly,
       c.firefly_id,
       f.proveedor,
       f.nit,
       CASE f.nit WHEN '890900608' THEN 'Exito'
                  WHEN '900276962' THEN 'D1'
                  WHEN '900522508' THEN 'Supervaquita'
                  ELSE f.proveedor END          AS cadena,
       f.sede,
       f.cufe,
       f.numero,
       f.pagada_con_puntos,
       f.medios_pago,
       l.codigo,
       COALESCE(p.descripcion, l.descripcion)   AS producto,
       COALESCE(p.tipo, 'Sin clasificar')       AS tipo,
       COALESCE(p.grupo, 'Sin clasificar')      AS grupo,
       COALESCE(p.categoria, 'Sin clasificar')  AS categoria,
       (COALESCE(p.tipo, '') = 'Consumible')    AS es_consumible,
       l.cantidad,
       l.unidad,
       l.precio_unitario,
       -- El signo de la factura hace que una nota credito reste sola.
       (l.total * f.signo)                      AS valor,
       ROUND(
         CASE WHEN pe.total_cufe > 0
              THEN f.total * (pe.con_iva / pe.total_cufe) * f.signo
              ELSE 0 END, 2)                    AS valor_pagado,
       (l.descuento * f.signo)                  AS descuento,
       l.iva_pct
FROM finanzas.factura_linea l
JOIN finanzas.factura f  ON f.cufe = l.cufe
JOIN peso pe ON pe.cufe = l.cufe AND pe.n = l.n
LEFT JOIN finanzas.producto p ON p.nit = l.nit AND p.codigo = l.codigo
LEFT JOIN finanzas.v_factura_cargo c ON c.cufe = l.cufe;

-- El gasto que se repite todos los meses: la compra de verdad. Deja por fuera
-- el televisor, el reloj y la licuadora, que son los que hacen que un mes
-- parezca el doble de otro.
--
-- `pagada_con_puntos` NO se usa para excluir: hay redenciones parciales de 400
-- puntos sobre una compra normal de 15.750. Lo que decide es el tipo del
-- producto. La marca queda disponible para explicar un pico.
CREATE OR REPLACE VIEW finanzas.v_consumible AS
SELECT * FROM finanzas.v_compra WHERE es_consumible;


-- La cabecera con la fecha ya resuelta. La usan las tarjetas que cuentan
-- tiquetes (ticket promedio, ahorro): sin esto seguian contando por fecha de
-- factura y no cuadraban con las de linea.
CREATE OR REPLACE VIEW finanzas.v_factura AS
SELECT f.cufe,
       COALESCE(c.fecha_cargo, f.fecha) AS fecha,
       f.fecha                          AS fecha_factura,
       (c.firefly_id IS NOT NULL)       AS cuadrada_con_firefly,
       c.firefly_id,
       f.nit, f.proveedor, f.numero, f.tipo, f.signo, f.sede, f.moneda,
       f.subtotal, f.descuento, f.total, f.medios_pago, f.puntos_redimidos,
       f.ahorro, f.pagada_con_puntos
FROM finanzas.factura f
LEFT JOIN finanzas.v_factura_cargo c ON c.cufe = f.cufe;

-- La canasta: lo que el dashboard de mercado tiene que mirar.
--
-- Deja por fuera tecnologia, electrodomesticos y menaje. No es que no se hayan
-- comprado — el reloj costo 1,2 millones — es que no son la compra del
-- supermercado y mezclarlos hace que un mes normal parezca el doble. Casi
-- todos entraron ademas por redencion de puntos, o sea que ni siquiera salio
-- plata de una cuenta. Quedan en `v_compra` y hay una tarjeta aparte que los
-- lista, para que el numero se pueda rastrear.
--
-- `bloque` es el corte que se mira todo el tiempo: lo que se come contra lo
-- que no. Los dos son consumibles y los dos se reponen cada mes, pero la
-- pregunta "cuanto costo la comida" es distinta de "cuanto costo el aseo".
CREATE OR REPLACE VIEW finanzas.v_canasta AS
SELECT v.*,
       CASE WHEN v.grupo IN ('Alimentacion', 'Comida preparada', 'Licores')
            THEN 'Comida' ELSE 'Hogar y cuidado' END AS bloque
FROM finanzas.v_compra v
WHERE v.tipo = 'Consumible';
