"""Prestamos entre personas: la plata que sale y vuelve.

El caso que lo trajo: el jefe le pide que le pase plata a una cuenta, y a los
dias se la devuelve. Anotarlo como gasto e ingreso inventa un mes con un gasto
y un ingreso que no fueron; no anotarlo deja dos movimientos del extracto
fuera de todos los libros, y la conciliacion los encuentra.

Se anota como lo que es: un traslado entre la cuenta de donde salio la plata y
una cuenta «Préstamos» del mismo libro. No toca categorias ni presupuestos, y
el saldo de «Préstamos» es, en todo momento, lo que le deben (positivo) o lo
que ella debe (negativo). Con quien fue va en `prestamo_con`, para saber el
saldo de cada persona y reconocer la devolucion cuando llega.

Sigue la misma regla que todo lo demas: el libro lo elige ella primero. Un
prestamo del trabajo va al libro personal; uno del estudio, al del estudio.

En el libro de Juan la alerta se publica apenas llega, y la categoria se
pregunta despues. Ahi un prestamo llega tarde: lo que ya se escribio como gasto
o ingreso se borra y se vuelve a escribir como traslado (`sacar_del_libro`).
"""

from __future__ import annotations

from typing import Any

from finanzas.adaptadores import actual
from finanzas.adaptadores.almacen import Almacen
from finanzas.aplicacion import libros, ruteo

# Que tan parecido tiene que ser el monto para marcar la devolucion con ✓.
TOLERANCIA = 0.01


def cuenta(libro: Any) -> str:
    return ruteo.ajuste(libro, 'cuenta_prestamos')


def es_prestamo(p: Any) -> bool:
    """Decidido como prestamo: con quien, y hacia que cuenta. Solo con quien
    es lo que dijo en el chat, esperando que lo confirme."""
    return bool(p['prestamo_con'] and p['cuenta_destino'])


def nombre(escrito: str | None) -> str | None:
    """«mi jefe» -> «Jefe». Corto y sin el posesivo, para que el saldo de la
    misma persona no se parta en «jefe», «mi jefe» y «el jefe»."""
    if not escrito:
        return None
    t = ' '.join(str(escrito).split()).strip(' .,;:!¡¿?«»"\'')
    for articulo in ('mi ', 'el ', 'la ', 'a mi ', 'al ', 'a la ', 'a '):
        if t.lower().startswith(articulo):
            t = t[len(articulo) :]
    t = t[:40].strip()
    return t[0].upper() + t[1:] if t else None


def saldos(alm: Almacen, usuario_id: int, libro_id: int) -> list[tuple[str, float]]:
    """[(persona, saldo)] de ese libro. Positivo: le deben a ella."""
    return [
        (r['persona'], float(r['saldo']))
        for r in alm.saldos_de_prestamos(usuario_id, libro_id)
    ]


def devoluciones_posibles(alm: Almacen, p: Any) -> list[tuple[str, float, bool]]:
    """[(persona, saldo, monto_exacto)] a las que este movimiento podria estar
    saldando: si entra plata, las que le deben; si sale, a las que ella debe.
    `monto_exacto` marca la que cuadra con el saldo: esa lleva ✓, y nada mas.
    """
    if not p['libro_id']:
        return []
    valor = float(p['valor'])
    salida = []
    for persona, saldo in saldos(alm, p['usuario_id'], p['libro_id']):
        if (valor > 0) != (saldo > 0):
            continue
        exacto = abs(abs(valor) - abs(saldo)) <= max(abs(saldo) * TOLERANCIA, 1)
        salida.append((persona, saldo, exacto))
    return salida


def elegir(alm: Almacen, pendiente_id: int, persona: str) -> Any:
    """Es un prestamo (o su devolucion) con esa persona. Cierra la pregunta:
    un traslado no lleva categoria."""
    p = alm.pendiente(pendiente_id)
    if not p['libro_id'] or not p['destino_por']:
        raise ValueError('primero va el libro')
    if p['pago_libro_id']:
        raise ValueError(
            'un gasto pagado con la plata del otro libro no es un prestamo'
        )
    libro = alm.libro(p['libro_id'])
    alm.actualizar_pendiente(
        pendiente_id,
        prestamo_con=persona,
        cuenta_destino=cuenta(libro),
        categoria=None,
        categoria_grupo=None,
        pregunta=None,
        decidido_por='prestamo',
    )
    alm.cx.commit()
    return alm.pendiente(pendiente_id)


# Movimientos que NO escribimos nosotros: estan enlazados a uno que ya estaba
# (una venta de Agendapro, uno que ella anoto a mano). Borrarlos seria borrar
# lo de otro.
ENLAZADOS = ('venta_agendapro', 'mismo_que_ya_estaba', 'ya_estaba_en_actual')


class NoSePuedeRehacer(Exception):
    """El movimiento ya publicado no es solo nuestro: no se borra."""


def sacar_del_libro(alm: Almacen, pendiente_id: int) -> None:
    """Un movimiento ya publicado resulta ser un prestamo: se borra lo que
    escribimos, para volver a escribirlo como traslado. Si volver a escribirlo
    falla, queda 'nuevo' y sin pregunta, y la siguiente pasada del publicador
    lo sube: no se pierde."""
    p = alm.pendiente(pendiente_id)
    if not p['firefly_id']:
        return
    if p['pago_libro_id'] or p['decidido_por'] in ENLAZADOS:
        raise NoSePuedeRehacer(
            'ese movimiento está enlazado a otro que ya estaba en el libro'
        )
    cliente = libros.cliente(alm.libro(p['libro_id']))
    cliente.borrar(p['firefly_id'])
    alm.anotar(
        'borrar',
        usuario_id=p['usuario_id'],
        pendiente_id=p['id'],
        firefly_id=p['firefly_id'],
        respuesta='se vuelve a escribir como prestamo',
    )
    alm.actualizar_pendiente(pendiente_id, estado='nuevo', firefly_id=None)
    alm.cx.commit()


def asegurar_cuenta_en_actual(cliente: actual.Cliente, nombre_cuenta: str) -> str:
    """El id de la cuenta de prestamos en ese Actual; la crea si no esta."""
    return cliente.cuenta_id(nombre_cuenta) or cliente.crear_cuenta(nombre_cuenta)


def saldo_de(alm: Almacen, p: Any) -> float | None:
    """El saldo con la persona de ese prestamo, ya contando este."""
    if not p['prestamo_con'] or not p['libro_id']:
        return None
    for persona, saldo in saldos(alm, p['usuario_id'], p['libro_id']):
        if persona.lower() == str(p['prestamo_con']).lower():
            return saldo
    return 0.0
