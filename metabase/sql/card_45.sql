WITH rango AS (
  SELECT date_trunc('month', MIN(date))                      AS ini,
         date_trunc('month', MAX(date)) + interval '1 month' AS fin
  FROM transaction_journals WHERE deleted_at IS NULL AND {{mes}}
)
, actual_budget AS (
  SELECT b.name AS concepto, SUM(t.amount) AS actual
  FROM transaction_journals tj
  JOIN transactions t ON t.transaction_journal_id=tj.id AND t.amount>0 AND t.deleted_at IS NULL
  JOIN budget_transaction_journal btj ON btj.transaction_journal_id=tj.id
  JOIN budgets b ON b.id=btj.budget_id
  JOIN transaction_types tt ON tt.id=tj.transaction_type_id
  JOIN transaction_currencies tc ON tc.id=t.transaction_currency_id
  WHERE tt.type='Withdrawal' AND tj.deleted_at IS NULL AND tc.code='COP'
    AND tj.date >= (SELECT ini FROM rango) AND tj.date < (SELECT fin FROM rango)
  GROUP BY b.name
),
plan_budget AS (
  SELECT b.name AS concepto, SUM(bl.amount) AS plan
  FROM budget_limits bl JOIN budgets b ON b.id=bl.budget_id
  WHERE bl.start_date < (SELECT fin FROM rango) AND bl.end_date >= (SELECT ini FROM rango)
  GROUP BY b.name
),
inv AS (
  SELECT COALESCE(SUM(t.amount),0) actual FROM transaction_journals tj
  JOIN transactions t ON t.transaction_journal_id=tj.id AND t.amount>0 AND t.deleted_at IS NULL
  JOIN tag_transaction_journal ttj ON ttj.transaction_journal_id=tj.id
  JOIN tags g ON g.id=ttj.tag_id AND g.tag='Inversión'
  JOIN transaction_currencies tc2 ON tc2.id=t.transaction_currency_id
  WHERE tj.deleted_at IS NULL AND tc2.code='COP'
    AND tj.date >= (SELECT ini FROM rango) AND tj.date < (SELECT fin FROM rango)
),
deuda AS (
  SELECT COALESCE(SUM(t.amount),0) actual FROM transaction_journals tj
  JOIN transactions t ON t.transaction_journal_id=tj.id AND t.amount>0 AND t.deleted_at IS NULL
  JOIN tag_transaction_journal ttj ON ttj.transaction_journal_id=tj.id
  JOIN tags g ON g.id=ttj.tag_id AND g.tag='DeudaFija'
  JOIN transaction_currencies tc2 ON tc2.id=t.transaction_currency_id
  WHERE tj.deleted_at IS NULL AND tc2.code='COP'
    AND tj.date >= (SELECT ini FROM rango) AND tj.date < (SELECT fin FROM rango)
)
SELECT pb.concepto, pb.plan, COALESCE(ab.actual,0) AS actual
FROM plan_budget pb LEFT JOIN actual_budget ab ON ab.concepto=pb.concepto
UNION ALL SELECT 'Inversión',  (SELECT COALESCE(SUM(pm.monto),0) FROM finanzas.parametro_mes pm, rango
    WHERE pm.concepto='plan_inversion' AND pm.mes >= rango.ini AND pm.mes < rango.fin), (SELECT actual FROM inv)
UNION ALL SELECT 'Deuda fija', (SELECT COALESCE(SUM(pm.monto),0) FROM finanzas.parametro_mes pm, rango
    WHERE pm.concepto='plan_deuda_fija' AND pm.mes >= rango.ini AND pm.mes < rango.fin), (SELECT actual FROM deuda)
ORDER BY plan DESC;
