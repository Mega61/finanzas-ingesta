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

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/automatizacion/
ENV PYTHONPATH=/app/automatizacion

# La base de la cola y el token de Graph viven en un volumen, no en la imagen.
ENV FINANZAS_DATOS=/datos
VOLUME ["/datos"]

# Usuario sin privilegios: este contenedor lee correo y tiene el token de Firefly.
RUN useradd --create-home --uid 10001 finanzas \
 && mkdir -p /datos && chown -R finanzas:finanzas /datos /app
USER finanzas

HEALTHCHECK --interval=5m --timeout=20s --start-period=30s --retries=3 \
  CMD python -c "import os,sqlite3,sys; \
p=os.path.join(os.environ['FINANZAS_DATOS'],'finanzas.db'); \
sys.exit(0) if not os.path.exists(p) else sqlite3.connect(p).execute('select 1')"

CMD ["python", "-m", "automatizacion.demonio"]
