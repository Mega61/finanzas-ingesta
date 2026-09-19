"""Manda los movimientos a la cuenta de gasto que les toca.

    python herramientas/recanalizar_cuentas.py            # solo dice que haria
    python herramientas/recanalizar_cuentas.py --aplicar  # lo hace

El publicador ya sabe resolver la cadena antes de rendirse al texto crudo
(`texto.cuenta_de_cadena`), pero eso solo vale para lo que se publique de aqui
en adelante. Lo que ya entro con el nombre del datafono se quedo asi:

    EXITO SABANETA          2 movimientos   aparte de 'Grupo Éxito' (128)
    ALMACENES EXITO         1               aparte de 'Grupo Éxito'
    KOBA COLOMBIA           1               aparte de 'D1' (22)
    TIENDA D1 SABANETA P    1               aparte de 'D1'
    PWS*COMBUSCOL SABANE    1               aparte de 'Texaco'

Cada uno parte en pedazos el historico del comercio justo donde se quiere
mirar el mes. Esto los junta.

Las cuentas viejas quedan vacias; borrarlas es un clic en Firefly y se hace a
mano a proposito: borrar cuentas por API es de las cosas que no conviene
automatizar.
"""

from __future__ import annotations

import sys

from finanzas.adaptadores import firefly
from finanzas.dominio import texto

# Lo que `cuenta_de_cadena` no cubre porque no son cadenas de mercado.
# PWS es una pasarela y COMBUSCOL el operador; el comercio es Texaco.
EXTRA = {
    'PWS*COMBUSCOL SABANE': 'Texaco',
}


def destino_bueno(nombre: str) -> str | None:
    """A que cuenta deberia ir, o None si ya esta bien."""
    if nombre in EXTRA:
        return EXTRA[nombre]
    cadena = texto.cuenta_de_cadena(nombre)
    return cadena if cadena and cadena != nombre else None


def main() -> int:
    aplicar = '--aplicar' in sys.argv
    cuentas = firefly.accounts_index()

    # Solo cuentas de GASTO: una cuenta de activo con nombre parecido no se
    # toca ni por error.
    candidatas = {}
    for nombre, info in cuentas.items():
        tipo = (info.get('type') or info.get('attributes', {}).get('type') or '').lower()
        if 'expense' not in tipo:
            continue
        bueno = destino_bueno(nombre)
        if bueno:
            candidatas[nombre] = bueno

    if not candidatas:
        print('Nada que juntar: ninguna cuenta de gasto esta fuera de su cadena.')
        return 0

    print('cuentas a juntar:')
    for malo, bueno in sorted(candidatas.items()):
        print(f'  {malo:<28} -> {bueno}')
    print()

    total = 0
    for malo, bueno in sorted(candidatas.items()):
        if bueno not in cuentas:
            print(f'  OJO: «{bueno}» no existe en Firefly, me salto «{malo}»')
            continue
        movs = firefly.get_all(
            f'/api/v1/accounts/{cuentas[malo]["id"]}/transactions'
        )
        for t in movs:
            tid = t.get('id')
            for s in t.get('attributes', {}).get('transactions', []):
                if s.get('destination_name') != malo:
                    continue
                total += 1
                print(
                    f'  {s.get("date","")[:10]}  {str(s.get("description"))[:34]:<36}'
                    f'{float(s.get("amount") or 0):>11,.0f}  {malo} -> {bueno}'
                )
                if aplicar:
                    firefly.actualizar_split(tid, destination_name=bueno)

    print()
    if aplicar:
        print(f'{total} movimientos movidos. Las cuentas viejas quedan vacias:')
        print('borralas a mano en Firefly (Accounts -> Expense accounts).')
    else:
        print(f'{total} movimientos CAMBIARIAN. Nada se escribio. Agrega --aplicar.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
