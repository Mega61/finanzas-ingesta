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
from dataclasses import dataclass, field
from typing import Any

from finanzas import config

# Si Firefly esta detras de Cloudflare, el user-agent por defecto de urllib es
# rechazado (error 1010). Hablandole por la red interna de Docker esto no hace
# falta, pero no estorba.
UA = config.get('FIREFLY_UA') or (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36'
)

TIMEOUT = int(config.get('FIREFLY_TIMEOUT', '60'))


class ApiError(Exception):
    def __init__(self, status: int, body: str):
        self.status, self.body = status, body
        super().__init__(f'HTTP {status}: {body[:600]}')


@dataclass(frozen=True)
class Conexion:
    """A que Firefly hablarle y con que token. Una por libro.

    Hasta aqui habia UNA, implicita: la del .env. Con dos personas en la misma
    instancia de Firefly, cada una con su usuario y su token, hablarle al
    Firefly equivocado es publicar en la contabilidad de otro.
    """

    url: str
    # repr=False: el token no sale ni en un traceback ni en un log.
    token: str = field(default='', repr=False)


def del_entorno() -> Conexion:
    """La conexion de siempre: FIREFLY_URL y FIREFLY_TOKEN del entorno."""
    url, tok = config.requerir('FIREFLY_URL', 'FIREFLY_TOKEN')
    return Conexion(url.rstrip('/'), tok)


def _base(conexion: Conexion | None = None) -> str:
    return (conexion or del_entorno()).url.rstrip('/')


def call(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    conexion: Conexion | None = None,
) -> dict[str, Any]:
    conexion = conexion or del_entorno()
    url = _base(conexion) + path
    datos = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(url, data=datos, method=method)
    req.add_header('Authorization', 'Bearer ' + conexion.token)
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
        raise ApiError(0, f'no pude conectarme a {conexion.url}: {ex.reason}') from None


def get_all(path: str, conexion: Conexion | None = None) -> list[dict[str, Any]]:
    """GET paginado -> lista de objetos data[]."""
    salida, pagina = [], 1
    while True:
        sep = '&' if '?' in path else '?'
        r = call('GET', f'{path}{sep}page={pagina}&limit=100', conexion=conexion)
        salida.extend(r.get('data', []))
        pg = r.get('meta', {}).get('pagination', {})
        if pagina >= pg.get('total_pages', 1):
            return salida
        pagina += 1


def whoami(conexion: Conexion | None = None) -> str:
    a = call('GET', '/api/v1/about', conexion=conexion)['data']
    return f'Firefly III v{a.get("version")} · API {a.get("api_version")} · {_base(conexion)}'


def accounts_index(conexion: Conexion | None = None) -> dict[str, dict[str, Any]]:
    """{nombre: {'id','type','active'}}. Si un nombre existe en varios tipos gana
    asset y luego liabilities: son los unicos que hay que referenciar por id."""
    PRIO = {'asset': 0, 'liabilities': 1}
    idx = {}
    for a in get_all('/api/v1/accounts', conexion=conexion):
        at = a['attributes']
        n = at['name']
        cur = idx.get(n)
        if cur is None or PRIO.get(at['type'], 9) < PRIO.get(cur['type'], 9):
            idx[n] = {
                'id': a['id'],
                'type': at['type'],
                'active': at.get('active', True),
            }
    return idx


def budgets_index(conexion: Conexion | None = None) -> dict[str, str]:
    return {
        b['attributes']['name']: b['id']
        for b in get_all('/api/v1/budgets', conexion=conexion)
    }


def buscar_por_external_id(
    external_id: str, conexion: Conexion | None = None
) -> str | None:
    """La red de idempotencia: si esta transaccion ya se publico, devuelve su id.

    Firefly guarda external_id por transaction_journal. Se consulta antes de
    crear para que un reintento no duplique.
    """
    q = urllib.parse.quote(f'external_id:"{external_id}"')
    try:
        r = call(
            'GET', f'/api/v1/search/transactions?query={q}&limit=5', conexion=conexion
        )
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


def _splits(tx_id: str, conexion: Conexion | None = None) -> list[dict[str, Any]]:
    t = call('GET', f'/api/v1/transactions/{tx_id}', conexion=conexion)
    return t['data']['attributes']['transactions']


def quitar_etiqueta(
    tx_id: str, etiqueta: str, conexion: Conexion | None = None
) -> bool:
    """Saca una etiqueta de todos los splits. Devuelve True si cambio algo."""
    nuevos, cambio = [], False
    for s in _splits(tx_id, conexion=conexion):
        tags = list(s.get('tags') or [])
        if etiqueta in tags:
            tags = [x for x in tags if x != etiqueta]
            cambio = True
        nuevos.append(
            {'transaction_journal_id': s.get('transaction_journal_id'), 'tags': tags}
        )
    if not cambio:
        return False
    call(
        'PUT',
        f'/api/v1/transactions/{tx_id}',
        {'transactions': nuevos},
        conexion=conexion,
    )
    return True


def agregar_etiqueta(
    tx_id: str, *etiquetas: str, conexion: Conexion | None = None
) -> list[str]:
    """Agrega etiquetas SIN borrar las que ya estan. Devuelve la lista final.

    La API reemplaza `tags` completo, asi que hay que leer, unir y escribir. Si
    se mandara solo la nueva se perderian `sin-confirmar` e
    `ingesta-automatica`, que son las que usa la conciliacion para saber que
    falta cruzar contra el extracto.
    """
    nuevos = [e.strip() for e in etiquetas if e and e.strip()]
    if not nuevos:
        return []
    cambios, final = [], []
    for s in _splits(tx_id, conexion=conexion):
        tags = list(s.get('tags') or [])
        bajas = {t.lower() for t in tags}
        for e in nuevos:
            if e.lower() not in bajas:
                tags.append(e)
                bajas.add(e.lower())
        final = tags
        cambios.append(
            {'transaction_journal_id': s.get('transaction_journal_id'), 'tags': tags}
        )
    call(
        'PUT',
        f'/api/v1/transactions/{tx_id}',
        {'transactions': cambios},
        conexion=conexion,
    )
    return final


