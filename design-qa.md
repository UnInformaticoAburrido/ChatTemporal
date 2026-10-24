# Nexo · revisión visual

Fecha: 2026-10-15. Alcance: adaptar la aplicación existente a los esquemas de
bienvenida, registro, acceso, cuenta y chat facilitados por el usuario.

## Referencia y evidencia

- Fuente visual: `/home/dima/Desktop/Nexo · Identidad y mensajería · UI + especificaciones/`.
  Se leyeron los cinco PDF visuales y `04 · Documentación funcional para IA.pdf`.
- Referencias rasterizadas: `artifacts/nexo/reference/native-1.png` a
  `native-5.png`, 1440 × 900 px; originales PDF de 1440 × 900 puntos.
- Implementación: `http://127.0.0.1:4173/`, Chromium, viewport 1440 × 900 CSS px,
  deviceScaleFactor 1. Móvil: 393 × 852 CSS px, deviceScaleFactor 1.
- Capturas: `artifacts/nexo/implementation/desktop-{welcome,register,login,account,chat}.png`
  y equivalentes `mobile-*`. Datos sintéticos exclusivamente en el script de QA,
  nunca insertados en los assets ni en las cuentas del usuario.
- Comparación conjunta: `comparison-{welcome,register,login,account,chat}.png`,
  cada imagen con referencia a la izquierda e implementación a la derecha,
  2880 × 900 px. Ambas partes tienen tamaño y densidad idénticos.
- Comparaciones de detalle: `focus-welcome.png`, `focus-account.png` y
  `focus-chat.png`; composición con el mismo recorte de referencia e implementación.
  Se revisaron el titular, los códigos/botones y las burbujas/horas.
- Reproducción: `node web/scripts/capture-nexo.mjs`, con el servidor de pruebas
  en 4173. No captura frases ni tokens de cuentas reales.

## Historial de comparación

1. La primera comparación mostró un titular demasiado estrecho, por tamaño y
   tracking, y diferencias de ritmo en formulario y tarjetas. Se ajustó el titular
   a 64 px con tracking de −0,5 px para aproximar las métricas de FigmaGlyphs con
   Inter; se compactó la ayuda del formulario y se ajustaron los códigos a 24 px.
   Las capturas posteriores de bienvenida/registro/cuenta y sus comparaciones
   conjuntas muestran la corrección.
2. El primer panel de chat dejaba burbujas recibidas demasiado estrechas respecto
   al esquema. Se midieron las cajas/textos del PDF, se ajustó el espacio derecho
   en escritorio y el ritmo vertical y se mantuvo padding compacto en móvil.
   `focus-chat.png` es la comprobación posterior. Las horas usan HH:mm.
3. La evidencia de cuenta se tomó una vez antes de finalizar la petición de
   invitaciones. Esa comparación no era del mismo estado y se descartó. El script
   espera ahora la segunda tarjeta antes de capturar. `comparison-account.png`
   contiene ambas tarjetas, con copiar y regenerar visibles.
4. Las pruebas detectaron acceso a cuenta oculto en móvil y un menú que interceptaba
   los botones de votación. Se añadió el acceso de cuenta al encabezado móvil y el
   menú se cierra al elegir una acción. Las capturas móviles y las regresiones
   posteriores confirman ambas correcciones.

## Superficies verificadas

| Superficie | Resultado |
|---|---|
| Tipografía | Inter variable local, jerarquía de titular/modal/perfil/chat, métricas y saltos contrastados con las capturas; fallback de sistema. El PDF usa una fuente vectorial FigmaGlyphs, no una fuente web distribuible. |
| Espaciado y composición | Bienvenida de dos columnas, modales centrados, perfil horizontal, tarjetas de invitación y chat 30/70. En móvil se alterna lista/conversación y los formularios largos desplazan dentro del modal. Sin desbordamiento horizontal en las diez capturas. |
| Colores | Fondo azul noche, paneles pizarra, borde azul, menta en acciones, enviado en verde azulado, avatar rosa y acento superior lila. Estados de error/foco/carga conservados. |
| Imágenes e iconos | Logotipos extraídos de los PDF a resolución 4×, sin aproximación dibujada. Iconos Feather originales con licencia MIT y fuente Inter con OFL, servidos desde el propio proyecto. Los avatares de iniciales son elementos de identidad, como en la referencia. |
| Texto y contenido | Copia principal de Nexo, botones de invitación reales, error/éxito por API y estados de mensajería autoritativos. Sin conversaciones ni credenciales ficticias en producción. |

## Adaptaciones deliberadas al producto existente

- Se mantienen email y **24 palabras** para recuperar las cuentas actuales.
  El PDF propone doce; se consultó al usuario y se conserva el contrato actual
  mientras no indique un cambio. La cuadrícula tiene 24 campos numerados de tipo
  password, permite pegar la frase y desaparece al completar/cerrar el acceso.
