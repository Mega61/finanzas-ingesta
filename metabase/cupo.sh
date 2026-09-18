#!/usr/bin/env bash
# Cupo de una tarjeta. Los extractos de Bancolombia se cargan solos; esto es para
# tarjetas sin extracto (Rappicard) o para un cambio que el extracto aun no refleja.
#   ./metabase/cupo.sh "RAPPICARD BLACK" 2026-09-01 9800000 "confirmado en la app"
#   ./metabase/cupo.sh --listar
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "${1:-}" = "--listar" ]; then
  ./mbq.sh 2 "SELECT tarjeta, vigente_desde, cupo, fuente, left(COALESCE(nota,''),45) AS nota FROM finanzas.v_cupo ORDER BY cupo DESC"
  exit 0
fi
tarjeta="$1"; desde="$2"; cupo="$3"; nota="${4:-}"
./mbq.sh 2 "INSERT INTO finanzas.cupo_tarjeta (tarjeta,vigente_desde,cupo,nota)
            VALUES ('$tarjeta','$desde',$cupo,$( [ -n "$nota" ] && echo "'$nota'" || echo NULL ))
            ON CONFLICT (tarjeta,vigente_desde) DO UPDATE SET cupo=EXCLUDED.cupo, nota=EXCLUDED.nota" 2>/dev/null || true
echo "listo:"
./mbq.sh 2 "SELECT tarjeta, vigente_desde, cupo, fuente FROM finanzas.v_cupo WHERE tarjeta='$tarjeta'"
