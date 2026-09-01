# -*- coding: utf-8 -*-
"""El proceso que corre siempre. Es lo que arranca el contenedor.

Junta las dos mitades que antes eran comandos separados:

  hilo de ingesta   cada N minutos: baja correo, parsea, clasifica, publica,
                    y manda por Telegram lo que no supo clasificar
  hilo del bot      long polling de Telegram, atendiendo tus respuestas

Y dos tareas con horario:

  resumen diario    a la hora que digas, te dice que quedo sin conciliar
  conciliacion      una vez al dia, cruza los extractos que hayan llegado

    python servicio.py                 # corre para siempre
    python servicio.py --una-vuelta    # una pasada y sale, para probar

Cada hilo abre su propia conexion a SQLite: no se pueden compartir entre
hilos. La base esta en WAL, asi que leer y escribir a la vez no se bloquea.
"""
import argparse
import os
import signal
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot  # noqa: E402
import config  # noqa: E402
import db  # noqa: E402
import demonio  # noqa: E402
import telegram  # noqa: E402

# Cada cuanto se revisa el correo.
INTERVALO_MIN = int(config.get('INGESTA_INTERVALO_MIN', '15'))
# Hora local del resumen diario. Vacio = no mandar resumen.
HORA_RESUMEN = config.get('RESUMEN_HORA', '21:00')
# Hora de la conciliacion contra extractos, una vez al dia.
HORA_CONCILIAR = config.get('CONCILIAR_HORA', '03:30')
# Si se publica de verdad. Arranca en seco a proposito: hay que decir que si.
EN_SERIO = str(config.get('INGESTA_EN_SERIO', 'no')).lower() in ('1', 'si', 'sí',
                                                                'yes', 'true')

_parar = threading.Event()


def log(donde, msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{ts}] {donde:9} {msg}", flush=True)


def _avisar(texto):
    """Le cuenta al usuario que algo se rompio, si se puede."""
    chat = config.get('TELEGRAM_CHAT_ID_JUAN')
    if not chat:
        return
    try:
        telegram.enviar(chat, texto)
    except Exception:
        pass


# --------------------------------------------------------------- hilo ingesta

def pasada(cx, uid):
    """Una vuelta completa. Devuelve cuantos movimientos nuevos entraron."""
    nuevos = demonio.paso_bajar(cx)
    if nuevos:
        demonio.paso_procesar(cx, uid)
    conteo = demonio.paso_publicar(cx, en_serio=EN_SERIO) or {}
    # lo que no se supo clasificar se pregunta de una
    mandadas = bot.preguntar_pendientes(cx)
    if nuevos or conteo or mandadas:
        log('ingesta', f"correos={nuevos} publicado={conteo.get('creado', 0)} "
                       f"duplicado={conteo.get('duplicado', 0)} "
                       f"seco={conteo.get('seco', 0)} preguntas={mandadas}")
    return nuevos


def hilo_ingesta(uid):
    cx = db.conectar()
    proximo_resumen = _proxima_hora(HORA_RESUMEN)
    proxima_concil = _proxima_hora(HORA_CONCILIAR)
    fallos = 0
    try:
        while not _parar.is_set():
            try:
                pasada(cx, uid)
                fallos = 0
            except Exception as ex:
                fallos += 1
                log('ingesta', f"ERROR: {type(ex).__name__}: {ex}")
                traceback.print_exc()
                # no se avisa en cada fallo, para no volverse spam
                if fallos == 3:
                    _avisar(f"⚠️ La ingesta lleva 3 fallos seguidos:\n"
                            f"<code>{type(ex).__name__}: {str(ex)[:300]}</code>")

            ahora = datetime.now()
            if proximo_resumen and ahora >= proximo_resumen:
                try:
                    chat = config.get('TELEGRAM_CHAT_ID_JUAN')
                    if chat:
                        bot.cmd_resumen(cx, chat)
                        log('resumen', 'mandado')
                except Exception as ex:
                    log('resumen', f"ERROR: {ex}")
                proximo_resumen = _proxima_hora(HORA_RESUMEN)

            if proxima_concil and ahora >= proxima_concil:
                try:
                    import conciliador
                    log('conciliar', 'cruzando extractos...')
                    conciliador.correr(cx, dry_run=not EN_SERIO)
                except Exception as ex:
                    log('conciliar', f"ERROR: {ex}")
                proxima_concil = _proxima_hora(HORA_CONCILIAR)

            _parar.wait(INTERVALO_MIN * 60)
    finally:
        cx.close()


