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

from finanzas import config
from finanzas.adaptadores import db, graph, ia
from finanzas.aplicacion import asesor, clasificador, interprete, presupuestos
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
    for t in ('usuarios', 'buzones', 'correos_crudos', 'pendientes', 'reglas',
              'bitacora', 'sugerencias', 'propuestas', 'preguntas_enviadas'):
        print(f'  {t:20} {alm.contar_por_tabla(t):6}')
    cx.close()


def modelos(_args):
    """Que modelos admite tu API key de Gemini, y si el configurado esta."""
    if not ia.disponible():
        sys.exit('falta GEMINI_API_KEY en el .env.\n'
                 'Se saca gratis en https://aistudio.google.com/apikey')
    print(f'modelo configurado: {ia.MODELO}\n')
    try:
        ms = ia.modelos()
    except ia.SinIA as ex:
        sys.exit(f'no pude listar modelos: {ex}')
    print(f'{len(ms)} modelos disponibles con tu key. Los flash:')
    for m in sorted(x for x in ms if 'flash' in x):
        print(f"  {m}{'  <-- configurado' if m == ia.MODELO else ''}")
    if ia.MODELO not in ms:
        print(f"\nOJO: '{ia.MODELO}' no aparece en tu lista. Cambia "
              f'GEMINI_MODELO a uno de los de arriba.')


def correo(_args):
    """Que el token de Graph sirva, y los ultimos 5 correos del banco."""
    import re

    tok = graph.token(interactivo=True)
    print('token ok. ultimos 5 correos del banco:\n')
    for m in graph.mensajes(tok, tope=5):
        frase = re.sub(r'\s+', ' ', graph.cuerpo_plano(m))[:150]
        print(f"  {m['receivedDateTime'][:16]}  {m.get('subject', '')[:34]}")
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
        print(f"  {'ok ' if got == pr['cuenta'] else 'MAL'} "
              f"*{pr['instrumento']} {f} -> {got}")
    sin = clasificador.cuenta_de_instrumento('0000', '2026-01-01')
    print(f"  {'ok ' if sin is None else 'MAL'} *0000 -> None   "
          f'(plastico desconocido: se pregunta)')
    print('\nnormalizacion de comercios')
    for t in ['UBER RIDES*DL', 'DLO*Didi', 'PAYU*CINEMARK', 'CYCLE GEAR N169',
              'UBER BV USD-USD COLO', 'GOOGLE *Workspace_go',
              'MERCADO PAGO*TIERRAG', 'DROGUERIA ALEMANA 47']:
        print(f'  {t:26} -> {clasificador.normalizar(t)}')


def budgets(_args):
    """Como van los presupuestos, y que categorias no deciden solas."""
    print('=== presupuestos activos, este mes ===')
    for b in presupuestos.estado():
        lim = f"{b['limite']:,.0f}" if b['limite'] else 'sin tope'
        pct = f"{b['pct']:.0f}%" if b['pct'] is not None else '-'
        print(f"  {b['nombre']:24} gastado={b['gastado']:>14,.0f} "
              f'de {lim:>14}  {pct}')
    print('\n=== categoria -> presupuesto ===')
    mapa = presupuestos.mapa_categoria()
    dudosas = [c for c, d in mapa.items() if not d['seguro']]
    print(f'  {len(mapa) - len(dudosas)} categorias deciden solas')
    print(f'  {len(dudosas)} hay que preguntarlas:')
    for c in sorted(dudosas):
        print(f"    {c:28} {mapa[c]['reparto']}")


def interpretar(_args):
    """Que entiende el interprete de varias frases, contra un movimiento fijo.

    Las seis frases son las que destaparon bugs: «mercado del mes» daba
    Minimercado Amonte y despues Mercado Libre, y «esto fue el gym» daba comida
    de gato porque MERCADO PAGO*ZONAFIT matcheaba MERCADO LIBRE.
    """
    db.inicializar()
    cx = db.conectar()
    cat = interprete.catalogo(cx, 1)
    print(f"catalogo: {len(cat['categorias'])} categorias, "
          f"{len(cat['presupuestos'])} presupuestos, "
          f"{len(cat['comercios'])} comercios")
    print('IA: ' + ('Gemini disponible' if interprete.ia_disponible()
                    else 'sin API key, solo heuristica') + '\n')
    mov = {'fecha': '2026-09-01', 'valor': -151495.0, 'moneda': 'COP',
           'contraparte': 'MERCADO PAGO*TIERRAG',
           'descripcion': 'MERCADO PAGO*TIERRAG',
           'cuenta_firefly': 'MASTERCARD BLACK'}
    for f in ('fue la comida de la gata en tierragro',
              'le compre granos a la michina',
              'mercado del mes',
              'almorzamos afuera, fue un antojo',
              'gasolina de la moto',
              'esto fue el gym'):
        d = interprete.interpretar(cx, 1, mov, f, cat=cat)
        print(f'  {f!r}')
        print(f"     -> categoria={d['categoria']!r} "
              f"presupuesto={d['presupuesto']!r} comercio={d['comercio']!r}")
        print(f"        conf={d['confianza']:.2f} fuente={d['fuente']} "
              f"pedir_presupuesto={d['pedir_presupuesto']}")
        print(f"        razon: {d['razon']}")
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
    carpeta = (args[0] if args
               else config.ruta_personal('Extractos Bancolombia', '_pdf'))
    exts = extracto_tarjeta.parse_carpeta(carpeta, clave)
    ok = [e for e in exts if not e.error]
    print(f'{len(exts)} extractos, {len(ok)} abiertos, '
          f'{sum(len(e.movimientos) for e in ok)} movimientos\n')
    for e in sorted(ok, key=lambda x: x.periodo_archivo)[-6:]:
        print(f'  {e.archivo}')
        print(f'    {e.marca} *{e.instrumento}  periodo {e.desde} -> {e.hasta}'
              f'  {len(e.movimientos)} movs')
        for mv in e.movimientos[:3]:
            print(f'      {mv.fecha} {mv.moneda} {mv.valor:>13,.2f} '
                  f'{mv.descripcion[:40]}')
    malos = [e for e in exts if e.error]
    if malos:
        print(f'\n{len(malos)} con error:')
        for e in malos[:5]:
            print(f'  {e.archivo}: {e.error}')


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
        print(f'No conozco «{argv[0]}». Opciones: '
              f"{', '.join(DIAGNOSTICOS)}", file=sys.stderr)
        return 2
    fn(argv[1:])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
