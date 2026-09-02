"""Diagnosticos que se corren a mano, cuando algo se ve raro.

    python herramientas/diagnostico.py                 # que hay
    python herramientas/diagnostico.py rutas
    python herramientas/diagnostico.py asesor "¿me alcanza para ropa de gym?"
    python herramientas/diagnostico.py interprete
    python herramientas/diagnostico.py extractos [carpeta]

Cada uno de estos vivia en un bloque `if __name__ == '__main__'` dentro del
modulo que diagnosticaba. Eso obligaba a que las capas de libreria imprimieran
—cuarenta `print` en `aplicacion/` y `adaptadores/`— y encima quedaban
invisibles: nadie que abriera el repo sabia que existian. Aqui estan juntos y
listados, y las capas de abajo quedaron mudas.

No modifican nada: leen y muestran.
"""

import sys
from collections import Counter

from finanzas import config
from finanzas.adaptadores import db, firefly, graph, ia
from finanzas.aplicacion import asesor, clasificador, interprete, presupuestos
from finanzas.dominio import fechas
from finanzas.parsers import extracto_tarjeta


def rutas(_args):
    """Las tres carpetas, resueltas, y que hay configurado."""
    print(config.describir_rutas())
    print()
    print(config.describir())


def base(_args):
    """Cuantas filas hay en cada tabla."""
    db.inicializar()
    cx = db.conectar()
    alm = db.almacen(cx)
    print(f'base: {db.ruta()}')
    for t in (
        'usuarios',
        'buzones',
        'correos_crudos',
        'pendientes',
        'reglas',
        'bitacora',
        'sugerencias',
        'propuestas',
        'preguntas_enviadas',
    ):
        print(f'  {t:20} {alm.contar_por_tabla(t):6}')
    cx.close()


def modelos(_args):
    """Que modelos admite tu API key de Gemini, y si el configurado esta."""
    if not ia.disponible():
        sys.exit(
            'falta GEMINI_API_KEY en el .env.\n'
            'Se saca gratis en https://aistudio.google.com/apikey'
        )
    print(f'modelo configurado: {ia.MODELO}\n')
    try:
        ms = ia.modelos()
    except ia.SinIA as ex:
        sys.exit(f'no pude listar modelos: {ex}')
    print(f'{len(ms)} modelos disponibles con tu key. Los flash:')
    for m in sorted(x for x in ms if 'flash' in x):
        print(f'  {m}{"  <-- configurado" if m == ia.MODELO else ""}')
    if ia.MODELO not in ms:
        print(
            f"\nOJO: '{ia.MODELO}' no aparece en tu lista. Cambia "
            f'GEMINI_MODELO a uno de los de arriba.'
        )


def correo(_args):
    """Que el token de Graph sirva, y los ultimos 5 correos del banco."""
    import re

    tok = graph.token(interactivo=True)
    print('token ok. ultimos 5 correos del banco:\n')
    for m in graph.mensajes(tok, tope=5):
        frase = re.sub(r'\s+', ' ', graph.cuerpo_plano(m))[:150]
        print(f'  {m["receivedDateTime"][:16]}  {m.get("subject", "")[:34]}')
        print(f'      {frase}\n')


def tarjetas(_args):
    """El mapeo instrumento + fecha -> cuenta, contra tu productos.csv.

    Es por fecha porque el banco repone plasticos: los mismos 4 digitos pueden
    ser un producto distinto segun cuando.
    """
    print('instrumento + fecha -> cuenta de Firefly')
    for pr in clasificador.productos():
        f = str(pr['desde'] or '2026-01-01')
        got = clasificador.cuenta_de_instrumento(pr['instrumento'], f)
        print(
            f'  {"ok " if got == pr["cuenta"] else "MAL"} '
            f'*{pr["instrumento"]} {f} -> {got}'
        )
    sin = clasificador.cuenta_de_instrumento('0000', '2026-01-01')
    print(
        f'  {"ok " if sin is None else "MAL"} *0000 -> None   '
        f'(plastico desconocido: se pregunta)'
    )
    print('\nnormalizacion de comercios')
    for t in [
        'UBER RIDES*DL',
        'DLO*Didi',
        'PAYU*CINEMARK',
        'CYCLE GEAR N169',
        'UBER BV USD-USD COLO',
        'GOOGLE *Workspace_go',
        'MERCADO PAGO*TIERRAG',
        'DROGUERIA ALEMANA 47',
    ]:
        print(f'  {t:26} -> {clasificador.normalizar(t)}')


