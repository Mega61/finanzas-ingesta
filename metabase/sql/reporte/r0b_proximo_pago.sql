-- El pago mas proximo que esta pendiente.
SELECT pago_total AS proximo_pago
FROM (
  SELECT DISTINCT ON (tarjeta) tarjeta, corte, pago_total, pagar_antes
  FROM finanzas.extracto_resumen WHERE corte IS NOT NULL ORDER BY tarjeta, corte DESC
) u
WHERE COALESCE(pago_total,0) > 0 AND pagar_antes >= CURRENT_DATE
ORDER BY pagar_antes LIMIT 1;
