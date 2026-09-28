-- 001 · Los libros, y la regla de que nada se publica sin destino cierto.
--
-- Hasta aqui habia UN destino implicito: el Firefly del .env. Con una segunda
-- persona que lleva dos libros (su Firefly personal y el Actual del estudio)
-- el destino deja de ser obvio, y equivocarse mezcla dos contabilidades. La
-- regla es de cero confianza: un movimiento no llega a NINGUN libro hasta que
-- se sabe con certeza a cual va. Certeza hay en dos casos y solo en dos:
--
--   unico     la persona tiene un solo libro activo
--   usuario   la persona lo eligio con un toque
--
-- Una sugerencia (la tarjeta, el modelo, el historico) puede preseleccionar un
-- boton, nunca publicar. Por eso la regla vive AQUI, en la base, y no solo en
-- el bot: cualquier camino nuevo que se le olvide preguntar choca con esto.


-- ------------------------------------------------------------------ libros

CREATE TABLE libros (
  id           INTEGER PRIMARY KEY,
  usuario_id   INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
  clave        TEXT    NOT NULL,             -- 'personal', 'estudio'
  nombre       TEXT    NOT NULL,             -- lo que se ve en los botones
  tipo         TEXT    NOT NULL CHECK (tipo IN ('firefly', 'actual')),
  url          TEXT    NOT NULL,
  -- El NOMBRE de la variable de entorno con el secreto, nunca el secreto. Los
  -- secretos viven en el entorno del contenedor; la base no los guarda.
  secreto_env  TEXT    NOT NULL,
  -- Lo propio de cada tipo (el sync id de Actual, por ejemplo), como JSON.
  ajustes      TEXT,
  -- Por libro y no por proceso: el de una persona nueva puede andar en seco
  -- mientras el de siempre publica de verdad.
  en_serio     INTEGER NOT NULL DEFAULT 0,
  -- La marca de agua de ESTE libro. NULL = la del proceso (INGESTA_DESDE).
  desde        TEXT,
  activo       INTEGER NOT NULL DEFAULT 1,
  ultimo_error TEXT,
  creado_en    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (usuario_id, clave)
);


-- ----------------------------------------------- el destino de un movimiento

ALTER TABLE pendientes ADD COLUMN libro_id INTEGER REFERENCES libros(id);
ALTER TABLE pendientes ADD COLUMN destino_por TEXT
  CHECK (destino_por IN ('unico', 'usuario'));

CREATE INDEX ix_pend_libro ON pendientes (libro_id, estado);


-- ------------------------------------------------ lo que ya habia, a su libro
-- Cada usuario que existe tenia un solo destino posible: su Firefly. Ese libro
-- se crea con los datos que ya estaban, y todo lo suyo queda asignado a el.
-- Es 'unico' de verdad, no una suposicion: no habia otro libro.

INSERT INTO libros (usuario_id, clave, nombre, tipo, url, secreto_env, en_serio)
SELECT id, 'personal', 'Personal', 'firefly', firefly_url, 'FIREFLY_TOKEN', 1
FROM usuarios;

UPDATE pendientes
SET libro_id = (SELECT l.id FROM libros l
                WHERE l.usuario_id = pendientes.usuario_id AND l.clave = 'personal'),
    destino_por = 'unico';


-- ----------------------------------------------------------------- la regla
-- Dos cosas que no pueden pasar, ni al crear ni al actualizar:
--
--   1. Que un movimiento quede en el libro (publicado, o despues de haberlo
--      estado) sin destino confirmado.
--   2. Que el libro sea de otra persona. Es la mezcla que mas cuesta: la
--      compra de una termina en la contabilidad del otro.

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
