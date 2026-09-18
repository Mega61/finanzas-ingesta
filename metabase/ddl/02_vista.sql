-- Expande parametro_mensual a un valor por mes calendario, arrastrando
-- el ultimo valor vigente. Asi una ventana de N meses suma el valor
-- correcto de cada mes, incluso si hubo un aumento en la mitad.
CREATE OR REPLACE VIEW finanzas.parametro_mes AS
SELECT c.concepto,
       g.mes::date AS mes,
       (SELECT p.monto FROM finanzas.parametro_mensual p
         WHERE p.concepto = c.concepto AND p.vigente_desde <= g.mes
         ORDER BY p.vigente_desde DESC LIMIT 1) AS monto
FROM (SELECT DISTINCT concepto FROM finanzas.parametro_mensual) c
CROSS JOIN generate_series(
       (SELECT date_trunc('month', MIN(vigente_desde)) FROM finanzas.parametro_mensual),
       date_trunc('month', CURRENT_DATE) + interval '36 months',
       interval '1 month') g(mes);
