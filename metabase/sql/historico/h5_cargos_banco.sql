-- Lo que cobra el banco, no lo que gastas tu: cuotas de manejo, intereses,
-- avances y refinanciaciones. Antes estaba mezclado con los comercios.
SELECT EXTRACT(YEAR FROM fecha)::int AS ano,
       comercio AS concepto,
       count(*) AS veces,
       SUM(gasto) AS total
FROM finanzas.v_comercio
WHERE fuente = 'extracto_tarjeta' AND tipo = 'banco' AND gasto > 0
GROUP BY 1,2
ORDER BY 1, SUM(gasto) DESC;
