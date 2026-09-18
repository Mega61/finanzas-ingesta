WITH rango AS (
  SELECT date_trunc('month', MIN(date))                      AS ini,
         date_trunc('month', MAX(date)) + interval '1 month' AS fin
  FROM transaction_journals WHERE deleted_at IS NULL AND {{mes}}
)
SELECT
  (SELECT COALESCE(SUM(amount),0) FROM budget_limits
   WHERE start_date < (SELECT fin FROM rango) AND end_date >= (SELECT ini FROM rango))
  -
  (SELECT COALESCE(SUM(t.amount),0)
   FROM transaction_journals tj
   JOIN transactions t ON t.transaction_journal_id=tj.id AND t.amount>0 AND t.deleted_at IS NULL
   JOIN budget_transaction_journal btj ON btj.transaction_journal_id=tj.id
   JOIN transaction_types tt ON tt.id=tj.transaction_type_id
   JOIN transaction_currencies tc ON tc.id=t.transaction_currency_id
   WHERE tt.type='Withdrawal' AND tj.deleted_at IS NULL AND tc.code='COP'
     AND tj.date >= (SELECT ini FROM rango) AND tj.date < (SELECT fin FROM rango)) AS por_gastar;
