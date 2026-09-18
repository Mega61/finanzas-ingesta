-- Gasto mensual con tarjetas de credito, 2022-2026. Excluye abonos (pagos a la tarjeta).
SELECT date_trunc('month', fecha)::date AS mes,
       SUM(gasto) AS gasto
FROM finanzas.v_gasto
WHERE fuente = 'extracto_tarjeta' AND NOT es_abono_tc AND gasto > 0
GROUP BY 1 ORDER BY 1;
