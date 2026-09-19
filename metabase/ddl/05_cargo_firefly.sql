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
--   monto  +/- 2 pesos. Las diferencias reales son de 1 peso, por el redondeo
--          entre lo que suman las lineas y lo que cobra la caja.
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
   AND ABS(ff.amount - f.total) <= 2
   AND ff.fecha BETWEEN f.fecha - 3 AND f.fecha + 10
), mejor_por_factura AS (
  SELECT DISTINCT ON (cufe) * FROM candidatos ORDER BY cufe, dias, dif
)
SELECT DISTINCT ON (firefly_id) *
FROM mejor_por_factura
ORDER BY firefly_id, dias, dif;
