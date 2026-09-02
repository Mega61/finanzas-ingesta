"""Auditoria de Firefly. NO cambia nada, solo busca lo que no cuadra.

    python herramientas/auditar_firefly.py
    python herramientas/auditar_firefly.py --saldo "Bancolombia=3893214.60"

Busca seis clases de problema:

1. Cuentas cuyo saldo no sale de sus propios movimientos.
2. Transacciones duplicadas: mismo monto, misma fecha, mismas cuentas.
3. external_id repetido (la ingesta deberia impedirlo, pero se verifica).
4. Transferencias con la misma cuenta a los dos lados.
5. Movimientos con monto cero o fecha futura.
6. Nombres de cuenta duplicados entre tipos, que hacen que cualquier analisis
   por nombre de numeros falsos. Ya paso: la cuenta de GASTO «Bancolombia» tiene
   el mismo nombre que la de activo, y las cuotas de manejo van ahi.

Con --saldo compara contra el saldo real que le des y, si no cuadra, muestra los
ultimos movimientos con saldo corrido para ubicar donde se separo.
"""

import argparse
import collections
import sys

# la raiz del repo: estos scripts viven un nivel abajo
from finanzas.adaptadores import firefly
from finanzas.dominio import dinero as _dinero
from finanzas.dominio import fechas


def _plata(v):
    return _dinero.formatear(v)


def cuentas_activo():
    salida = []
    for a in firefly.get_all('/api/v1/accounts?type=asset'):
        at = a['attributes']
        if not at.get('active'):
            continue
        try:
            saldo = float(at.get('current_balance') or 0)
        except (TypeError, ValueError):
            saldo = 0.0
        salida.append(
            {
                'id': a['id'],
                'nombre': at['name'],
                'saldo': saldo,
                'rol': at.get('account_role') or '',
                'moneda': at.get('currency_code') or 'COP',
            }
        )
    return salida


def movimientos_de(cuenta_id):
    """Los movimientos de una cuenta, con el signo desde su punto de vista.

    Se filtra por ID y no por nombre: hay nombres repetidos entre tipos de
    cuenta y filtrar por nombre mezcla la cuenta de activo con la de gasto.
    """
    movs = []
    for t in firefly.get_all(f'/api/v1/accounts/{cuenta_id}/transactions'):
        for s in t['attributes']['transactions']:
            try:
                v = abs(float(s['amount']))
            except (TypeError, ValueError, KeyError):
                continue
            if str(s.get('source_id')) == str(cuenta_id):
                v = -v
            elif str(s.get('destination_id')) != str(cuenta_id):
                continue
            movs.append(
                {
                    'fecha': s['date'][:10],
                    'id': t['id'],
                    'valor': v,
                    'tipo': s['type'],
                    'desc': (s.get('description') or '')[:36],
                    'otro': (
                        s.get('destination_name') if v < 0 else s.get('source_name')
                    )
                    or '',
                }
            )
    movs.sort(key=lambda m: (m['fecha'], int(m['id'])))
    return movs


def revisar_saldos(cuentas):
    print('=' * 78)
    print('1. ¿EL SALDO DE CADA CUENTA SALE DE SUS MOVIMIENTOS?')
    print('=' * 78)
    problemas = []
    for c in cuentas:
        movs = movimientos_de(c['id'])
        suma = sum(m['valor'] for m in movs)
        # la diferencia deberia ser el saldo inicial de apertura, que en Firefly
        # es una transaccion mas; si aparece, es que hay algo fuera de la cuenta
        d = c['saldo'] - suma
        marca = '' if abs(d) < 0.01 else '   <-- no cuadra'
        print(
            f'  {c["nombre"]:30} saldo={_plata(c["saldo"]):>16} '
            f'movs={len(movs):4} suma={_plata(suma):>16}{marca}'
        )
        if abs(d) >= 0.01:
            problemas.append((c, d))
    return problemas