def budgets(_args):
    """Como van los presupuestos, y que categorias no deciden solas."""
    print('=== presupuestos activos, este mes ===')
    for b in presupuestos.estado():
        lim = f'{b["limite"]:,.0f}' if b['limite'] else 'sin tope'
        pct = f'{b["pct"]:.0f}%' if b['pct'] is not None else '-'
        print(f'  {b["nombre"]:24} gastado={b["gastado"]:>14,.0f} de {lim:>14}  {pct}')
    print('\n=== categoria -> presupuesto ===')
    mapa = presupuestos.mapa_categoria()
    dudosas = [c for c, d in mapa.items() if not d['seguro']]
    print(f'  {len(mapa) - len(dudosas)} categorias deciden solas')
    print(f'  {len(dudosas)} hay que preguntarlas:')
    for c in sorted(dudosas):
        print(f'    {c:28} {mapa[c]["reparto"]}')


def interpretar(_args):
    """Que entiende el interprete de varias frases, contra un movimiento fijo.

    Las seis frases son las que destaparon bugs: «mercado del mes» daba
    Minimercado Amonte y despues Mercado Libre, y «esto fue el gym» daba comida
    de gato porque MERCADO PAGO*ZONAFIT matcheaba MERCADO LIBRE.
    """
    db.inicializar()
    cx = db.conectar()
    cat = interprete.catalogo(cx, 1)
    print(
        f'catalogo: {len(cat["categorias"])} categorias, '
        f'{len(cat["presupuestos"])} presupuestos, '
        f'{len(cat["comercios"])} comercios'
    )
    print(
        'IA: '
        + (
            'Gemini disponible'
            if interprete.ia_disponible()
            else 'sin API key, solo heuristica'
        )
        + '\n'
    )
    mov = {
        'fecha': '2026-09-01',
        'valor': -151495.0,
        'moneda': 'COP',
        'contraparte': 'MERCADO PAGO*TIERRAG',
        'descripcion': 'MERCADO PAGO*TIERRAG',
        'cuenta_firefly': 'MASTERCARD BLACK',
    }
    for f in (
        'fue la comida de la gata en tierragro',
        'le compre granos a la michina',
        'mercado del mes',
        'almorzamos afuera, fue un antojo',
        'gasolina de la moto',
        'esto fue el gym',
    ):
        d = interprete.interpretar(cx, 1, mov, f, cat=cat)
        print(f'  {f!r}')
        print(
            f'     -> categoria={d["categoria"]!r} '
            f'presupuesto={d["presupuesto"]!r} comercio={d["comercio"]!r}'
        )
        print(
            f'        conf={d["confianza"]:.2f} fuente={d["fuente"]} '
            f'pedir_presupuesto={d["pedir_presupuesto"]}'
        )
        print(f'        razon: {d["razon"]}')
    cx.close()


def contexto(args):
    """Lo que el asesor ve de tus finanzas, y cuanto cuesta preguntarle.

    Con una pregunta detras, se la hace de verdad:
        python herramientas/diagnostico.py asesor "quiero comprar una bici"
    """
    txt = asesor.contexto_en_texto()
    print('=' * 72)
    print('CONTEXTO QUE VE EL ASESOR')
    print('=' * 72)
    print(txt)
    aprox = len(txt) / 3.5
    print('\n' + '=' * 72)
    print(f'~{aprox:.0f} tokens de contexto por pregunta')
    print(f'a $0.75 por millon: ~${aprox / 1_000_000 * 0.75:.4f} por pregunta')
    if not args:
        return
    if not ia.disponible():
        print('\n(falta GEMINI_API_KEY para preguntar de verdad)')
        return
    pregunta = ' '.join(args)
    print('\n' + '=' * 72)
    print(f'PREGUNTA: {pregunta}\n')
    print(asesor.preguntar(pregunta, ctx_texto=txt))


def extractos(args):
    """Lee los PDF de extracto y muestra los ultimos seis."""
    clave = config.get('EXTRACTO_CLAVE') or config.get('CLAVE')
    if not clave:
        sys.exit('falta EXTRACTO_CLAVE (la cedula) en el .env')
    carpeta = args[0] if args else config.ruta_personal('Extractos Bancolombia', '_pdf')
    exts = extracto_tarjeta.parse_carpeta(carpeta, clave)
    ok = [e for e in exts if not e.error]
    print(
        f'{len(exts)} extractos, {len(ok)} abiertos, '
        f'{sum(len(e.movimientos) for e in ok)} movimientos\n'
    )
    for e in sorted(ok, key=lambda x: x.periodo_archivo)[-6:]:
        print(f'  {e.archivo}')
        print(
            f'    {e.marca} *{e.instrumento}  periodo {e.desde} -> {e.hasta}'
            f'  {len(e.movimientos)} movs'
        )
        for mv in e.movimientos[:3]:
            print(
                f'      {mv.fecha} {mv.moneda} {mv.valor:>13,.2f} {mv.descripcion[:40]}'
            )
    malos = [e for e in exts if e.error]
    if malos:
        print(f'\n{len(malos)} con error:')
        for e in malos[:5]:
            print(f'  {e.archivo}: {e.error}')


