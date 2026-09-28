"""Actual Budget, a traves del puente HTTP (jhonderson/actual-http-api).

Actual no tiene API REST: su API es la libreria de Node `@actual-app/api`, que
baja una copia del presupuesto y sincroniza. El puente es esa misma libreria
detras de HTTP, y corre como un servicio mas del stack (`actual-api`). Este
modulo solo habla HTTP con el puente.

Cosas que no son obvias y estan medidas contra el servidor real:

- **Los montos son enteros en centavos**: 65.000 pesos son 6500000, y un gasto
  es negativo.
- **Crear no devuelve el id** (el puente contesta "ok"). Se busca despues por
  `imported_id`, que es nuestro external_id y es unico.
- **No se usa `import`**: ese endpoint empareja por monto y fecha, y podria
  fundir el movimiento con una venta de Agendapro o con uno que la persona
  anoto a mano, sin decir nada. Es la misma razon por la que el CRM usa
  `addTransactions`. La deduplicacion es nuestra y la confirma la persona.
- **Hay dos categorias «Otros»**, una de ingreso y otra de gasto: el nombre
  solo no identifica una categoria, hace falta la direccion.
- **La llave del puente solo se exige con NODE_ENV=production.** En el stack
  va puesto; sin eso el puente atiende a cualquiera en la red.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

TIMEOUT = 90


class ApiError(Exception):
    def __init__(self, status: int, body: str):
        self.status, self.body = status, body
        super().__init__(f'Actual HTTP {status}: {body[:400]}')


@dataclass(frozen=True)
class Conexion:
    url: str
    llave: str = field(default='', repr=False)
    presupuesto: str = ''


def centavos(valor: float) -> int:
    """Pesos -> el entero que guarda Actual. Negativo = sale plata."""
    return round(float(valor) * 100)


class Cliente:
    """Un presupuesto de Actual concreto."""

    def __init__(self, conexion: Conexion):
        if not conexion.presupuesto:
            raise ValueError('falta el sync_id del presupuesto de Actual')
        self.conexion = conexion
        self._cache: dict[str, Any] = {}

    def __repr__(self) -> str:
        return f'Cliente({self.conexion!r})'

    # ------------------------------------------------------------ transporte

    def _call(
        self, metodo: str, ruta: str, cuerpo: dict[str, Any] | None = None
    ) -> Any:
        base = self.conexion.url.rstrip('/')
        url = f'{base}/v1/budgets/{self.conexion.presupuesto}{ruta}'
        datos = json.dumps(cuerpo).encode('utf-8') if cuerpo is not None else None
        req = urllib.request.Request(url, data=datos, method=metodo)
        req.add_header('x-api-key', self.conexion.llave)
        req.add_header('Accept', 'application/json')
        if datos:
            req.add_header('Content-Type', 'application/json')
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                crudo = r.read().decode('utf-8')
        except urllib.error.HTTPError as ex:
            raise ApiError(ex.code, ex.read().decode('utf-8', 'replace')) from None
        except urllib.error.URLError as ex:
            raise ApiError(
                0, f'no pude llegar al puente de Actual en {base}: {ex.reason}'
            ) from None
        r = json.loads(crudo) if crudo.strip() else {}
        return r.get('data', r) if isinstance(r, dict) else r

    # --------------------------------------------------------------- lecturas

    def cuentas(self) -> list[dict[str, Any]]:
        if 'cuentas' not in self._cache:
            self._cache['cuentas'] = self._call('GET', '/accounts')
        return self._cache['cuentas']

    def cuenta_id(self, nombre: str) -> str | None:
        for c in self.cuentas():
            if c['name'] == nombre and not c.get('closed'):
                return c['id']
        return None

    def categorias(self, ingreso: bool | None = None) -> list[dict[str, Any]]:
        """Las categorias visibles. Con `ingreso` solo las de esa direccion."""
        if 'categorias' not in self._cache:
            self._cache['categorias'] = [
                c for c in self._call('GET', '/categories') if not c.get('hidden')
            ]
        todas = self._cache['categorias']
        if ingreso is None:
            return todas
        return [c for c in todas if bool(c.get('is_income')) == ingreso]

    def categoria_id(self, nombre: str, ingreso: bool) -> str | None:
        for c in self.categorias(ingreso):
            if c['name'] == nombre:
                return c['id']
        return None

    def payee_de_traslado(self, cuenta_id: str) -> str | None:
        """El «payee» que en Actual convierte un movimiento en traslado hacia
        esa cuenta. Asi es como Actual representa un pago de la tarjeta desde
        la cuenta de ahorros: un solo movimiento, no un gasto y un ingreso."""
        if 'payees' not in self._cache:
            self._cache['payees'] = self._call('GET', '/payees')
        for p in self._cache['payees']:
            if p.get('transfer_acct') == cuenta_id:
                return p['id']
        return None

    def transacciones(
        self, cuenta_id: str, desde: date, hasta: date
    ) -> list[dict[str, Any]]:
        q = urllib.parse.urlencode({'since_date': str(desde), 'until_date': str(hasta)})
        return self._call('GET', f'/accounts/{cuenta_id}/transactions?{q}')

    def cerca_de(
        self, cuenta_id: str, fecha: date, dias: int = 3
    ) -> list[dict[str, Any]]:
        return self.transacciones(
            cuenta_id, fecha - timedelta(days=dias), fecha + timedelta(days=dias)
        )

    # --------------------------------------------------------------- escritura

    def crear(self, cuenta_id: str, transaccion: dict[str, Any]) -> str | None:
        """Crea el movimiento y devuelve su id, buscandolo por imported_id."""
        self._call(
            'POST',
            f'/accounts/{cuenta_id}/transactions',
            {
                'learnCategories': False,
                'runTransfers': True,
                'transaction': transaccion,
            },
        )
        fecha = date.fromisoformat(transaccion['date'])
        for t in self.transacciones(cuenta_id, fecha, fecha):
            if t.get('imported_id') == transaccion.get('imported_id'):
                return t['id']
        return None

    def actualizar(self, transaccion_id: str, campos: dict[str, Any]) -> None:
        self._call('PATCH', f'/transactions/{transaccion_id}', {'transaction': campos})

    def borrar(self, transaccion_id: str) -> None:
        self._call('DELETE', f'/transactions/{transaccion_id}')
