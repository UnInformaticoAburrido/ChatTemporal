# Cliente del mismo PostgreSQL que Compose, exclusivo de la imagen de pruebas.
FROM postgres:17.9-bookworm@sha256:47f917f7409eacd22fc5dfb1dee634e1b55cf0c01d1a7eb701be2227a03e0641 AS pg-client
RUN mkdir -p /pg-client/bin /pg-client/lib \
    && cp /usr/lib/postgresql/17/bin/pg_dump /usr/lib/postgresql/17/bin/psql /pg-client/bin/ \
    && cp -L "$(ldd /usr/lib/postgresql/17/bin/pg_dump | awk '/libpq.so.5 =>/ {print $3}')" /pg-client/lib/libpq.so.5

# DEC-09: Python 3.13, versión fijada; actualizar mediante CI.
FROM python:3.13.12-slim-bookworm@sha256:a58daefb915e1e03ad48f3ca4df8832065412c5c35cacb9d39f4229184de12b6 AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock
# El código se consume del bind mount ./python:/app; dependencias fuera de /app.
USER 1000:1000
CMD ["python", "-m", "chat.bootstrap", "api"]

FROM runtime AS test
USER root
COPY --from=pg-client /pg-client /opt/pg-client
ENV CHAT_TEST_PG_BIN=/opt/pg-client/bin LD_LIBRARY_PATH=/opt/pg-client/lib
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgssapi-krb5-2 libldap-2.5-0 liblz4-1 libreadline8 libssl3 libzstd1 zlib1g \
    && rm -rf /var/lib/apt/lists/* \
    && /opt/pg-client/bin/pg_dump --version \
    && /opt/pg-client/bin/psql --version
COPY requirements-dev.lock requirements-client.lock /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements-dev.lock
USER 1000:1000
