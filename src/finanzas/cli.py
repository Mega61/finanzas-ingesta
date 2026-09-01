"""Un solo punto de entrada.

    finanzas                      # que hay para hacer
    finanzas servicio             # lo que corre en el contenedor
    finanzas estado               # como va la cola
    finanzas ciclo --en-serio     # bajar, procesar y publicar de verdad
    finanzas bot escuchar
    finanzas revisar firefly      # que las credenciales sirvan

Antes eran cuatro archivos con su propio `if __name__ == '__main__'`, cada uno
con sus opciones y ninguno mencionando a los otros: para saber que se podia
hacer habia que abrirlos. Esto no reimplementa nada, solo enruta a lo que ya
existe — cada modulo sigue corriendo suelto con `python demonio.py estado`.

El grupo se elige por la primera palabra. Todo lo que venga despues se le pasa
tal cual al modulo, asi que sus opciones siguen sirviendo sin duplicarlas aqui.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Los modulos de aplicacion todavia viven en la raiz del repo, un nivel arriba
# de src/. Mientras se muevan, hay que ponerla en el path.
RAIZ = Path(__file__).resolve().parent.parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

# Cada grupo: (modulo, que hace, ejemplos). El modulo se importa solo cuando se
# usa: `finanzas --ayuda` no tiene por que cargar Gemini ni abrir la base.
GRUPOS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    'servicio': (
        'servicio',
        'el proceso del contenedor: ingesta con horario + bot',
        ('finanzas servicio', 'finanzas servicio --una-vuelta'),
    ),
    'bot': (
        'bot',
        'Telegram: escuchar, preguntar, resumen',
        ('finanzas bot escuchar', 'finanzas bot resumen'),
    ),
    'conciliar': (
        'conciliador',
        'cruzar los extractos en PDF contra lo publicado',
        ('finanzas conciliar --carpeta ../extractos',),
    ),
    'revisar': (
        'verificar',
        'que las credenciales de afuera sirvan',
        ('finanzas revisar', 'finanzas revisar firefly telegram'),
    ),
}

# Las acciones del demonio se exponen directo, sin escribir "demonio" antes:
# son las que se usan a diario.
ACCIONES_DEMONIO = (
    ('estado', 'como va la cola y cuantas reglas hay'),
    ('ciclo', 'bajar, procesar y publicar, todo seguido'),
    ('bajar', 'traer correo nuevo'),
    ('procesar', 'parsear y clasificar lo que este sin procesar'),
    ('publicar', 'subir a Firefly lo que este listo'),
    ('reclasificar', 'volver a pasar las reglas sobre lo que sigue abierto'),
    ('sembrar', 'aprender reglas del historico de Firefly'),
    ('importar', 'cargar .eml de una carpeta'),
)


def _ayuda() -> str:
    anchos = max(len(a) for a, _ in ACCIONES_DEMONIO)
    lineas = [__doc__.strip().split('\n\n')[0], '']
    lineas.append('Uso: finanzas <comando> [opciones]')
    lineas.append('')
    lineas.append('Del dia a dia:')
    lineas += [f'  {a:<{anchos}}  {d}' for a, d in ACCIONES_DEMONIO]
    lineas.append('')
    lineas.append('Procesos y utilidades:')
    anchos2 = max(len(g) for g in GRUPOS)
    lineas += [f'  {g:<{anchos2}}  {GRUPOS[g][1]}' for g in GRUPOS]
    lineas.append('')
    lineas.append('Cada comando acepta --help para ver sus opciones.')
    lineas.append('Nada publica en Firefly sin --en-serio.')
    return '\n'.join(lineas)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv or argv[0] in ('-h', '--help', 'ayuda'):
        print(_ayuda())
        return 0

    comando, resto = argv[0], argv[1:]

    if comando in ('--version', 'version'):
        sha = os.environ.get('GIT_SHA', 'desconocido')
        print(f'finanzas {sha[:12]}  ({os.environ.get("BUILD_FECHA", "sin fecha")})')
        return 0

    if comando in GRUPOS:
        modulo = __import__(GRUPOS[comando][0])
        salida = modulo.main(resto)
        return int(salida or 0)

    if comando in {a for a, _ in ACCIONES_DEMONIO}:
        import demonio

        return int(demonio.main([comando, *resto]) or 0)

    print(f'No conozco «{comando}».\n', file=sys.stderr)
    print(_ayuda(), file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
