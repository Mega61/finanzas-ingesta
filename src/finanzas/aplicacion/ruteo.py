"""A que libro, a que cuenta y con que categoria, para quien lleva varios libros.

Es el camino de las personas que se configuran en `personas`. El de Juan -- un
solo libro, productos.csv, el clasificador aprendido de su historico -- no pasa
por aqui y no cambia.

El orden es siempre el mismo, y el primer paso nunca se salta:

  1. **destino**    a que libro va. Se pregunta siempre que haya mas de uno. Lo
                    que se sabe (la tarjeta, lo que dijo en el audio, donde fue
                    la vez pasada) solo PRESELECCIONA un boton.
  2. **cuenta**     la del instrumento EN ESE libro: la misma cuenta de ahorros
                    se llama distinto en Firefly y en Actual.
  3. **categoria**  la del libro elegido. Si ya la contesto para ese comercio
                    en ese mismo libro, se usa; si no, se pregunta.
  4. **publicar**   en el libro elegido, y solo en ese.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from finanzas.adaptadores.almacen import Almacen
from finanzas.dominio import destino as _destino
from finanzas.dominio import fechas
from finanzas.dominio import texto as _texto

EFECTIVO = 'efectivo'


def usa_ruteo(alm: Almacen, usuario_id: int) -> bool:
    """Si la persona va por este camino: la configurada con sus instrumentos,
    o cualquiera con mas de un libro. Juan, con un libro y productos.csv, no."""
    return bool(alm.instrumentos_de(usuario_id)) or len(alm.libros_de(usuario_id)) > 1


# ---------------------------------------------------------------- opciones


def _vigente(fila: Any, fecha: Any) -> bool:
    f = fechas.a_fecha(fecha)
    if not f:
        return True
    desde, hasta = fechas.a_fecha(fila['desde']), fechas.a_fecha(fila['hasta'])
    return (desde is None or f >= desde) and (hasta is None or f <= hasta)


def cuentas_del_instrumento(
    alm: Almacen, usuario_id: int, instrumento: str | None, fecha: Any = None
) -> dict[int, str]:
    """{libro_id: cuenta} donde ese instrumento tiene cuenta."""
    if not instrumento:
        return {}
    clave = str(instrumento).strip().lower()
    return {
        i['libro_id']: i['cuenta']
        for i in alm.instrumentos_de(usuario_id)
        if i['clave'] == clave and _vigente(i, fecha)
    }


def libros_posibles(alm: Almacen, p: Any) -> list[Any]:
    """Los libros que se le ofrecen a este movimiento.

    Solo aquellos donde el instrumento tiene cuenta: la tarjeta del estudio no
    tiene cuenta en el Firefly personal, asi que ahi no se puede publicar. Un
    traslado exige que los DOS extremos existan en el libro. Un instrumento que
    no esta configurado no tiene a donde ir: se ofrecen todos y el bot avisa al
    elegir que falta la cuenta.
    """
    todos = alm.libros_de(p['usuario_id'])
    origen = cuentas_del_instrumento(alm, p['usuario_id'], p['instrumento'], p['fecha'])
    if not origen:
        return todos
    ids = set(origen)
    if p['traslado_a']:
        ids &= set(
            cuentas_del_instrumento(alm, p['usuario_id'], p['traslado_a'], p['fecha'])
        )
    return [lb for lb in todos if lb['id'] in ids] or todos


def sugerencia(alm: Almacen, p: Any, posibles: list[Any]) -> int | None:
    """El libro que se preselecciona. Nunca decide: marca un boton."""
    ids = [lb['id'] for lb in posibles]
    if p['sugerido_libro_id'] in ids:
        return p['sugerido_libro_id']
    if p['sugerido_libro_id']:
        # Dijo otro libro (el que solo se alcanza «pagado con la otra plata»):
        # marcar el unico directo seria marcar justo el que NO dijo.
        return None
    regla = alm.regla_de_libro(p['usuario_id'], clave_de(p))
    if regla and regla['libro_id'] in ids:
        return regla['libro_id']
    return ids[0] if len(ids) == 1 else None


def clave_de(p: Any) -> str:
    return _texto.normalizar(p['contraparte'] or p['descripcion'] or '') or ''


# ----------------------------------------------------------------- crear


def campos_iniciales(alm: Almacen, usuario_id: int) -> dict[str, Any]:
    """Destino y pregunta con los que nace un movimiento de esta persona.

    Con un solo libro el destino es cierto; con varios, nace sin libro y con la
    pregunta del destino abierta. Nunca nace publicable sin haber preguntado.
    """
    d = _destino.cierto(lb['id'] for lb in alm.libros_de(usuario_id))
    if d is None:
        return {'pregunta': 'destino'}
    return {**d.como_campos(), 'pregunta': 'categoria'}


def external_id_de_chat(chat: str | int, mensaje_id: int) -> str:
    """Estable ante un reenvio del mismo update de Telegram: el mismo mensaje
    nunca crea dos movimientos."""
    h = hashlib.sha256(f'{chat}:{mensaje_id}'.encode()).hexdigest()[:24]
    return f'tg-{h}'


@dataclass
class Contado:
    """Lo que la persona conto por el chat, ya entendido."""

    valor: float  # negativo = gasto
    comercio: str | None
    descripcion: str | None
    fecha: str
    instrumento: str | None  # la clave: 'nu', 'efectivo', '5788'
    libro_sugerido: int | None = None
    categoria_sugerida: str | None = None
    transcripcion: str | None = None
    # «le presté 200 mil a mi jefe»: con quien. Es solo lo que dijo; el
    # prestamo se confirma con un toque, como todo lo demas.
    prestamo: str | None = None


def instrumento_por_nombre(
    alm: Almacen, usuario_id: int, dicho: str | None
) -> str | None:
    """'la nu', 'en efectivo', 'con la de ahorros *5788' -> la clave del
    instrumento, por su clave o sus alias. None si no se reconoce."""
    if not dicho:
        return None
    t = _texto.sin_tildes(str(dicho)).lower()
    for i in alm.instrumentos_de(usuario_id):
        nombres = [
            i['clave'],
            *[a.strip() for a in (i['alias'] or '').split(',') if a.strip()],
        ]
        if any(_texto.sin_tildes(n).lower() in t for n in nombres):
            return i['clave']
    return None


def crear_desde_chat(
    alm: Almacen, usuario_id: int, chat: str | int, mensaje_id: int, c: Contado
) -> tuple[int, bool]:
    """El movimiento que la persona conto. Devuelve (id, era_nuevo)."""
    clase = None
    for i in alm.instrumentos_de(usuario_id):
        if i['clave'] == c.instrumento:
            clase = i['clase']
            break
    pid, nuevo = alm.crear_pendiente(
        **campos_iniciales(alm, usuario_id),
        usuario_id=usuario_id,
        origen='chat',
        referencia=f'{chat}:{mensaje_id}',
        external_id=external_id_de_chat(chat, mensaje_id),
        tipo='gasto_contado' if c.valor < 0 else 'ingreso_contado',
        fecha=c.fecha,
        valor=c.valor,
        instrumento=c.instrumento,
        clase_instrumento=clase,
        contraparte=c.comercio,
        descripcion=c.descripcion or c.transcripcion,
        categoria=None,
        sugerido_libro_id=c.libro_sugerido,
        prestamo_con=c.prestamo,
        decidido_por='chat',
    )
    alm.cx.commit()
    return pid, nuevo


# ---------------------------------------------------------------- elegir


class SinCuenta(Exception):
    """El instrumento no tiene cuenta en el libro elegido."""


def elegir_destino(alm: Almacen, pendiente_id: int, libro_id: int) -> Any:
    """La persona eligio el libro. Solo vale entre SUS libros activos, y el
    instrumento tiene que tener cuenta ahi. Devuelve el pendiente actualizado."""
    p = alm.pendiente(pendiente_id)
    d = _destino.elegido(libro_id, [lb['id'] for lb in alm.libros_de(p['usuario_id'])])
    origen = cuentas_del_instrumento(alm, p['usuario_id'], p['instrumento'], p['fecha'])
    cuenta = origen.get(libro_id)
    if not cuenta:
        raise SinCuenta(
            f'el instrumento {p["instrumento"] or "?"} no tiene cuenta en ese libro'
        )
    campos: dict[str, Any] = {**d.como_campos(), 'cuenta_firefly': cuenta}
    if p['traslado_a']:
        destino = cuentas_del_instrumento(
            alm, p['usuario_id'], p['traslado_a'], p['fecha']
        ).get(libro_id)
        if not destino:
            raise SinCuenta(f'{p["traslado_a"]} no tiene cuenta en ese libro')
        campos['cuenta_destino'] = destino
        campos['pregunta'] = None  # un traslado no lleva categoria
    else:
        regla = alm.regla_de_libro(p['usuario_id'], clave_de(p))
        # Si conto que era un prestamo, la regla del comercio no lo convierte
        # en gasto: se pregunta, con el prestamo preseleccionado.
        if (
            regla
            and regla['libro_id'] == libro_id
            and regla['categoria']
            and not p['prestamo_con']
        ):
            campos.update(
                categoria=regla['categoria'], pregunta=None, decidido_por='regla'
            )
        else:
            campos['pregunta'] = 'categoria'
    alm.actualizar_pendiente(pendiente_id, **campos)
    alm.cx.commit()
    return alm.pendiente(pendiente_id)


def elegir_categoria(alm: Almacen, pendiente_id: int, categoria: str) -> Any:
    """Cierra la pregunta y aprende, para ESE libro."""
    p = alm.pendiente(pendiente_id)
    # Una categoria es un gasto o un ingreso: deja de ser un prestamo.
    alm.actualizar_pendiente(
        pendiente_id,
        categoria=categoria,
        pregunta=None,
        decidido_por='usuario',
        prestamo_con=None,
    )
    alm.guardar_regla_de_libro(
        p['usuario_id'],
        p['libro_id'],
        clave_de(p),
        categoria,
        'ingreso' if float(p['valor']) > 0 else 'gasto',
    )
    alm.cx.commit()
    return alm.pendiente(pendiente_id)


# --------------------------------------------------------- desde una alerta


def _cuenta_principal(alm: Almacen, usuario_id: int) -> str | None:
    """La clave de su unica cuenta de ahorros configurada, si hay una sola.

    Varias plantillas dicen solo «en tu cuenta de Ahorros» sin los digitos, y
    un pago de la tarjeta nombra la tarjeta pero no la cuenta de donde sale.
    """
    claves = {
        i['clave'] for i in alm.instrumentos_de(usuario_id) if i['clase'] == 'cuenta'
    }
    return claves.pop() if len(claves) == 1 else None


def marca_de_agua(alm: Almacen, usuario_id: int, por_defecto: str) -> str:
    """Desde que fecha entra lo de esta persona: la mas temprana de sus
    libros, o la del proceso si alguno no dice. Lo anterior se guarda como
    descartado, igual que para Juan."""
    desdes = [lb['desde'] for lb in alm.libros_de(usuario_id)]
    if desdes and all(desdes):
        return min(str(d)[:10] for d in desdes)
    return por_defecto


def crear_desde_alerta(
    alm: Almacen, correo: Any, ev: Any, external_id: str
) -> tuple[int, bool]:
    """El movimiento de una alerta del banco, sin clasificar: nace con la
    pregunta del destino abierta (o la de categoria, si tiene un solo libro)."""
    uid = correo['usuario_id']
    instrumento = ev.instrumento
    if not instrumento and (ev.clase_instrumento == 'cuenta' or ev.traslado_a):
        instrumento = _cuenta_principal(alm, uid)
    pid, nuevo = alm.crear_pendiente(
        **campos_iniciales(alm, uid),
        correo_id=correo['id'],
        usuario_id=uid,
        origen='correo',
        tipo=ev.tipo,
        fecha=str(ev.fecha) if ev.fecha else None,
        hora=ev.hora,
        moneda=ev.moneda,
        valor=ev.valor,
        instrumento=instrumento,
        clase_instrumento=ev.clase_instrumento,
        traslado_a=ev.traslado_a,
        contraparte=ev.contraparte,
        descripcion=ev.descripcion,
        plantilla=ev.plantilla,
        external_id=external_id,
        decidido_por='alerta',
    )
    return pid, nuevo


# ----------------------------------------------- pagado con la plata del otro
#
# El estudio no se sostiene solo, y la duena le paga cosas con su tarjeta
# personal. O al reves: con la tarjeta del estudio se paga algo suyo. Ninguna
# de las dos es un error: son un aporte de la duena, o un pago a la duena, en
# especie. Se ofrecen como botones aparte y nunca se asumen.

DEFECTOS = {
    # en el libro de Actual del negocio
    'cuenta_aportes': 'Aportes en especie',
    'categoria_aportes': 'Aportes de la dueña',
    'categoria_pago_duena': 'Salario Dueña',
    # en el Firefly personal
    'cuenta_negocio': 'Golden Beauty Studio',
    'categoria_aporte': 'Aporte al estudio',
    # en cualquier libro: la cuenta de los prestamos entre personas
    # (aplicacion/prestamos.py). Se crea sola la primera vez que se usa.
    'cuenta_prestamos': 'Préstamos',
}

APORTE = 'aporte'
PAGO_DUENA = 'pago_duena'


def ajuste(libro: Any, clave: str) -> str:
    """Un nombre de cuenta o categoria del libro, con su valor por defecto."""
    propios = json.loads(libro['ajustes']) if libro['ajustes'] else {}
    return propios.get(clave) or DEFECTOS[clave]


def cruces(alm: Almacen, p: Any) -> list[tuple[str, Any, Any]]:
    """[(tipo, libro_del_gasto, libro_que_paga)] para un GASTO pagado con un
    instrumento que no tiene cuenta en el libro al que pertenece.

      aporte      el gasto es del negocio (Actual), la plata salio de lo
                  personal (Firefly): la Nu pagando insumos
      pago_duena  el gasto es personal (Firefly), lo pago el negocio (Actual):
                  la tarjeta del estudio pagando algo suyo
    """
    if float(p['valor']) >= 0 or p['traslado_a']:
        return []
    origen = cuentas_del_instrumento(alm, p['usuario_id'], p['instrumento'], p['fecha'])
    if not origen:
        return []
    libros = {lb['id']: lb for lb in alm.libros_de(p['usuario_id'])}
    fuera = []
    for gasto in libros.values():
        if gasto['id'] in origen:
            continue
        for paga_id in origen:
            paga = libros.get(paga_id)
            if not paga:
                continue
            if gasto['tipo'] == 'actual' and paga['tipo'] == 'firefly':
                fuera.append((APORTE, gasto, paga))
            elif gasto['tipo'] == 'firefly' and paga['tipo'] == 'actual':
                fuera.append((PAGO_DUENA, gasto, paga))
    return fuera


def _cruce(alm: Almacen, p: Any, tipo: str, libro_id: int) -> tuple[Any, Any]:
    for t, gasto, paga in cruces(alm, p):
        if t == tipo and gasto['id'] == libro_id:
            return gasto, paga
    raise ValueError(f'ese libro no se puede elegir asi para este movimiento: {tipo}')


def elegir_aporte(alm: Almacen, pendiente_id: int, libro_id: int) -> Any:
    """«Es del estudio, lo pague yo»: el gasto va al libro del negocio, y la
    plata sale del personal. Se pregunta la categoria del negocio."""
    p = alm.pendiente(pendiente_id)
    gasto, paga = _cruce(alm, p, APORTE, libro_id)
    d = _destino.elegido(
        gasto['id'], [lb['id'] for lb in alm.libros_de(p['usuario_id'])]
    )
    origen = cuentas_del_instrumento(alm, p['usuario_id'], p['instrumento'], p['fecha'])
    campos: dict[str, Any] = {
        **d.como_campos(),
        'pago_libro_id': paga['id'],
        'cuenta_pago': origen[paga['id']],
        'cuenta_firefly': ajuste(gasto, 'cuenta_aportes'),
        'pregunta': 'categoria',
    }
    regla = alm.regla_de_libro(p['usuario_id'], clave_de(p))
    if regla and regla['libro_id'] == gasto['id'] and regla['categoria']:
        campos.update(categoria=regla['categoria'], pregunta=None, decidido_por='regla')
    alm.actualizar_pendiente(pendiente_id, **campos)
    alm.cx.commit()
    return alm.pendiente(pendiente_id)


def elegir_pago_duena(alm: Almacen, pendiente_id: int, libro_id: int) -> Any:
    """«Fue personal, lo pago el estudio»: es un pago a la duena. Se escribe
    en el libro del negocio, que es donde se movio la plata, como su salario."""
    p = alm.pendiente(pendiente_id)
    _gasto, paga = _cruce(alm, p, PAGO_DUENA, libro_id)
    d = _destino.elegido(
        paga['id'], [lb['id'] for lb in alm.libros_de(p['usuario_id'])]
    )
    origen = cuentas_del_instrumento(alm, p['usuario_id'], p['instrumento'], p['fecha'])
    alm.actualizar_pendiente(
        pendiente_id,
        **d.como_campos(),
        cuenta_firefly=origen[paga['id']],
        categoria=ajuste(paga, 'categoria_pago_duena'),
        pregunta=None,
        decidido_por='pago_de_la_duena',
    )
    alm.cx.commit()
    return alm.pendiente(pendiente_id)
