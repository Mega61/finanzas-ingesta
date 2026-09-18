-- Porcentaje del cupo total que estas usando hoy.
WITH ult AS (
  SELECT DISTINCT ON (tarjeta) tarjeta, corte, deuda_corte, cupo
  FROM finanzas.extracto_resumen WHERE corte IS NOT NULL ORDER BY tarjeta, corte DESC
),
post AS (
  SELECT a.name AS tarjeta, COALESCE(-SUM(t.amount),0) AS post
  FROM accounts a JOIN ult ON ult.tarjeta=a.name
  JOIN transactions t ON t.account_id=a.id AND t.deleted_at IS NULL
  JOIN transaction_journals tj ON tj.id=t.transaction_journal_id AND tj.deleted_at IS NULL
  JOIN transaction_currencies tc ON tc.id=t.transaction_currency_id AND tc.code='COP'
  WHERE a.deleted_at IS NULL AND tj.date > ult.corte GROUP BY a.name
)
SELECT ROUND(100.0*SUM(u.deuda_corte + COALESCE(p.post,0))/NULLIF(SUM(u.cupo),0),1) AS pct_cupo_usado
FROM ult u LEFT JOIN post p ON p.tarjeta=u.tarjeta;
