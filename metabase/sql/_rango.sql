WITH rango AS (
  SELECT date_trunc('month', MIN(date))                      AS ini,
         date_trunc('month', MAX(date)) + interval '1 month' AS fin
  FROM transaction_journals WHERE deleted_at IS NULL AND {{mes}}
)
