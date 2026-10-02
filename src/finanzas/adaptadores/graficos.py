"""Las graficas del resumen diario, en PNG para mandarlas por Telegram.

Dos paneles, porque el resumen contesta dos preguntas:

  1. ¿Como voy con los presupuestos del mes?  Un medidor por presupuesto: la
     barra es lo gastado sobre el tope, y una raya marca donde deberia ir a
     estas alturas del mes si se gastara parejo.
  2. ¿Me alcanza la cuenta para lo que viene?  El saldo proyectado dia a dia,
     con los movimientos grandes marcados y el minimo señalado.

Las decisiones de color: un solo azul para los datos; rojo SOLO para lo que se
paso del tope o baja de cero, y siempre con una palabra al lado (el color no
carga el significado solo). El texto va en tinta, nunca del color de la serie.
"""

from __future__ import annotations

import io
from datetime import date

import matplotlib

matplotlib.use('Agg')  # sin pantalla: el contenedor no tiene una

import matplotlib.pyplot as plt

SUPERFICIE = '#fcfcfb'
TINTA = '#0b0b0b'
TINTA_2 = '#52514e'
TINTA_3 = '#8a8984'
GRILLA = '#e4e3df'
PISTA = '#ecebe7'
AZUL = '#2a78d6'
CRITICO = '#d03b3b'
CRITICO_SUAVE = '#fbe9e9'

MESES_LARGOS = (
    'enero',
    'febrero',
    'marzo',
    'abril',
    'mayo',
    'junio',
    'julio',
    'agosto',
    'septiembre',
    'octubre',
    'noviembre',
    'diciembre',
)
MESES = (
    'ene',
    'feb',
    'mar',
    'abr',
    'may',
    'jun',
    'jul',
    'ago',
    'sep',
    'oct',
    'nov',
    'dic',
)


def corto(v: float, signo: bool = False) -> str:
    """1.803.809 -> '1,8M'; 450.000 -> '450k'. Para etiquetas que no caben."""
    s = '-' if v < 0 else ('+' if signo and v > 0 else '')
    a = abs(v)
    if a >= 1_000_000:
        txt = f'{a / 1_000_000:.1f}'.replace('.', ',').replace(',0', '')
        return f'{s}{txt}M'
    if a >= 1_000:
        return f'{s}{a / 1_000:.0f}k'
    return f'{s}{a:.0f}'


def dia_mes(d: date) -> str:
    return f'{d.day} {MESES[d.month - 1]}'


def _ejes_limpios(ax):
    ax.set_facecolor(SUPERFICIE)
    for lado in ('top', 'right', 'left'):
        ax.spines[lado].set_visible(False)
    ax.spines['bottom'].set_color(GRILLA)
    ax.tick_params(colors=TINTA_2, labelsize=9, length=0)


def _medidores(ax, presupuestos: list[dict], dia: int, dias: int, titulo: str):
    filas = [p for p in presupuestos if p.get('limite')]
    ritmo = dia / dias
    ax.set_xlim(0, 1)
    ax.set_ylim(len(filas) - 0.4, -0.9)
    ax.axis('off')
    ax.text(
        0,
        -0.85,
        titulo,
        fontsize=13,
        fontweight='bold',
        color=TINTA,
        va='bottom',
    )
    ax.text(
        1,
        -0.85,
        f'día {dia} de {dias}  ·  ┃ = ritmo parejo',
        fontsize=9,
        color=TINTA_2,
        ha='right',
        va='bottom',
    )
    for i, p in enumerate(filas):
        lim, gas = p['limite'], p['gastado']
        frac = gas / lim if lim else 0
        pasado = gas > lim
        y = i + 0.15
        # La pista y el relleno: 4px de radio no se puede en barh, asi que se
        # dejan rectas y finas. El relleno se corta en el tope y lo que sobra
        # se dice con palabras.
        ax.barh(y, 1, height=0.26, color=PISTA, linewidth=0)
        ax.barh(
            y,
            min(frac, 1),
            height=0.26,
            color=CRITICO if pasado else AZUL,
            linewidth=0,
        )
        ax.plot([ritmo, ritmo], [y - 0.2, y + 0.2], color=TINTA, linewidth=1.5)
        ax.text(0, y - 0.22, p['nombre'], fontsize=10.5, color=TINTA, va='bottom')
        derecha = f'{corto(gas)} de {corto(lim)}'
        ax.text(
            1, y - 0.22, derecha, fontsize=10, color=TINTA_2, ha='right', va='bottom'
        )
        if pasado:
            nota, color = f'⚠ pasado por {corto(gas - lim)}', CRITICO
        elif frac > ritmo + 0.1:
            nota, color = '▲ va más rápido que el mes', TINTA_2
        else:
            nota, color = '', TINTA_2
        if nota:
            ax.text(
                0.5, y - 0.22, nota, fontsize=9, color=color, ha='center', va='bottom'
            )


