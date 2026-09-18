#!/usr/bin/env bash
# Fija un parametro mensual desde una fecha.  Uso:
#   ./metabase/parametro.sh salario_base 2027-01-01 9200000 "aumento enero"
#   ./metabase/parametro.sh --listar
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "${1:-}" = "--listar" ]; then
  ./mbq.sh 2 "SELECT concepto, vigente_desde::date, monto, nota FROM finanzas.parametro_mensual ORDER BY concepto, vigente_desde"
  exit 0
fi
concepto="$1"; desde="$2"; monto="$3"; nota="${4:-}"
./mbq.sh 2 "INSERT INTO finanzas.parametro_mensual (concepto,vigente_desde,monto,nota)
            VALUES ('$concepto','$desde',$monto,$( [ -n "$nota" ] && echo "'$nota'" || echo NULL ))
            ON CONFLICT (concepto,vigente_desde) DO UPDATE SET monto=EXCLUDED.monto, nota=EXCLUDED.nota" 2>/dev/null || true
echo "listo. estado actual:"
./mbq.sh 2 "SELECT concepto, vigente_desde::date, monto, nota FROM finanzas.parametro_mensual WHERE concepto='$concepto' ORDER BY vigente_desde"
