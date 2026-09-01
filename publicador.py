# -*- coding: utf-8 -*-
"""Escribe los movimientos en Firefly III.

Tres redes de seguridad, porque aqui es donde se puede hacer dano de verdad:

1. **Marca de agua** (`INGESTA_DESDE`). El archivo de correos va de sep-2025 a
   hoy, y TODO ese rango ya esta registrado en Firefly a mano. Publicarlo
   crearia ~845 duplicados. Asi que por defecto solo se publica lo que llegue
   desde la fecha de arranque; lo viejo se guarda y se marca, pero no se sube.

2. **external_id**. Es un hash del Message-ID del correo. Antes de crear se
   busca: si ya existe, no se crea de nuevo. Reintentar mil veces es seguro.

3. **Anti-duplicado por monto y fecha.** Aunque el correo sea nuevo, el
   movimiento pudo haberse registrado a mano. Se busca en Firefly la misma
   cuenta con el mismo monto y fecha cercana antes de crear.
"""
import hashlib
from datetime import date, datetime, timedelta

import db
import firefly

ETIQUETA = 'sin-confirmar'
ETIQUETA_ORIGEN = 'ingesta-automatica'

# Cuantos dias de diferencia se aceptan para considerar que un movimiento de
# Firefly es "el mismo" que el de la alerta. La fecha del cargo y la de la
# alerta no siempre coinciden.
TOLERANCIA_DIAS = 3


def external_id(message_id, indice=0):
    """Hash del Message-ID. Estable entre corridas, y no revela el correo."""
    h = hashlib.sha256(str(message_id).encode('utf-8')).hexdigest()[:24]
    return f"bc-{h}" if indice == 0 else f"bc-{h}-{indice}"


