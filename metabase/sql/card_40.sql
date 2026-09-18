WITH rango AS (
  SELECT date_trunc('month', MIN(date))                      AS ini,
         date_trunc('month', MAX(date)) + interval '1 month' AS fin
  FROM transaction_journals WHERE deleted_at IS NULL AND {{mes}}
)
, gasto AS (
  SELECT b.id AS budget_id, SUM(t.amount) AS gastado
  FROM transaction_journals tj
  JOIN transactions t ON t.transaction_journal_id=tj.id AND t.amount>0 AND t.deleted_at IS NULL
  JOIN transaction_currencies tc ON tc.id=t.transaction_currency_id
  JOIN budget_transaction_journal btj ON btj.transaction_journal_id=tj.id
  JOIN budgets b ON b.id=btj.budget_id
  JOIN transaction_types tt ON tt.id=tj.transaction_type_id
  WHERE tt.type='Withdrawal' AND tj.deleted_at IS NULL AND tc.code='COP'
    AND tj.date >= (SELECT ini FROM rango) AND tj.date < (SELECT fin FROM rango)
  GROUP BY b.id
),
tope AS (
  SELECT budget_id, SUM(amount) AS presupuestado FROM budget_limits
  WHERE start_date < (SELECT fin FROM rango) AND end_date >= (SELECT ini FROM rango)
  GROUP BY budget_id
)
SELECT b.name AS bucket,
       COALESCE(tope.presupuestado,0) AS tope,
       COALESCE(gasto.gastado,0)      AS gastado,
       COALESCE(tope.presupuestado,0)-COALESCE(gasto.gastado,0) AS restante,
       ROUND(100.0*COALESCE(gasto.gastado,0)/NULLIF(tope.presupuestado,0),0) AS pct_usado
FROM budgets b
LEFT JOIN gasto ON gasto.budget_id=b.id
LEFT JOIN tope  ON tope.budget_id=b.id
WHERE b.active AND b.deleted_at IS NULL AND tope.presupuestado IS NOT NULL
ORDER BY b.name;
