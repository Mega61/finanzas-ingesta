"""Que es categoria y que es etiqueta. Un solo lugar, para que no se desincronice.

La regla: **categoria = QUE compraste, etiqueta = POR QUE o PARA QUIEN.**

`Viaticos` no dice que compraste, dice que lo pago el trabajo. Un viatico puede
ser un restaurante, un taxi o un hotel. Como categoria esconde el que; como
etiqueta deja ver el que Y el por que al mismo tiempo.

Lo usan tres modulos: el clasificador (para no volver a asignarlas), el
interprete (para no ofrecerlas) y migrar_taxonomia (para convertir el historico
reciente).
"""

# Categorias que en realidad son atributos. No se vuelven a asignar: se pone la
# etiqueta y se pregunta la categoria de verdad.
RETIRADAS = {
    'Viáticos': 'viatico',
    'Viaticos': 'viatico',
    'Gastos de trabajo': 'trabajo',
    'Compras Viaje': 'viaje',
    'Compras': 'compra-suelta',
    'Compras Presentación Personal': 'presentacion-personal',
    'Compras Presentacion Personal': 'presentacion-personal',
    'Regalos': 'regalo',
}

# Fusiones: la de la izquierda ya no se usa, va a la de la derecha.
FUSIONES = {
    'Comida de calle': 'Restaurante',
    'Desayuno': 'Restaurante',
    'Café': 'Mecato',
    'Cafe': 'Mecato',
    'Mecato Gym': 'Mecato',
    'Medicamentos': 'Salud',
    'Homelab': 'Compras Tecnología',
    'Intereses': 'Intereses TC',
    'Intereses Compra de cartera': 'Intereses TC',
    'Reposición TC': 'Cuotas de manejo',
    'TCO': 'Cuotas de manejo',
    'Compras de utileria': 'Compras Casa',
    'Articulos Personales': 'Ropa',
    'Transporte Privado': 'Transporte Aplicación',
}

# Categorias que se quedan aunque tengan poco uso, porque el usuario las
# confirmo: Juegos son juegos de Steam (un solo comercio), y Suplementos es una
# compra recurrente que no es mecato ni salud.
INTOCABLES = {
    'Juegos',
    'Suplementos',
    'Tatuaje',
    'GBS Infra',
    'Salud',
    'Declaración de Renta',
    'Viajes',
}

# Presupuesto que el usuario definio a mano para ciertas categorias.
PRESUPUESTO_FIJO = {
    'Suplementos': 'Vivir',
}


def resolver(categoria):
    """La categoria que se debe usar hoy en lugar de `categoria`.

    Devuelve (categoria_final, etiqueta, hay_que_preguntar).
    """
    if not categoria:
        return None, None, True
    if categoria in RETIRADAS:
        # es un atributo: la etiqueta si aplica, pero la categoria real no se
        # puede deducir del nombre viejo
        return None, RETIRADAS[categoria], True
    if categoria in FUSIONES:
        return FUSIONES[categoria], None, False
    return categoria, None, False


def presupuesto_de(categoria):
    return PRESUPUESTO_FIJO.get(categoria)


def vigentes(categorias):
    """Filtra una lista de categorias dejando solo las que se siguen usando."""
    fuera = set(RETIRADAS) | set(FUSIONES)
    return [c for c in categorias if c not in fuera]