def cambiar_monto(
    tx_id: str,
    monto: float,
    nota_extra: str | None = None,
    conexion: Conexion | None = None,
) -> bool:
    """Corrige el monto cuando el extracto trae otro. Solo para transacciones
    de un solo split: si hay varios no se toca, se avisa."""
    ss = _splits(tx_id, conexion=conexion)
    if len(ss) != 1:
        raise ApiError(0, f'la transaccion {tx_id} tiene {len(ss)} splits, no la toco')
    s = ss[0]
    cambio = {
        'transaction_journal_id': s.get('transaction_journal_id'),
        'amount': f'{abs(float(monto)):.2f}',
    }
    if nota_extra:
        cambio['notes'] = ((s.get('notes') or '') + '\n' + nota_extra).strip()[:4000]
    call(
        'PUT',
        f'/api/v1/transactions/{tx_id}',
        {'transactions': [cambio]},
        conexion=conexion,
    )
    return True


def borrar(tx_id: str, conexion: Conexion | None = None) -> bool:
    call('DELETE', f'/api/v1/transactions/{tx_id}', conexion=conexion)
    return True


def actualizar_split(
    tx_id: str, conexion: Conexion | None = None, **campos: Any
) -> bool:
    """Cambia campos de una transaccion de un solo split.

    Campos utiles: category_name, budget_name, destination_name,
    source_name, description, tags, notes.
    """
    ss = _splits(tx_id, conexion=conexion)
    if len(ss) != 1:
        raise ApiError(0, f'la transaccion {tx_id} tiene {len(ss)} splits, no la toco')
    cambio = {'transaction_journal_id': ss[0].get('transaction_journal_id')}
    cambio.update(campos)
    call(
        'PUT',
        f'/api/v1/transactions/{tx_id}',
        {'transactions': [cambio]},
        conexion=conexion,
    )
    return True


def destinos_recurrentes(conexion: Conexion | None = None) -> dict[str, float]:
    """Cuenta -> monto del recibo que Firefly ya crea solo cada mes.

    Son los programados: Tigo, el arriendo, el gimnasio, las cuotas de manejo,
    Amazon Prime. Firefly los crea por su cuenta, asi que si la ingesta publica
    ADEMAS el cargo que llega por la alerta del banco, el recibo queda contado
    dos veces.

    El anti-duplicado por monto no los agarra: el programado lleva el monto de
    siempre (119.900 de Tigo) y el cargo real trae el del mes (129.721), que
    nunca se parecen lo suficiente para chocar.

    Va el MONTO y no solo el nombre porque una cuenta puede recibir cosas que
    no son el recibo: a 'Bancolombia' le entran las cuotas de manejo de tres
    tarjetas y tambien cualquier traslado. Tapar todo lo que le llegue seria
    peor que el duplicado.
    """
    salida: dict[str, float] = {}
    for r in get_all('/api/v1/recurrences', conexion=conexion):
        a = r.get('attributes', {})
        if not a.get('active', True):
            continue
        for t in a.get('transactions', []) or []:
            nombre = t.get('destination_name')
            if not nombre:
                continue
            try:
                salida[nombre] = float(t.get('amount') or 0)
            except (TypeError, ValueError):
                continue
    return salida


# ------------------------------------------------------------------ cliente


class Cliente:
    """Un Firefly concreto: el del libro de una persona.

    Tiene los mismos nombres que las funciones del modulo, asi que donde antes
    se usaba el modulo (`firefly.get_all(...)`) sirve un cliente
    (`cliente.get_all(...)`). Delega en esas funciones y no las repite: lo que
    se arregle ahi vale para los dos, y las pruebas que reemplazan
    `firefly.call` siguen interceptando todo.
    """

    def __init__(self, conexion: Conexion):
        self.conexion = conexion

    def __repr__(self) -> str:
        return f'Cliente({self.conexion!r})'

    def call(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return call(method, path, payload, conexion=self.conexion)

    def get_all(self, path: str) -> list[dict[str, Any]]:
        return get_all(path, conexion=self.conexion)

    def whoami(self) -> str:
        return whoami(conexion=self.conexion)

    def accounts_index(self) -> dict[str, dict[str, Any]]:
        return accounts_index(conexion=self.conexion)

    def budgets_index(self) -> dict[str, str]:
        return budgets_index(conexion=self.conexion)

    def buscar_por_external_id(self, external_id: str) -> str | None:
        return buscar_por_external_id(external_id, conexion=self.conexion)

    def quitar_etiqueta(self, tx_id: str, etiqueta: str) -> bool:
        return quitar_etiqueta(tx_id, etiqueta, conexion=self.conexion)

    def agregar_etiqueta(self, tx_id: str, *etiquetas: str) -> list[str]:
        return agregar_etiqueta(tx_id, *etiquetas, conexion=self.conexion)

    def cambiar_monto(
        self, tx_id: str, monto: float, nota_extra: str | None = None
    ) -> bool:
        return cambiar_monto(tx_id, monto, nota_extra, conexion=self.conexion)

    def borrar(self, tx_id: str) -> bool:
        return borrar(tx_id, conexion=self.conexion)

    def actualizar_split(self, tx_id: str, **campos: Any) -> bool:
        return actualizar_split(tx_id, conexion=self.conexion, **campos)

    def destinos_recurrentes(self) -> dict[str, float]:
        return destinos_recurrentes(conexion=self.conexion)
