WITH rango AS (
  SELECT date_trunc('month', MIN(date))                      AS ini,
         date_trunc('month', MAX(date)) + interval '1 month' AS fin
  FROM transaction_journals WHERE deleted_at IS NULL AND {{mes}}
)
SELECT COALESCE(SUM(t.amount),0) AS invertido
FROM transaction_journals tj
JOIN transactions t ON t.transaction_journal_id=tj.id AND t.amount>0 AND t.deleted_at IS NULL
JOIN tag_transaction_journal ttj ON ttj.transaction_journal_id=tj.id
JOIN tags g ON g.id=ttj.tag_id AND g.tag='Inversión'
JOIN transaction_currencies tc ON tc.id=t.transaction_currency_id
WHERE tj.deleted_at IS NULL AND tc.code='COP'
  AND tj.date >= (SELECT ini FROM rango) AND tj.date < (SELECT fin FROM rango);
