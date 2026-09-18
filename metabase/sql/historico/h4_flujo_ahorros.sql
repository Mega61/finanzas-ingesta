-- Entradas y salidas de la cuenta de ahorros, mes a mes.
SELECT date_trunc('month', fecha)::date AS mes,
       SUM(gasto) FILTER (WHERE gasto > 0)  AS salidas,
       -SUM(gasto) FILTER (WHERE gasto < 0) AS entradas,
       SUM(gasto) AS neto
FROM finanzas.v_gasto
WHERE fuente = 'extracto_ahorros'
GROUP BY 1 ORDER BY 1;