def limpiar_cola(cx):
    """Saca de la cola lo que nunca debio entrar. Es idempotente.

    Existe porque una version anterior aplicaba la marca de agua en el
    publicador, y el publicador solo mira los movimientos que ya tienen cuenta
    resuelta. Los viejos sin cuenta se quedaban en 'nuevo' para siempre, y el
    bot preguntaba por transacciones de hace meses. Ahora la marca se aplica al
    nacer, pero las filas que ya quedaron mal hay que arreglarlas.
    """
    marca = demonio.marca_de_agua()
    viejos = cx.execute(
        """UPDATE pendientes SET estado = 'descartado', pregunta = NULL,
           decidido_por = 'anterior_a_la_marca_de_agua'
           WHERE estado IN ('nuevo', 'error') AND fecha IS NOT NULL
             AND fecha < ?""", (marca,)).rowcount
    sinfecha = cx.execute(
        """UPDATE pendientes SET estado = 'descartado', pregunta = NULL,
           decidido_por = 'sin_fecha'
           WHERE estado IN ('nuevo', 'error') AND fecha IS NULL""").rowcount
    cx.commit()
    if viejos or sinfecha:
        log('limpieza', f"saque de la cola {viejos} anteriores a {marca} "
                        f"y {sinfecha} sin fecha")
    abiertas = cx.execute(
        'SELECT count(*) FROM v_por_preguntar').fetchone()[0]
    log('inicio', f"preguntas abiertas: {abiertas}")


def _proxima_hora(hhmm):
    """El proximo datetime que caiga en esa hora. None si no esta configurada."""
    if not hhmm:
        return None
    try:
        h, m = [int(x) for x in str(hhmm).split(':')[:2]]
    except (ValueError, IndexError):
        return None
    ahora = datetime.now()
    objetivo = ahora.replace(hour=h, minute=m, second=0, microsecond=0)
    if objetivo <= ahora:
        objetivo += timedelta(days=1)
    return objetivo


# ------------------------------------------------------------------ hilo bot

def hilo_bot():
    cx = db.conectar()
    try:
        bot._asegurar_tabla_sug(cx)
        try:
            telegram.poner_comandos()
        except Exception:
            pass
        log('bot', f"escuchando como @{telegram.yo().get('username')}")
        while not _parar.is_set():
            try:
                for u in telegram.updates(espera=25):
                    try:
                        bot.manejar_update(cx, u)
                    except Exception:
                        traceback.print_exc()
            except telegram.TelegramError as ex:
                log('bot', f"telegram: {ex}")
                _parar.wait(10)
            except Exception as ex:
                log('bot', f"ERROR: {type(ex).__name__}: {ex}")
                _parar.wait(10)
    finally:
        cx.close()


# ---------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--una-vuelta', action='store_true',
                    help='una pasada de ingesta y sale, sin bot')
    ap.add_argument('--en-serio', action='store_true',
                    help='publicar de verdad, sin importar el .env')
    a = ap.parse_args(argv)

    global EN_SERIO
    if a.en_serio:
        EN_SERIO = True

    db.inicializar()
    cx = db.conectar()
    uid, _ = demonio.paso_asegurar_usuario(cx)

    n = cx.execute('SELECT count(*) FROM reglas').fetchone()[0]
    if n == 0:
        log('inicio', 'sembrando reglas desde Firefly (primera vez)...')
        demonio.paso_sembrar(cx, uid)

    limpiar_cola(cx)

    log('inicio', f"base={db.ruta()}")
    log('inicio', f"intervalo={INTERVALO_MIN}min  resumen={HORA_RESUMEN or 'no'}  "
                  f"conciliar={HORA_CONCILIAR or 'no'}  "
                  f"publicar={'EN SERIO' if EN_SERIO else 'SECO'}")
    log('inicio', f"marca de agua={demonio.marca_de_agua()}")

    if a.una_vuelta:
        pasada(cx, uid)
        cx.close()
        return 0
    cx.close()

    def apagar(*_):
        log('inicio', 'apagando...')
        _parar.set()
    for s in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(s, apagar)
        except (ValueError, AttributeError):
            pass

    hilos = [
        threading.Thread(target=hilo_ingesta, args=(uid,), name='ingesta',
                         daemon=True),
        threading.Thread(target=hilo_bot, name='bot', daemon=True),
    ]
    for h in hilos:
        h.start()

    try:
        while not _parar.is_set() and any(h.is_alive() for h in hilos):
            time.sleep(1)
    except KeyboardInterrupt:
        _parar.set()
    for h in hilos:
        h.join(timeout=30)
    log('inicio', 'chao')
    return 0


if __name__ == '__main__':
    sys.exit(main())
