-- Deuda por tarjeta de credito. Antes esto listaba 4 nombres a mano; ahora
-- sale del rol de la cuenta en Firefly (ccAsset), asi que una tarjeta nueva
-- aparece sola y renombrar una no la borra de la grafica.
-- Sin filtro de fecha a proposito: es un saldo, no un flujo del periodo.
SELECT a.name AS tarjeta,
       -SUM(t.amount) AS deuda
FROM accounts a
JOIN account_meta am           ON am.account_id = a.id
                              AND am.name = 'account_role'
                              AND am.data LIKE '%ccAsset%'
JOIN transactions t            ON t.account_id = a.id
JOIN transaction_journals tj   ON tj.id = t.transaction_journal_id
JOIN transaction_currencies tc ON tc.id = t.transaction_currency_id
WHERE a.deleted_at IS NULL AND a.active
  AND t.deleted_at IS NULL
  AND tj.deleted_at IS NULL
  AND tc.code = 'COP'
GROUP BY a.name
HAVING -SUM(t.amount) <> 0
ORDER BY deuda DESC;
