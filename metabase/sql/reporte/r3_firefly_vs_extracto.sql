-- Alerta de conciliacion. La diferencia esperada es lo comprado despues del corte;
-- lo que sobre de ahi es un descuadre real que hay que revisar en Firefly.
WITH ult AS (
  SELECT DISTINCT ON (tarjeta) tarjeta, corte, cupo, deuda_corte
  FROM finanzas.extracto_resumen WHERE corte IS NOT NULL
  ORDER BY tarjeta, corte DESC
),
ff AS (
  SELECT a.name AS tarjeta, -SUM(t.amount) AS deuda_firefly, MAX(a.virtual_balance) AS cupo_firefly
  FROM accounts a
  JOIN account_meta am ON am.account_id=a.id AND am.name='account_role' AND am.data LIKE '%ccAsset%'
  JOIN transactions t ON t.account_id=a.id AND t.deleted_at IS NULL
  JOIN transaction_journals tj ON tj.id=t.transaction_journal_id AND tj.deleted_at IS NULL
  JOIN transaction_currencies tc ON tc.id=t.transaction_currency_id AND tc.code='COP'
  WHERE a.deleted_at IS NULL AND a.active
  GROUP BY a.name
),
post AS (
  SELECT a.name AS tarjeta, COALESCE(-SUM(t.amount),0) AS post
  FROM accounts a JOIN ult ON ult.tarjeta=a.name
  JOIN transactions t ON t.account_id=a.id AND t.deleted_at IS NULL
  JOIN transaction_journals tj ON tj.id=t.transaction_journal_id AND tj.deleted_at IS NULL
  JOIN transaction_currencies tc ON tc.id=t.transaction_currency_id AND tc.code='COP'
  WHERE a.deleted_at IS NULL AND tj.date > ult.corte GROUP BY a.name
)
SELECT u.tarjeta,
       u.deuda_corte                AS extracto,
       ROUND(f.deuda_firefly)       AS firefly,
       COALESCE(p.post,0)           AS explicado_por_compras_post_corte,
       ROUND(COALESCE(f.deuda_firefly,0) - u.deuda_corte - COALESCE(p.post,0)) AS descuadre,
       u.cupo                       AS cupo_extracto,
       f.cupo_firefly,
       ROUND(COALESCE(f.cupo_firefly,0) - COALESCE(u.cupo,0)) AS descuadre_cupo
FROM ult u
LEFT JOIN ff f   ON f.tarjeta = u.tarjeta
LEFT JOIN post p ON p.tarjeta = u.tarjeta
ORDER BY abs(ROUND(COALESCE(f.deuda_firefly,0) - u.deuda_corte - COALESCE(p.post,0))) DESC;
