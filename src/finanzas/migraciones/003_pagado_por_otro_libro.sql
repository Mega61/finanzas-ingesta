-- 003 · Un gasto de un libro pagado con la plata de otro.
--
-- El estudio todavia no se sostiene solo: la duena le paga cosas con su
-- tarjeta Nu, que es personal. Ese gasto es del estudio (va en «Insumos»),
-- pero la plata salio de ella: es un aporte de la duena en especie. Se escribe
-- en los DOS libros, enlazados:
--
--   Actual (estudio)   la compra en su categoria, compensada con un aporte
--                      de la duena. No mueve ninguna cuenta real del estudio.
--   Firefly (personal) el cargo en la Nu, hacia «Golden Beauty Studio»,
--                      como «Aporte al estudio».
--
-- `libro_id` sigue siendo el libro al que pertenece el gasto; esto dice de que
-- libro salio la plata y de que cuenta.

ALTER TABLE pendientes ADD COLUMN pago_libro_id INTEGER REFERENCES libros(id);
ALTER TABLE pendientes ADD COLUMN cuenta_pago TEXT;

-- La plata tampoco puede salir del libro de otra persona.
CREATE TRIGGER pendientes_pago_al_crear
BEFORE INSERT ON pendientes
WHEN NEW.pago_libro_id IS NOT NULL
BEGIN
  SELECT RAISE(ABORT, 'el libro que paga es de otra persona')
  WHERE NEW.usuario_id IS NOT
        (SELECT usuario_id FROM libros WHERE id = NEW.pago_libro_id);
END;

CREATE TRIGGER pendientes_pago_al_actualizar
BEFORE UPDATE OF pago_libro_id, usuario_id ON pendientes
WHEN NEW.pago_libro_id IS NOT NULL
BEGIN
  SELECT RAISE(ABORT, 'el libro que paga es de otra persona')
  WHERE NEW.usuario_id IS NOT
        (SELECT usuario_id FROM libros WHERE id = NEW.pago_libro_id);
END;