def _proyeccion(ax, proy, cuenta: str):
    # El eje x son dias desde hoy, no fechas: la conversion de fechas de
    # matplotlib pasa por una ruta de numpy que ya esta deprecada, y las
    # etiquetas se escriben a mano igual.
    inicio = proy.serie[0][0]

    def x(d: date) -> int:
        return (d - inicio).days

    xs = [x(d) for d, _ in proy.serie]
    ys = [s / 1_000_000 for _, s in proy.serie]
    _ejes_limpios(ax)
    ax.step(xs, ys, where='post', color=AZUL, linewidth=2, zorder=3)
    ax.grid(axis='y', color=GRILLA, linewidth=0.8)
    ax.set_axisbelow(True)
    piso = min(*ys, 0) - 1.2
    techo = max(ys) * 1.18 + 0.3
    ax.set_ylim(piso, techo)
    if piso < 0:
        ax.axhspan(piso, 0, color=CRITICO_SUAVE, linewidth=0, zorder=0)
        ax.text(
            xs[0], piso + 0.15, 'bajo cero = sobregiro', fontsize=8.5, color=CRITICO
        )
    ax.axhline(0, color=TINTA_3, linewidth=1)
    if proy.colchon:
        c = proy.colchon / 1_000_000
        ax.axhline(c, color=TINTA_3, linewidth=1, linestyle=(0, (3, 3)))
        ax.text(xs[0], c + 0.08, 'colchón', fontsize=8.5, color=TINTA_3, va='bottom')

    # Las salidas grandes, con su nombre: son las que hay que tener presentes.
    # Las quincenas no se rotulan; se ven solas en los escalones hacia arriba.
    # El rotulo va a la derecha del punto, con fondo: dos salidas a tres dias
    # (el contrato el 20 y el arriendo el 23) se pisarian sin el.
    saldo_de = dict(proy.serie)
    salidas = [m for m in proy.eventos if m.monto <= -500_000]
    salidas = sorted(salidas, key=lambda m: m.monto)[:4]
    for m in salidas:
        y = saldo_de.get(m.fecha, 0) / 1_000_000
        ax.plot(
            x(m.fecha),
            y,
            'o',
            markersize=6,
            color=AZUL,
            markeredgecolor=SUPERFICIE,
            markeredgewidth=1.5,
            zorder=4,
        )
        # Cerca del borde derecho el rotulo va a la izquierda, o se sale.
        al_borde = x(m.fecha) > xs[-1] * 0.8
        ax.annotate(
            f'{m.concepto[:16]} {corto(m.monto, signo=True)}',
            (x(m.fecha), y),
            xytext=(-6 if al_borde else 6, -5),
            textcoords='offset points',
            ha='right' if al_borde else 'left',
            va='top',
            fontsize=7.5,
            color=TINTA_2,
            bbox={'boxstyle': 'round,pad=0.2', 'fc': SUPERFICIE, 'ec': 'none'},
            zorder=5,
        )
    dmin, smin = proy.minimo
    al_final = x(dmin) >= xs[-1] * 0.6
    ax.annotate(
        f'mínimo {corto(smin)} · {dia_mes(dmin)}',
        (x(dmin), smin / 1_000_000),
        xytext=(-10 if al_final else 10, 8),
        textcoords='offset points',
        ha='right' if al_final else 'left',
        va='bottom',
        fontsize=9.5,
        fontweight='bold',
        color=CRITICO if smin < 0 else TINTA,
    )
    # Una marca cada lunes, con su dia y mes.
    lunes = [d for d, _ in proy.serie if d.weekday() == 0]
    ax.set_xticks([x(d) for d in lunes], [dia_mes(d) for d in lunes])
    ax.set_xlim(-0.5, xs[-1] + 0.5)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f'{v:.0f}M'))
    ax.set_title(
        f'{cuenta}: saldo proyectado',
        loc='left',
        fontsize=13,
        fontweight='bold',
        color=TINTA,
        pad=10,
    )


def tablero(
    titulo: str,
    presupuestos: list[dict],
    dia: int,
    dias: int,
    proyeccion=None,
    cuenta: str = '',
) -> bytes:
    """El PNG del resumen. Sin proyeccion, solo los presupuestos."""
    n = len([p for p in presupuestos if p.get('limite')])
    alto_medidores = 0.7 + 0.6 * max(n, 1)
    alto_proy = 4.4 if proyeccion else 0
    fig = plt.figure(
        figsize=(7.2, alto_medidores + alto_proy + 0.5), dpi=150, facecolor=SUPERFICIE
    )
    if proyeccion:
        gs = fig.add_gridspec(
            2,
            1,
            height_ratios=[alto_medidores, alto_proy],
            hspace=0.12,
            top=0.97,
            bottom=0.05,
            left=0.09,
            right=0.95,
        )
        _medidores(fig.add_subplot(gs[0]), presupuestos, dia, dias, titulo)
        _proyeccion(fig.add_subplot(gs[1]), proyeccion, cuenta)
    else:
        fig.subplots_adjust(top=0.95, bottom=0.03, left=0.09, right=0.95)
        _medidores(fig.add_subplot(111), presupuestos, dia, dias, titulo)
    buf = io.BytesIO()
    fig.savefig(buf, format='png', facecolor=SUPERFICIE)
    plt.close(fig)
    return buf.getvalue()