def buscar_duplicados():
    print('\n' + '=' * 78)
    print('2. TRANSACCIONES DUPLICADAS (mismo monto, fecha y cuentas)')
    print('=' * 78)
    vistos = collections.defaultdict(list)
    ext = collections.defaultdict(list)
    ceros, futuras, mismo_lado = [], [], []
    hoy = str(fechas.hoy())

    for t in firefly.get_all('/api/v1/transactions'):
        for s in t['attributes']['transactions']:
            try:
                v = round(abs(float(s.get('amount') or 0)), 2)
            except (TypeError, ValueError):
                continue
            f = (s.get('date') or '')[:10]
            clave = (f, v, s.get('source_id'), s.get('destination_id'))
            vistos[clave].append((t['id'], (s.get('description') or '')[:34]))
            if s.get('external_id'):
                ext[s['external_id']].append(t['id'])
            if v == 0:
                ceros.append((t['id'], f, s.get('description', '')[:30]))
            if f > hoy:
                futuras.append((t['id'], f, v, s.get('description', '')[:30]))
            if s.get('source_id') and s.get('source_id') == s.get('destination_id'):
                mismo_lado.append((t['id'], f, v))

    dups = {k: v for k, v in vistos.items() if len(v) > 1}
    if not dups:
        print('  ninguna')
    for (f, v, _si, _di), items in sorted(dups.items(), key=lambda x: -x[0][1])[:20]:
        print(f'  {f}  {_plata(v):>16}  x{len(items)}  ids={[i for i, _ in items]}')
        for _, d in items[:3]:
            print(f'        «{d}»')

    print('\n3. external_id REPETIDO')
    rep = {k: v for k, v in ext.items() if len(v) > 1}
    print(f'  {rep if rep else "ninguno"}')

    print('\n4. TRANSFERENCIAS CON LA MISMA CUENTA A LOS DOS LADOS')
    print(f'  {mismo_lado if mismo_lado else "ninguna"}')

    print('\n5. MONTO CERO O FECHA FUTURA')
    print(f'  monto cero: {len(ceros)}' + (f'  {ceros[:5]}' if ceros else ''))
    print(f'  fecha futura: {len(futuras)}' + (f'  {futuras[:5]}' if futuras else ''))
    return dups


def nombres_duplicados():
    print('\n' + '=' * 78)
    print('6. NOMBRES DE CUENTA REPETIDOS ENTRE TIPOS')
    print('=' * 78)
    print('  Cualquier analisis que agrupe por NOMBRE y no por id da numeros')
    print('  falsos con estos:\n')
    idx = collections.defaultdict(set)
    for a in firefly.get_all('/api/v1/accounts'):
        at = a['attributes']
        idx[at['name']].add(at['type'])
    n = 0
    for nombre, tipos in sorted(idx.items()):
        if len(tipos) > 1:
            n += 1
            print(f'  «{nombre}»: {", ".join(sorted(tipos))}')
    if not n:
        print('  ninguno')


def comparar_con_real(cuentas, pares):
    print('\n' + '=' * 78)
    print('7. FIREFLY CONTRA EL SALDO REAL QUE DISTE')
    print('=' * 78)
    por_nombre = {c['nombre']: c for c in cuentas}
    for nombre, real in pares.items():
        c = por_nombre.get(nombre)
        if not c:
            print(f'  no encontre la cuenta «{nombre}»')
            continue
        d = c['saldo'] - real
        print(f'\n  {nombre}')
        print(f'    Firefly : {_plata(c["saldo"]):>16}')
        print(f'    real    : {_plata(real):>16}')
        print(
            f'    gap     : {_plata(d):>16}'
            + ('   OK' if abs(d) < 1 else '   <-- hay que encontrarlo')
        )
        if abs(d) < 1:
            continue
        movs = movimientos_de(c['id'])
        # se recorre al reves buscando el punto donde el saldo corrido daria
        # el valor real: ahi es donde se separaron
        corr = c['saldo']
        print('\n    ultimos movimientos, con saldo corrido hacia atras:')
        for m in reversed(movs[-14:]):
            antes = corr - m['valor']
            pista = '   <== aqui el saldo era el real' if abs(antes - real) < 1 else ''
            print(
                f'      {m["fecha"]} id={m["id"]:5} {_plata(m["valor"]):>15} '
                f'-> {_plata(corr):>15}  {m["desc"][:28]}{pista}'
            )
            corr = antes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        '--saldo',
        action='append',
        default=[],
        help='Cuenta=monto real, se puede repetir',
    )
    a = ap.parse_args()

    pares = {}
    for s in a.saldo:
        if '=' in s:
            k, v = s.rsplit('=', 1)
            try:
                pares[k.strip()] = float(v)
            except ValueError:
                print(f'no entendi el saldo {s!r}')

    cuentas = cuentas_activo()
    revisar_saldos(cuentas)
    buscar_duplicados()
    nombres_duplicados()
    if pares:
        comparar_con_real(cuentas, pares)
    return 0


if __name__ == '__main__':
    sys.exit(main())
