-- Donde se ha ido la plata en 4 anos, con el comercio ya normalizado
-- (ver finanzas.alias_comercio y la vista finanzas.v_comercio).
-- La columna tipo separa comercios reales de pasarelas de pago y cargos del banco.
SELECT comercio,
       tipo,
       count(*)   AS compras,
       SUM(gasto) AS total,
       ROUND(AVG(gasto),0) AS ticket_promedio,
       MIN(fecha)::text AS primera,
       MAX(fecha)::text AS ultima
FROM finanzas.v_comercio
WHERE fuente = 'extracto_tarjeta' AND NOT es_abono_tc AND gasto > 0
GROUP BY 1,2
ORDER BY SUM(gasto) DESC
LIMIT 30;