def pasarelas(args):
    """Reglas aprendidas cuyo patron es solo una pasarela de pago.

    Con --en-serio las borra. Existen de antes del guardian y mientras esten,
    siguen clasificando mal todo lo que pase por esa pasarela.
    """
    db.inicializar()
    cx = db.conectar()
    alm = db.almacen(cx)
    malas = alm.reglas_de_pasarela()
    if not malas:
        print('ninguna regla envenenada. Bien.')
        cx.close()
        return
    print(f'{len(malas)} reglas cuyo patron es solo una pasarela:')
    print()
    for r in malas:
        print(
            f'  {r["patron"]!r:16} -> {r["categoria"]!r:24} '
            f'origen={r["origen"]} aciertos={r["aciertos"]}'
        )
    if '--en-serio' not in args:
        print()
        print('SECO: no borre nada. Corre con --en-serio para borrarlas.')
        cx.close()
        return
    for r in malas:
        alm.borrar_regla(r['id'])
        print(f'  borrada {r["patron"]!r}')
    cx.close()


def sin_presupuesto(args):
    """Gastos ya en Firefly sin presupuesto, y cuales se pueden llenar solos.

    Con --en-serio les pone el presupuesto que tu propio historico apunta el
    80% o mas de las veces. Los que estan repartidos no se tocan: ahi es un
    juicio de verdad y adivinar seria peor.
    """
    mapa = presupuestos.mapa_categoria()
    desde = args[0] if args and args[0][:2] == '20' else '2026-06-01'
    ruta = f'/api/v1/transactions?type=withdrawal&start={desde}&end={fechas.hoy()}'

    claros, dudosos = [], []
    for t in firefly.get_all(ruta):
        for s in t['attributes']['transactions']:
            if s.get('budget_name'):
                continue
            cat = (s.get('category_name') or '').strip()
            seguro = presupuestos.presupuesto_seguro(cat, mapa)
            fila = (
                t['id'],
                s['date'][:10],
                float(s['amount']),
                cat,
                s.get('destination_name') or s.get('description'),
                seguro,
            )
            (claros if seguro else dudosos).append(fila)

    print(f'desde {desde}: {len(claros) + len(dudosos)} gastos sin presupuesto')
    print()
    if claros:
        print(f'{len(claros)} con presupuesto claro segun tu historico:')
        for tid, f, monto, cat, quien, seg in sorted(claros, key=lambda x: x[1]):
            print(
                f'  #{tid:5} {f} {monto:>12,.0f} {cat:22} {str(quien)[:20]:20} -> {seg}'
            )
    if dudosos:
        print()
        print(f'{len(dudosos)} donde el historico esta repartido; NO se tocan:')
        for cat, n in Counter(x[3] or '(sin categoria)' for x in dudosos).most_common():
            info = mapa.get(cat)
            reparto = info['reparto'] if info else 'sin historico'
            print(f'  {cat or "(sin categoria)":24} {n:3}  {reparto}')

    if '--en-serio' not in args:
        print()
        print('SECO: no cambie nada. Corre con --en-serio para ponerle el')
        print('presupuesto a los que estan claros.')
        return
    print()
    for tid, _f, _m, _c, _q, seg in claros:
        try:
            firefly.actualizar_split(str(tid), budget_name=seg)
            print(f'  #{tid} -> {seg}')
        except Exception as ex:
            print(f'  #{tid} FALLO: {str(ex)[:110]}')


DIAGNOSTICOS = {
    'rutas': rutas,
    'base': base,
    'modelos': modelos,
    'correo': correo,
    'tarjetas': tarjetas,
    'presupuestos': budgets,
    'interprete': interpretar,
    'asesor': contexto,
    'extractos': extractos,
    'pasarelas': pasarelas,
    'sin-presupuesto': sin_presupuesto,
}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__.strip().split('\n\n')[0] + '\n')
        ancho = max(len(k) for k in DIAGNOSTICOS)
        for nombre, fn in DIAGNOSTICOS.items():
            primera = (fn.__doc__ or '').strip().split('\n')[0]
            print(f'  {nombre:<{ancho}}  {primera}')
        return 0
    fn = DIAGNOSTICOS.get(argv[0])
    if fn is None:
        print(
            f'No conozco «{argv[0]}». Opciones: {", ".join(DIAGNOSTICOS)}',
            file=sys.stderr,
        )
        return 2
    fn(argv[1:])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
