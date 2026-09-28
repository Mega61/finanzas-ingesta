-- claves-foraneas: apagadas
--
-- 002 · Movimientos que no vienen de un correo, la pregunta del destino, y lo
-- que cada persona tiene en cada libro.
--
-- La segunda persona paga con una tarjeta Nu que no manda correos: sus
-- compras entran porque ella las cuenta por Telegram, con una nota de voz o
-- por escrito. Hasta aqui todo movimiento tenia que colgar de un correo
-- (`correo_id NOT NULL`), asi que no habia donde ponerlas.
--
-- SQLite no sabe cambiar un NOT NULL ni un CHECK con ALTER TABLE: la tabla se
-- reconstruye (el procedimiento de 12 pasos de la documentacion de SQLite),
-- con las claves foraneas apagadas mientras dura. El migrador verifica
-- `foreign_key_check` antes de confirmar: si algo quedo colgando, no se
-- confirma nada.
--
-- Las vistas se borran y NO se crean aqui: esquema.sql las vuelve a crear
-- despues de migrar, y asi hay una sola definicion de cada una.

DROP VIEW IF EXISTS v_por_preguntar;
DROP VIEW IF EXISTS v_abiertos;
DROP VIEW IF EXISTS v_sin_conciliar;
DROP VIEW IF EXISTS v_sospechosos;

CREATE TABLE pendientes_nueva (
  id            INTEGER PRIMARY KEY,
  -- NULL cuando el movimiento lo conto la persona por el chat.
  correo_id     INTEGER REFERENCES correos_crudos(id) ON DELETE CASCADE,
  usuario_id    INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,

  -- De donde salio: la alerta del banco, o la persona en el chat.
  origen        TEXT    NOT NULL DEFAULT 'correo' CHECK (origen IN ('correo', 'chat')),
  -- Si vino del chat, el mensaje: 'chat:mensaje'. Es lo que se vuelve a
  -- mostrar, y de lo que sale el external_id.
  referencia    TEXT,

  tipo          TEXT    NOT NULL,
  fecha         TEXT,
  hora          TEXT,
  moneda        TEXT    NOT NULL DEFAULT 'COP',
  valor         REAL    NOT NULL,
  instrumento       TEXT,
  clase_instrumento TEXT CHECK (clase_instrumento IN ('tarjeta', 'cuenta', 'efectivo')),
  traslado_a    TEXT,
  contraparte   TEXT,
  descripcion   TEXT,
  plantilla     TEXT,

  cuenta_firefly TEXT,
  cuenta_destino TEXT,
  categoria      TEXT,
  presupuesto    TEXT,
  etiquetas      TEXT,
  confianza      REAL,
  decidido_por   TEXT,

  estado        TEXT    NOT NULL DEFAULT 'nuevo' CHECK (estado IN (
                  'nuevo', 'publicado', 'confirmado', 'corregido',
                  'fantasma', 'descartado', 'error')),

  -- 'destino' es nueva: a que libro va. Se pregunta ANTES que la categoria,
  -- porque las categorias dependen del libro.
  pregunta      TEXT    CHECK (pregunta IN ('destino', 'categoria', 'existencia', 'monto')),

  external_id   TEXT    UNIQUE,
  -- El id del movimiento EN SU LIBRO. Se sigue llamando firefly_id porque asi
  -- lo lee medio sistema; en un libro de Actual guarda el id de Actual.
  firefly_id    TEXT,

  visto_en          TEXT,
  valor_confirmado  REAL,

  preguntado_en TEXT,
  creado_en     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  actualizado_en TEXT,

  libro_id      INTEGER REFERENCES libros(id),
  destino_por   TEXT CHECK (destino_por IN ('unico', 'usuario')),
  -- Lo que se va a PRESELECCIONAR en la pregunta del destino: la tarjeta, lo
  -- que dijo en el audio. Es una sugerencia y nada mas: no publica.
  sugerido_libro_id INTEGER REFERENCES libros(id),

  -- Un movimiento sin correo tiene que venir del chat, y al reves.
  CHECK ((origen = 'correo') = (correo_id IS NOT NULL))
);

INSERT INTO pendientes_nueva (
  id, correo_id, usuario_id, origen, tipo, fecha, hora, moneda, valor,
  instrumento, clase_instrumento, traslado_a, contraparte, descripcion,
  plantilla, cuenta_firefly, cuenta_destino, categoria, presupuesto, etiquetas,
  confianza, decidido_por, estado, pregunta, external_id, firefly_id, visto_en,
  valor_confirmado, preguntado_en, creado_en, actualizado_en, libro_id,
  destino_por)
SELECT
  id, correo_id, usuario_id, 'correo', tipo, fecha, hora, moneda, valor,
  instrumento, clase_instrumento, traslado_a, contraparte, descripcion,
  plantilla, cuenta_firefly, cuenta_destino, categoria, presupuesto, etiquetas,
  confianza, decidido_por, estado, pregunta, external_id, firefly_id, visto_en,
  valor_confirmado, preguntado_en, creado_en, actualizado_en, libro_id,
  destino_por