def _a_fecha(v):
    if isinstance(v, date):
        return v
    for f in ('%Y-%m-%d', '%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(str(v)[:19], f).date()
        except (ValueError, TypeError):
            pass
    return None


# ------------------------------------------------------- indice anti-duplicado

class IndiceFirefly:
    """Lo que ya hay en Firefly, para no volver a crearlo.

    Se indexa por (cuenta, monto redondeado) -> fechas. El monto se redondea al
    peso porque la alerta y el extracto a veces difieren en centavos.
    """

    def __init__(self, desde=None, hasta=None):
        ruta = '/api/v1/transactions'
        params = []
        if desde:
            params.append(f'start={desde}')
        if hasta:
            params.append(f'end={hasta}')
        if params:
            ruta += '?' + '&'.join(params)
        self.por_monto = {}
        self.external = set()
        self.n = 0
        for t in firefly.get_all(ruta):
            for s in t.get('attributes', {}).get('transactions', []):
                self.n += 1
                ext = s.get('external_id')
                if ext:
                    self.external.add(ext)
                f = _a_fecha(s.get('date'))
                if not f:
                    continue
                try:
                    monto = round(abs(float(s.get('amount') or 0)))
                except (TypeError, ValueError):
                    continue
                for cuenta in (s.get('source_name'), s.get('destination_name')):
                    if cuenta:
                        self.por_monto.setdefault((cuenta, monto), []).append(f)

    def ya_existe(self, cuenta, fecha, valor, tolerancia=TOLERANCIA_DIAS):
        f = _a_fecha(fecha)
        if not f or not cuenta:
            return None
        monto = round(abs(float(valor)))
        # +-1 peso, por si hubo redondeo distinto
        for m in (monto, monto - 1, monto + 1):
            for otra in self.por_monto.get((cuenta, m), []):
                if abs((otra - f).days) <= tolerancia:
                    return otra
        return None


# ------------------------------------------------------------------ payload

def armar_payload(p):
    """De una fila de `pendientes` al JSON de Firefly.

    En Firefly el monto SIEMPRE es positivo: la direccion la lleva el `type`.
    """
    valor = float(p['valor'])
    cuenta = p['cuenta_firefly']
    destino = p['cuenta_destino'] or (p['contraparte'] or 'Sin identificar')

    if p['traslado_a'] and p['cuenta_destino']:
        tipo = 'transfer'
        origen, dest = cuenta, p['cuenta_destino']
        if p['tipo'] == 'avance':
            # el avance sube la deuda de la tarjeta y mete efectivo en la cuenta
            origen, dest = cuenta, p['cuenta_destino']
    elif valor < 0:
        tipo = 'withdrawal'
        origen, dest = cuenta, destino
    else:
        tipo = 'deposit'
        origen, dest = destino, cuenta

    split = {
        'type': tipo,
        'date': str(p['fecha']),
        'amount': f"{abs(valor):.2f}",
        'currency_code': p['moneda'] or 'COP',
        'description': (p['descripcion'] or p['contraparte'] or 'Movimiento')[:255],
        'source_name': origen,
        'destination_name': dest,
        'tags': [ETIQUETA, ETIQUETA_ORIGEN],
        'external_id': p['external_id'],
        'notes': (f"Alerta de Bancolombia, plantilla {p['plantilla']}. "
                  f"Instrumento *{p['instrumento'] or '?'}. "
                  f"Confianza del clasificador {p['confianza'] or 0:.2f}. "
                  f"Sin confirmar contra extracto."),
    }
    if p['categoria']:
        split['category_name'] = p['categoria']
    if p['presupuesto'] and tipo == 'withdrawal':
        split['budget_name'] = p['presupuesto']
    if p['hora']:
        split['description'] = split['description']

    return {'apply_rules': False, 'fire_webhooks': False, 'transactions': [split]}


# ---------------------------------------------------------------- publicar

def publicar_uno(cx, p, idx=None, dry_run=True):
    """Devuelve (accion, detalle). accion: creado | duplicado | ya_estaba |
    seco | error."""
    if not p['cuenta_firefly']:
        return 'error', 'sin cuenta de Firefly resuelta'
    if not p['fecha']:
        return 'error', 'sin fecha'

    # red 2: el external_id ya esta en Firefly
    if idx is not None and p['external_id'] in idx.external:
        db.pendiente_actualizar(cx, p['id'], estado='publicado')
        cx.commit()
        return 'ya_estaba', 'el external_id ya existe en Firefly'

    # red 3: mismo monto, misma cuenta, fecha cercana
    if idx is not None:
        choque = idx.ya_existe(p['cuenta_firefly'], p['fecha'], p['valor'])
        if choque:
            db.pendiente_actualizar(cx, p['id'], estado='descartado',
                                    pregunta=None,
                                    decidido_por='duplicado_de_firefly')
            cx.commit()
            return 'duplicado', f'ya hay uno igual el {choque}'

    payload = armar_payload(p)
    if dry_run:
        s = payload['transactions'][0]
        return 'seco', (f"{s['type']:10} {s['date']} {s['amount']:>12} "
                        f"{s['source_name']} -> {s['destination_name']}"
                        f"  [{s.get('category_name', 'sin categoria')}]")

    try:
        r = firefly.call('POST', '/api/v1/transactions', payload)
        fid = (r.get('data') or {}).get('id')
        db.pendiente_actualizar(cx, p['id'], estado='publicado', firefly_id=fid)
        db.bitacora(cx, 'crear', usuario_id=p['usuario_id'], pendiente_id=p['id'],
                    firefly_id=fid, payload=payload, ok=True)
        cx.commit()
        return 'creado', f"firefly_id={fid}"
    except firefly.ApiError as ex:
        db.pendiente_actualizar(cx, p['id'], estado='error')
        db.bitacora(cx, 'crear', usuario_id=p['usuario_id'], pendiente_id=p['id'],
                    payload=payload, respuesta=str(ex), ok=False)
        cx.commit()
        return 'error', str(ex)[:200]


def publicar_pendientes(cx, desde=None, dry_run=True, limite=500):
    """Publica lo que este listo. `desde` es la marca de agua: nada anterior
    se sube."""
    filas = db.pendientes_por_publicar(cx, limite=limite)
    if desde:
        antes = [p for p in filas if str(p['fecha']) < str(desde)]
        for p in antes:
            db.pendiente_actualizar(cx, p['id'], estado='descartado',
                                    pregunta=None,
                                    decidido_por='anterior_a_la_marca_de_agua')
        cx.commit()
        filas = [p for p in filas if str(p['fecha']) >= str(desde)]
        if antes:
            print(f"  {len(antes)} anteriores a {desde}: guardados, no publicados")

    if not filas:
        return {}

    fechas = [str(p['fecha']) for p in filas if p['fecha']]
    ini = (min(fechas) if fechas else None)
    fin = (max(fechas) if fechas else None)
    if ini:
        ini = str(_a_fecha(ini) - timedelta(days=TOLERANCIA_DIAS + 1))
        fin = str(_a_fecha(fin) + timedelta(days=TOLERANCIA_DIAS + 1))
    idx = IndiceFirefly(desde=ini, hasta=fin)

    conteo = {}
    for p in filas:
        accion, detalle = publicar_uno(cx, p, idx=idx, dry_run=dry_run)
        conteo[accion] = conteo.get(accion, 0) + 1
        if accion in ('seco', 'creado', 'error'):
            print(f"  {accion:9} {detalle}")
        elif accion == 'duplicado':
            print(f"  {accion:9} {p['fecha']} {p['valor']:>12,.0f} "
                  f"{(p['descripcion'] or '')[:34]} — {detalle}")
    return conteo
