-- Que hay que pagar y cuando. Ordenado por urgencia.
SELECT tarjeta,
       pagar_antes                          AS pagar_antes,
       (pagar_antes - CURRENT_DATE)         AS dias_restantes,
       pago_total,
       pago_minimo,
       corte                                AS corte_del_extracto
FROM (
  SELECT DISTINCT ON (tarjeta) tarjeta, corte, pago_total, pago_minimo, pagar_antes
  FROM finanzas.extracto_resumen WHERE corte IS NOT NULL
  ORDER BY tarjeta, corte DESC
) u
WHERE COALESCE(pago_total,0) > 0
ORDER BY pagar_antes;
