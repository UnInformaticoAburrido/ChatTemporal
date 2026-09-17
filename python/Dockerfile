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
COPY requirements-dev.lock requirements-client.lock /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements-dev.lock
USER 1000:1000
