-- Parametros mensuales del dashboard Control Financiero.
-- Un cambio de salario = una fila nueva. El historico nunca se reescribe:
-- agosto siempre se compara contra el salario vigente en agosto.
CREATE TABLE IF NOT EXISTS finanzas.parametro_mensual (
  concepto      text          NOT NULL,
  vigente_desde date          NOT NULL,
  monto         numeric(14,2) NOT NULL,
  nota          text,
  PRIMARY KEY (concepto, vigente_desde)
);
