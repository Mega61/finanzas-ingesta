"""Quien usa el sistema, con que libros, buzones e instrumentos.

Juan se sigue configurando con las variables de siempre (FIREFLY_URL,
GRAPH_*, productos.csv). Todos los demas se declaran aqui, en UN solo lugar,
y el arranque los deja en la base. Se lee, en este orden:

  1. PERSONAS_JSON   en una linea. Es lo que va en Portainer, cuya UI parte
                     las variables por saltos de linea.
  2. PERSONAS_TOML   el mismo contenido en TOML, para un .env de desarrollo.
  3. personas.toml   un archivo en el volumen de datos o en la raiz del repo.

`herramientas/generar_variables.py personas` convierte el TOML en la linea de
JSON para Portainer.

Los secretos NUNCA van aqui: cada libro y cada buzon dice el NOMBRE de la
variable de entorno que tiene su secreto (`secreto = "FIREFLY_TOKEN_NOVIA"`).
Lo que no es secreto -- una URL, un chat_id -- puede ir escrito o como
"${VARIABLE}".

    [[persona]]
    nombre = "Mariana"
    telegram = "${TELEGRAM_CHAT_ID_NOVIA}"

      [[persona.libro]]
      clave = "personal"
      nombre = "Personal"
      tipo = "firefly"
      url = "${FIREFLY_URL}"
      secreto = "FIREFLY_TOKEN_NOVIA"
      en_serio = false

      [[persona.buzon]]
      tipo = "imap"
      direccion = "ella@gmail.com"
      secreto = "GMAIL_APP_PASSWORD_NOVIA"

      [[persona.instrumento]]
      clave = "5788"
      clase = "cuenta"
      cuentas = { personal = "Bancolombia Ahorros *5788", estudio = "Bancolombia" }
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from typing import Any

from finanzas import config
from finanzas.adaptadores.almacen import Almacen

_NOMBRE_VARIABLE = re.compile(r'^[A-Z_][A-Z0-9_]*$')
_REFERENCIA = re.compile(r'\$\{([A-Z_][A-Z0-9_]*)\}')

TIPOS_LIBRO = ('firefly', 'actual')
TIPOS_BUZON = ('imap', 'graph')
CLASES = ('tarjeta', 'cuenta', 'efectivo')


class ConfiguracionInvalida(ValueError):
    """La configuracion de personas no se puede usar. Dice donde y por que."""


@dataclass(frozen=True)
class Libro:
    clave: str
    nombre: str
    tipo: str
    url: str
    secreto: str
    en_serio: bool = False
    desde: str | None = None
    ajustes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Buzon:
    tipo: str
    direccion: str
    secreto: str
    host: str = 'imap.gmail.com:993'
    facturas: bool = False


@dataclass(frozen=True)
class Instrumento:
    clave: str
    clase: str
    cuentas: dict[str, str]
    alias: tuple[str, ...] = ()
    desde: str | None = None
    hasta: str | None = None


@dataclass(frozen=True)
class Persona:
    nombre: str
    telegram: str | None
    libros: tuple[Libro, ...]
    buzones: tuple[Buzon, ...] = ()
    instrumentos: tuple[Instrumento, ...] = ()


# ------------------------------------------------------------------- leer


def _texto_crudo() -> tuple[str, str] | None:
    """(formato, contenido) de donde haya configuracion, o None."""
    if config.get('PERSONAS_JSON'):
        return 'json', config.get('PERSONAS_JSON')
    if config.get('PERSONAS_TOML'):
        return 'toml', config.get('PERSONAS_TOML')
    for ruta in (
        config.ruta_datos('personas.toml'),
        config.ruta_proyecto('personas.toml'),
    ):
        if os.path.exists(ruta):
            with open(ruta, encoding='utf-8') as fh:
                return 'toml', fh.read()
    return None


def leer() -> list[Persona]:
    """Las personas configuradas. [] si no hay ninguna configuracion."""
    crudo = _texto_crudo()
    if crudo is None:
        return []
    formato, contenido = crudo
    try:
        datos = json.loads(contenido) if formato == 'json' else tomllib.loads(contenido)
    except (json.JSONDecodeError, tomllib.TOMLDecodeError) as ex:
        raise ConfiguracionInvalida(
            f'la configuracion de personas no se lee: {ex}'
        ) from None
    return interpretar(datos)


def _expandir(valor: Any, donde: str) -> Any:
    """ "${VARIABLE}" -> su valor. Solo para lo que no es secreto."""
    if not isinstance(valor, str):
        return valor

    def uno(m: re.Match) -> str:
        v = config.get(m.group(1))
        if not v:
            raise ConfiguracionInvalida(f'{donde}: falta la variable {m.group(1)}')
        return v

    return _REFERENCIA.sub(uno, valor)


def _secreto(valor: Any, donde: str) -> str:
    """El NOMBRE de la variable del secreto. Si alguien pega el secreto mismo,
    se rechaza: terminaria en la base y en los logs."""
    if not isinstance(valor, str) or not _NOMBRE_VARIABLE.match(valor):
        raise ConfiguracionInvalida(
            f'{donde}: `secreto` es el NOMBRE de una variable de entorno '
            f'(p. ej. FIREFLY_TOKEN_NOVIA), no el secreto'
        )
    return valor


def interpretar(datos: dict[str, Any]) -> list[Persona]:
    """El dict ya leido -> personas validadas. Separado de `leer` para probarlo."""
    fuera: list[Persona] = []
    vistas: set[str] = set()
    for i, p in enumerate(datos.get('persona') or []):
        nombre = str(p.get('nombre') or '').strip()
        donde = f'persona «{nombre or i}»'
        if not nombre:
            raise ConfiguracionInvalida(f'la persona {i} no tiene nombre')
        if nombre in vistas:
            raise ConfiguracionInvalida(f'{donde} esta dos veces')
        if nombre == 'Juan':
            raise ConfiguracionInvalida(
                'Juan se configura con las variables de siempre, no aqui'
            )
        vistas.add(nombre)

        libros = []
        for lb in p.get('libro') or []:
            d = f'{donde}, libro «{lb.get("clave")}»'
            if lb.get('tipo') not in TIPOS_LIBRO:
                raise ConfiguracionInvalida(f'{d}: tipo tiene que ser {TIPOS_LIBRO}')
            if not lb.get('clave') or not lb.get('url'):
                raise ConfiguracionInvalida(f'{d}: faltan clave o url')
            libros.append(
                Libro(
                    clave=str(lb['clave']),
                    nombre=str(lb.get('nombre') or lb['clave']),
                    tipo=lb['tipo'],
                    url=str(_expandir(lb['url'], d)).rstrip('/'),
                    secreto=_secreto(lb.get('secreto'), d),
                    en_serio=bool(lb.get('en_serio', False)),
                    desde=lb.get('desde'),
                    ajustes={
                        k: _expandir(v, d) for k, v in (lb.get('ajustes') or {}).items()
                    },
                )
            )
        if not libros:
            raise ConfiguracionInvalida(f'{donde}: necesita al menos un libro')
        claves = [lb.clave for lb in libros]
        if len(claves) != len(set(claves)):
            raise ConfiguracionInvalida(f'{donde}: dos libros con la misma clave')

        buzones = []
        for bz in p.get('buzon') or []:
            d = f'{donde}, buzon «{bz.get("direccion")}»'
            if bz.get('tipo') not in TIPOS_BUZON:
                raise ConfiguracionInvalida(f'{d}: tipo tiene que ser {TIPOS_BUZON}')
            buzones.append(
                Buzon(
                    tipo=bz['tipo'],
                    direccion=str(bz.get('direccion') or '').strip(),
                    secreto=_secreto(bz.get('secreto'), d),
                    host=str(bz.get('host') or 'imap.gmail.com:993'),
                    facturas=bool(bz.get('facturas', False)),
                )
            )

        instrumentos = []
        for ins in p.get('instrumento') or []:
            d = f'{donde}, instrumento «{ins.get("clave")}»'
            if ins.get('clase') not in CLASES:
                raise ConfiguracionInvalida(f'{d}: clase tiene que ser {CLASES}')
            cuentas = {str(k): str(v) for k, v in (ins.get('cuentas') or {}).items()}
            if not cuentas:
                raise ConfiguracionInvalida(
                    f'{d}: necesita la cuenta en al menos un libro'
                )
            ajenos = set(cuentas) - set(claves)
            if ajenos:
                raise ConfiguracionInvalida(
                    f'{d}: los libros {sorted(ajenos)} no existen'
                )
            alias = ins.get('alias') or ()
            if isinstance(alias, str):
                alias = [a.strip() for a in alias.split(',')]
            instrumentos.append(
                Instrumento(
                    clave=str(ins['clave']).strip().lower(),
                    clase=ins['clase'],
                    cuentas=cuentas,
                    alias=tuple(a.lower() for a in alias if a),
                    desde=ins.get('desde'),
                    hasta=ins.get('hasta'),
                )
            )

        # El chat puede faltar todavia: se sabe cuando la persona le escribe al
        # bot por primera vez. Sin el, la persona existe pero el bot no la
        # atiende; no es motivo para no arrancar.
        try:
            telegram = (
                _expandir(p.get('telegram'), donde) if p.get('telegram') else None
            )
        except ConfiguracionInvalida:
            telegram = None
        fuera.append(
            Persona(
                nombre=nombre,
                telegram=str(telegram) if telegram else None,
                libros=tuple(libros),
                buzones=tuple(buzones),
                instrumentos=tuple(instrumentos),
            )
        )
    return fuera


# ---------------------------------------------------------------- aplicar


def aplicar(alm: Almacen, personas: list[Persona]) -> dict[str, int]:
    """Deja en la base lo que dice la configuracion. Idempotente.

    Un libro que sale de la configuracion se DESACTIVA y no se borra: sus
    movimientos lo siguen nombrando. Los instrumentos se reemplazan completos,
    porque nada los referencia.
    """
    cuenta = {'personas': 0, 'libros': 0, 'buzones': 0, 'instrumentos': 0}
    for p in personas:
        url_ff = next((lb.url for lb in p.libros if lb.tipo == 'firefly'), '')
        uid = alm.guardar_usuario(p.nombre, url_ff, '', p.telegram)
        cuenta['personas'] += 1

        ids = {}
        for lb in p.libros:
            ids[lb.clave] = alm.guardar_libro(
                uid,
                lb.clave,
                lb.nombre,
                lb.tipo,
                lb.url,
                lb.secreto,
                en_serio=lb.en_serio,
                desde=lb.desde,
                ajustes=lb.ajustes,
            )
            cuenta['libros'] += 1
        alm.desactivar_libros_salvo(uid, list(ids.values()))

        for bz in p.buzones:
            alm.guardar_buzon_configurado(
                uid, bz.tipo, bz.direccion, bz.secreto, bz.host, bz.facturas
            )
            cuenta['buzones'] += 1

        filas = [
            (
                ins.clave,
                ins.clase,
                ', '.join(ins.alias) or None,
                ids[lib],
                cta,
                ins.desde,
                ins.hasta,
            )
            for ins in p.instrumentos
            for lib, cta in ins.cuentas.items()
        ]
        alm.reemplazar_instrumentos(uid, filas)
        cuenta['instrumentos'] += len(filas)
    return cuenta


def chats() -> dict[str, str]:
    """chat_id -> nombre, de las personas configuradas. Para autorizar el bot
    y para saber a quien atar cada chat."""
    try:
        return {p.telegram: p.nombre for p in leer() if p.telegram}
    except ConfiguracionInvalida:
        # Una configuracion rota no abre el bot a nadie: se cierra.
        return {}
