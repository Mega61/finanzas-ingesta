# Puesta en marcha

Cuatro cosas. **Ninguna cuesta plata.** Unos 25 minutos.

```bash
cp despliegue/.env.ejemplo .env
cp despliegue/productos.ejemplo.csv productos.csv
pip install -e .
```

Los dos archivos que acabas de copiar están en `.gitignore`: uno tiene tus
credenciales y el otro los últimos dígitos de tus tarjetas.

Después de cada paso, el verificador te dice si quedó bien:

```bash
finanzas revisar              # todo
finanzas revisar graph      # solo uno
```

---

## Costos

| Qué | Costo | Por qué |
|---|---|---|
| Registro de app en Azure / Entra | **$0** | Entra ID Free incluye registros de app ilimitados, sin límite de tiempo. Registrar una app no se factura. |
| Microsoft Graph API | **$0** | Leer tu propio buzón no tiene costo ni cuota facturable. |
| App password de Gmail + IMAP | **$0** | Función normal de la cuenta. |
| Bot de Telegram | **$0** | La Bot API es gratis. |
| **Total** | **$0** | |

**La letra chica:** la documentación de Microsoft pone como prerrequisito «si no
tienes tenant, crea una cuenta gratuita de Azure». Con una cuenta personal,
entrar a `entra.microsoft.com` normalmente crea solo un *Default Directory* y no
pide nada más. Si te exige crear suscripción y meter tarjeta, no lo hagas: usa el
**Plan B** del final, que evita Azure por completo.

---

## 1 · Firefly III

En Firefly: **Options → Profile → pestaña OAuth → Personal Access Tokens →
Create new token**. El token solo se muestra una vez.

```
FIREFLY_URL=https://firefly.tudominio.com
FIREFLY_TOKEN=eyJ0eXAi...
```

```bash
finanzas revisar firefly
```

## 2 · Outlook / Hotmail — el `client_id`

Es lo único sin alternativa: sin OAuth, Outlook no se puede leer. **No necesitas
client secret**, es una app de cliente público.

1. Entra a **https://entra.microsoft.com** con tu cuenta.
2. **Identity → Applications → App registrations → New registration**.
3. **Name**: el que quieras.
4. **Supported account types**: `Personal Microsoft accounts only`.
   *(Si además necesitas cuentas de organización, elige «Accounts in any
   organizational directory and personal Microsoft accounts» y pon
   `GRAPH_AUTHORITY=common`.)*
5. **Redirect URI**: déjalo vacío.
6. **Register**. En **Overview**, copia el **Application (client) ID**.
7. **Manage → Authentication → Add a platform → Mobile and desktop
   applications** y marca
   `https://login.microsoftonline.com/common/oauth2/nativeclient`.
8. En la misma página, **Advanced settings → Allow public client flows = Yes**.
   **Save**.
9. **Manage → API permissions → Add a permission → Microsoft Graph → Delegated
   permissions** → `Mail.Read` → **Add permissions**. No hace falta consentimiento
   de administrador: en cuenta personal lo das tú en el paso siguiente.

> **El paso que más se olvida es el 8.** Sin él el device code flow falla con un
> error poco claro. El verificador te lo dice explícitamente si pasa.

```
GRAPH_CLIENT_ID=11111111-2222-3333-4444-555555555555
GRAPH_AUTHORITY=consumers
GRAPH_CUENTA=tucorreo@hotmail.com
```

```bash
finanzas revisar graph
```

Te imprime una URL y un código. Abres, escribes el código, aceptas, y guarda el
refresh token. **Esto pasa una sola vez.** Si quedó bien, te dice cuántas alertas
del banco encontró.

## 3 · Telegram — el bot

1. Busca **@BotFather** en Telegram y mándale `/newbot`.
2. Nombre para mostrar, y un username único terminado en `bot`.
3. Copia el token a `TELEGRAM_TOKEN`.
4. Busca tu bot, ábrelo y mándale `/start`. **Es necesario:** un bot no puede
   escribirte primero si nunca le hablaste.

```bash
finanzas revisar telegram
```

Te imprime tu `chat_id`. Cópialo al `.env`, vuelve a correrlo, y te llega un
mensaje de prueba.

Opcional: en BotFather, `/setprivacy` → tu bot → **Enable**.

## 4 · Gmail (segundo usuario) — app password

1. Activa verificación en 2 pasos en la cuenta.
2. Genera un app password en `https://myaccount.google.com/apppasswords`.
   Pégalo sin espacios.
3. Activa IMAP en **Gmail → Ver todos los ajustes → Reenvío y correo POP/IMAP**.

```bash
finanzas revisar gmail
```

---

## 5 · Tus tarjetas

Edita `productos.csv`. Una fila por instrumento, con los **últimos 4 dígitos**
que manda el banco en la alerta y el **nombre exacto** de la cuenta en Firefly.

Las columnas `desde` / `hasta` son las que permiten que un plástico reemplazado
siga resolviendo bien en el histórico: los mismos 4 dígitos pueden ser un
producto distinto según la fecha. Exactamente **una** fila debe tener
`clase=cuenta`: es la cuenta principal, y se usa cuando la alerta dice solo «en
tu cuenta de ahorros» sin dígitos.

Si un instrumento no está en el archivo, el movimiento **se pregunta** en vez de
caer en la cuenta equivocada.

---

## 6 · La marca de agua

Si ya llevabas la contabilidad a mano, el archivo de correos cubre un rango que
**ya está en Firefly**. Publicarlo entero crea cientos de duplicados.

Por defecto solo se publica lo que llegue **desde hoy**. Para cambiarlo:

```
INGESTA_DESDE=2026-09-01
```

Empieza siempre en seco, que es lo que hace por defecto:

```bash
finanzas ciclo              # SECO: no escribe nada
finanzas ciclo --en-serio   # ahora sí
```

---

## Plan B · Si Azure te pide tarjeta

Evita Azure: que Outlook **reenvíe** las alertas del banco a una dirección de
Gmail, y el sistema lee solo por IMAP.

1. Outlook.com: **Settings → Mail → Rules → Add new rule**.
2. Condición: **From** contiene el dominio de las notificaciones del banco.
3. Acción: **Forward to** → un Gmail dedicado.
4. En ese Gmail: 2FA, app password, IMAP activado (paso 4 de arriba).

**Lo que pierdes:** el histórico no se reenvía, solo lo que llegue de ahora en
adelante. Y dependes de que la regla siga viva: si el proveedor la desactiva, la
ingesta se para en silencio. Por eso Graph sigue siendo el plan A.
