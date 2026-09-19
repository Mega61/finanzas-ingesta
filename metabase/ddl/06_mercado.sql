-- El mercado, con Firefly como espina y la factura como detalle.
--
-- Antes el dashboard se construia al reves: la factura era la espina y lo que
-- no tuviera factura electronica sencillamente no existia. En septiembre de
-- 2026 eso escondia 490.519 de 1.729.284 — un 28%:
--
--   Mercado de alacena que faltaba   110.580   D1 no mando factura
--   Toallas de cocina                  5.250   D1 no mando factura
--   ALMACENES EXITO (18-sep)         374.689   la factura dice 373.099
--
-- Las dos primeras son entradas hechas a mano. La tercera si tiene factura
-- (SF6812317, 33 lineas, BONO VIRTUAL) pero el cobro trae 1.590 mas de lo que
-- la factura suma, asi que queda por fuera de la tolerancia de +/-2.
--
-- La regla ahora es: **la plata la dice Firefly**. Cada movimiento de Firefly
-- hacia un supermercado es una fila, tenga factura o no. La factura se engancha
-- al lado y aporta el desglose por producto cuando existe.
--
-- Lo que se gana es que el total del dashboard SIEMPRE cuadra con el banco.
-- Lo que se pierde no es nada: lo que antes se veia sigue viendose, y lo que
-- no tenia factura deja de estar escondido y pasa a estar contado como 'Sin
-- factura', que es la verdad.

-- Un movimiento de supermercado por fila. La espina.
CREATE OR REPLACE VIEW finanzas.v_mercado AS
SELECT ff.id                                   AS firefly_id,
       ff.fecha,
       date_trunc('month', ff.fecha)::date     AS mes,
       ff.description                          AS descripcion,
       ff.amount                               AS pagado,
       ff.nit,
       CASE ff.nit WHEN '890900608' THEN 'Exito'
                   WHEN '900276962' THEN 'D1'
                   WHEN '900522508' THEN 'Supervaquita'
                   ELSE ff.nit END             AS cadena,
       fc.cufe,
       f.fecha                                 AS fecha_factura,
       f.total                                 AS total_factura,
       f.medios_pago,
       (fc.cufe IS NOT NULL)                   AS tiene_factura,
       -- Cuanto se aparta el cobro de lo que la factura dice. Normalmente 0 o
       -- 1 peso por redondeo de caja; si crece, hay algo que mirar en el
       -- tiquete.
       CASE WHEN fc.cufe IS NOT NULL THEN ff.amount - f.total END AS dif_cobro
FROM finanzas.v_firefly_super ff
LEFT JOIN finanzas.v_factura_cargo fc ON fc.firefly_id = ff.id
LEFT JOIN finanzas.factura f          ON f.cufe = fc.cufe
WHERE ff.nit IS NOT NULL;

-- El desglose. Una fila por producto cuando hay factura, MAS una fila de
-- cierre por lo que la factura no explica.
--
-- Esa fila de cierre es la pieza que hace que esto sirva: sin ella el desglose
-- sumaria menos que el movimiento y volveriamos al problema de antes, solo que
-- mas escondido. Con ella, SUM(valor) por mes es identico a lo que cobro el
-- banco, y cada peso esta o bien en una categoria o bien declarado como no
-- detallado.
--
--   Sin factura     no llego factura electronica de esa compra
--   No detallado    hay factura, pero estas lineas no son consumible (el
--                   televisor, la licuadora) o el cobro no cuadra con ella
--
-- Comprobado contra septiembre de 2026: la suma da 1.729.284, identica a la de
-- Firefly. Si se redondea POR CATEGORIA antes de sumar puede aparecer un peso
-- de diferencia; es el redondeo de la presentacion, no del dato.
-- DROP y no CREATE OR REPLACE: Postgres no deja renombrar ni reordenar las
-- columnas de una vista existente. Nada cuelga de esta todavia.
DROP VIEW IF EXISTS finanzas.v_mercado_linea;

CREATE VIEW finanzas.v_mercado_linea AS
WITH detalle AS (
  SELECT m.firefly_id, m.fecha, m.mes, m.cadena, m.nit, m.cufe,
         v.codigo, v.producto, v.grupo, v.categoria, v.tipo, v.sede, v.cantidad,
         v.valor_pagado
  FROM finanzas.v_mercado m
  JOIN finanzas.v_compra v ON v.cufe = m.cufe
  WHERE m.tiene_factura AND v.tipo = 'Consumible'
), explicado AS (
  SELECT firefly_id, SUM(valor_pagado) AS suma FROM detalle GROUP BY 1
)
SELECT firefly_id, fecha, mes, cadena, nit, cufe, codigo, producto, grupo,
       categoria, tipo, sede, cantidad, valor_pagado,
       CASE WHEN grupo IN ('Alimentacion', 'Comida preparada', 'Licores')
            THEN 'Comida' ELSE 'Hogar y cuidado' END AS bloque,
       TRUE AS detallado
FROM detalle
UNION ALL
SELECT m.firefly_id, m.fecha, m.mes, m.cadena, m.nit, NULL, NULL,
       CASE WHEN m.tiene_factura THEN 'No detallado' ELSE 'Sin factura' END,
       CASE WHEN m.tiene_factura THEN 'No detallado' ELSE 'Sin factura' END,
       CASE WHEN m.tiene_factura THEN 'No detallado' ELSE 'Sin factura' END,
       'Sin detalle', NULL, NULL,
       m.pagado - COALESCE(e.suma, 0),
       'Sin detalle',
       FALSE
FROM finanzas.v_mercado m
LEFT JOIN explicado e ON e.firefly_id = m.firefly_id
WHERE ROUND(m.pagado - COALESCE(e.suma, 0)) <> 0;
