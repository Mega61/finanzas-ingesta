INSERT INTO finanzas.parametro_mensual (concepto, vigente_desde, monto, nota) VALUES
  ('salario_base',    '2025-07-01',  6912964, 'ESTIMADO de los depositos 2025 (Q1+Q2 de octubre). Confirmar.'),
  ('salario_base',    '2026-02-01',  7948200, 'Q1 4.128.500 + Q2 3.819.700 - Andante Labs'),
  ('salario_base',    '2026-08-01',  8617050, 'Q1 4.211.025 + Q2 4.406.025 - Andante Labs'),
  ('plan_inversion',  '2025-07-01',  1160000, 'valor que estaba hardcodeado en la card 45'),
  ('plan_deuda_fija', '2025-07-01',   261955, 'valor que estaba hardcodeado en la card 45')
ON CONFLICT (concepto, vigente_desde) DO UPDATE
  SET monto = EXCLUDED.monto, nota = EXCLUDED.nota;
