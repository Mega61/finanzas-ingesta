"""Consultar y editar movimientos concretos de Firefly.

Faltaba, y se notaba: el asesor solo recibia agregados —saldos, presupuestos,
gasto del mes por categoria— y ni un movimiento individual. Preguntarle «cual
fue la ultima transaccion» era imposible, no porque el modelo no supiera
responder sino porque nadie le habia dado el dato.

Y editar tampoco se podia. El bot solo sabia resolver preguntas ABIERTAS; una
vez cerrada, la unica forma de corregir algo era entrar a Firefly a mano.

Aqui vive las dos mitades: leer movimientos y cambiarlos. Todo contra Firefly,
que es la fuente de verdad de lo que ya esta registrado.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from finanzas.adaptadores import firefly
from finanzas.dominio import fechas
from finanzas.dominio import texto as _texto

# Cuantos dias hacia atras se miran por defecto. Un mes cubre «la ultima»,
# «las de esta semana» y «lo del mes» sin traer miles de filas.
DIAS_POR_DEFECTO = 35

# La etiqueta que pone la ingesta y que quita la conciliacion.
SIN_CONFIRMAR = 'sin-confirmar'


def _plano(t: dict[str, Any], s: dict[str, Any]) -> dict[str, Any]:
    """Una transaccion de Firefly aplanada a lo que hace falta.

    Firefly devuelve la transaccion con sus splits anidados y los montos como
    texto sin signo. Aqui el signo lo da el tipo: un `withdrawal` es negativo.
    """
    try:
        monto = float(s.get('amount') or 0)
    except (TypeError, ValueError):
        monto = 0.0
    tipo = s.get('type') or ''
    if tipo == 'withdrawal':
        monto = -abs(monto)
    elif tipo == 'deposit':
        monto = abs(monto)
    return {
        'id': t.get('id'),
        'fecha': str(s.get('date') or '')[:10],
        'valor': monto,
        'moneda': s.get('currency_code') or 'COP',
        'descripcion': s.get('description') or '',
        'categoria': s.get('category_name'),
        'presupuesto': s.get('budget_name'),
        'origen': s.get('source_name'),
        'destino': s.get('destination_name'),
        'etiquetas': list(s.get('tags') or []),
        'tipo': tipo,
        'notas': s.get('notes') or '',
        'partes': 1,
    }


def ultimos(limite: int = 15, dias: int = DIAS_POR_DEFECTO) -> list[dict]:
    """Los movimientos mas recientes, el mas nuevo primero.

    Firefly no garantiza el orden de la paginacion, asi que se ordena aqui por
    fecha y despues por id: dos compras del mismo dia se desempatan por cual
    entro despues, que es lo que quiere decir «la ultima».
    """
    desde = fechas.hoy() - timedelta(days=dias)
    ruta = f'/api/v1/transactions?start={desde}&end={fechas.hoy()}'
    fuera = []
    for t in firefly.get_all(ruta):
        splits = t.get('attributes', {}).get('transactions', [])
        for s in splits:
            fila = _plano(t, s)
            fila['partes'] = len(splits)
            fuera.append(fila)
    fuera.sort(key=lambda x: (x['fecha'], int(x['id'] or 0)), reverse=True)
    return fuera[:limite]


def buscar(
    consulta: str | None = None,
    dias: int = DIAS_POR_DEFECTO,
    limite: int = 20,
    categoria: str | None = None,
) -> list[dict]:
    """Movimientos que coincidan con `consulta` en comercio o categoria.

    La coincidencia usa los tokens distintivos, igual que el resto del sistema:
    buscar «tierragro» encuentra 'MERCADO PAGO*TIERRAG' aunque el banco lo
    truncara.
    """
    todos = ultimos(limite=10_000, dias=dias)
    if categoria:
        objetivo = _texto.normalizar(categoria)
        todos = [m for m in todos if _texto.normalizar(m['categoria']) == objetivo]
    if not consulta:
        return todos[:limite]

    palabras = _texto.tokens_distintivos(consulta)
    if not palabras:
        return todos[:limite]

    def puntaje(m: dict) -> int:
        campos = f'{m["descripcion"]} {m["destino"] or ""} {m["categoria"] or ""}'
        del_mov = _texto.tokens_distintivos(campos)
        n = len(palabras & del_mov)
        if n:
            return n * 2
        # el banco trunca: 'TIERRAG' es prefijo de 'TIERRAGRO'
        for a in palabras:
            for b in del_mov:
                corto, largo = sorted((a, b), key=len)
                if len(corto) >= 5 and largo.startswith(corto):
                    return 1
        return 0

    con_puntaje = [(puntaje(m), m) for m in todos]
    return [m for p, m in con_puntaje if p > 0][:limite]


def uno(tx_id: str) -> dict | None:
    """Un movimiento por su id de Firefly."""
    try:
        t = firefly.call('GET', f'/api/v1/transactions/{tx_id}')['data']
    except firefly.ApiError:
        return None
    splits = t.get('attributes', {}).get('transactions', [])
    if not splits:
        return None
    fila = _plano(t, splits[0])
    fila['partes'] = len(splits)
    return fila


# ------------------------------------------------------------------- editar

# Lo que se puede cambiar, y como se llama en la API de Firefly. `comercio` se
# traduce segun el signo: en un gasto es la cuenta de DESTINO y en un ingreso la
# de ORIGEN. Mandarlo al lado equivocado mueve la plata de cuenta.
CAMBIABLES = ('categoria', 'presupuesto', 'comercio', 'descripcion', 'notas')


def editar(tx_id: str, **cambios: Any) -> dict[str, Any]:
    """Aplica los cambios en Firefly. Devuelve lo que quedo, ya releido.

    Levanta ValueError si se pide cambiar algo que no se puede, en vez de
    ignorarlo en silencio: un nombre de campo mal escrito no debe verse como un
    cambio aplicado.
    """
    desconocidos = sorted(set(cambios) - set(CAMBIABLES) - {'monto'})
    if desconocidos:
        raise ValueError(
            f'no se puede cambiar {desconocidos}. Se puede: {[*CAMBIABLES, "monto"]}'
        )
    actual = uno(tx_id)
    if actual is None:
        raise ValueError(f'el movimiento {tx_id} no existe en Firefly')
    if actual['partes'] != 1:
        raise ValueError(
            f'el movimiento {tx_id} tiene {actual["partes"]} partes; '
            f'esos se editan en Firefly a mano'
        )

    campos: dict[str, Any] = {}
    if cambios.get('categoria'):
        campos['category_name'] = cambios['categoria']
    if cambios.get('presupuesto'):
        campos['budget_name'] = cambios['presupuesto']
    if cambios.get('descripcion'):
        campos['description'] = cambios['descripcion']
    if cambios.get('notas'):
        campos['notes'] = cambios['notas']
    if cambios.get('comercio'):
        lado = 'destination_name' if actual['valor'] < 0 else 'source_name'
        campos[lado] = cambios['comercio']

    if campos:
        firefly.actualizar_split(str(tx_id), **campos)
    if cambios.get('monto') is not None:
        firefly.cambiar_monto(
            str(tx_id),
            float(cambios['monto']),
            nota_extra='monto corregido desde el bot',
        )
    return uno(tx_id) or actual


def borrar(tx_id: str) -> bool:
    """Borra el movimiento de Firefly. No hay vuelta atras."""
    firefly.borrar(str(tx_id))
    return True


def confirmar(tx_id: str) -> bool:
    """Le quita la etiqueta `sin-confirmar`: el movimiento queda cerrado.

    Es lo que hace la conciliacion cuando el extracto lo trae igual, y lo que
    hace falta poder hacer a mano cuando el movimiento ya se sabe correcto y no
    hay por que esperar el extracto.
    """
    return firefly.quitar_etiqueta(str(tx_id), SIN_CONFIRMAR)


# ---------------------------------------------------------------- para el chat


def describir(m: dict, con_id: bool = False) -> str:
    """Una linea legible. Es lo que se manda al chat y lo que ve el modelo."""
    signo = '' if m['valor'] < 0 else '+'
    plata = f'{signo}${abs(m["valor"]):,.0f}'.replace(',', '.')
    partes = [m['fecha'], plata, (m['destino'] or m['descripcion'])[:26]]
    if m['categoria']:
        partes.append(f'[{m["categoria"]}]')
    if SIN_CONFIRMAR in m['etiquetas']:
        partes.append('· sin confirmar')
    if con_id:
        partes.append(f'#{m["id"]}')
    return '  '.join(partes)


def en_texto(movs: list[dict], titulo: str = 'ULTIMOS MOVIMIENTOS') -> str:
    """El bloque que se le pasa al asesor.

    Lleva el id de Firefly a proposito: es lo que le permite al modelo decir «la
    de 212.000 en Etre» y que el bot sepa cual es.
    """
    if not movs:
        return f'{titulo}: ninguno en el periodo'
    lineas = [titulo]
    lineas += [f'  {describir(m, con_id=True)}' for m in movs]
    return '\n'.join(lineas)
