# -*- coding: utf-8 -*-
"""Fase 5: cruza los extractos contra lo que se publico, y cierra el ciclo.

Tres desenlaces por movimiento publicado:

  confirmado  el extracto lo trajo con el mismo monto -> se quita la etiqueta
              `sin-confirmar` y no se molesta a nadie
  corregido   el extracto trajo OTRO monto -> se ajusta en Firefly y se avisa
  fantasma    el extracto cubre esa fecha y NO lo trajo -> era una
              preautorizacion que nunca se cobro. Se propone borrarlo.

El fantasma no es teorico: entre julio y agosto de 2026, 13 de 30 alertas de
viaje nunca se volvieron un cargo real (217.469 en falso). Uber preautoriza el
precio estimado y despues cobra la tarifa real.

Emparejamiento: por monto absoluto con tolerancia de fecha creciente, y gana
el mas cercano. Es el metodo que ya funciono en la reconciliacion manual.
"""
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
import db  # noqa: E402
import firefly  # noqa: E402
from parsers import extracto_tarjeta  # noqa: E402
from publicador import ETIQUETA  # noqa: E402

# Se prueba en este orden y gana el primer match mas cercano.
TOLERANCIAS = (0, 1, 2, 3, 5, 8, 20, 45)
# Diferencia de monto que se considera "el mismo" (redondeos, centavos).
EPS = 2.0
# Dias despues del cierre del extracto antes de declarar fantasma. Se espera un
# poco porque un cargo puede aparecer en el extracto siguiente.
GRACIA_FANTASMA = 45


