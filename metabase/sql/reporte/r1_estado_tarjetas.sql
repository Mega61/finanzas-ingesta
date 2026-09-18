-- Estado real de cada tarjeta: lo que dice el ultimo extracto del banco, mas
-- las compras registradas en Firefly DESPUES de esa fecha de corte.
-- Los cortes no son el mismo dia en todas (VISA corta el 15/17, el resto a fin de mes).
WITH ult AS (
  SELECT DISTINCT ON (tarjeta) tarjeta, corte, cupo, deuda_corte, pago_total, pago_minimo, pagar_antes
  FROM finanzas.extracto_resumen WHERE corte IS NOT NULL
  ORDER BY tarjeta, corte DESC
),
post AS (
  SELECT a.name AS tarjeta, COALESCE(-SUM(t.amount),0) AS movs_post_corte
  FROM accounts a
  JOIN ult ON ult.tarjeta = a.name
  JOIN transactions t ON t.account_id=a.id AND t.deleted_at IS NULL
  JOIN transaction_journals tj ON tj.id=t.transaction_journal_id AND tj.deleted_at IS NULL
  JOIN transaction_currencies tc ON tc.id=t.transaction_currency_id AND tc.code='COP'
  WHERE a.deleted_at IS NULL AND tj.date > ult.corte
  GROUP BY a.name
)
SELECT u.tarjeta,
       u.corte,
       u.deuda_corte                                    AS deuda_al_corte,
       COALESCE(p.movs_post_corte,0)                    AS gastado_despues,
       u.deuda_corte + COALESCE(p.movs_post_corte,0)    AS deuda_hoy,
       u.cupo,
       u.cupo - (u.deuda_corte + COALESCE(p.movs_post_corte,0)) AS disponible_hoy,
       ROUND(100.0*(u.deuda_corte + COALESCE(p.movs_post_corte,0))/NULLIF(u.cupo,0),1) AS pct_usado
FROM ult u LEFT JOIN post p ON p.tarjeta = u.tarjeta
ORDER BY deuda_hoy DESC;
