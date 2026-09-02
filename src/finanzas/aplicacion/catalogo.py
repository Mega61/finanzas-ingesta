"""Clasifica un producto de supermercado: (nit, codigo) -> tipo, grupo, categoria.

Tres niveles, del que manda al detalle:

  tipo       Consumible o No consumible. LA pregunta del dashboard.
  grupo      Alimentacion, Aseo y hogar, Cuidado personal, Mascotas,
             Comida preparada, Licores, Servicios | Tecnologia,
             Electrodomesticos, Hogar.
  categoria  el detalle dentro del grupo (Carnes, Lacteos, Frutas...).

**Por que el corte de arriba es consumible y no "mercado".** Separar la comida
del jabon no sirve de nada: el jabon, la comida del gato y el papel higienico
se acaban y se vuelven a comprar todos los meses igual que el arroz. Son el
mismo gasto y se comportan igual. Lo que de verdad parte la cuenta en dos es si
el producto **se consume y se repone** o si fue una **compra de una vez** — el
televisor, el reloj, la licuadora. Sin ese corte, un mes con un electrodomestico
parece un mes de hambre.

`Alimentacion` sigue existiendo como grupo para poder abrir el consumible por
dentro, pero no es la linea principal.

Sembrado a mano sobre los 683 productos distintos de 2025-2026: 681
clasificados, 2 sin resolver. Los que queden sin clasificar son la cola del
bot de Telegram, y cada respuesta se guarda como `origen='usuario'`, que
ninguna regla vuelve a pisar.

Tres reglas duras van antes que cualquier palabra, porque el impuesto dice mas
que un nombre truncado a 18 caracteres:

  IVA 8%   impuesto al consumo -> es el patio de comidas
  override el codigo esta en la tabla de excepciones
  IVA 0%   exento -> carne o pescado fresco

EL ORDEN DE LAS REGLAS ES LA MITAD DEL TRABAJO. Un nombre de fruta dentro de
un producto procesado es la trampa principal: "Salsa de Tomate", "Granola
Fresa", "Energizante Sandia" y "Papas Lima Limon" no son frutas ni verduras.
Por eso las formas preparadas (salsa, jugo, bebida, galleta, granola, aceite)
se resuelven ANTES que los ingredientes, y "Frutas y verduras" queda de
ultima, como red de arrastre de lo que quedo sin forma.
"""

from __future__ import annotations

import re
import unicodedata

# --- codigos que ninguna palabra clave acierta -------------------------------
# La razon va al lado porque dentro de un ano nadie se acuerda.
OVERRIDES = {
    # Redenciones de puntos / durables. NO son mercado y no se repiten.
    '3730739': ('Tecnologia', 'Tecnologia'),  # Galaxy Watch 8
    '3577394': ('Tecnologia', 'Tecnologia'),  # Galaxy Buds 3
    '3347573': ('Electrodomesticos', 'Electrodomesticos'),  # Licuadora XL + Pro
    '1223056': ('Electrodomesticos', 'Electrodomesticos'),  # Exprimidor de citricos
    '3211876': ('Hogar', 'Hogar'),  # Set x4 vaso largo
    '3568939': ('Hogar', 'Hogar'),  # Botella Fenice
    '616712': ('Hogar', 'Hogar'),  # Conjunto prof gold
    '876325': ('Hogar', 'Hogar'),  # Frasco premium papel
    '951390': ('Hogar', 'Hogar'),  # Carros basicos
    '522293': ('Hogar', 'Hogar'),  # Papel de regalo
    # Comida de gato de Fancy Feast: viene en ingles y "Chicken" o "Tuna" la
    # mandarian a Carnes o Pescados.
    '3526220': ('Mascotas', 'Gato'),  # Tuna With Scallop
    '3526218': ('Mascotas', 'Gato'),  # Tuna With Salmon
    '3526219': ('Mascotas', 'Gato'),  # Tuna Recipe
    '3526217': ('Mascotas', 'Gato'),  # Chicken Recipe
    '117406': ('Mascotas', 'Perro'),  # Activos Adultos
    '1763324': ('Mascotas', 'Gato'),  # Pala gatos
    '1565219': ('Mascotas', 'Perro'),  # Correa grande
    # "Combo Perro Insuperable" es un perro caliente del patio de comidas.
    '3207312': ('Comida preparada', 'Comida preparada'),
    # "Leche reparadora" es tratamiento de pelo, no lacteo.
    '460453': ('Cuidado personal', 'Cabello'),
    # Cabezales de cepillo de dientes electrico.
    '3059520': ('Cuidado personal', 'Higiene bucal'),
    '3016471': ('Cuidado personal', 'Higiene bucal'),
    '3277373': ('Cuidado personal', 'Salud'),  # Antigripal
    '3228499': ('Cuidado personal', 'Salud'),  # Kola granulada
    '3497832': ('Cuidado personal', 'Salud'),  # Lubricante
    # Bolsas reutilizables de la caja (BRG/BRP).
    '1883151': ('Servicios', 'Bolsas y empaques'),
    '1920871': ('Servicios', 'Bolsas y empaques'),
    # Cortes de res cuyo nombre no dice que son carne.
    '199810': ('Alimentacion', 'Carnes y pollo'),  # TABLA BJ
    '237700': ('Alimentacion', 'Carnes y pollo'),  # ASAR FREIR SI
    '1437031': ('Alimentacion', 'Carnes y pollo'),  # ASAR FREIR TF
    '1173312': ('Alimentacion', 'Pescados y mariscos'),  # Medios filetes de salmon
    '294688': ('Alimentacion', 'Snacks y dulces'),  # Mix Original (frutos secos)
    '157821': ('Alimentacion', 'Abarrotes'),  # Gallina Blanca (caldo)
    '1393': ('Alimentacion', 'Frutas y verduras'),  # Verdura simple
    '3260265': ('Alimentacion', 'Snacks y dulces'),  # Lonja dulce de guayaba
    '3683458': ('Alimentacion', 'Bebidas'),  # Rb Tropical Edition
    '834374': ('Alimentacion', 'Cereales y desayuno'),  # Barras Tosh Yogurt
    '1093589': ('Alimentacion', 'Frutas y verduras'),  # Mango De Azucar (variedad)
    '1219': ('Alimentacion', 'Frutas y verduras'),  # Mango de azucar seleccionado
}
OVERRIDES_SUPERVAQUITA = {
    '2400007': ('Alimentacion', 'Carnes y pollo'),  # TABLA
    '2400013': ('Alimentacion', 'Carnes y pollo'),  # CASCARA PARA FREIR (cerdo)
}
OVERRIDES_D1 = {
    '8699141157005': ('Alimentacion', 'Snacks y dulces'),  # GTA = galleta
}

