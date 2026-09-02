# Ingesta de alertas bancarias a Firefly III

Lee las alertas por correo de **Bancolombia**, saca los movimientos, los mete a
[Firefly III](https://www.firefly-iii.org/), y lo que el clasificador no entiende
lo pregunta por Telegram con botones. Cada respuesta queda aprendida.

Después cruza los extractos mensuales contra lo publicado y cierra cada
movimiento: confirmado, corregido, o fantasma.

Pensado para varias personas y varios buzones (Outlook por Graph, Gmail por IMAP).

> Este repo es **solo código**. Ni credenciales, ni movimientos, ni extractos, ni
> el mapeo de tus tarjetas. Todo eso se monta en tiempo de ejecución.

---

## Por qué existe

Un banco te manda un correo por cada movimiento. Meterlos a mano a Firefly es
tedioso y se acumula. Pero automatizarlo tiene tres trampas que no son obvias, y
resolverlas es casi todo el trabajo:

**1. Las alertas no son fuente de verdad para los montos.** Las apps de
transporte preautorizan el precio estimado y después cobran la tarifa real. En el
periodo que se midió para construir esto, **13 de 30 alertas de viaje nunca se
volvieron un cargo real**. Si se toman las alertas como verdad, el libro se
infla con gastos que no ocurrieron. De ahí la etiqueta `sin-confirmar` y la
conciliación contra extracto.

**2. Los correos rechazados traen monto y comercio.** Una compra que no pasó
manda una alerta que se parece muchísimo a una que sí. Hay que descartarla
explícitamente.

**3. El mapeo tarjeta → cuenta depende de la fecha.** El banco repone plásticos,
y los mismos 4 dígitos pueden ser un producto distinto según cuándo. Un
diccionario fijo se equivoca en todo el histórico.

---

## Cómo funciona

```
   Outlook (Graph/OAuth)      Gmail (IMAP/app password)
            │                          │
            └────────────┬─────────────┘
                         ▼
                 [1] ingesta          → correos_crudos (crudo, nunca se borra)
                         ▼
                 [2] parser           → 13 familias + 8 reglas de descarte
                         ▼
                 [3] clasificador     → cuenta / categoría / presupuesto
                         ▼
                 ┌───────┴────────┐
            confianza alta   confianza baja
                 │                │
                 │          [4] Telegram  ── pregunta y aprende
                 └───────┬────────┘
                         ▼
                    Firefly III   (etiquetado `sin-confirmar`)
                         ▼
                 [5] extracto mensual → confirma / corrige / fantasma
```

Los correos crudos no se borran nunca: cuando el parser mejora, se reprocesa todo
sin volver al buzón.

### Estados

```
nuevo → publicado ──→ confirmado        (el extracto o el usuario confirmaron)
             │    └─→ corregido         (el extracto trajo otro monto)
             │    └─→ fantasma          (nunca ocurrió: se borra de Firefly)
             └─────→ descartado         (no era un movimiento)
```

`estado` y `pregunta` son columnas **separadas** a propósito: un movimiento puede
estar `publicado` en Firefly y con una pregunta abierta al mismo tiempo, y ese es
el caso normal.

---

## Por qué cada buzón usa un mecanismo distinto

Lo intuitivo es unificar. Es la decisión equivocada: cada proveedor cerró el
camino fácil en un lado distinto (verificado a mediados de 2026).

### Outlook.com / Hotmail — OAuth obligatorio

- **App password + IMAP está muerto** desde ~octubre de 2024: responde
  `NO LOGIN`. Microsoft los dejó vivos para SMTP pero los mató para IMAP y POP.
- Basic auth para envío se retiró en marzo de 2026.
- Queda OAuth2: `XOAUTH2` sobre IMAP, o **Microsoft Graph**. Las dos piden el
  mismo registro de app, así que gana Graph: JSON en vez de la máquina de estados
  de IMAP, y consultas `delta`.
- El registro de app es gratis. Permisos delegados `Mail.Read` + `offline_access`,
  tipo de cuenta *Personal Microsoft accounts*, y **Allow public client flows =
  Yes** (sin eso el device code flow falla con un error poco claro).
- Autenticación por **device code flow**: una vez, sin redirect URI ni servidor
  web. Los refresh token de cuenta personal rotan en cada uso y no expiran
  mientras se usen.

### Gmail — el app password sigue siendo el mejor camino

- **App password + IMAP funciona**, con verificación en 2 pasos activada. Lo que
  murió en 2022/2025 fue *Less Secure Apps*, o sea la clave normal de la cuenta.
- **La Gmail API no conviene:** `gmail.readonly` es un scope *restringido* y
  publicar la app exige auditoría de seguridad. Mientras el proyecto esté en
  *Testing*, el refresh token **se vence cada 7 días**.

---

## El parser

`src/finanzas/parsers/bancolombia_alertas.py` — probado contra un archivo de
**863 correos
reales: 100% clasificados, 0 sin reconocer.** Las 71 plantillas distintas del
banco se reducen a 13 familias.

```bash
pytest tests/integracion/test_alertas.py -s
```

| Familia | Qué es |
|---|---|
| `compra_tcred` | compra con tarjeta, plantilla normal |
| `compra_asociada` | compra, plantilla «asociada a T.Cred» |
| `pago_qr` | pago con código QR desde la cuenta |
| `transf_cuenta` · `transf_llave` | transferencia a cuenta o a llave Bre-b |
| `transf_entrada` · `transf_entrada_simple` | transferencia recibida |
| `pago_producto` · `boton_bancolombia` | pago de factura y PSE |
| `ingreso_nomina` · `ingreso_cuenta` | nómina y pagos recibidos |
| `debito_tarjeta` · `avance` | traslados entre productos propios |

### Reglas de descarte

Correos que traen monto y pinta de transacción pero **no son un movimiento**:
`compra_rechazada`, `debito_no_ejecutado`, `factura_inscrita`,
`factura_programada`, `factura_disponible`, `clave_dinamica`, `topes`,
`aviso_extracto`.

### Trampas que maneja

- **Dos formatos de monto mezclados** en el mismo buzón: `178.679,08` a la
  colombiana y `205,967.00` a la gringa. Y `9,000`, que son nueve mil y no nueve.
  La regla: manda el último separador; si va seguido de exactamente dos dígitos es
  decimal, si no todos son de miles.
- **Fecha y hora invertidas** en varias plantillas (`el 07:57 a las 10/12/2025`).
  Se decide por la forma del token, no por la posición.
- Años de dos dígitos y orden `yyyy/mm/dd`.
- Correos que vienen solo en HTML, sin `text/plain`.
- **Traslados internos**: un avance o un débito a tarjeta son *un* transfer en
  Firefly, no dos transacciones.

---

## El clasificador

Aprende del histórico ya clasificado a mano en Firefly. La medición sobre 845
alertas reales:

| | Acierto |
|---|---|
| Solo descripciones | 30,3% |
| Descripciones y comercios mezclados | 53,7% |
| **Universos separados** | **70,5%** |

La clave es que hay **dos fuentes distintas** y mezclarlas las arruina:

- **Nombres de cuentas de gasto e ingreso** (`Grupo Super`, `Farmacia Central`). Esos sí
  son nombres de comercio y se parecen a lo que manda el banco. Se pueden
  emparejar por palabras distintivas.
- **Descripciones escritas a mano** (`ALMUERZO SUPER`). Son notas sobre *qué* fue
  la compra, no el comercio. Solo sirven para coincidencia exacta o por
  subcadena, con confianza baja a propósito.

Mezclados, un `SUPER NORTE 45` del banco competía contra diez patrones con la
palabra SUPER y ninguno era el comercio.

Lo que queda sin resolver se pregunta **una sola vez por comercio**. Algunos son
irresolubles de otra forma: un pago QR trae solo el número de la llave, sin
nombre.

---

## La conciliación

`conciliador.py` lee los PDF de extracto (cifrados, la clave es el documento de
identidad) y cruza contra lo publicado. Medido sobre 830 movimientos y 167
extractos:

| | |
|---|---|
| confirmado | 500 |
| fantasma | 62 |
| ambiguo | 13 |
| corregido | 8 |

Emparejamiento por monto absoluto con tolerancia de fecha creciente (0, 1, 2, 3,
5, 8, 20, 45 días), y gana el más cercano.

**Dos cosas que hay que hacer bien o se hace daño:**

**No corregir montos a la ligera.** La primera versión tomaba «el más parecido
del mismo comercio» cuando no cuadraba el monto exacto. Con varios viajes en
pocos días eso encadena correcciones equivocadas y cambia montos al azar. Ahora
solo corrige si el candidato es **único** y la diferencia es creíble (≤60%); si
no, marca ambiguo y pregunta.

**Cruzar monedas.** Si una tarjeta factura en USD pero las alertas llegan en la
moneda local, ninguna cuadra y *todas* se declaran fantasma. La tasa se
auto-calibra por extracto (mediana de las razones de los pares cuyo comercio
coincide), con una tasa global de respaldo para los extractos sin suficientes
pares propios.

**Nada se borra solo.** El conciliador marca y el bot pregunta, con botones.

---

## Puesta en marcha

```bash
pip install -e .                                  # instala el paquete
cp despliegue/.env.ejemplo .env                   # y llenarlo
cp despliegue/productos.ejemplo.csv productos.csv # y poner tus tarjetas
finanzas revisar                                  # ¿sirven las credenciales?
```

`finanzas revisar` prueba Firefly, Graph, Gmail y Telegram por separado: si una
falla las demás siguen. Para Graph hace el device code flow una vez.

El `.env` y `productos.csv` van en la raíz del repo, no en `despliegue/`: ahí
están los ejemplos, no tus datos. `finanzas config` dice dónde los está
buscando.

```bash
finanzas estado                # qué hay en la cola
finanzas sembrar               # aprender del histórico de Firefly
finanzas bajar                 # traer correo nuevo
finanzas procesar              # parsear y clasificar
finanzas publicar              # SECO por defecto
finanzas publicar --en-serio
finanzas conciliar             # cruzar extractos
finanzas ciclo                 # todo lo anterior
finanzas bot escuchar          # el bot de Telegram
```

### La marca de agua

Importa. Si ya llevabas tu contabilidad a mano, el archivo de correos cubre un
rango que **ya está en Firefly**, y publicarlo entero crea cientos de
duplicados. Por defecto solo se publica lo que llegue desde hoy; se cambia con
`INGESTA_DESDE`.

Además hay dos redes más: `external_id` es un hash del `Message-ID` y se consulta
antes de crear, y hay un anti-duplicado por cuenta + monto + fecha cercana para
el caso de que el movimiento se hubiera registrado a mano.

### Los comandos

Después de `pip install -e .` queda un solo comando, `finanzas`, que sin
argumentos dice qué se puede hacer:

```bash
finanzas                        # el catálogo
finanzas estado                 # cómo va la cola
finanzas ciclo                  # bajar, procesar y publicar — SECO
finanzas ciclo --en-serio       # lo mismo, escribiendo en Firefly
finanzas bot escuchar           # el bot en primer plano
finanzas conciliar --carpeta ../extractos
finanzas revisar firefly telegram    # que las credenciales sirvan
finanzas version                # qué commit está corriendo
```

**Nada escribe en Firefly sin `--en-serio`.** Es lo que hace seguro probar un
comando sin saber bien qué hace.

Dentro del contenedor también sirve, que es la forma rápida de ver qué está
pasando sin abrir la base:

```bash
docker exec -it finanzas-ingesta finanzas estado
docker exec -it finanzas-ingesta finanzas version
```

Cada módulo sigue corriéndose suelto (`finanzas estado`): el comando
solo enruta, no reimplementa nada.

### Despliegue

`despliegue/stack.portainer.yml` es una plantilla. En Portainer, el campo
**Compose path** tiene que decir `despliegue/stack.portainer.yml`. Hay que ajustar el nombre de la red y de
la URL de Firefly a tu instalación; van como variables, no en el código. El
servicio no publica puertos: nadie tiene que entrarle de afuera.

Se monta en tiempo de ejecución: `.env`, `productos.csv`, y un volumen para la
base de la cola y el token de OAuth.

---

## Cómo está organizado

Los diagramas están en [`docs/arquitectura.md`](docs/arquitectura.md): el
recorrido de un movimiento, las capas y quién puede llamar a quién, los
estados de un pendiente, y lo que corre en el contenedor.

**Todo el código es un paquete.** La raíz solo tiene lo que tiene que estar ahí:

```
pyproject.toml   README.md   LICENSE   Dockerfile   .gitignore
src/finanzas/    el paquete
tests/           las pruebas — UNA sola carpeta
herramientas/    diagnóstico y migraciones, se corren a mano
docs/            arquitectura y puesta en marcha
despliegue/      el stack de Portainer y los .ejemplo
```

Cuatro capas, y una regla que verifican las pruebas: **las flechas solo bajan.**

```
src/finanzas/
  config.py         las tres carpetas, y los secretos
  registro.py       el único sitio del paquete donde print() es correcto
  cli.py            el comando `finanzas`: enruta, no reimplementa
  esquema.sql       la definición de la base, como dato del paquete

  dominio/          lógica pura, cero I/O — se prueba sin montar nada
    dinero.py         parsear y formatear plata (Decimal, no float)
    fechas.py         todo en hora de Bogotá; nada de datetime naive
    texto.py          normalizar nombres de comercio
    conciliacion.py   emparejar libro contra extracto

  adaptadores/      el mundo de afuera
    almacen.py        TODO el SQL, un método con nombre por consulta
    db.py             capa delgada sobre el almacén, por compatibilidad
    firefly.py  telegram.py  ia.py  graph.py

  aplicacion/       casos de uso
    clasificador.py   cuenta, categoría y presupuesto
    publicador.py     escribe en Firefly, idempotente por external_id
    conciliador.py    cruza extractos y cierra movimientos
    interprete.py     entiende «fue la comida de la gata en Tierragro»
    asesor.py         responde «¿me alcanza para esto?» con tus números
    presupuestos.py   estado de los presupuestos y categoría → presupuesto
    taxonomia.py      qué es categoría y qué es etiqueta

  entrada/          los puntos de entrada
    servicio.py       el proceso del contenedor: ingesta con horario + bot
    demonio.py        las acciones sueltas
    bot.py            Telegram: pregunta, aprende y responde
    verificar.py      ¿sirven las credenciales?

  parsers/          correo y PDF del banco → movimiento
```

### Las tres carpetas

`config.py` resuelve tres rutas distintas, y las tres se pueden fijar por
entorno. Antes eran dos variables (`AQUI` y `RAIZ`) cargando tres significados,
y `RAIZ` quería decir «la carpeta arriba del código» — que daba lo correcto solo
porque el código estaba justo debajo. `finanzas config` las muestra resueltas.

| | Qué hay ahí | Variable |
|---|---|---|
| `PROYECTO` | el repo: `.env`, `productos.csv` | `FINANZAS_PROYECTO` |
| `PERSONAL` | tus datos, **fuera** del repo: extractos, correos | `FINANZAS_PERSONAL` |
| `DATOS` | el volumen: `finanzas.db`, el token de Graph | `FINANZAS_DATOS` |

### Por qué esta forma

Ocho bugs llegaron a producción. Siete estaban en módulos sin una sola prueba, y
no se podían probar porque la lógica venía enredada con las llamadas de red: para
verificar una regla de conciliación había que tener un Firefly andando. El único
módulo con pruebas de verdad no produjo ninguno.

Sacar la lógica pura a `dominio/` es lo que hizo posible escribirlas. Cada
prueba rara que hay ahí documenta el bug del que salió.

Y mientras la mitad del código eran archivos sueltos en la raíz, cada módulo
remendaba `sys.path` para encontrar a los demás, el `.env` se buscaba «un nivel
arriba de mí», y el resto de los archivos —el esquema, los `.md`, el stack— no
tenían más sitio donde vivir que al lado. Ahora se instala, y una prueba falla
si vuelve a aparecer un `sys.path.insert`.

---

## Correr las pruebas

```bash
pip install -e ".[dev]"

pytest tests                       # 409 pruebas, ~3 segundos
pytest tests --cov=src/finanzas    # cobertura por archivo

ruff check src tests herramientas
ruff format --check src tests
```

Una sola carpeta. Antes había dos (`tests/` y `pruebas/`) y las de `pruebas/`
solo corrían en CI, así que sus guardianes no protegían mientras se editaba —
y eso dejó pasar un `TELEGRAM_BOT_TOKEN` inventado.

`tests/test_arquitectura.py` cuida la estructura, y vale la pena entenderlo:

- **`test_el_sql_solo_vive_en_el_almacen`** — había 69 consultas repartidas en
  siete archivos, varias con la misma lógica escrita distinto. Cambiar el
  esquema obligaba a cazarlas todas y siempre se escapaba una.
- **`test_las_flechas_solo_bajan`** — una capa puede depender de las de abajo,
  nunca de las de arriba.
- **`test_el_dominio_no_sabe_de_sqlite`** — si el dominio importa la base, deja
  de poderse probar sin montar una, y ahí es donde se acumularon los bugs.
- **`test_nadie_crea_tablas_en_tiempo_de_ejecucion`** — `bot.py` creaba tres
  tablas al vuelo con `CREATE TABLE IF NOT EXISTS`, así que `esquema.sql` no era
  la fuente de verdad y las pruebas veían un esquema distinto al de producción.
- **`test_no_quedan_remiendos_de_sys_path`** — cada `sys.path.insert` era el
  síntoma de que los módulos eran archivos sueltos y no un paquete.
- **`test_la_frontera_esta_anotada`** — los cuatro módulos que hablan con el
  mundo son contratos; equivocarse en la forma de lo que devuelven no da error,
  da `None` en silencio.

Ninguna de esas listas se escribe a mano: recorren el paquete. Cuando estaban
escritas a mano quedaron obsoletas el día que los módulos cambiaron de sitio, y
pasaron a verificar archivos que ya no existían.

El CI construye la imagen y comprueba que arranque. Eso también existe por un
motivo: el contenedor llegó a producción sin el paquete `finanzas` instalado.

---

## Estado

Funciona de punta a punta y corre solo: baja correo, parsea, clasifica, publica,
concilia, pregunta lo que no sabe y manda el resumen diario. Un único proceso
(`finanzas servicio`) hace la ingesta con horario y atiende el bot.

- parser: 100% de 863 correos reales
- clasificador: ~70% sin preguntar, o sea unas 28 preguntas al mes
- pruebas: 409

**Cobertura: el núcleo en 92%, el total en 32%.** El CI exige las dos cosas: 90%
en `dominio/` + `almacen.py` + el parser de alertas, y el total como trinquete
que solo puede subir.

Ese 32% no es una caída. Antes decía 93% porque solo se medía el núcleo — el
resto del código vivía fuera de `src/` y no se contaba. Al entrar todo al
paquete, el número por fin mide todo, y dice la verdad: `aplicacion/` y
`entrada/` están entre 7% y 30%. Es lo que sigue.

Falta también el segundo usuario por Gmail.

## Licencia

MIT.