def _f(v):
    if isinstance(v, date):
        return v
    for fmt in ('%Y-%m-%d', '%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(str(v)[:19], fmt).date()
        except (ValueError, TypeError):
            pass
    return None


# ------------------------------------------------------------ emparejamiento

# La AMEX factura en USD pero las alertas llegan en COP. Sin cruzar monedas,
# cada compra en la AMEX se declaraba fantasma: eran ~93 de los 115 falsos
# positivos de la primera medicion. La tasa se auto-calibra por extracto.
TASA_MIN, TASA_MAX = 2600.0, 5400.0
# Cuanto puede diferir del monto real una preautorizacion para seguir siendo
# creible que sean el mismo viaje. Mas alla de esto no se corrige: se pregunta.
MAX_DELTA_REL = 0.6


def tasa_implicita(pendientes, movimientos):
    """La tasa COP/USD de este extracto, deducida de los pares que se parecen.

    Se toma la mediana de las razones COP/USD de los candidatos cuyo comercio
    coincide. Si no hay con que deducirla, devuelve None y no se cruza moneda.
    """
    razones = []
    for p in pendientes:
        if (p['moneda'] or 'COP').upper() != 'COP':
            continue
        fp = _f(p['fecha'])
        vp = abs(float(p['valor']))
        for m in movimientos:
            if (m.moneda or 'COP').upper() != 'USD' or not m.valor:
                continue
            if fp and abs((m.fecha - fp).days) > 5:
                continue
            r = vp / abs(m.valor)
            if TASA_MIN <= r <= TASA_MAX and _parecido(p, m):
                razones.append(r)
    if len(razones) < 2:
        return None
    razones.sort()
    return razones[len(razones) // 2]


def emparejar(pendientes, movimientos, tasa_respaldo=None):
    """Devuelve (pares, sin_pareja, libres). Cada movimiento del extracto se
    consume una sola vez.

    `clase` de cada par:
      igual       mismo monto (o mismo monto convertido)
      otro_monto  el extracto trae otro monto y es creible que sea el mismo
                  cargo -> se corrige
      ambiguo     hay varios candidatos del mismo comercio, o la diferencia es
                  demasiado grande -> NO se corrige, se pregunta
    """
    tasa = tasa_implicita(pendientes, movimientos) or tasa_respaldo
    usados = set()
    pares = []
    sin = []

    def equivalente(vp, moneda_p, m):
        """El monto del extracto en la moneda de la alerta, o None."""
        mm = (m.moneda or 'COP').upper()
        if mm == moneda_p:
            return abs(m.valor)
        if tasa and moneda_p == 'COP' and mm == 'USD':
            return abs(m.valor) * tasa
        if tasa and moneda_p == 'USD' and mm == 'COP':
            return abs(m.valor) / tasa
        return None

    for p in pendientes:
        fp = _f(p['fecha'])
        vp = abs(float(p['valor']))
        moneda_p = (p['moneda'] or 'COP').upper()

        mejor = None
        for tol in TOLERANCIAS:
            candidatos = []
            for i, m in enumerate(movimientos):
                if i in usados:
                    continue
                eq = equivalente(vp, moneda_p, m)
                if eq is None:
                    continue
                # al convertir moneda el redondeo es mayor: se tolera 1,5%
                margen = EPS if (m.moneda or 'COP').upper() == moneda_p else vp * 0.015
                if abs(eq - vp) > max(margen, EPS):
                    continue
                if fp and abs((m.fecha - fp).days) > tol:
                    continue
                candidatos.append((abs((m.fecha - fp).days) if fp else 0, i, m))
            if candidatos:
                candidatos.sort()
                mejor = candidatos[0]
                break
        if mejor is not None:
            usados.add(mejor[1])
            pares.append((p, mejor[2], 'igual'))
            continue

        # No cuadro el monto. ¿Hay algo del mismo comercio con otro monto? Asi
        # se detecta la preautorizacion de Uber corregida a la tarifa real.
        #
        # OJO: aqui es facil hacer dano. Con varios Ubers en pocos dias, tomar
        # "el mas parecido" encadenaba correcciones equivocadas y cambiaba
        # montos al azar. Asi que solo se corrige si el candidato es UNICO y la
        # diferencia es creible; si no, se marca ambiguo y se pregunta.
        cands = []
        for i, m in enumerate(movimientos):
            if i in usados:
                continue
            eq = equivalente(vp, moneda_p, m)
            if eq is None:
                continue
            if not fp or abs((m.fecha - fp).days) > 3:
                continue
            if not _parecido(p, m):
                continue
            cands.append((abs(eq - vp) / vp if vp else 1.0, i, m))

        if len(cands) == 1 and cands[0][0] <= MAX_DELTA_REL:
            usados.add(cands[0][1])
            pares.append((p, cands[0][2], 'otro_monto'))
        elif cands:
            pares.append((p, cands[0][2], 'ambiguo'))
        else:
            sin.append(p)

    libres = [m for i, m in enumerate(movimientos) if i not in usados]
    return pares, sin, libres


def _parecido(p, m):
    """¿La descripcion del extracto y la contraparte de la alerta hablan del
    mismo comercio? Basta con que compartan una palabra de 4+ letras."""
    import clasificador
    a = set(clasificador.normalizar(p['contraparte'] or p['descripcion']).split())
    b = set(clasificador.normalizar(m.descripcion).split())
    return any(len(t) >= 4 for t in (a & b))


def tasa_global_implicita(cx, extractos):
    """La tasa COP/USD juntando todos los extractos, como respaldo.

    Se usa la mediana de las razones de todos los pares creibles del historico.
    Es aproximada a proposito: solo tiene que servir para reconocer que una
    alerta en COP y una linea en USD son el mismo cargo, y eso se decide junto
    con que el comercio coincida.
    """
    razones = []
    for ext in extractos:
        if not ext.desde or not ext.hasta:
            continue
        usd = [m for m in ext.movimientos
               if (m.moneda or 'COP').upper() == 'USD' and m.valor]
        if not usd:
            continue
        pend = cx.execute("""SELECT * FROM pendientes
                             WHERE instrumento = ? AND moneda = 'COP'
                               AND fecha BETWEEN ? AND ?""",
                          (ext.instrumento, str(ext.desde),
                           str(ext.hasta))).fetchall()
        for p in pend:
            fp = _f(p['fecha'])
            vp = abs(float(p['valor']))
            for m in usd:
                if fp and abs((m.fecha - fp).days) > 5:
                    continue
                r = vp / abs(m.valor)
                if TASA_MIN <= r <= TASA_MAX and _parecido(p, m):
                    razones.append(r)
    if len(razones) < 3:
        return None
    razones.sort()
    return razones[len(razones) // 2]


# --------------------------------------------------------------- conciliar

def conciliar_extracto(cx, ext, dry_run=True, tasa_respaldo=None):
    """Cruza un extracto contra los pendientes publicados de esa tarjeta."""
    if ext.error or not ext.movimientos:
        return {}
    if not ext.desde or not ext.hasta:
        return {'sin_periodo': 1}

    pend = cx.execute("""SELECT * FROM pendientes
                         WHERE estado = 'publicado'
                           AND instrumento = ?
                           AND fecha BETWEEN ? AND ?
                         ORDER BY fecha""",
                      (ext.instrumento, str(ext.desde), str(ext.hasta))).fetchall()
    if not pend:
        return {}

    movs = [m for m in ext.movimientos if m.en_periodo]
    pares, sin, libres = emparejar(pend, movs, tasa_respaldo=tasa_respaldo)

    conteo = {}
    for p, m, clase in pares:
        if clase == 'igual':
            conteo['confirmado'] = conteo.get('confirmado', 0) + 1
            if not dry_run:
                _confirmar(cx, p, m, ext)
        elif clase == 'otro_monto':
            conteo['corregido'] = conteo.get('corregido', 0) + 1
            print(f"    corrige   {p['fecha']} {p['valor']:>13,.2f} -> "
                  f"{m.valor:>13,.2f} {m.moneda}  {(p['contraparte'] or '')[:24]}")
            if not dry_run:
                _corregir(cx, p, m, ext)
        else:
            # varios candidatos o diferencia demasiado grande: no se toca el
            # monto solo. Se pregunta.
            conteo['ambiguo'] = conteo.get('ambiguo', 0) + 1
            print(f"    ambiguo   {p['fecha']} {p['valor']:>13,.2f} "
                  f"{(p['contraparte'] or '')[:26]}  (varios candidatos, pregunto)")
            if not dry_run:
                db.pendiente_actualizar(cx, p['id'], pregunta='monto')
                cx.commit()

    # fantasmas: el extracto cubre la fecha y no lo trajo
    limite = date.today() - timedelta(days=GRACIA_FANTASMA)
    for p in sin:
        fp = _f(p['fecha'])
        if fp and fp > limite:
            conteo['esperando'] = conteo.get('esperando', 0) + 1
            continue
        conteo['fantasma'] = conteo.get('fantasma', 0) + 1
        print(f"    FANTASMA  {p['fecha']} {p['valor']:>13,.2f} "
              f"{(p['contraparte'] or '')[:28]}  (no aparecio en el extracto)")
        if not dry_run:
            _marcar_fantasma(cx, p, ext)

    if libres:
        conteo['solo_en_extracto'] = len(libres)
    return conteo


def _confirmar(cx, p, m, ext):
    try:
        if p['firefly_id']:
            firefly.quitar_etiqueta(p['firefly_id'], ETIQUETA)
        db.pendiente_actualizar(cx, p['id'], estado='confirmado',
                                visto_en=ext.archivo,
                                valor_confirmado=m.valor)
        db.bitacora(cx, 'confirmar', usuario_id=p['usuario_id'],
                    pendiente_id=p['id'], firefly_id=p['firefly_id'],
                    respuesta=f"confirmado por {ext.archivo}")
        cx.commit()
    except firefly.ApiError as ex:
        db.bitacora(cx, 'confirmar', usuario_id=p['usuario_id'],
                    pendiente_id=p['id'], firefly_id=p['firefly_id'],
                    respuesta=str(ex), ok=False)
        cx.commit()


def _corregir(cx, p, m, ext):
    try:
        if p['firefly_id']:
            firefly.cambiar_monto(
                p['firefly_id'], m.valor,
                nota_extra=(f"Monto corregido con el extracto {ext.archivo}: "
                            f"la alerta decia {abs(float(p['valor'])):,.2f} y el "
                            f"cargo real fue {abs(m.valor):,.2f}."))
            firefly.quitar_etiqueta(p['firefly_id'], ETIQUETA)
        db.pendiente_actualizar(cx, p['id'], estado='corregido',
                                visto_en=ext.archivo, valor_confirmado=m.valor)
        db.bitacora(cx, 'corregir', usuario_id=p['usuario_id'],
                    pendiente_id=p['id'], firefly_id=p['firefly_id'],
                    respuesta=f"{p['valor']} -> {m.valor} por {ext.archivo}")
        cx.commit()
    except firefly.ApiError as ex:
        db.bitacora(cx, 'corregir', usuario_id=p['usuario_id'],
                    pendiente_id=p['id'], firefly_id=p['firefly_id'],
                    respuesta=str(ex), ok=False)
        cx.commit()


def _marcar_fantasma(cx, p, ext):
    """No borra nada solo: marca y deja que el bot pregunte. Borrar plata del
    libro sin avisar es justo lo que no se quiere."""
    db.pendiente_actualizar(cx, p['id'], estado='publicado',
                            pregunta='existencia', visto_en=None)
    db.bitacora(cx, 'sospecha_fantasma', usuario_id=p['usuario_id'],
                pendiente_id=p['id'], firefly_id=p['firefly_id'],
                respuesta=f"no aparecio en {ext.archivo}")
    cx.commit()


# -------------------------------------------------------------------- main

def correr(cx, carpeta=None, dry_run=True, solo=None):
    clave = config.get('EXTRACTO_CLAVE') or config.get('CLAVE')
    if not clave:
        print("falta EXTRACTO_CLAVE (la cedula) para abrir los PDF")
        return {}
    carpeta = carpeta or os.path.join(config.RAIZ, 'Extractos Bancolombia', '_pdf')
    if not os.path.isdir(carpeta):
        print(f"no existe la carpeta de extractos: {carpeta}")
        return {}

    exts = extracto_tarjeta.parse_carpeta(carpeta, clave)
    exts = [e for e in exts if not e.error]
    if solo:
        exts = [e for e in exts if solo in e.archivo]
    print(f"{len(exts)} extractos leidos"
          + ("   [SECO, no escribe]" if dry_run else ""))

    # Tasa global de respaldo. Un extracto suelto puede no tener suficientes
    # pares para deducir su propia tasa; juntando todos si alcanza. Sin esto,
    # cada compra en la AMEX de esos meses se declaraba fantasma.
    tasa_global = tasa_global_implicita(cx, exts)
    if tasa_global:
        print(f"tasa COP/USD de respaldo: {tasa_global:,.0f}")

    total = {}
    for e in sorted(exts, key=lambda x: (x.periodo_archivo, x.instrumento)):
        c = conciliar_extracto(cx, e, dry_run=dry_run, tasa_respaldo=tasa_global)
        if c:
            print(f"  {e.archivo}  ({e.desde} -> {e.hasta})")
            print(f"    {', '.join(f'{k}={v}' for k, v in c.items())}")
            for k, v in c.items():
                total[k] = total.get(k, 0) + v
    print("\ntotal: " + (', '.join(f'{k}={v}' for k, v in total.items()) or 'nada'))
    return total


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--en-serio', action='store_true')
    ap.add_argument('--carpeta')
    ap.add_argument('--solo', help='filtra por nombre de archivo')
    a = ap.parse_args()
    db.inicializar()
    cx = db.conectar()
    try:
        correr(cx, carpeta=a.carpeta, dry_run=not a.en_serio, solo=a.solo)
    finally:
        cx.close()