# Los correos de Exito llegan con mojibake en 159 de 430: la misma palabra
# aparece como "Arandanos" y "Ar?ndanos". Se normaliza ANTES de mirar palabras
# clave; si no, cada acento roto pide su propio override.
MOJIBAKE = str.maketrans(
    {
        '\xdf': 'a',
        '\xbe': 'o',
        '\xdd': 'i',
        '\xb7': 'u',
        '\xd0': 'n',
        '\xb1': 'n',
        '┌': 'u',
        '\xa6': 'i',
        '\xa8': 'o',
    }
)


def normalizar(texto):
    t = texto.translate(MOJIBAKE)
    t = unicodedata.normalize('NFKD', t)
    return ''.join(c for c in t if not unicodedata.combining(c)).lower()


# --- palabras clave, en orden: la primera que casa manda ---------------------
REGLAS = [
    # ---------- lo que no es comida, primero -------------------------------
    # "Alimento H?medo Pa..." se trunca antes de decir "perro": basta con
    # "alimento humedo", que en este catalogo nunca es comida de humanos.
    (
        r'alimento (para |humedo |h.medo )?(gato|gatit|perr)|alimento h.?medo|'
        r'fancy feast|arena.*gato|pala gatos',
        'Mascotas',
        'Gato',
    ),
    (
        r'\b(ron|tequila|vino|whisky|aguardiente|cerveza|coctel|aperitivo|refajo)\b|'
        r'\bcerv|four pack ice|ice green',
        'Licores',
        'Licores',
    ),
    (
        r'crema dental|enjuague|seda dental|flosser|cepillo dental|higiene bucal',
        'Cuidado personal',
        'Higiene bucal',
    ),
    (
        r'shampoo|acondicionador|sh \+ aco|tratamiento rep|tto sachet|reconstru',
        'Cuidado personal',
        'Cabello',
    ),
    (r'desodorante|desod|deo rollon|deos ', 'Cuidado personal', 'Desodorante'),
    (r'esmalte|gel evolution|unas', 'Cuidado personal', 'Otros'),
    (
        r'crema corp|crem corporal|copitos|ruedita.*facial|panitos|'
        r'jabon(?!.*(azul|barra|blanco))',
        'Cuidado personal',
        'Otros',
    ),
    (
        r'bolsa (de )?basur|bolsa apartamento|bolsa apto|bolsa.*resid|bolsa.*indus|'
        r'bolsa pequena',
        'Aseo y hogar',
        'Basura',
    ),
    (
        r'detergente|deter |lavaloza|blanqueador|suavizante|limpiador|limpia ?pisos|'
        r'limpiavidrios|desengrasante|desmanchador|quitamanchas|esponja|'
        r'toallita|toallitas|toalla.*humeda|toallas humeda|toalla.*cocina|'
        r'toalla multiusos|rollos toallas|jabon (azul|blanco)|jabon.*barra|'
        r'papel higienico|cabezal escoba|vinipel|papel aluminio',
        'Aseo y hogar',
        'Limpieza',
    ),
    (
        r'\b(tenedor|cuchara|vaso desechable|pila|pilas)\b',
        'Aseo y hogar',
        'Desechables',
    ),
    (
        r'domicilio|domcilio|preparaci|bolsa papel|bolsa reciclada|bolsa reutilizable',
        'Servicios',
        'Bolsas y empaques',
    ),
    # ---------- proteina y panaderia -------------------------------------
    # Van ANTES de "formas preparadas" por dos casos concretos: "Tortilla de
    # Harina" es pan, no harina, y el atun enlatado dice "Aceite" en el
    # nombre, que lo mandaba a Abarrotes mientras el mismo atun sin la
    # palabra caia en Pescados. Partido en dos, el dashboard mentia.
    # Especias antes que todo lo demas: "PIMIENTA MOLIDA" y "Canela Molida"
    # caian en Carnes por la palabra "molida".
    (
        r'pimienta|canela|oregano|paprika|chapeta|condiment|finas hierbas',
        'Alimentacion',
        'Abarrotes',
    ),
    # Pescados: "Lomo Atun Aceite" empieza por "lomo" y se iba a Carnes.
    (
        r'salmon|trucha|atun|camaron|pescado|tilapia|suprema filetes',
        'Alimentacion',
        'Pescados y mariscos',
    ),
    # Fiambres antes que Carnes: "Jamon De Pavo" y "Jamon Pechuga De Pavo"
    # son embutido, pero "pavo" y "pechuga" los mandaban a Carnes.
    (
        r'jamon|tocineta|cabano|chorizo|salchicha|salchichon|mortadela|serrano',
        'Alimentacion',
        'Fiambres y embutidos',
    ),
    (
        r'pechuga|pollo|contramuslo|corazones pollo|\bpavo\b',
        'Alimentacion',
        'Carnes y pollo',
    ),
    (
        r'molida|\bres\b|solomo|\blomo |lomito de|bife|churrasco|milanesa|cerdo|'
        r'chata|medallones|beef|steak|asar freir|tabla|costilla|tocino\b',
        'Alimentacion',
        'Carnes y pollo',
    ),
    (
        r'\bpan\b|pan (blanco|de mesa|perro|hamburguesa|artesanal|tipo)|baguette|'
        r'buffet|arepa|tortilla|gala tajada|tostada|tartaleta|tarta|'
        r'palito.*(queso|bocadillo)',
        'Alimentacion',
        'Panaderia',
    ),
    # ---------- formas PREPARADAS antes que los ingredientes ---------------
    # Sin este bloque "Salsa de Tomate" cae en frutas y "Granola Fresa" tambien.
    (
        r'\bbebida|gaseosa|\bjugo|zumo|\bte\b|\bte |energizante|hidratante|'
        r'\bagua\b|agua (con|sin|mineral|saboriz)|malta|hit frutas|'
        r'cristal aloe|refresc',
        'Alimentacion',
        'Bebidas',
    ),
    (
        r'granola|cereal|\bavena|barra.*cereal|barras tosh|pancake|hojuela|'
        r'frescavena',
        'Alimentacion',
        'Cereales y desayuno',
    ),
    (
        r'galleta|chocolat|chokis|masmelo|caramelo|goma (de )?mascar|chicle|'
        r'papas (fritas|original|lima)|palomitas|crispetas|\bmani\b|mani confitado|'
        r'almendra natural|pasabocas|maiz soplado|maiz pira|postre|popsy|wafer|'
        r'bocadillo|semilla.*chia|semillas chia|crema de mani|dulces masmelos|'
        r'ponque|panelita',
        'Alimentacion',
        'Snacks y dulces',
    ),
    (
        r'salsa|\baceite|oliva|mayonesa|vinagre|\bpasta\b|penne|fettuccine|macarr|'
        r'\barroz|azucar|harina|panela|\bmiel|caldo|condiment|pimienta|'
        r'oregano|canela|chapeta|sriracha|tajin|pepinillo|(en|de) lata|divella|'
        r'polvo de horneo|bechamel|napolitana|duraznos? en|leche condensada|'
        r'(ajo|cebolla).*(polvo|molido|puro)|sal con ajo|\bsal\b|frasco x|'
        r'\bcafe|liofilizado|paprika|maiz dulce|lata de maiz',
        'Alimentacion',
        'Abarrotes',
    ),
    (r'papas cong|congelad|pizza|lasagna|nuggets', 'Alimentacion', 'Congelados'),
    (
        r'leche|yogurt|kumis|kefir|quesito|queso|moz+arel+a|parmesano|'
        r'mantequilla|matequilla|margarina|esparcible|crema de leche|crema leche|'
        r'crema chantilly|deslactosada|suero coste|huevo|alpin|arequipe',
        'Alimentacion',
        'Lacteos y huevos',
    ),
    # ---------- red de arrastre: ingredientes frescos ----------------------
    (
        r'banano|uva|naranja|mandarina|limon|sandia|kiwi|coco|mango|fresa|'
        r'arandano|uchuva|pitahaya|aguacate|tomate|cebolla|papa\b|papa criolla|'
        r'papa capira|zanahoria|brocoli|espinaca|apio|champi|pimenton|platano|'
        r'berenjena|pepino|\bajo\b|ajo malla|cilantro|perejil|yerbabuena|'
        r'albahaca|romero|esparrago|lulo|verdura|maiz tierno|mix vegetales|'
        r'\bpulpa|mora\b|maracuya',
        'Alimentacion',
        'Frutas y verduras',
    ),
]
COMPILADAS = [(re.compile(p, re.I), g, c) for p, g, c in REGLAS]


