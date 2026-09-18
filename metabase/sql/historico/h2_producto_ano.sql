-- Gasto por producto logico y ano. dim_producto ya consolida las tarjetas reemplazadas.
SELECT EXTRACT(YEAR FROM fecha)::int AS ano,
       producto,
       SUM(gasto) AS gasto
FROM finanzas.v_gasto
WHERE fuente = 'extracto_tarjeta' AND NOT es_abono_tc AND gasto > 0
GROUP BY 1,2 ORDER BY 1,3 DESC;
