# -*- coding: utf-8 -*-
"""El asesor. Responde preguntas de plata con TUS numeros, no con opiniones.

    "quiero comprar un conjunto de ropa deportiva, ¿deberia?"

Para responder eso de verdad hay que saber: cuanto queda en el presupuesto que
le toca, cuanto se ha gastado este mes, cuanta deuda hay en las tarjetas, cuando
cortan, y que obligaciones fijas vienen. Todo eso se arma aqui y se le pasa al
modelo, que responde con las cifras al frente.

La regla dura: el asesor NO inventa numeros. Todo dato que cite viene del
contexto que se le arma desde Firefly. Si le falta un dato, tiene que decir que
le falta, no estimarlo.
"""
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
import firefly  # noqa: E402
import presupuestos  # noqa: E402


def _plata(v):
    return f"${abs(v):,.0f}".replace(',', '.')


def _fin_de_mes(d):
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


# ------------------------------------------------------------------ contexto

def saldos():
    """Cuanto hay y cuanto se debe, por cuenta."""
    activos, deudas = [], []
    for a in firefly.get_all('/api/v1/accounts?type=asset'):
        at = a['attributes']
        if not at.get('active'):
            continue
        try:
            saldo = float(at.get('current_balance') or 0)
        except (TypeError, ValueError):
            saldo = 0.0
        rol = (at.get('account_role') or '')
        item = {'nombre': at['name'], 'saldo': saldo, 'rol': rol,
                'moneda': at.get('currency_code') or 'COP'}
        # una tarjeta de credito con saldo negativo es deuda
        if rol == 'ccAsset' or saldo < 0:
            deudas.append(item)
        else:
            activos.append(item)
    return sorted(activos, key=lambda x: -x['saldo']), sorted(deudas, key=lambda x: x['saldo'])


def gasto_del_mes(cuando=None):
    """Gasto de este mes por categoria, y el total."""
    hoy = cuando or date.today()
    ini, fin = hoy.replace(day=1), _fin_de_mes(hoy)
    porcat = {}
    total = 0.0
    ingresos = 0.0
    for t in firefly.get_all(f'/api/v1/transactions?start={ini}&end={fin}'):
        for s in t['attributes']['transactions']:
            tipo = (s.get('type') or '').lower()
            try:
                monto = abs(float(s.get('amount') or 0))
            except (TypeError, ValueError):
                continue
            if tipo == 'withdrawal':
                c = (s.get('category_name') or 'sin categoria').strip()
                porcat[c] = porcat.get(c, 0.0) + monto
                total += monto
            elif tipo == 'deposit':
                ingresos += monto
    return {'por_categoria': dict(sorted(porcat.items(), key=lambda x: -x[1])),
            'total': total, 'ingresos': ingresos,
            'dia_del_mes': hoy.day, 'dias_del_mes': fin.day}


def cortes_de_tarjeta():
    """Cuando corta cada tarjeta, para saber a que extracto va una compra."""
    import clasificador
    salida = []
    for p in clasificador.productos():
        if p['clase'] != 'tarjeta' or p['hasta']:
            continue
        salida.append({'cuenta': p['cuenta'], 'instrumento': p['instrumento']})
    return salida


def obligaciones_fijas(meses=3):
    """Lo que se repite todos los meses: arriendo, facturas, suscripciones.

    Se saca de lo que efectivamente se pago los ultimos meses, no de una lista
    escrita a mano que se desactualiza.
    """
    hoy = date.today()
    desde = (hoy.replace(day=1) - timedelta(days=31 * meses)).replace(day=1)
    porcat = {}
    for t in firefly.get_all(f'/api/v1/transactions?start={desde}&end={hoy}'):
        for s in t['attributes']['transactions']:
            if (s.get('type') or '').lower() != 'withdrawal':
                continue
            c = (s.get('category_name') or '').strip()
            if c not in ('Arriendo', 'Facturas', 'Cuotas de manejo', 'Impuestos',
                         'Gimnasio', 'Intereses TC'):
                continue
            mes = (s.get('date') or '')[:7]
            porcat.setdefault(c, {})
            try:
                porcat[c][mes] = porcat[c].get(mes, 0.0) + abs(float(s.get('amount') or 0))
            except (TypeError, ValueError):
                pass
    salida = []
    for c, meses_d in porcat.items():
        if not meses_d:
            continue
        prom = sum(meses_d.values()) / len(meses_d)
        salida.append({'categoria': c, 'promedio_mensual': prom,
                       'meses_vistos': len(meses_d)})
    return sorted(salida, key=lambda x: -x['promedio_mensual'])


def armar_contexto():
    """Todo lo que el asesor necesita saber, en un dict."""
    act, deu = saldos()
    return {
        'hoy': str(date.today()),
        'presupuestos': presupuestos.estado(),
        'gasto_mes': gasto_del_mes(),
        'activos': act,
        'deudas': deu,
        'obligaciones': obligaciones_fijas(),
        'tarjetas': cortes_de_tarjeta(),
    }