# Los tres grupos que NO se reponen. Todo lo demas se acaba y se vuelve a
# comprar, y por eso cuenta como consumible: el jabon y el arroz se comportan
# igual en un presupuesto, el televisor no.
NO_CONSUMIBLES = frozenset({'Tecnologia', 'Electrodomesticos', 'Hogar'})

# Grupos validos, para que el bot no invente uno nuevo al responder.
GRUPOS_CONSUMIBLE = (
    'Alimentacion',
    'Aseo y hogar',
    'Cuidado personal',
    'Mascotas',
    'Comida preparada',
    'Licores',
    'Servicios',
)
GRUPOS = GRUPOS_CONSUMIBLE + tuple(sorted(NO_CONSUMIBLES))

# Las categorias que se le ofrecen al usuario por grupo. El bot las usa para
# armar los botones, asi que el orden es el orden en que aparecen.
CATEGORIAS = {
    'Alimentacion': (
        'Frutas y verduras',
        'Carnes y pollo',
        'Pescados y mariscos',
        'Fiambres y embutidos',
        'Lacteos y huevos',
        'Panaderia',
        'Abarrotes',
        'Cereales y desayuno',
        'Snacks y dulces',
        'Bebidas',
        'Congelados',
    ),
    'Aseo y hogar': ('Limpieza', 'Basura', 'Desechables'),
    'Cuidado personal': (
        'Higiene bucal',
        'Cabello',
        'Desodorante',
        'Salud',
        'Otros',
    ),
    'Mascotas': ('Gato', 'Perro'),
    'Comida preparada': ('Comida preparada',),
    'Licores': ('Licores',),
    'Servicios': ('Bolsas y empaques',),
    'Tecnologia': ('Tecnologia',),
    'Electrodomesticos': ('Electrodomesticos',),
    'Hogar': ('Hogar',),
}


