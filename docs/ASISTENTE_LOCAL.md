# Conectar el asistente local de ayuda

Este documento es para quien instala Nexo. **No se entrega al LLM**.
El único documento de contexto es `docs/GUIA_USUARIO_LLM.md`.

## Puertos y conexión

El servicio de ayuda recibe `POST /help-api/question` en `127.0.0.1:18081`.
El cuerpo es `{"question":"¿Cómo cambio mi nombre?"}` y la respuesta correcta es
`{"answer":"..."}`. Los errores devuelven `{"error":"..."}` con estado 400, 413,
415, 429 o 503. No admite campos para historial, perfil o credenciales.
El puerto se cambia con `NEXO_HELP_PORT`.

La web local usa la misma ruta a través de su puerto habitual (18080 por defecto).
La conexión con el modelo usa la API nativa de Ollama, `POST /api/chat`, sin streaming:
[contrato oficial](https://github.com/ollama/ollama/blob/main/docs/api.md).
No requiere una cuenta ni una clave de OpenAI. No descarga ni instala un modelo.

Configura en el entorno de Compose:

- `NEXO_HELP_MODEL`: nombre exacto de un modelo ya instalado en tu Ollama local.
- `NEXO_HELP_OLLAMA_URL`: dirección local de Ollama, sin `/api/chat` al final.
  Por defecto es `http://host.docker.internal:11434` desde el contenedor.
- `NEXO_HELP_PORT`: puerto de ayuda, por defecto 18081, publicado solo en este equipo.

Por ejemplo, sustituyendo `mi-modelo-local` por el nombre de tu modelo:

```bash
NEXO_HELP_MODEL=mi-modelo-local docker compose -f docker-compose.yml -f docker-compose.local.yml up -d help
make reload-web
```

Ollama debe ser accesible desde el contenedor. Si escucha únicamente en 127.0.0.1
del host, `host.docker.internal` no permite llegar a ese socket. Puedes usar un
Ollama en la red local de Docker, o ejecutar el servicio de ayuda directamente en
el host para probarlo sin modificar la escucha de Ollama:

```bash
cd python
NEXO_HELP_MODEL=mi-modelo-local ../.venv/bin/python -m uvicorn chat.help_assistant:app --host 127.0.0.1 --port 18081 --no-access-log
```

Esta alternativa permite probar el puerto directamente; la ruta web de Compose
apunta al servicio `help` del contenedor, no al proceso del host. No arranques ambas
alternativas en el mismo puerto. Para utilizar el formulario web, usa el servicio
Compose y una dirección de Ollama alcanzable desde ese contenedor.

## Comprobación

```bash
curl http://127.0.0.1:18081/health/live
curl http://127.0.0.1:18081/help-api/question \
  -H 'Content-Type: application/json' \
  -d '{"question":"¿Dónde cambio mi nombre?"}'
```

La primera llamada verifica el servicio, no la disponibilidad del modelo.
La segunda verifica la respuesta real. Con modelo sin configurar, el servicio
responde 503 y la web explica que la ayuda escrita sigue disponible.
Las consultas son independientes y solo se admite una generación a la vez.
Hay un plazo máximo de espera; un modelo lento puede necesitar un nuevo intento.

## Alcance

El servicio no monta secretos, bases de datos ni archivos de conversaciones.
Solo envía la guía pública y el texto que el usuario escribe explícitamente.
No registra preguntas ni respuestas. El comportamiento de registro del servidor
Ollama depende de su propia configuración. La respuesta se muestra como texto,
sin ejecutar HTML. El modelo no dispone de herramientas para realizar acciones.
El catálogo limita su información, pero no garantiza que el modelo nunca se equivoque.

El servicio y la ruta se añaden únicamente a la instalación local. No se habilitan
por defecto en producción. Las pruebas automáticas usan respuestas simuladas;
la validación de un modelo concreto requiere conectarlo y realizar la segunda llamada.
