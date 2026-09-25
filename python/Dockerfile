# Cliente PostgreSQL 17.11 sobre glibc para la imagen Python de pruebas.
# El servidor Compose usa la misma versión PostgreSQL sobre Alpine.
FROM postgres:17.11-trixie@sha256:f4c66b820c6f974249089d3d16d86a3698eae11e8746eb6644b2271031e91232 AS pg-client
RUN mkdir -p /pg-client/bin /pg-client/lib \
    && cp /usr/lib/postgresql/17/bin/pg_dump /usr/lib/postgresql/17/bin/psql /pg-client/bin/ \
    && cp -L "$(ldd /usr/lib/postgresql/17/bin/pg_dump | awk '/libpq.so.5 =>/ {print $3}')" /pg-client/lib/libpq.so.5

# DEC-09: Python 3.13, versión fijada; actualizar mediante CI.
FROM python:3.13.15-slim-trixie@sha256:8d9d0b8bcf6506481eae4907c18f5e3e7902e629f5f6d684f9e7c32e85e3ddf0 AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock /tmp/requirements.lock
RUN python -m pip install --no-cache-dir --upgrade pip==26.2.0 \
    && python -m pip install --no-cache-dir -r /tmp/requirements.lock
# El código se consume del bind mount ./python:/app; dependencias fuera de /app.
USER 1000:1000
CMD ["python", "-m", "chat.bootstrap", "api"]

FROM runtime AS test
USER root
COPY --from=pg-client /pg-client /opt/pg-client
ENV CHAT_TEST_PG_BIN=/opt/pg-client/bin LD_LIBRARY_PATH=/opt/pg-client/lib
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgssapi-krb5-2 libldap2 liblz4-1 libreadline8t64 libssl3t64 libzstd1 zlib1g \
    && rm -rf /var/lib/apt/lists/* \
    && /opt/pg-client/bin/pg_dump --version \
    && /opt/pg-client/bin/psql --version
COPY requirements-dev.lock requirements-client.lock /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements-dev.lock
USER 1000:1000
