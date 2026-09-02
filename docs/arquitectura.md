# Arquitectura

Cuatro vistas, de afuera hacia adentro: el recorrido de un movimiento, las capas
y quién puede llamar a quién, los estados por los que pasa un pendiente, y lo que
corre dentro del contenedor.

---

## 1. El recorrido de un movimiento

Desde que el banco manda el correo hasta que el movimiento queda cerrado. Las dos
flechas gruesas son las que llevan plata a Firefly.

```mermaid
flowchart LR
    subgraph afuera["  "]
        BANCO[Bancolombia<br/>alerta por correo]
        PDF[Extracto mensual<br/>PDF]
    end

    BANCO -->|Graph / IMAP| ING[ingesta<br/>guarda el correo crudo]
    ING --> PAR[parser<br/>monto, comercio, tarjeta]
    PAR --> CLA{clasificador<br/>¿sabe qué es?}

    CLA -->|sí, con confianza| PUB[publicador]
    CLA -->|no| COLA[(cola<br/>pregunta abierta)]

    PUB ==>|POST idempotente<br/>por external_id| FF[(Firefly III<br/>etiqueta sin-confirmar)]

    COLA --> BOT[bot de Telegram]
    BOT -->|botones o texto libre| VOS([vos])
    VOS -->|«fue la comida de la gata»| INT[intérprete + Gemini]
    INT --> REGLA[regla aprendida]
    REGLA --> PUB
    REGLA -.->|el mismo comercio<br/>no se vuelve a preguntar| CLA

    PDF --> EXT[parser de extracto]
    EXT --> CON{conciliador<br/>cruza libro vs extracto}
    FF --> CON
    CON ==>|corrige el monto| FF
    CON -->|no apareció en 45 días| FANT[fantasma:<br/>se borra de Firefly]

    style FF fill:#1f4e3d,color:#fff
    style VOS fill:#3d2f4e,color:#fff
    style COLA fill:#4e3d1f,color:#fff
```

**Por qué la cola existe.** Las alertas no son fuente de verdad para los montos:
las apps de transporte preautorizan un estimado y después cobran la tarifa real.
En el periodo que se midió, 13 de 30 alertas de viaje nunca se volvieron un cargo
real. Todo entra de una — etiquetado `sin-confirmar` — y el extracto es el que
manda al final.

---

## 2. Las capas, y quién puede llamar a quién

Cada caja es una carpeta de verdad dentro de `src/finanzas/`, y la regla es una
sola, verificada por `tests/test_arquitectura.py`: **las flechas solo bajan.**
Una capa depende de las de abajo, nunca de las de arriba, y el dominio no puede
importar nada que hable con el mundo.

(Están las llamadas principales, no las 60 aristas del grafo completo:
`config`, `registro` y `taxonomia` los lee casi todo el mundo y no se dibujan
para que el diagrama se pueda leer.)

No hay un solo ciclo de importación: el grafo es un DAG. Por eso los 25 imports
que estaban dentro de funciones «por si hay ciclos» subieron al tope — un import
adentro solo logra que una dependencia faltante explote a las 3am en el
contenedor en vez de al arrancar.

Tampoco queda un solo `sys.path.insert`. Los había en catorce archivos, y eran
el síntoma de que la mitad del código eran archivos sueltos que se buscaban
entre sí a mano en vez de ser un paquete instalado.

```mermaid
flowchart TD
    subgraph E["entrada/ + cli.py"]
        CLI[cli.py<br/>el comando finanzas]
        SERV[servicio.py<br/>el proceso del contenedor]
        DEM[demonio.py<br/>acciones sueltas]
        HERR[herramientas/<br/>diagnostico a mano]
    end

    subgraph A["aplicacion/ · casos de uso"]
        BOT[bot.py]
        CLA[clasificador.py]
        PUBL[publicador.py]
        CONC[conciliador.py]
        INTE[interprete.py]
        ASES[asesor.py]
        PRES[presupuestos.py]
    end

    subgraph AD["adaptadores/ · el mundo de afuera"]
        ALM[almacen.py<br/>TODO el SQL]
        FIRE[firefly.py]
        TELE[telegram.py]
        IA[ia.py · Gemini]
        GRAPH[ingesta/graph.py]
    end

    subgraph P["parsers/ · el formato del banco"]
        ALER[bancolombia_alertas.py]
        EXTR[extracto_tarjeta.py]
    end

    subgraph D["dominio/ · logica pura, cero I/O"]
        DIN[dinero]
        FEC[fechas]
        TEX[texto]
        RECO[conciliacion]
    end

    CLI --> SERV & DEM & BOT & CONC
    SERV --> BOT & CLA & PUBL & CONC
    DEM --> CLA & PUBL & CONC
    HERR --> FIRE
    DEM --> GRAPH

    BOT --> INTE & ASES & PRES & CLA & PUBL
    CLA --> ALM & FIRE
    PUBL --> ALM & FIRE
    CONC --> ALM & FIRE & CLA
    INTE --> IA & ALM & FIRE & PRES
    ASES --> IA & FIRE & PRES & CLA
    PRES --> FIRE
    BOT --> TELE & ALM & IA

    DEM --> ALER
    CONC --> EXTR
    ALER --> DIN & FEC
    EXTR --> DIN & FEC

    CLA --> TEX
    PUBL --> DIN & FEC
    CONC --> RECO & DIN & FEC
    INTE --> TEX

    style D fill:#1f3d4e,color:#fff
    style P fill:#1f3d4e,color:#fff
    style ALM fill:#4e3d1f,color:#fff
```

