# Imagen del servicio de ingesta. Pensada para correr al lado de Firefly III.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=America/Bogota

WORKDIR /app

# tzdata: las fechas del banco vienen en hora local, hay que fijar TZ.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Todo el codigo es un paquete y se INSTALA. Antes esto eran dos pasos —
# instalar src/ y ademas copiar los modulos sueltos de la raiz, con un
# PYTHONPATH apuntandoles— porque la mitad del codigo no era paquete. Ya no
# hay modulos sueltos, asi que se instala y se acaba.
#
# Copiar solo lo que define el paquete antes de nada mas aprovecha la cache:
# si no cambian las dependencias, esta capa no se reconstruye.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# El commit con el que se construyo, para que `finanzas version` lo reporte.
# Asi se sabe de una si el contenedor esta corriendo codigo viejo.
ARG GIT_SHA=desconocido
ARG BUILD_FECHA=desconocida
ENV GIT_SHA=$GIT_SHA BUILD_FECHA=$BUILD_FECHA

# Las tres carpetas, explicitas. El paquete instalado vive en site-packages,
# asi que ya no puede deducirlas de su propia ubicacion.
#
#   DATOS      el volumen: la cola, el token de Graph, el offset de Telegram.
#   PERSONAL   apunta al volumen a proposito: aqui no hay extractos, pero si
#              alguna vez se copian ahi, `finanzas conciliar` los encuentra sin
#              pasarle --carpeta.
#   PROYECTO   /app, donde quedo el pyproject. En el contenedor no hay .env ni
#              productos.csv: TODA la configuracion entra por variables de
#              entorno (PRODUCTOS_CSV trae el CSV en una sola linea).
ENV FINANZAS_DATOS=/datos \
    FINANZAS_PERSONAL=/datos \
    FINANZAS_PROYECTO=/app
VOLUME ["/datos"]

# Usuario sin privilegios: este contenedor lee correo y tiene el token de Firefly.
RUN useradd --create-home --uid 10001 finanzas \
 && mkdir -p /datos && chown -R finanzas:finanzas /datos /app
USER finanzas

HEALTHCHECK --interval=5m --timeout=20s --start-period=30s --retries=3 \
  CMD python -c "import os,sqlite3,sys; \
p=os.path.join(os.environ['FINANZAS_DATOS'],'finanzas.db'); \
sys.exit(0) if not os.path.exists(p) else sqlite3.connect(p).execute('select 1')"

# Un solo proceso: ingesta con horario + bot de Telegram. Se entra por el
# comando `finanzas`, que el paquete instala, asi que dentro del contenedor
# tambien vale `docker exec ... finanzas estado`.
CMD ["finanzas", "servicio"]
