"""El unico lugar donde hay SQL.

Antes habia 40 consultas repartidas en seis archivos, 23 de ellas dentro de
`bot.py`. Con eso, cambiar una columna del esquema obligaba a buscar por todo
el repo, y nada garantizaba que la busqueda fuera completa.

El resto del sistema le habla a este objeto con metodos que dicen QUE se quiere,
no COMO se consulta. Eso tambien es lo que permite, mas adelante, cambiar SQLite
por otra cosa sin tocar la logica: el `Protocol` de abajo es el contrato.

Convenciones:
- Los metodos que leen devuelven filas de sqlite3 (acceso por nombre) o dicts.
- Los que escriben hacen commit, porque cada uno es una operacion completa.
- Ninguno imprime ni lanza excepciones propias: si SQLite falla, sube tal cual.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

CAMPOS_PENDIENTE = (
    'correo_id',
    'usuario_id',
    'tipo',
    'fecha',
    'hora',
    'moneda',
    'valor',
    'instrumento',
    'clase_instrumento',
    'traslado_a',
    'contraparte',
    'descripcion',
    'plantilla',
    'cuenta_firefly',
    'cuenta_destino',
    'categoria',
    'presupuesto',
    'etiquetas',
    'confianza',
    'decidido_por',
    'estado',
    'pregunta',
    'external_id',
)

# Los estados en los que un movimiento todavia esta abierto: no llego a Firefly
# o llego pero falta confirmarlo.
ESTADOS_ABIERTOS = ('nuevo', 'publicado', 'error')


@runtime_checkable
class Repositorio(Protocol):
    """Lo que el resto del sistema necesita de un almacen.

    Es un Protocol y no una clase base a proposito: no obliga a heredar, y una
    implementacion falsa para pruebas solo tiene que tener estos metodos.
    """

    def pendiente(self, pendiente_id: int) -> sqlite3.Row | None: ...
    def pendientes_por_publicar(self, limite: int = 200) -> list[sqlite3.Row]: ...
    def pendientes_por_preguntar(self, limite: int = 50) -> list[sqlite3.Row]: ...
    def actualizar_pendiente(self, pendiente_id: int, **campos: Any) -> None: ...


class Almacen:
    """SQLite. Recibe la conexion ya abierta: no decide donde vive la base."""

    def __init__(self, cx: sqlite3.Connection):
        self.cx = cx

    # ------------------------------------------------------------- esquema

    @classmethod
    def abrir(cls, ruta: str | Path, esquema: str | Path | None = None) -> Almacen:
        cx = sqlite3.connect(str(ruta), timeout=30)
        cx.row_factory = sqlite3.Row
        cx.execute('PRAGMA foreign_keys = ON')
        alm = cls(cx)
        if esquema:
            alm.inicializar(esquema)
        return alm

    def inicializar(self, esquema: str | Path) -> None:
        """Crea el esquema. Es idempotente."""
        self.cx.executescript(Path(esquema).read_text(encoding='utf-8'))
        self.cx.commit()

    def cerrar(self) -> None:
        self.cx.close()

    # ------------------------------------------------------------ usuarios

    def usuario_por_nombre(self, nombre: str) -> sqlite3.Row | None:
        return self.cx.execute(
            'SELECT * FROM usuarios WHERE nombre = ?', (nombre,)
        ).fetchone()

    def usuarios(self) -> list[sqlite3.Row]:
        return self.cx.execute(
            'SELECT id, nombre, telegram_chat_id FROM usuarios ORDER BY id'
        ).fetchall()

    def guardar_usuario(
        self,
        nombre: str,
        firefly_url: str,
        firefly_token: str,
        telegram_chat_id: str | None = None,
    ) -> int:
        fila = self.usuario_por_nombre(nombre)
        if fila:
            self.cx.execute(
                """UPDATE usuarios SET firefly_url = ?, firefly_token_enc = ?,
                   telegram_chat_id = COALESCE(?, telegram_chat_id)
                   WHERE id = ?""",
                (firefly_url, firefly_token, telegram_chat_id, fila['id']),
            )
            self.cx.commit()
            return fila['id']
        cur = self.cx.execute(
            """INSERT INTO usuarios (nombre, firefly_url, firefly_token_enc,
               telegram_chat_id) VALUES (?, ?, ?, ?)""",
            (nombre, firefly_url, firefly_token, telegram_chat_id),
        )
        self.cx.commit()
        return cur.lastrowid

    def vincular_chat(self, chat_id: str) -> int | None:
        """Ata un chat de Telegram al primer usuario que no tenga uno.

        Es lo que pasa en el primer /start: el usuario existe (lo creo el
        arranque desde el .env) pero todavia no sabe a que chat escribirle.
        """
        fila = self.cx.execute(
            'SELECT id FROM usuarios WHERE telegram_chat_id IS NULL ORDER BY id LIMIT 1'
        ).fetchone()
        if not fila:
            return None
        self.cx.execute(
            'UPDATE usuarios SET telegram_chat_id = ? WHERE id = ?',
            (str(chat_id), fila['id']),
        )
        self.cx.commit()
        return fila['id']

    # -------------------------------------------------------------- buzones

    def buzones(self, proveedor: str | None = None) -> list[sqlite3.Row]:
        if proveedor:
            return self.cx.execute(
                'SELECT * FROM buzones WHERE proveedor = ? AND activo = 1', (proveedor,)
            ).fetchall()
        return self.cx.execute('SELECT * FROM buzones WHERE activo = 1').fetchall()

    def primer_buzon(self, usuario_id: int) -> sqlite3.Row | None:
        return self.cx.execute(
            'SELECT * FROM buzones WHERE usuario_id = ? ORDER BY id LIMIT 1',
            (usuario_id,),
        ).fetchone()

    def guardar_buzon(
        self,
        usuario_id: int,
        proveedor: str,
        direccion: str,
        secreto: str | None = None,
        imap_host: str | None = None,
    ) -> int:
        fila = self.cx.execute(
            'SELECT id FROM buzones WHERE usuario_id = ? AND direccion = ?',
            (usuario_id, direccion),
        ).fetchone()
        if fila:
            return fila['id']
        cur = self.cx.execute(
            """INSERT INTO buzones (usuario_id, proveedor, direccion,
               secreto_enc, imap_host) VALUES (?, ?, ?, ?, ?)""",
            (usuario_id, proveedor, direccion, secreto, imap_host),
        )
        self.cx.commit()
        return cur.lastrowid

    def marcar_sync(self, buzon_id: int) -> None:
        self.cx.execute(
            """UPDATE buzones SET ultimo_sync = datetime('now'),
               ultimo_error = NULL WHERE id = ?""",
            (buzon_id,),
        )
        self.cx.commit()

    def marcar_error_buzon(self, buzon_id: int, mensaje: str) -> None:
        self.cx.execute(
            'UPDATE buzones SET ultimo_error = ? WHERE id = ?',
            (str(mensaje)[:500], buzon_id),
        )
        self.cx.commit()

    # --------------------------------------------------------- correo crudo

    def guardar_correo(
        self,
        buzon_id: int,
        message_id: str,
        remitente: str,
        asunto: str,
        fecha_correo: str,
        cuerpo: str,
    ) -> tuple[int, bool]:
        """Devuelve (id, era_nuevo). El UNIQUE(buzon, message_id) es el dedupe."""
        fila = self.cx.execute(
            'SELECT id FROM correos_crudos WHERE buzon_id = ? AND message_id = ?',
            (buzon_id, message_id),
        ).fetchone()
        if fila:
            return fila['id'], False
        cur = self.cx.execute(
            """INSERT INTO correos_crudos
               (buzon_id, message_id, remitente, asunto, fecha_correo, cuerpo)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (buzon_id, message_id, remitente, asunto, fecha_correo, cuerpo),
        )
        self.cx.commit()
        return cur.lastrowid, True

    def correos_sin_procesar(self, limite: int = 500) -> list[sqlite3.Row]:
        return self.cx.execute(
            """SELECT c.*, b.usuario_id FROM correos_crudos c
               JOIN buzones b ON b.id = c.buzon_id
               WHERE c.procesado_en IS NULL
               ORDER BY c.fecha_correo LIMIT ?""",
            (limite,),
        ).fetchall()

    def contar_correos_sin_procesar(self) -> int:
        return self.cx.execute(
            'SELECT count(*) FROM correos_crudos WHERE procesado_en IS NULL'
        ).fetchone()[0]

    def marcar_correo_procesado(self, correo_id: int) -> None:
        self.cx.execute(
            "UPDATE correos_crudos SET procesado_en = datetime('now') WHERE id = ?",
            (correo_id,),
        )

    # ------------------------------------------------------------- la cola

    def crear_pendiente(self, **campos: Any) -> tuple[int, bool]:
        """Devuelve (id, era_nuevo).

        Si el external_id ya existe no inserta y devuelve el id que ya estaba:
        eso es la idempotencia, y es lo que hace seguro reintentar.
        """
        ext = campos.get('external_id')
        if ext:
            fila = self.cx.execute(
                'SELECT id FROM pendientes WHERE external_id = ?', (ext,)
            ).fetchone()
            if fila:
                return fila['id'], False
        usados = [c for c in CAMPOS_PENDIENTE if c in campos]
        marcas = ', '.join('?' for _ in usados)
        cur = self.cx.execute(
            f'INSERT INTO pendientes ({", ".join(usados)}) VALUES ({marcas})',
            [campos[c] for c in usados],
        )
        return cur.lastrowid, True

    def pendiente(self, pendiente_id: int) -> sqlite3.Row | None:
        return self.cx.execute(
            'SELECT * FROM pendientes WHERE id = ?', (pendiente_id,)
        ).fetchone()

    def actualizar_pendiente(self, pendiente_id: int, **campos: Any) -> None:
        if not campos:
            return
        sets = ', '.join(f'{k} = ?' for k in campos)
        self.cx.execute(
            f"UPDATE pendientes SET {sets}, actualizado_en = datetime('now') "
            f'WHERE id = ?',
            [*campos.values(), pendiente_id],
        )

    def pendientes_por_publicar(self, limite: int = 200) -> list[sqlite3.Row]:
        return self.cx.execute(
            """SELECT * FROM pendientes
               WHERE estado IN ('nuevo', 'error') AND cuenta_firefly IS NOT NULL
               ORDER BY fecha, id LIMIT ?""",
            (limite,),
        ).fetchall()

    def pendientes_por_preguntar(self, limite: int = 50) -> list[sqlite3.Row]:
        """Los que toca preguntar AHORA. La vista ya excluye los preguntados
        hace menos de un dia, para no repetir la misma pregunta cada pasada."""
        return self.cx.execute(
            'SELECT * FROM v_por_preguntar LIMIT ?', (limite,)
        ).fetchall()

    def contar_por_preguntar(self) -> int:
        return self.cx.execute('SELECT count(*) FROM v_por_preguntar').fetchone()[0]

    def pendientes_abiertos_de_chat(self, chat_id: str) -> list[sqlite3.Row]:
        marcas = ', '.join('?' for _ in ESTADOS_ABIERTOS)
        return self.cx.execute(
            f"""SELECT p.* FROM pendientes p JOIN usuarios u ON u.id = p.usuario_id
                WHERE p.pregunta IS NOT NULL AND p.estado IN ({marcas})
                  AND u.telegram_chat_id = ?
                ORDER BY p.preguntado_en DESC, p.id DESC""",
            (*ESTADOS_ABIERTOS, str(chat_id)),
        ).fetchall()

    def marcar_preguntado(self, pendiente_id: int) -> None:
        self.cx.execute(
            "UPDATE pendientes SET preguntado_en = datetime('now') WHERE id = ?",
            (pendiente_id,),
        )
        self.cx.commit()

    def pendientes_del_instrumento(
        self,
        instrumento: str,
        desde: str,
        hasta: str,
        estado: str | None = None,
        moneda: str | None = None,
    ) -> list[sqlite3.Row]:
        """Lo que hay en la cola para una tarjeta en un periodo. Es lo que el
        conciliador cruza contra el extracto."""
        sql = [
            'SELECT * FROM pendientes WHERE instrumento = ?',
            'AND fecha BETWEEN ? AND ?',
        ]
        args: list[Any] = [instrumento, desde, hasta]
        if estado:
            sql.append('AND estado = ?')
            args.append(estado)
        if moneda:
            sql.append('AND moneda = ?')
            args.append(moneda)
        sql.append('ORDER BY fecha')
        return self.cx.execute(' '.join(sql), args).fetchall()

    def pendientes_por_estado(self, *estados: str) -> list[sqlite3.Row]:
        """Todo lo que esta en alguno de esos estados. Lo usa la reclasificacion
        para volver a pasar las reglas nuevas sobre lo que sigue abierto."""
        marcas = ', '.join('?' * len(estados))
        return self.cx.execute(
            f'SELECT * FROM pendientes WHERE estado IN ({marcas}) ORDER BY id', estados
        ).fetchall()

    def sin_confirmar(self, limite: int = 20) -> list[sqlite3.Row]:
        return self.cx.execute(
            """SELECT * FROM pendientes
               WHERE estado = 'publicado' AND visto_en IS NULL
               ORDER BY fecha DESC LIMIT ?""",
            (limite,),
        ).fetchall()

    def total_sin_confirmar(self) -> sqlite3.Row:
        return self.cx.execute(
            """SELECT count(*) n, sum(valor) t FROM pendientes
               WHERE estado = 'publicado' AND visto_en IS NULL"""
        ).fetchone()

    def contar_sospechosos(self) -> int:
        """Tarjetas publicadas hace mas de 45 dias que ningun extracto
        confirmo: los candidatos a fantasma."""
        return self.cx.execute('SELECT count(*) FROM v_sospechosos').fetchone()[0]

    def resumen(self, usuario_id: int | None = None) -> list[sqlite3.Row]:
        if usuario_id:
            return self.cx.execute(
                'SELECT * FROM v_sin_conciliar WHERE usuario_id = ?', (usuario_id,)
            ).fetchall()
        return self.cx.execute('SELECT * FROM v_sin_conciliar').fetchall()

    def contar_por_tabla(self, tabla: str) -> int:
        """Solo para el comando de estado. La tabla se valida contra una lista
        blanca: interpolar un nombre de tabla que venga de afuera seria una
        inyeccion."""
        permitidas = {
            'usuarios',
            'buzones',
            'correos_crudos',
            'pendientes',
            'reglas',
            'bitacora',
            'sugerencias',
            'propuestas',
            'preguntas_enviadas',
        }
        if tabla not in permitidas:
            raise ValueError(f'tabla no permitida: {tabla!r}')
        return self.cx.execute(f'SELECT count(*) FROM {tabla}').fetchone()[0]

    # ----------------------------------------------------- limpieza de cola

    def descartar_anteriores_a(self, marca: str) -> int:
        """Saca de la cola lo anterior a la marca de agua.

        La marca se aplica al crear el movimiento; esto arregla las filas que
        quedaron mal de una version anterior, donde se aplicaba al publicar y
        los movimientos sin cuenta resuelta nunca llegaban al filtro.
        """
        n = self.cx.execute(
            """UPDATE pendientes SET estado = 'descartado', pregunta = NULL,
               decidido_por = 'anterior_a_la_marca_de_agua'
               WHERE estado IN ('nuevo', 'error') AND fecha IS NOT NULL
                 AND fecha < ?""",
            (marca,),
        ).rowcount
        self.cx.commit()
        return n

    def descartar_sin_fecha(self) -> int:
        n = self.cx.execute(
            """UPDATE pendientes SET estado = 'descartado', pregunta = NULL,
               decidido_por = 'sin_fecha'
               WHERE estado IN ('nuevo', 'error') AND fecha IS NULL"""
        ).rowcount
        self.cx.commit()
        return n

    # -------------------------------------------------------------- reglas

    def reglas(
        self, usuario_id: int | None = None, solo_texto: bool = True
    ) -> list[sqlite3.Row]:
        sql = ['SELECT * FROM reglas WHERE 1=1']
        args: list[Any] = []
        if usuario_id is not None:
            sql.append('AND (usuario_id = ? OR usuario_id IS NULL)')
            args.append(usuario_id)
        if solo_texto:
            sql.append("AND es_regex = 0 AND patron <> ''")
        return self.cx.execute(' '.join(sql), args).fetchall()

    def guardar_regla(
        self,
        usuario_id: int | None,
        patron: str,
        cuenta_firefly: str | None = None,
        categoria: str | None = None,
        presupuesto: str | None = None,
        etiquetas: str | None = None,
        origen: str = 'usuario',
        direccion: str | None = None,
        aciertos: int | None = None,
    ) -> None:
        self.cx.execute(
            """INSERT INTO reglas (usuario_id, patron, cuenta_firefly, categoria,
                  presupuesto, etiquetas, origen, direccion, aciertos)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, 0))
               ON CONFLICT (usuario_id, patron) DO UPDATE SET
                  cuenta_firefly = COALESCE(excluded.cuenta_firefly, cuenta_firefly),
                  categoria      = COALESCE(excluded.categoria, categoria),
                  presupuesto    = COALESCE(excluded.presupuesto, presupuesto),
                  etiquetas      = COALESCE(excluded.etiquetas, etiquetas),
                  direccion      = COALESCE(excluded.direccion, direccion),
                  aciertos       = MAX(reglas.aciertos, excluded.aciertos)""",
            (
                usuario_id,
                patron,
                cuenta_firefly,
                categoria,
                presupuesto,
                etiquetas,
                origen,
                direccion,
                aciertos,
            ),
        )
        self.cx.commit()

    def borrar_reglas(self) -> int:
        n = self.cx.execute('DELETE FROM reglas').rowcount
        self.cx.commit()
        return n

    def contar_reglas(self) -> int:
        return self.cx.execute('SELECT count(*) FROM reglas').fetchone()[0]

    def reglas_por_origen(self) -> list[sqlite3.Row]:
        return self.cx.execute(
            'SELECT origen, count(*) n FROM reglas GROUP BY origen'
        ).fetchall()

    def categorias_usadas(self, direccion: str) -> list[sqlite3.Row]:
        """Las categorias mas usadas en una direccion, para ofrecerlas primero.

        Filtrar por direccion es lo que evita ofrecerle 'Mecato' a una nomina
        que ENTRA.
        """
        return self.cx.execute(
            """SELECT categoria, count(*) n FROM reglas
               WHERE categoria IS NOT NULL AND categoria <> '' AND direccion = ?
               GROUP BY categoria ORDER BY n DESC""",
            (direccion,),
        ).fetchall()

    def reglas_con_categoria(self, usuario_id: int) -> list[sqlite3.Row]:
        return self.cx.execute(
            """SELECT patron, categoria, presupuesto FROM reglas
               WHERE categoria IS NOT NULL AND categoria <> ''
                 AND (usuario_id = ? OR usuario_id IS NULL)""",
            (usuario_id,),
        ).fetchall()

    # --------------------------------- estado de la conversacion de Telegram

    def guardar_sugerencias(self, pendiente_id: int, opciones: list[str]) -> None:
        self.cx.execute(
            'INSERT OR REPLACE INTO sugerencias (pendiente_id, opciones) VALUES (?, ?)',
            (pendiente_id, '\n'.join(opciones)),
        )
        self.cx.commit()

    def sugerencias(self, pendiente_id: int) -> list[str]:
        r = self.cx.execute(
            'SELECT opciones FROM sugerencias WHERE pendiente_id = ?', (pendiente_id,)
        ).fetchone()
        return r['opciones'].split('\n') if r else []

    def guardar_propuesta(
        self,
        pendiente_id: int,
        categoria: str | None,
        presupuesto: str | None,
        comercio: str | None,
        pedir_presupuesto: bool,
    ) -> None:
        self.cx.execute(
            """INSERT OR REPLACE INTO propuestas
               (pendiente_id, categoria, presupuesto, comercio, pedir_presupuesto)
               VALUES (?, ?, ?, ?, ?)""",
            (
                pendiente_id,
                categoria,
                presupuesto,
                comercio,
                1 if pedir_presupuesto else 0,
            ),
        )
        self.cx.commit()

    def propuesta(self, pendiente_id: int) -> sqlite3.Row | None:
        return self.cx.execute(
            'SELECT * FROM propuestas WHERE pendiente_id = ?', (pendiente_id,)
        ).fetchone()

    def guardar_mensaje(self, chat_id: str, mensaje_id: int, pendiente_id: int) -> None:
        self.cx.execute(
            """INSERT OR REPLACE INTO preguntas_enviadas
               (chat_id, mensaje_id, pendiente_id) VALUES (?, ?, ?)""",
            (str(chat_id), int(mensaje_id), int(pendiente_id)),
        )
        self.cx.commit()

    def pendiente_de_mensaje(self, chat_id: str, mensaje_id: int) -> int | None:
        r = self.cx.execute(
            """SELECT pendiente_id FROM preguntas_enviadas
               WHERE chat_id = ? AND mensaje_id = ?""",
            (str(chat_id), int(mensaje_id)),
        ).fetchone()
        return r['pendiente_id'] if r else None

    # ------------------------------------------------------------ bitacora

    def anotar(
        self,
        accion: str,
        usuario_id: int | None = None,
        pendiente_id: int | None = None,
        firefly_id: str | None = None,
        payload: Any = None,
        respuesta: Any = None,
        ok: bool = True,
    ) -> None:
        """Todo lo que se le escribe a Firefly queda registrado, para poder
        reconstruir que paso y deshacerlo."""
        self.cx.execute(
            """INSERT INTO bitacora (usuario_id, pendiente_id, accion, firefly_id,
                  payload, respuesta, ok) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                usuario_id,
                pendiente_id,
                accion,
                firefly_id,
                str(payload)[:4000] if payload else None,
                str(respuesta)[:4000] if respuesta else None,
                1 if ok else 0,
            ),
        )
        self.cx.commit()
