-- 004 · Prestamos entre personas, y categorias que la persona crea.
--
-- Prestamos. El jefe le pide a ella que le pase plata a una cuenta, y a los
-- dias se la devuelve. No es un gasto ni un ingreso: si se anotara asi, el mes
-- saldria con un gasto que no fue y un ingreso que no fue. Y si no se anota,
-- el extracto trae dos movimientos que no estan en ningun libro. Se anota como
-- un traslado a una cuenta «Préstamos» del libro, y el saldo de esa cuenta es
-- lo que le deben (positivo) o lo que ella debe (negativo).
--
-- `prestamo_con` es con quien: el saldo por persona sale de sumar sus
-- movimientos. Solo es un prestamo cuando ademas tiene `cuenta_destino`; sin
-- ella es la sugerencia de lo que dijo en el chat, esperando que lo confirme.
ALTER TABLE pendientes ADD COLUMN prestamo_con TEXT;

-- Categorias nuevas. En Firefly basta con el nombre: la crea al publicar. En
-- Actual toda categoria vive en un grupo; este es el grupo que ella eligio,
-- y la categoria se crea en el mismo momento que el movimiento. Asi un libro
-- en seco no se toca, ni siquiera para agregarle una categoria.
ALTER TABLE pendientes ADD COLUMN categoria_grupo TEXT;

-- Que se le pregunto en cada mensaje. Un mismo movimiento puede tener abierto
-- «escribe la categoria» y «escribe con quien fue el prestamo», y la respuesta
-- por texto tiene que caer en la pregunta que se contesto.
ALTER TABLE preguntas_enviadas ADD COLUMN tipo TEXT NOT NULL DEFAULT 'categoria';