- Las invitaciones reales son opacas y más largas que `NX-…`; no se acortan ni se
  sustituyen por muestras. Las tarjetas admiten varias líneas y la copia es íntegra.
- Regenerar invalida ambos códigos, como determina el backend. La confirmación lo
  explica, aunque la acción se ofrezca en cualquiera de las tarjetas.
- La presencia del interlocutor no tiene un contrato público implementado. Se
  muestra el estado real de conexión propia, sin inventar «En línea» del otro.
- El avatar sigue siendo de iniciales. «Editar perfil» guarda nick/email con el
  endpoint existente. La carga persistente de fotografías necesita una función
  adicional de backend y no se representa con un botón que finja guardarlas.
- La lista muestra el último texto descifrado que conserva esta sesión. Donde
  todavía no se consultó historial muestra un estado real de la conversación.
- Las burbujas se ordenan cronológicamente, aunque las horas de muestra del PDF
  no siguen ese orden. Los metadatos incluyen autor/entrega en texto accesible.

Estas diferencias son de contrato y contenido; no se evalúan como una pérdida
de fidelidad de la apariencia solicitada («algo similar»).

## Interacciones y errores

- Bienvenida, registro/login sin recarga, cierre con Escape y retirada de campos.
- Privacidad/cómo funciona, incluido menú móvil; foco visible y modales nativos.
- Registro, verificación, edición de perfil y actualización de la cabecera.
- Claves, invitaciones, canje, renovación, copiar y alternativa manual de clipboard.
- Lista/buscador, selección, navegación móvil, Enter/Shift+Enter y envío vacío.
- Scroll sin saltos al leer arriba, reconexión, votos, transferencia y logout.
- `console.json`: cero errores de consola y cero excepciones en las capturas.
- **68 recorridos Chromium correctos**, 50 pruebas Node correctas y seis recorridos
  E2E con API/WS/PostgreSQL/Redis reales. Después del último ajuste de burbujas:
  otras 12 comprobaciones de chat correctas.

## Pendientes ajenos a la revisión visual

No se ha aplicado el cambio al Docker del usuario ni se ha verificado su Caddy
en ejecución. `sudo make start reload-web` inicia los servicios y recarga las
nuevas rutas/cabeceras. CI, proveedores y homologación de producción mantienen
sus puertas propias. La vista local en 4173 sirve para inspeccionar la interfaz;
el chat conectado se utiliza en el despliegue habitual de la aplicación.

## Checklist final

- [x] Comparación conjunta de las cinco pantallas al mismo tamaño y estado.
- [x] Comparaciones de detalle y corrección de los desajustes encontrados.
- [x] Tipografía, tokens, composición, recursos y contenido revisados.
- [x] Versiones móviles e interacciones principales comprobadas.
- [x] Sin P0/P1/P2 visuales pendientes dentro del alcance de esta adaptación.

## Continuación · navegación (2026-10-20)

Se han añadido rutas públicas de pantalla y enlaces de cabecera/bienvenida,
restauración con Atrás/Adelante y protección de cuenta/chat sin sesión. Se conserva
la adaptación visual existente y las diferencias de contrato documentadas arriba.

Se ha abierto la comparación histórica `comparison-register.png`; representa la
versión del 15 de octubre y no se presenta como evidencia de estos cambios nuevos.
Las cinco superficies visuales descritas arriba conservan su evaluación histórica;
la conversión de botones de navegación a enlaces requiere nueva captura para
confirmar dimensiones, foco y estados en escritorio y móvil.

Validación actual: sintaxis JavaScript correcta, `git diff --check` correcto y
50 pruebas Node correctas usando el Python del entorno virtual del proyecto.
Se ha añadido un recorrido de enlaces directos, historial y limpieza de frase.
Los recorridos de navegador nuevos siguen sin ejecutarse. No hay navegador
integrado disponible; se ha solicitado permiso para Chromium/Playwright conforme
a la guía Product Design. Pendientes la respuesta y la comparación visual nueva.
No se ha desplegado esta continuación al Docker del usuario.

## Continuación · conversaciones (2026-10-22)

Se completa la comprobación que quedó pendiente el 20 de octubre. Se han vuelto
a generar las diez capturas originales mediante `capture-nexo.mjs`, incluidas
las comparaciones conjuntas de las cinco pantallas y los tres recortes de detalle.
Se han abierto las cinco comparaciones actualizadas y las capturas nuevas de
lista, formulario de invitación y mensajes en móvil.

- Fuente: los mismos PDF y `artifacts/nexo/reference/native-{1..5}.png`.
- Comparación actual: `artifacts/nexo/implementation/comparison-*.png`, fuente e
  implementación 1440 × 900, viewport CSS 1440 × 900, densidad 1; conjunto 2880 ×
  900. Los recortes `focus-*.png` se regeneran a la misma escala.