Del dominio no sale ni una flecha hacia arriba, y eso es lo que se verifica: no
puede importar el almacén, ni Firefly, ni Telegram, ni siquiera `config`.

Lo que **no** puede pasar, y falla el CI si pasa:

| Regla | Qué la rompía antes |
|---|---|
| El dominio no importa `sqlite3`, `db`, `firefly`, `telegram`, `requests`, `config` | La lógica de conciliación llevaba dos años sin una prueba porque hacía falta un Firefly andando para ejecutarla |
| Nadie ejecuta SQL fuera de `almacen.py` | 69 consultas en siete archivos, varias con la misma lógica escrita distinto |
| Nadie crea tablas en tiempo de ejecución | `bot.py` creaba tres con `CREATE TABLE IF NOT EXISTS`, así que `esquema.sql` no era la fuente de verdad |
| Ninguna capa importa de una de arriba | sin esto el grafo se vuelve una maraña y nada se puede probar por separado |
| No queda ningún `sys.path.insert` | catorce archivos remendaban el path para encontrarse entre sí |

---

## 3. Los estados de un pendiente

`estado` y `pregunta` son **columnas independientes**, y eso es a propósito: lo
normal es que un movimiento esté publicado en Firefly *y* con una pregunta
abierta al mismo tiempo.

```mermaid
stateDiagram-v2
    [*] --> nuevo: el parser lo entendió

    nuevo --> publicado: tiene cuenta resuelta
    nuevo --> error: Firefly lo rechazó
    nuevo --> descartado: anterior a la marca de agua<br/>o sin fecha
    error --> publicado: reintento

    publicado --> confirmado: el extracto lo trajo igual
    publicado --> corregido: el extracto trae otro monto<br/>(y el candidato es único)
    publicado --> fantasma: 45 días y ningún extracto<br/>lo trajo → se borra de Firefly

    confirmado --> [*]
    corregido --> [*]
    fantasma --> [*]
    descartado --> [*]

    note right of nuevo
        La marca de agua se aplica AQUÍ, al crear.
        Cuando se aplicaba en el publicador, lo viejo
        se quedaba en «nuevo» para siempre y el bot
        preguntaba por compras de hace meses.
    end note

    note right of corregido
        Solo se corrige el monto cuando hay UN
        candidato. Con varios queda ambiguo y se
        pregunta: corregir en cascada contra el
        candidato equivocado desalinea el resto
        del extracto.
    end note
```

La columna `pregunta` es aparte: `categoria`, `existencia` (¿es fantasma?) o
`monto`, y `NULL` cuando no hay nada que preguntar.

---

## 4. Lo que corre en el contenedor

Un solo proceso. Antes eran dos, y los dos hacían `getUpdates` contra Telegram,
que solo admite un consumidor: HTTP 409 permanente.

```mermaid
flowchart TB
    subgraph C["contenedor finanzas-ingesta"]
        LOOP["finanzas servicio<br/>bucle unico"]
        LOOP --> T1["cada INGESTA_INTERVALO_MIN (15 por defecto):<br/>bajar · parsear · clasificar · publicar"]
        LOOP --> T2["long polling de Telegram<br/>(un solo consumidor)"]
        LOOP --> T3["a la hora de RESUMEN_HORA (21:00):<br/>resumen diario + presupuestos"]
    end

    VOL[("volumen /datos<br/>finanzas.db · token de Graph<br/>FINANZAS_DATOS")]
    LOOP <--> VOL

    T1 -->|HTTPS| MS[Microsoft Graph]
    T1 -->|HTTPS| FF[Firefly III<br/>misma red de Docker]
    T2 -->|HTTPS| TG[API de Telegram]
    LOOP -->|HTTPS| GEM[Gemini]

    GHCR[ghcr.io/mega61/finanzas-ingesta] -.->|pull| C
    CI[GitHub Actions:<br/>pruebas → lint → build → arranque] -.->|push| GHCR

    style C fill:#1f3d4e,color:#fff
    style VOL fill:#4e3d1f,color:#fff
```

La base y el token viven en el volumen, nunca en la imagen. El contenedor corre
con un usuario sin privilegios: lee correo y tiene el token de Firefly.

No monta ningún archivo de configuración: **todo** entra por variables de
entorno, incluido `productos.csv`, que viaja como `PRODUCTOS_CSV` en una sola
línea. Eso es a propósito — un stack de repositorio no puede montar archivos
que el repo no tiene, y los secretos no están en el repo.

`docker exec -it finanzas_ingesta finanzas estado` es la forma rápida de ver qué
está pasando; `finanzas config` dice qué variables llegaron y a qué carpetas
está apuntando.

**Por qué la imagen se construye en el CI y no en el servidor.** Construir en el
servidor depende de que tenga salida a internet, memoria y disco justo en ese
momento, y cuando falla el error sale enterrado en el log de Portainer. El CI,
además, comprueba que la imagen *arranque* — eso atrapó un contenedor que llegó
a producción sin el paquete `finanzas` instalado.