def tipo_de(grupo: str) -> str:
    """Consumible o no. Es el corte de arriba del dashboard."""
    if grupo in NO_CONSUMIBLES:
        return 'No consumible'
    if grupo == 'Sin clasificar':
        return 'Sin clasificar'
    return 'Consumible'


def clasificar(nit, codigo, desc, iva):
    """Devuelve (tipo, grupo, categoria, origen)."""
    grupo, categoria, origen = _grupo_y_categoria(nit, codigo, normalizar(desc), iva)
    return tipo_de(grupo), grupo, categoria, origen


def _grupo_y_categoria(nit, codigo, desc, iva):
    # 1. reglas duras por impuesto
    if iva == 8.0:
        return 'Comida preparada', 'Comida preparada', 'iva8'
    # 2. overrides explicitos
    tabla = OVERRIDES
    if nit == '900522508':
        tabla = OVERRIDES_SUPERVAQUITA
    elif nit == '900276962':
        tabla = OVERRIDES_D1
    if codigo in tabla:
        g, c = tabla[codigo]
        return g, c, 'override'
    # 3. IVA 0% = carne / pescado exento. Va DESPUES de los overrides porque
    #    hay exentos que no son carne, y ANTES de las palabras porque un corte
    #    llamado "TABLA" no lo caza ninguna regla de texto.
    if iva == 0.0:
        for rx, g, c in COMPILADAS:
            if rx.search(desc) and c in ('Carnes y pollo', 'Pescados y mariscos'):
                return g, c, 'iva0'
        return 'Alimentacion', 'Carnes y pollo', 'iva0'
    # 4. palabras clave
    for rx, g, c in COMPILADAS:
        if rx.search(desc):
            return g, c, 'palabra'
    return 'Sin clasificar', 'Sin clasificar', 'ninguna'
