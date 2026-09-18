WITH rango AS (
  SELECT date_trunc('month', MIN(date))                      AS ini,
         date_trunc('month', MAX(date)) + interval '1 month' AS fin
  FROM transaction_journals WHERE deleted_at IS NULL AND {{mes}}
)
, mov AS (
  SELECT tj.id,
    MAX(CASE WHEN tt.type='Withdrawal' THEN 1 ELSE 0 END) AS is_wd,
    MAX(CASE WHEN tt.type='Transfer'   THEN 1 ELSE 0 END) AS is_tr,
    MAX(tr.amount) AS amount,
    MAX(CASE WHEN g.tag='Inversión'      THEN 1 ELSE 0 END) AS es_inv,
    MAX(CASE WHEN g.tag='Extraordinario' THEN 1 ELSE 0 END) AS es_extra,
    MAX(CASE WHEN g.tag='DeudaFija'      THEN 1 ELSE 0 END) AS es_deuda
  FROM transaction_journals tj
  JOIN transaction_types tt ON tt.id=tj.transaction_type_id
  JOIN transactions tr ON tr.transaction_journal_id=tj.id AND tr.amount>0 AND tr.deleted_at IS NULL
  JOIN transaction_currencies tc ON tc.id=tr.transaction_currency_id
  LEFT JOIN tag_transaction_journal ttj ON ttj.transaction_journal_id=tj.id
  LEFT JOIN tags g ON g.id=ttj.tag_id
  WHERE tj.deleted_at IS NULL AND tc.code='COP'
    AND tj.date >= (SELECT ini FROM rango) AND tj.date < (SELECT fin FROM rango)
  GROUP BY tj.id
),
agg AS (
  SELECT
    COALESCE(SUM(amount) FILTER (WHERE is_wd=1 AND es_inv=0 AND es_extra=0 AND es_deuda=0),0) AS gastado,
    COALESCE(SUM(amount) FILTER (WHERE es_inv=1),0) AS invertido,
    COALESCE(SUM(amount) FILTER (WHERE is_tr=1 AND es_deuda=1),0) AS deuda_fija,
    (SELECT COALESCE(SUM(pm.monto),0) FROM finanzas.parametro_mes pm, rango
      WHERE pm.concepto='salario_base' AND pm.mes >= rango.ini AND pm.mes < rango.fin) AS ingreso_plan
  FROM mov
)
SELECT 'Gastado'  AS concepto, gastado    AS monto FROM agg
UNION ALL SELECT 'Invertido',  invertido  FROM agg
UNION ALL SELECT 'Deuda fija', deuda_fija FROM agg
UNION ALL SELECT 'Disponible', GREATEST(ingreso_plan - gastado - invertido - deuda_fija, 0) FROM agg
ORDER BY monto DESC;