- Nuevas superficies: `artifacts/conversations/desktop-{list,new-conversation,messages}.png`,
  1280 × 720 CSS/píxeles, densidad 1. Móvil: los tres `mobile-*`, viewport 412 ×
  839 CSS, densidad 2,625, capturas 1082 × 2202 píxeles por redondeo. Estas vistas
  nuevas se inspeccionan en su propio viewport, no como comparación a escala 1
  con los PDF de escritorio. La recaptura general móvil usa 393 × 852/densidad 1.

La lista antes de desbloquear y el modal son una ampliación funcional solicitada,
con los mismos tokens, fuente Inter, iconos, contraste y controles del producto.
Se conserva la composición 30/70 del chat abierto. Las diferencias de contrato
(24 palabras, presencia propia, historial previo) siguen siendo deliberadas.
Se revisan tipografía y saltos; espaciado y proporciones; paleta y estados;
calidad del logotipo/iconos; y textos que explican el modo efímero. No se añade
ninguna imagen de demostración a las cuentas reales.

Hallazgo de esta iteración: [P2] el botón nuevo heredaba el color de texto apagado
del antiguo desplegable y un ancho excesivo con sus márgenes. Se eliminan las
reglas del desplegable, se aplica texto oscuro sobre menta y un ancho descontando
los márgenes. La nueva `comparison-chat.png` muestra el contraste y los límites
corregidos; las capturas posteriores conservan el formulario de envío accesible.

Verificación funcional: 38 recorridos distintos de navegador correctos (32 de
cuenta/claves/chat/navegación más seis nuevos de conversaciones); 51 pruebas Node
y 212 pruebas Python unitarias. Los recorridos nuevos comprueban selección guiada,
rechazo de invitaciones con historial, dos páginas que intercambian mensajes
cifrados en ambos sentidos, limpieza al recargar y aislamiento entre dos chats.
`console.json` sigue mostrando cero errores en las diez capturas generales; los
recorridos nuevos no registran excepciones de página ni desbordamiento horizontal.
El relevo entre páginas del ensayo nuevo es sintético. No se ha repetido la suite
con PostgreSQL/Redis reales: el acceso Docker requiere contraseña de sudo.

No quedan P0/P1/P2 de interfaz dentro de este alcance. La actualización al Docker
del usuario sigue pendiente: reiniciar `python worker` y recargar Caddy según
`docs/CLIENTE_WEB.md`. No se han reiniciado ni borrado sus bases de datos.

## Barra persistente y preguntas frecuentes (2026-10-22)

Alcance adicional solicitado: mantener la misma barra superior entre apartados,
crear ayuda pública y contextual y ampliar las explicaciones de las acciones.
La cabecera del chat deja de sustituir la navegación global; es una diferencia
intencional respecto al PDF original, pedida por el usuario en esta iteración.

Evidencia actual: `artifacts/conversations/desktop-persistent-header.png` y
`mobile-persistent-header.png` muestran el chat con toda la navegación;
`desktop-faq-keys.png` y `mobile-faq-keys.png` muestran la respuesta sobre claves.
Los tamaños/densidades de estos recorridos siguen siendo 1280 × 720/densidad 1
y 412 × 839/densidad 2,625. `desktop-faq-overview.png` y
`mobile-faq-overview.png` proceden de la instancia real en 18080, sin sesión,
a 1440 × 900 y 320 × 740 CSS/píxeles, densidad 1. Las comparaciones anteriores
con los PDF documentan el aspecto previo a esta ampliación.

Revisión: la fuente, la paleta y los recursos originales se mantienen. Las
respuestas usan párrafos legibles, listas de pasos y definiciones de los cuatro
conceptos que se confundían. Las preguntas se abren por tema o desde enlaces
contextuales. La información técnica de la clave pública queda después de las
instrucciones y el formulario. El chat descuenta la altura de la cabecera para
mantener visible el compositor. No hay desbordamiento horizontal ni controles
de navegación fuera de la cabecera en las comprobaciones de 320 px.

La ayuda se muestra sin desmontar la pantalla de sesión: se comprueban el mismo
nodo de cabecera, las mismas dimensiones y la conservación de claves, mensajes,
conversación seleccionada, borrador y formulario de apertura de copia. El regreso
al chat sin correo verificado muestra la cuenta y sus instrucciones. Se conserva
la navegación de entrada desde la aplicación principal incluso cuando se abre
su marcador dentro de una página ya cargada.

Verificación: se ejecutan los 82 recorridos de la suite de navegador. Las cuatro
comprobaciones de entrada desde la aplicación principal que detectó la suite
se corrigen y pasan en la repetición dirigida de 24 recorridos; también se repite
la prueba de registro/verificación tras ampliar sus instrucciones. Las pruebas
siguen comprobando que no se exponen tokens en URL, storage ni DOM. La inspección
de la FAQ servida en 18080 registra cero errores de consola y de página. No se
ha cambiado el API ni la base de datos. Los assets están montados y ya se sirven;
las pestañas abiertas necesitan recargar para ejecutar la nueva versión.

final result: passed
