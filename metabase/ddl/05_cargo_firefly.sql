-- Cruce factura <-> movimiento de Firefly, para que el mes sea el mes en que
-- SE COBRO y no el dia en que fuiste al super.
--
-- El problema real: la factura SF6811490 es del 28 de agosto de 2026 por
-- 304.372, y el cargo entro a la tarjeta el 1 de septiembre por 304.371. El
-- dashboard la contaba en agosto; la plata salio en septiembre. Con tres casos
-- asi, el mes cierra mal por 400 mil pesos.
--
-- **Firefly manda.** Cuando una factura tiene su movimiento, la fecha del
-- movimiento es la fecha buena: es la que dice cuando salio la plata de la
-- cuenta y la que cuadra contra el extracto. La fecha de la factura queda
-- disponible como `fecha_factura` para cuando lo que interese sea cuando
-- fuiste a comprar.
--
-- Cuando NO hay movimiento, se queda la fecha de la factura. Y no tener
-- movimiento es lo normal en muchos casos, no una falla del cruce: de las 121
-- facturas del periodo que cubre Firefly, 90 emparejan y las 31 que no tienen
-- una razon en el propio tiquete —
--
--   BONO VIRTUAL       se pago con un bono, no con la tarjeta
--   PAGO CON PUNTOS    se redimieron puntos, no salio plata
--   TARJETAS PRIVADAS  tarjeta del almacen, que no esta en Firefly
--
-- Por eso el cruce NO se fuerza: emparejar por aproximacion una compra pagada
-- con puntos contra cualquier cargo cercano meteria una fecha inventada.

-- Los egresos de Firefly hacia los supermercados, con el NIT ya resuelto.
CREATE OR REPLACE VIEW finanzas.v_firefly_super AS
SELECT tj.id,
       tj.date::date  AS fecha,
       tj.description,
       t.amount,
       CASE
         WHEN a.name ILIKE '%xito%' OR a.name ILIKE '%carulla%' THEN '890900608'
         -- Ojo con la igualdad: era `a.name = 'D1'` y dejaba por fuera
         -- 'TIENDA D1 SABANETA P' y 'KOBA COLOMBIA' (Koba es la dueña de D1).
         -- Sin NIT no hay cruce, y sin cruce la factura se queda con SU fecha
         -- en vez de la del cargo, que es justo lo que este archivo arregla.
         WHEN a.name ~* '\yD1\y' OR a.name ~* '\yKOBA\y'      THEN '900276962'
         WHEN a.name ILIKE '%vaquita%' OR a.name ILIKE '%supermu%' THEN '900522508'
       END AS nit
FROM public.transaction_journals tj
JOIN public.transactions t
  ON t.transaction_journal_id = tj.id AND t.amount > 0 AND t.deleted_at IS NULL
JOIN public.accounts a ON a.id = t.account_id
JOIN public.transaction_types tt ON tt.id = tj.transaction_type_id
WHERE tj.deleted_at IS NULL
  AND tt.type = 'Withdrawal'
  AND (a.name ILIKE '%xito%' OR a.name ILIKE '%carulla%'
       OR a.name ~* '\yD1\y' OR a.name ~* '\yKOBA\y'
       OR a.name ILIKE '%vaquita%' OR a.name ILIKE '%supermu%');

-- El emparejamiento, uno a uno.
--
-- Tolerancias, sacadas de mirar los 90 pares que salen:
--   monto  el 1% del total, con piso de 2 pesos. Era +/- 2 fijo, y con eso se
--          perdian cuatro compras enteras por diferencias de caja:
--
--            SF6812317   18-sep-2026   factura 373.099   cobro 374.689   0,43%
--            SF688342    05-jun-2026   factura 296.717   cobro 295.683   0,35%
--            UE5317718   28-feb-2026   factura 525.146   cobro 523.353   0,34%
--            UE534252    11-oct-2025   factura 347.917   cobro 347.565   0,10%
--
--          Las cuatro son el mismo dia o el siguiente. Tirar 33 lineas de
--          detalle por 1.590 pesos no tiene sentido: la diferencia la absorbe
--          la fila de cierre de v_mercado_linea, que existe para eso.
--
--          El 1% no sale gratis: dos almuerzos de 14.385 y 14.310 del mismo
--          fin de semana quedan cada uno dentro del 1% del cargo del OTRO. No
--          hace daño porque el orden es `dias` primero y el par exacto —mismo
--          dia, cero diferencia— gana siempre. Pero por eso el orden importa
--          y no se puede cambiar a la ligera.
--   fecha  de 3 dias antes a 10 despues. El maximo observado son 4 dias, y
--          siempre hacia adelante: el cargo entra despues de la compra, nunca
--          antes. Los 3 dias hacia atras son holgura por zona horaria.
--
-- Los dos DISTINCT ON encadenados fuerzan el 1:1. Con uno solo, dos facturas
-- identicas del mismo dia (paso: dos de 15.900 el 2025-08-09) se quedaban las
-- dos con el mismo movimiento.
CREATE OR REPLACE VIEW finanzas.v_factura_cargo AS
WITH candidatos AS (
  SELECT f.cufe, f.fecha AS fecha_factura, f.total,
         ff.id AS firefly_id, ff.fecha AS fecha_cargo, ff.description, ff.amount,
         ABS(ff.fecha - f.fecha)      AS dias,
         ABS(ff.amount - f.total)     AS dif
  FROM finanzas.factura f
  JOIN finanzas.v_firefly_super ff
    ON ff.nit = f.nit
   AND ABS(ff.amount - f.total) <= GREATEST(2, f.total * 0.01)
   AND ff.fecha BETWEEN f.fecha - 3 AND f.fecha + 10
), mejor_por_factura AS (
  SELECT DISTINCT ON (cufe) * FROM candidatos ORDER BY cufe, dias, dif
)
SELECT DISTINCT ON (firefly_id) *
FROM mejor_por_factura
ORDER BY firefly_id, dias, dif;
