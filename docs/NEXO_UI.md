# Adaptación a Nexo

Referencia aportada el 2026-10-15: cinco pantallas y contrato funcional en PDF,
`/home/dima/Desktop/Nexo · Identidad y mensajería · UI + especificaciones/`.

Se adapta la aplicación existente, conservando API y mensajería reales. No se
crea una maqueta independiente ni se sustituyen cuentas por datos de demostración.

## Objetivos

- Paleta azul noche, superficies azul pizarra, acento menta, fuente Inter local.
- Bienvenida, registro e inicio de sesión en modal accesible.
- Cuenta con identidad y dos tarjetas de invitación reales.
- Chat con lista/buscador y panel de conversación; adaptación móvil.
- Envío con Enter, salto con Shift+Enter, scroll que respeta lectura anterior.
- Mantener votos, claves cifradas, transferencia, Push y cierre de sesión.

## Compatibilidad

Los PDF proponen 12 palabras; el backend y las cuentas existentes usan 24. Se ha
consultado al usuario y, mientras no indique el cambio, se conservan 24. El correo
también sigue siendo obligatorio para recuperar la cuenta. Las invitaciones reales
son opacas y más largas que los códigos de muestra: no se acortan ni se inventan.
La regeneración actual invalida ambos códigos y se explica al confirmar.
La API no publica presencia del interlocutor: solo se muestra conexión propia.
El perfil usa su avatar de iniciales; la edición de nick/email usa el endpoint real.

## Verificación

68 recorridos de navegador correctos, 50 pruebas Node y suite de seis recorridos
con API, WebSocket, PostgreSQL y Redis reales. Otras 12 comprobaciones de chat pasan
después del ajuste final de burbujas. Capturas de cinco estados en escritorio y
móvil, sin desbordamiento ni errores de consola. Informe: [design-qa.md](../design-qa.md).

Las fotos personalizadas siguen necesitando una ampliación de backend: se conserva
el avatar por iniciales y la edición real de nick/email.

## Aplicar al Docker local

```bash
sudo make start reload-web
```

Después abrir `http://localhost:18080/` y volver a acceder. La recarga de Caddy es
necesaria para las rutas nuevas de fuente/iconos y las cabeceras CSP; no basta con
reiniciar la API. No hay migración ni dependencias Python/npm nuevas.

## Navegación entre pantallas

- `/#inicio`: bienvenida; el logotipo vuelve aquí cuando no hay sesión.
- `/#registro`: modal de creación de cuenta.
- `/#acceso`: modal de inicio de sesión y recuperación.
- `/#cuenta`: ajustes, perfil, invitaciones, claves y notificaciones.
- `/#chat`: conversaciones, después de verificar el correo y abrir las claves.

Los enlaces de cabecera y la acción de bienvenida tienen una dirección propia.
Registro y acceso se alternan sin recargar. Cerrar el modal o pulsar Escape vuelve
al inicio. Atrás y Adelante restauran la pantalla y limpian los campos de acceso.
Las direcciones de cuenta/chat piden acceso si no existe sesión. El historial
solo guarda nombres de pantallas; nunca palabras, correos ni tokens. La sesión
sigue siendo temporal y una recarga exige identificarse de nuevo.


## Ampliación de conversaciones (2026-10-22)

La sección se puede abrir antes de desbloquear la clave privada y muestra una
lista de conversaciones activas/pendientes. Seleccionar una guía a abrir las
claves y carga el hilo seleccionado. «Nueva conversación» abre el modal del código;
el flujo nuevo exige invitación efímera en el servidor. Los mensajes se cifran
en el navegador y no se guardan como historial remoto en ese modo. Ambos usuarios
necesitan estar conectados; el historial local solo dura mientras la página sigue
abierta. Detalles, límites, pruebas y comandos para aplicar la actualización:
[CLIENTE_WEB.md](CLIENTE_WEB.md#conversaciones-como-apartado-propio).
