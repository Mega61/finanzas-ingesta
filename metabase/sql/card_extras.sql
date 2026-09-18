WITH rango AS (
  SELECT date_trunc('month', MIN(date))                      AS ini,
         date_trunc('month', MAX(date)) + interval '1 month' AS fin
  FROM transaction_journals WHERE deleted_at IS NULL AND {{mes}}
),
base AS (
  SELECT COALESCE(SUM(pm.monto),0) AS monto
  FROM finanzas.parametro_mes pm, rango
  WHERE pm.concepto='salario_base' AND pm.mes >= rango.ini AND pm.mes < rango.fin
),
recibido AS (
  SELECT COALESCE(SUM(t.amount),0) AS monto
  FROM transaction_journals tj
  JOIN transactions t ON t.transaction_journal_id=tj.id AND t.amount>0 AND t.deleted_at IS NULL
  JOIN transaction_types tt ON tt.id=tj.transaction_type_id AND tt.type='Deposit'
  JOIN transaction_currencies tc ON tc.id=t.transaction_currency_id AND tc.code='COP'
  WHERE tj.deleted_at IS NULL
    AND tj.date >= (SELECT ini FROM rango) AND tj.date < (SELECT fin FROM rango)
)
SELECT 'Salario base (lo que planeas)' AS concepto, (SELECT monto FROM base) AS monto
UNION ALL SELECT 'Ingreso real recibido',  (SELECT monto FROM recibido)
UNION ALL SELECT 'Extra sobre la base',    (SELECT monto FROM recibido) - (SELECT monto FROM base);
