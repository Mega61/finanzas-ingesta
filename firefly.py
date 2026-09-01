"""Cliente de la API de Firefly III.

Version autocontenida de reconciliacion/api/_firefly.py: lee la configuracion
de config.py en vez de un archivo dos niveles arriba, para que el contenedor
funcione sin depender de nada de afuera de automatizacion/.

Nunca imprime el token.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import config

# Si Firefly esta detras de Cloudflare, el user-agent por defecto de urllib es
# rechazado (error 1010). Hablandole por la red interna de Docker esto no hace
# falta, pero no estorba.
UA = config.get('FIREFLY_UA') or (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36')

TIMEOUT = int(config.get('FIREFLY_TIMEOUT', '60'))


class ApiError(Exception):
    def __init__(self, status: int, body: str):
        self.status, self.body = status, body
        super().__init__(f"HTTP {status}: {body[:600]}")


def _base() -> str:
    url, _ = config.requerir('FIREFLY_URL', 'FIREFLY_TOKEN')
    return url.rstrip('/')


def call(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    url = _base() + path
    datos = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(url, data=datos, method=method)
    req.add_header('Authorization', 'Bearer ' + config.get('FIREFLY_TOKEN'))
    req.add_header('Accept', 'application/json')
    req.add_header('User-Agent', UA)
    if datos:
        req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            crudo = r.read().decode('utf-8')
            return json.loads(crudo) if crudo.strip() else {}
    except urllib.error.HTTPError as ex:
        raise ApiError(ex.code, ex.read().decode('utf-8', 'replace')) from None
    except urllib.error.URLError as ex:
        raise ApiError(0, f"no pude conectarme a {_base()}: {ex.reason}") from None


def get_all(path: str) -> list[dict[str, Any]]:
    """GET paginado -> lista de objetos data[]."""
    salida, pagina = [], 1
    while True:
        sep = '&' if '?' in path else '?'
        r = call('GET', f"{path}{sep}page={pagina}&limit=100")
        salida.extend(r.get('data', []))
        pg = r.get('meta', {}).get('pagination', {})
        if pagina >= pg.get('total_pages', 1):
            return salida
        pagina += 1


def whoami() -> str:
    a = call('GET', '/api/v1/about')['data']
    return f"Firefly III v{a.get('version')} · API {a.get('api_version')} · {_base()}"


def accounts_index() -> dict[str, dict[str, Any]]:
    """{nombre: {'id','type','active'}}. Si un nombre existe en varios tipos gana
    asset y luego liabilities: son los unicos que hay que referenciar por id."""
    PRIO = {'asset': 0, 'liabilities': 1}
    idx = {}
    for a in get_all('/api/v1/accounts'):
        at = a['attributes']
        n = at['name']
        cur = idx.get(n)
        if cur is None or PRIO.get(at['type'], 9) < PRIO.get(cur['type'], 9):
            idx[n] = {'id': a['id'], 'type': at['type'],
                      'active': at.get('active', True)}
    return idx


def budgets_index() -> dict[str, str]:
    return {b['attributes']['name']: b['id'] for b in get_all('/api/v1/budgets')}


def buscar_por_external_id(external_id: str) -> str | None:
    """La red de idempotencia: si esta transaccion ya se publico, devuelve su id.

    Firefly guarda external_id por transaction_journal. Se consulta antes de
    crear para que un reintento no duplique.
    """
    q = urllib.parse.quote(f'external_id:"{external_id}"')
    try:
        r = call('GET', f'/api/v1/search/transactions?query={q}&limit=5')
    except ApiError:
        return None
    for t in r.get('data', []):
        for split in t.get('attributes', {}).get('transactions', []):
            if split.get('external_id') == external_id:
                return t['id']
    return None


# ------------------------------------------------- edicion de transacciones
# Se usan en la conciliacion: confirmar quita la etiqueta, corregir cambia el
# monto. Firefly exige mandar el transaction_journal_id de cada split.

def _splits(tx_id: str) -> list[dict[str, Any]]:
    t = call('GET', f'/api/v1/transactions/{tx_id}')
    return t['data']['attributes']['transactions']


def quitar_etiqueta(tx_id: str, etiqueta: str) -> bool:
    """Saca una etiqueta de todos los splits. Devuelve True si cambio algo."""
    nuevos, cambio = [], False
    for s in _splits(tx_id):
        tags = list(s.get('tags') or [])
        if etiqueta in tags:
            tags = [x for x in tags if x != etiqueta]
            cambio = True
        nuevos.append({'transaction_journal_id': s.get('transaction_journal_id'),
                       'tags': tags})
    if not cambio:
        return False
    call('PUT', f'/api/v1/transactions/{tx_id}', {'transactions': nuevos})
    return True


def cambiar_monto(tx_id: str, monto: float, nota_extra: str | None = None) -> bool:
    """Corrige el monto cuando el extracto trae otro. Solo para transacciones
    de un solo split: si hay varios no se toca, se avisa."""
    ss = _splits(tx_id)
    if len(ss) != 1:
        raise ApiError(0, f'la transaccion {tx_id} tiene {len(ss)} splits, no la toco')
    s = ss[0]
    cambio = {'transaction_journal_id': s.get('transaction_journal_id'),
              'amount': f"{abs(float(monto)):.2f}"}
    if nota_extra:
        cambio['notes'] = ((s.get('notes') or '') + '\n' + nota_extra).strip()[:4000]
    call('PUT', f'/api/v1/transactions/{tx_id}', {'transactions': [cambio]})
    return True


def borrar(tx_id: str) -> bool:
    call('DELETE', f'/api/v1/transactions/{tx_id}')
    return True


def actualizar_split(tx_id: str, **campos: Any) -> bool:
    """Cambia campos de una transaccion de un solo split.

    Campos utiles: category_name, budget_name, destination_name,
    source_name, description, tags, notes.
    """
    ss = _splits(tx_id)
    if len(ss) != 1:
        raise ApiError(0, f'la transaccion {tx_id} tiene {len(ss)} splits, no la toco')
    cambio = {'transaction_journal_id': ss[0].get('transaction_journal_id')}
    cambio.update(campos)
    call('PUT', f'/api/v1/transactions/{tx_id}', {'transactions': [cambio]})
    return True
