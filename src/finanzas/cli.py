"""Un solo punto de entrada.

    finanzas                      # que hay para hacer
    finanzas servicio             # lo que corre en el contenedor
    finanzas estado               # como va la cola
    finanzas ciclo --en-serio     # bajar, procesar y publicar de verdad
    finanzas bot escuchar
    finanzas revisar firefly      # que las credenciales sirvan
    finanzas config               # las tres carpetas y que hay configurado

Antes eran cuatro archivos con su propio `if __name__ == '__main__'`, cada uno
con sus opciones y ninguno mencionando a los otros: para saber que se podia
hacer habia que abrirlos.

Esto no reimplementa nada, solo enruta. El grupo se elige por la primera
palabra y todo lo que venga despues se le pasa tal cual al modulo, asi que sus
opciones siguen sirviendo sin duplicarlas aqui.
"""

from __future__ import annotations

import importlib
import os
import sys

# Cada grupo: (modulo, que hace, ejemplos). Se importa solo cuando se usa:
# `finanzas --help` no tiene por que cargar Gemini ni abrir la base.
GRUPOS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    'servicio': (
        'finanzas.entrada.servicio',
        'el proceso del contenedor: ingesta con horario + bot',
        ('finanzas servicio', 'finanzas servicio --una-vuelta'),
    ),
    'bot': (
        'finanzas.entrada.bot',
        'Telegram: escuchar, preguntar, resumen',
        ('finanzas bot escuchar', 'finanzas bot resumen'),
    ),
    'conciliar': (
        'finanzas.aplicacion.conciliador',
        'cruzar los extractos en PDF contra lo publicado',
        ('finanzas conciliar --carpeta ../extractos',),
    ),
    'revisar': (
        'finanzas.entrada.verificar',
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

DEMONIO = 'finanzas.entrada.demonio'


def _ayuda() -> str:
    ancho = max(len(a) for a, _ in ACCIONES_DEMONIO)
    lineas = [__doc__.strip().split('\n\n')[0], '']
    lineas.append('Uso: finanzas <comando> [opciones]')
    lineas.append('')
    lineas.append('Del dia a dia:')
    lineas += [f'  {a:<{ancho}}  {d}' for a, d in ACCIONES_DEMONIO]
    lineas.append('')
    lineas.append('Procesos y utilidades:')
    ancho2 = max(*(len(g) for g in GRUPOS), len('config'))
    lineas += [f'  {g:<{ancho2}}  {GRUPOS[g][1]}' for g in GRUPOS]
    lineas.append(f'  {"config":<{ancho2}}  las tres carpetas y que hay configurado')
    lineas.append('')
    lineas.append('Cada comando acepta --help para ver sus opciones.')
    lineas.append('Nada publica en Firefly sin --en-serio.')
    lineas.append('Diagnosticos a mano: python herramientas/diagnostico.py')
    return '\n'.join(lineas)


def _version() -> str:
    sha = os.environ.get('GIT_SHA', 'desconocido')
    return f'finanzas {sha[:12]}  ({os.environ.get("BUILD_FECHA", "sin fecha")})'


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv or argv[0] in ('-h', '--help', 'ayuda'):
        print(_ayuda())
        return 0

    comando, resto = argv[0], argv[1:]

    if comando in ('--version', 'version'):
        print(_version())
        return 0

    if comando == 'config':
        from finanzas import config  # noqa: PLC0415

        print(config.describir_rutas())
        print()
        print(config.describir())
        return 0

    if comando in GRUPOS:
        modulo = importlib.import_module(GRUPOS[comando][0])
        return int(modulo.main(resto) or 0)

    if comando in {a for a, _ in ACCIONES_DEMONIO}:
        demonio = importlib.import_module(DEMONIO)
        return int(demonio.main([comando, *resto]) or 0)

    print(f'No conozco «{comando}».\n', file=sys.stderr)
    print(_ayuda(), file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
