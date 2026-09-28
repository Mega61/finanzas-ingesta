# Otra persona, con varios libros

Cómo se suma alguien más al bot: con su propio Firefly, y opcionalmente el
libro de su negocio en Actual Budget. Es el caso para el que se construyó:
una cuenta de ahorros que recibe a la vez el salario de la persona y los pagos
de las clientas de su negocio.

## La regla

**Nada llega a ningún libro hasta que la persona dice a cuál va.** Si tiene un
solo libro, el destino es obvio. Si tiene varios, el bot pregunta siempre, sin
excepción. La tarjeta, lo que dijo en un audio y lo que eligió la vez pasada
solo **preseleccionan** un botón (✓); nunca publican.

La regla vive en tres sitios, para que un camino nuevo que se olvide de
preguntar choque con alguno:

| Dónde | Qué hace |
|---|---|
| la base (`migraciones/001`, `002`) | un trigger rechaza marcar publicado un movimiento sin destino confirmado, o con el libro de otra persona |
| la cola (`pendientes_por_publicar`) | no ofrece lo que no tiene destino |
| el publicador | publica libro por libro, cada uno con **su** credencial, y se niega si el libro no es el del movimiento |

## El recorrido

```mermaid
flowchart LR
    A[alerta de Bancolombia<br/>por su Gmail] --> P
    V([nota de voz o texto<br/>«45 mil de esmaltes con la Nu»]) --> G[Gemini entiende<br/>monto, medio, fecha]
    G --> P[pendiente<br/>sin libro]
    P --> M{¿con qué pagó?}
    M --> D{¿a qué libro va?}
    D -->|estudio, y entra plata| AG{¿es una venta<br/>de Agendapro?}
    AG -->|sí, ya está| E[se enlaza:<br/>no se escribe nada]
    AG -->|todavía no aparece| ESP[espera y se<br/>enlaza sola]
    AG -->|no| C
    D --> C{¿qué categoría?<br/>la de ESE libro}
    C --> PAR{¿hay uno<br/>muy parecido?}
    PAR -->|se pregunta| C2[el mismo, o son dos]
    PAR -->|no| PUB[(se publica en<br/>ese libro)]

    style P fill:#4e3d1f,color:#fff
    style PUB fill:#1f4e3d,color:#fff
```

- **Solo se ofrecen los libros donde el medio de pago tiene cuenta.** La
  tarjeta del estudio no tiene cuenta en el Firefly personal: ahí ni aparece.
- **Agendapro ya escribe las ventas.** El CRM sube cada noche los pagos de las
  clientas a Actual. Una transferencia que es una de esas ventas se **enlaza**;
  escribirla otra vez la contaría doble.
- **Un parecido no se descarta solo.** En un salón es normal comprar dos veces
  lo mismo el mismo día.
- **Cada respuesta se aprende para ese libro.** «Insumos» en el estudio no
  existe en el libro personal.

## Configurarla

Todo va en **un** lugar: `personas.toml` en la raíz del repo (está en
`.gitignore`). Ver `despliegue/personas.ejemplo.toml`. Los **secretos no van
ahí**: cada libro y buzón dice el *nombre* de la variable de entorno que lo
tiene, y un secreto pegado se rechaza.

```bash
finanzas revisar personas     # ¿contesta cada libro y cada buzón?
```

Verifica también que cada cuenta nombrada en `instrumento.cuentas` exista de
verdad en su libro.

### Actual Budget

Actual no tiene API REST. El stack trae el servicio `actual-api`
([jhonderson/actual-http-api](https://github.com/jhonderson/actual-http-api)):
la librería oficial de Node detrás de HTTP. Habla con el servidor de Actual por
su URL pública, así que en la VM de Actual no se abre nada.

- **`NODE_ENV=production` no se quita:** sin eso el puente no exige su llave.
- **La versión va fija y en sincronía** con el servidor de Actual y con el CRM
  (`automation/actual-sync`). El servidor corre con `:latest`: si se actualiza
  solo, el cliente puede dejar de entenderlo. Fija el servidor también.
- Se crea con `addTransactions`, no con `import`: `import` empareja por monto
  y fecha y fundiría el movimiento con una venta o con uno anotado a mano.

## Desplegar

1. `python herramientas/generar_variables.py` — el bloque para Portainer, con
   `PERSONAS_JSON` en una línea y `ACTUAL_API_URL` apuntando al servicio.
2. En Portainer: pegar las variables y redesplegar. Arranca el puente de Actual
   junto a la ingesta.
3. **Su chat de Telegram.** Que le escriba al bot. El log dice
   `mensaje ignorado, chat no autorizado: <id>`. Ese número va en
   `TELEGRAM_CHAT_ID_NOVIA`; redesplegar.
4. **En seco primero.** Los libros arrancan con `en_serio = false`: el bot
   pregunta todo y contesta «🧪 En prueba: lo guardaría en…», sin escribir.
   Cuando se vea bien, `en_serio = true` en `personas.toml`, regenerar y
   redesplegar. Lo que ya contestó en seco se publica en la siguiente pasada.

## El corte (cutover)

El saldo inicial se fija con los **extractos bancarios**, no a mano. Hasta el
corte, los saldos de sus libros no se usan para nada. Al hacerlo: el saldo de
la cuenta de ahorros al corte, la deuda de cada tarjeta, y el aporte histórico
de la dueña que cuadra Actual con el banco.

## Pagado con la plata del otro libro

El estudio todavía no se sostiene solo: la dueña le paga cosas con su Nu. Y a
veces la tarjeta del estudio paga algo suyo. Ninguna de las dos es un error, y
las dos se ofrecen como **botones aparte** en la pregunta del destino, nunca
preseleccionados salvo que ella lo haya dicho:

| Botón | Qué es | Qué se escribe |
|---|---|---|
| 💅 Golden Beauty (lo pagué yo) | un aporte de la dueña en especie | **Actual**: una transacción dividida de monto 0 en «Aportes en especie»: +X «Aportes de la dueña» y −X en la categoría del gasto. Ninguna cuenta real del estudio se mueve. **Firefly**: el cargo en la Nu hacia «Golden Beauty Studio», como «Aporte al estudio». |
| 📒 Personal (lo pagó Golden Beauty) | un pago a la dueña en especie | **Actual**: el gasto en la tarjeta del estudio como «Salario Dueña». En Firefly no se movió plata suya. |

Se escribe primero en Actual y después en Firefly, cada uno buscado por su id
externo antes de escribir: si el segundo falla, reintentar no duplica el
primero. Los nombres de cuentas y categorías se pueden cambiar en `ajustes` de
cada libro (ver `ruteo.DEFECTOS`).

## Lo que todavía no hace

- **«Le pasé plata al estudio» / «me pagué del estudio»** sin movimiento en el
  banco (la plata está en la misma cuenta). Es el mismo par, disparado por una
  frase en vez de una compra.
- Editar desde el chat un movimiento ya guardado en Actual.
- El asesor («¿me alcanza para…?») y los productos del súper son de Juan.