def contexto_en_texto(ctx=None):
    """El contexto como texto, que es lo que se le manda al modelo."""
    c = ctx or armar_contexto()
    L = [f"FECHA DE HOY: {c['hoy']}"]

    g = c['gasto_mes']
    L += ["", f"ESTE MES (dia {g['dia_del_mes']} de {g['dias_del_mes']})",
          f"  gastado: {_plata(g['total'])}",
          f"  ingresos registrados: {_plata(g['ingresos'])}"]
    if g['por_categoria']:
        L.append("  por categoria:")
        for cat, v in list(g['por_categoria'].items())[:14]:
            L.append(f"    {cat}: {_plata(v)}")

    L += ["", "PRESUPUESTOS DEL MES"]
    for b in c['presupuestos']:
        if b['limite']:
            L.append(f"  {b['nombre']}: gastado {_plata(b['gastado'])} de "
                     f"{_plata(b['limite'])} ({b['pct']:.0f}%), "
                     f"queda {_plata(b['queda'])}")
        else:
            L.append(f"  {b['nombre']}: gastado {_plata(b['gastado'])}, sin tope puesto")

    if c['activos']:
        L += ["", "PLATA DISPONIBLE"]
        for a in c['activos']:
            L.append(f"  {a['nombre']}: {_plata(a['saldo'])} {a['moneda']}")
    if c['deudas']:
        L += ["", "DEUDA DE TARJETAS"]
        for d in c['deudas']:
            L.append(f"  {d['nombre']}: {_plata(d['saldo'])} {d['moneda']}")

    if c['obligaciones']:
        L += ["", "OBLIGACIONES QUE SE REPITEN CADA MES (promedio real)"]
        for o in c['obligaciones']:
            L.append(f"  {o['categoria']}: {_plata(o['promedio_mensual'])}")
    return '\n'.join(L)


# -------------------------------------------------------------------- asesor

INSTRUCCIONES = """Eres el asesor financiero personal de este usuario. Hablas
espanol colombiano, directo y sin rodeos, como un amigo que sabe de plata y no
te endulza las cosas.

REGLAS DURAS:
- NUNCA inventes un numero. Todo dato que cites tiene que estar en el CONTEXTO.
  Si te falta un dato para responder bien, dilo: "no tengo tu X".
- Responde con las cifras al frente, no con generalidades. Mal: "deberias
  cuidar tu presupuesto". Bien: "en Antojos te quedan 220.000 y estamos a dia
  12, o sea 12.000 por dia hasta fin de mes".
- Si te preguntan si comprar algo, mira el presupuesto al que iria, cuanto
  queda, cuantos dias faltan del mes, y que obligaciones vienen. Da una
  respuesta clara: si, no, o si pero espera a tal fecha.
- Si la compra cabe pero deja el presupuesto muy justo, dilo con el numero.
- Ten en cuenta la deuda de tarjetas: si hay deuda alta, una compra nueva a
  credito no es lo mismo que pagarla de la cuenta.
- No sermonees. Una recomendacion clara y el porque en numeros. Maximo 6 lineas
  salvo que te pidan detalle.
- Usa formato de Telegram: <b>negrita</b> y <i>cursiva</i>. Nada de markdown
  con asteriscos ni tablas.
"""


def preguntar(pregunta, historial=None, ctx_texto=None):
    """Le pregunta al asesor. `historial` es [(rol, texto)] de la conversacion.

    Devuelve el texto de la respuesta, o levanta ia.SinIA.
    """
    import ia

    ctx_texto = ctx_texto if ctx_texto is not None else contexto_en_texto()
    contenidos = []
    for rol, txt in (historial or [])[-8:]:
        contenidos.append({'role': 'user' if rol == 'usuario' else 'model',
                           'parts': [{'text': txt}]})
    contenidos.append({'role': 'user', 'parts': [{'text':
        f"CONTEXTO FINANCIERO (datos reales, no inventes nada mas):\n"
        f"{ctx_texto}\n\n"
        f"PREGUNTA: {pregunta}"}]})

    payload = {
        'systemInstruction': {'parts': [{'text': INSTRUCCIONES}]},
        'contents': contenidos,
        'generationConfig': {'temperature': 0.3, 'maxOutputTokens': 900},
    }
    r = ia._llamar(payload)
    try:
        return r['candidates'][0]['content']['parts'][0]['text'].strip()
    except (KeyError, IndexError):
        raise ia.SinIA(f"respuesta inesperada: {str(r)[:200]}") from None


if __name__ == '__main__':
    txt = contexto_en_texto()
    print("=" * 72)
    print("CONTEXTO QUE VE EL ASESOR")
    print("=" * 72)
    print(txt)
    print("\n" + "=" * 72)
    aprox = len(txt) // 3.5
    print(f"~{aprox:.0f} tokens de contexto por pregunta")
    print(f"a $0.75 por millon: ~${aprox/1_000_000*0.75:.4f} por pregunta")

    import ia
    if len(sys.argv) > 1 and ia.disponible():
        print("\n" + "=" * 72)
        p = ' '.join(sys.argv[1:])
        print(f"PREGUNTA: {p}\n")
        print(preguntar(p, ctx_texto=txt))
    elif len(sys.argv) > 1:
        print("\n(falta GEMINI_API_KEY para preguntar de verdad)")