FROM pendientes;

DROP TABLE pendientes;
ALTER TABLE pendientes_nueva RENAME TO pendientes;

CREATE INDEX ix_pend_estado ON pendientes (usuario_id, estado);
CREATE INDEX ix_pend_fecha  ON pendientes (fecha);
CREATE INDEX ix_pend_match  ON pendientes (instrumento, fecha, valor);
CREATE INDEX ix_pend_libro  ON pendientes (libro_id, estado);

-- Los triggers de la 001 se fueron con la tabla vieja. Son los mismos.
CREATE TRIGGER pendientes_destino_al_crear
BEFORE INSERT ON pendientes
BEGIN
  SELECT RAISE(ABORT, 'destino sin confirmar: no se publica')
  WHERE (NEW.estado IN ('publicado', 'confirmado', 'corregido', 'fantasma')
         OR NEW.firefly_id IS NOT NULL)
    AND (NEW.libro_id IS NULL OR NEW.destino_por IS NULL);
  SELECT RAISE(ABORT, 'el libro es de otra persona')
  WHERE NEW.libro_id IS NOT NULL
    AND NEW.usuario_id IS NOT
        (SELECT usuario_id FROM libros WHERE id = NEW.libro_id);
END;

CREATE TRIGGER pendientes_destino_al_actualizar
BEFORE UPDATE ON pendientes
BEGIN
  SELECT RAISE(ABORT, 'destino sin confirmar: no se publica')
  WHERE (NEW.estado IN ('publicado', 'confirmado', 'corregido', 'fantasma')
         OR NEW.firefly_id IS NOT NULL)
    AND (NEW.libro_id IS NULL OR NEW.destino_por IS NULL);
  SELECT RAISE(ABORT, 'el libro es de otra persona')
  WHERE NEW.libro_id IS NOT NULL
    AND NEW.usuario_id IS NOT
        (SELECT usuario_id FROM libros WHERE id = NEW.libro_id);
END;


-- ------------------------------------------------------------ instrumentos
-- Que cuenta es cada tarjeta EN CADA LIBRO. La misma cuenta de ahorros es
-- 'Bancolombia Ahorros *5788' en el Firefly personal y 'Bancolombia' en el
-- Actual del estudio: es la misma plata fisica vista desde dos libros.
--
-- Tambien dice a que libros PUEDE ir un movimiento de ese instrumento: solo se
-- ofrecen los libros donde el instrumento tiene cuenta.
--
-- Para Juan esto sigue saliendo de productos.csv; esta tabla es para quien se
-- configura con PERSONAS_TOML.

CREATE TABLE instrumentos (
  id          INTEGER PRIMARY KEY,
  usuario_id  INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
  -- Los 4 digitos que manda el banco, o un nombre para lo que no tiene
  -- digitos: 'nu', 'efectivo'.
  clave       TEXT    NOT NULL,
  clase       TEXT    NOT NULL CHECK (clase IN ('tarjeta', 'cuenta', 'efectivo')),
  -- Como lo nombra la persona al hablar, separado por comas: 'nu, nubank'.
  alias       TEXT,
  libro_id    INTEGER NOT NULL REFERENCES libros(id) ON DELETE CASCADE,
  cuenta      TEXT    NOT NULL,
  desde       TEXT,
  hasta       TEXT,
  UNIQUE (usuario_id, clave, libro_id)
);


-- ---------------------------------------------------------- reglas por libro
-- 'DISTRIBUIDORA BELLEZA' es Insumos en el estudio; en el libro personal esa
-- categoria ni existe. Una regla aprendida vale para el libro donde se
-- aprendio. NULL = las de siempre, de un usuario con un solo libro.

ALTER TABLE reglas ADD COLUMN libro_id INTEGER REFERENCES libros(id);


-- ------------------------------------------------------------------ buzones
-- El secreto de un buzon IMAP vive en el entorno, como el de los libros: aqui
-- va el nombre de la variable.

ALTER TABLE buzones ADD COLUMN secreto_env TEXT;
-- Si de ese buzon se sacan las facturas del supermercado. Son de Juan.
ALTER TABLE buzones ADD COLUMN facturas INTEGER NOT NULL DEFAULT 1;
-- Hasta donde se leyo. En IMAP es el UID del ultimo correo.
ALTER TABLE buzones ADD COLUMN cursor TEXT;


-- ---------------------------------------------------------- tokens en claro
-- `usuarios.firefly_token_enc` decia cifrado y guardaba el token en claro.
-- Desde la 001 el token vive solo en el entorno y la base guarda el NOMBRE de
-- la variable (`libros.secreto_env`). Se vacia.

UPDATE usuarios SET firefly_token_enc = '';
