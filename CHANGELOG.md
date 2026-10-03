# Registro de cambios

Todos los cambios notables en este proyecto se documentarán en este archivo.

El formato está basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.0.0/),
y este proyecto se adhiere a [Semantic Versioning](https://semver.org/lang/es/).

## [1.6.5-alpha] - 2026-10-03

### Añadido
- Pruebas de robustez del parser/stream JPEG: EOI en múltiples posiciones de borde de 64 KiB, segmento que termina exactamente en el límite de bloque, límite operativo a mitad de marcador, lecturas cortas, error de lectura inyectado, cancelación tras el primer bloque. No se encontraron defectos reproducibles en la auditoría; el comportamiento existente se preservó y se cubrió con regresiones.

---

## [1.6.4-alpha] - 2026-10-03

### Añadido
- Pruebas de integración del parser JPEG con JPEGs reales generados en tiempo de ejecución con Pillow del sistema (sin añadir dependencias al proyecto): baseline, progresivo (múltiples escaneos) y truncado sin EOI. Verifican extracción byte-exacta hasta el límite y evidencia `AMBIGUO`/`ESTIMADA` honesta. Se omiten (`skipTest`) si Pillow no está disponible.

---

## [1.6.3-alpha] - 2026-10-03

### Corregido
- **Parser JPEG incremental y conservador**: `_ParserJPEG` reemplaza la búsqueda ciega del primer `FF D9`. Interpreta SOI, EOI, SOS, segmentos con longitud de 2 bytes, datos de imagen tras SOS, byte stuffing (`FF 00`), marcadores de reinicio (`FF D0`-`FF D7`) y múltiples segmentos SOS (JPEG progresivo). Los `FF D9` dentro de segmentos delimitados por longitud y el stuffing ya no se tratan como fin de archivo.
- Estructura inválida o longitudes contradictorias producen resultado `ESTIMADO` honesto con razón explícita; nunca éxito ni completitud.
- Se conserva la extracción incremental con memoria acotada, el tope operativo de 100 MiB, AUD-005, creación exclusiva `"xb"`, limpieza parcial y contrato de evidencia.

### Limitaciones conocidas
- El parser interpreta la estructura de marcadores, pero no decodifica ni valida completamente la imagen; el resultado sigue siendo `AMBIGUO` ante un EOI plausible.

---

## [1.6.2-alpha] - 2026-10-03

### Corregido
- **Extracción JPEG con memoria acotada**: `_extraer_jpeg_stream` reemplaza a `_extraer_jpeg_exacto`. El payload ya no se acumula en un `bytearray` del tamaño del archivo: se lee en ventanas de 64 KiB y se escribe incrementalmente en el destino creado con `"xb"`, reteniendo 1 byte (carry) para detectar un `FF D9` partido entre lecturas.
- Cancelación o error de E/S durante la escritura dejan evidencia `PARCIAL`/`FALLIDO` con `bytes_escritos=0` y eliminan la salida parcial; nunca se reporta éxito ni completitud.
- Fuente vacía para JPEG ahora produce `FALLIDO` en lugar de éxito con 0 bytes.

---

## [1.6.1-alpha] - 2026-10-03

### Corregido
- **Límite JPEG de 8 MiB eliminado**: la extracción JPEG ahora usa el tope operativo global de 100 MiB (`EscaneoProfundo.TAMANO_MAX_ARCHIVO`), por lo que un JPEG legítimo de más de 8 MiB ya no se trunca automáticamente. El límite de 100 MiB sigue siendo operativo, explícito y se reporta en la evidencia cuando se alcanza.

---

## [1.6.0-alpha] - 2026-10-03

### Añadido
- **Contrato de evidencia de recuperación**: `ResultadoRecuperacion` y `EvidenciaRecuperacion` en `core/scanner.py`; cada `ArchivoEncontrado` ahora lleva `evidencia` con tamaño detectado, límite exacto, validación estructural (desconocida por defecto), bytes escritos, resultado y detalle.
- Columna "Resultado" en la tabla de resultados y desglose por clasificación + bytes conservados en el diálogo de recuperación.
- Cabecera de columna "Salud (tamaño)" para evitar confundir "salud" con integridad.

### Corregido
- **Límite JPEG**: `_extraer_jpeg_exacto` ya no puede exceder el máximo de 8 MiB al encontrar el marcador `FF D9` justo tras la ventana de lectura.
- JPEG sin marcador EOI dentro del límite ahora se etiqueta explícitamente como estimación (`tamano_exacto=False`, resultado `Estimado`) y nunca como completo.

### Limitaciones conocidas
- No existe validador estructural real para ningún formato: el resultado "Completo estructuralmente" queda definido pero no se emite todavía.
- El límite de 8 MiB por JPEG sigue siendo operativo y no representa el tamaño original.

---

## [1.5.3-alpha] - 2026-09-29

### Corregido
- **Botón "Pausar" mostraba "Reanudar"**: Al iniciar un nuevo escaneo después de pausar uno anterior, el botón ahora se resetea correctamente a "⏸️ Pausar"

### Añadido
- **Filtro interactivo por tipo de archivo**: Los nodos de categorías en el panel izquierdo ahora son clickeables. Clic en una categoría filtra la lista del lado derecho, clic de nuevo quita el filtro (toggle)

---

## [1.5.2-alpha] - 2026-09-29

### Corregido
- **Archivos corruptos (48 bytes)**: Los archivos recuperados ahora contienen el payload completo extraído del disco
- **Extracción de payload**: Implementada lectura real desde el offset del dispositivo de bloque
- **Modos binarios estrictos**: Lectura 'rb' y escritura 'wb' para integridad de datos

### Añadido
- **Tamaños máximos de carving por tipo**:
  - Imágenes (jpg, png, gif, bmp): 8 MB
  - Documentos (pdf, docx, xlsx, pptx, zip): 15 MB
  - Multimedia (mp3, wav, mp4, avi): 50 MB
  - Sistema (elf, exe): 10 MB
  - Bases de datos (db): 20 MB
  - Default: 10 MB
- **Método `obtener_tamano_maximo(extension)`**: Retorna tamaño máximo según tipo
- **Método `_extraer_payload(archivo)`**: Extrae payload completo desde el dispositivo
- **Método `_extraer_desde_dispositivo()`**: Lectura en bloques de 64KB desde dispositivo raw
- **Método `_extraer_desde_archivo()`**: Lectura desde archivo regular

### Técnica
- **Problema**: El recuperador anterior solo escribía un placeholder de 48 bytes
- **Solución**:
  1. Obtener tamaño máximo según tipo de archivo
  2. Extraer ruta del dispositivo desde `archivo.ruta` (formato: `/dev/sdX#offset=12345`)
  3. Abrir dispositivo en modo binario `'rb'`
  4. `seek(offset)` al sector donde se encontró el header
  5. Leer en bloques de 64KB hasta tamaño máximo o fin del dispositivo
  6. Escribir payload en archivo destino en modo binario `'wb'`
- **Flujo de extracción**:
  ```
  Archivo: foto.jpg (offset: 15335424, dispositivo: /dev/sda1)
      │
      ▼
  obtener_tamano_maximo("jpg") → 8,388,608 bytes (8 MB)
      │
      ▼
  open("/dev/sda1", "rb")
      │
      ▼
  seek(15335424) → Posición del header JPEG
      │
      ▼
  Leer bloques de 64KB hasta 8 MB
      │
      ▼
  write(payload) → foto.jpg con ~8 MB de datos reales
  ```
- **Integridad binaria**: Todos los archivos se abren con modos `'rb'` y `'wb'` estrictos

---

## [1.5.0-alpha] - 2026-09-29

### Añadido
- **ttk.Treeview nativo**: Reemplazo completo de widgets personalizados
- **Sistema de paginación**: 100 elementos por página
- **Persistencia de selección**: Diccionario `self.selecciones`
- **Estilo oscuro personalizado**: ttk.Style con paleta minimalista

### Técnica
- **Rendimiento X11**: Solo se crean máximo 100 items por página
- **Memoria RAM**: Archivos fuera de página no consumen recursos gráficos

---

## [1.4.7-alpha] - 2026-09-29

### Añadido
- **Destrucción asíncrona por lotes**: Lotes de 50 con delay de 5ms
- **Método `_iniciar_actualizacion_filtro()`**: Oculta contenedor y gestiona proceso

---

## [1.4.6-alpha] - 2026-09-29

### Añadido
- **Sistema de reutilización de widgets**: configure() en lugar de recrear

---

## [1.4.5-alpha] - 2026-09-29

### Añadido
- **Carga progresiva por lotes**: Lotes de 50 con delay de 10ms

---

## [1.4.4-alpha] - 2026-09-29

### Corregido
- **TypeError en CTkScrollableFrame**: Eliminada grid_propagate(False) directa

---

## [1.4.3-alpha] - 2026-09-28

### Corregido
- **Crash X11 al filtrar**: Protección anti-colapso

---

## [1.4.2-alpha] - 2026-09-28

### Corregido
- **Crash X11**: Protocolo de 4 pasos para carga segura

---

## [1.4.1-alpha] - 2026-09-28

### Corregido
- **Crash X11 en transiciones**: Uso de self.after()

---

## [1.4.0-alpha] - 2026-09-28

### Añadido
- **Botón "Ver archivos encontrados"**: Parada parcial durante escaneo
- **Barra de filtros avanzada**: Búsqueda, tamaño, salud

---

## [1.3.4-alpha] - 2026-09-28

### Corregido
- **NameError en carving**: Uso de self.ruta_dispositivo

---

## [1.3.3-alpha] - 2026-09-28

### Añadido
- **Resolución de tamaño multiplataforma**: platform.system() + ioctl/seek

---

## [1.3.1-alpha] - 2026-09-28

### Añadido
- **Tiempo restante estimado**: Cálculo en tiempo real

---

## [1.2.1-alpha] - 2026-09-28

### Corregido
- **Resolución de dispositivos**: Función resolver_ruta_dispositivo()

---

## [1.2.0-alpha] - 2026-09-28

### Añadido
- **Motor de escaneo real (File Carving)**: Escaneo profundo por firmas mágicas

---

## [1.1.3-alpha] - 2026-09-28

### Corregido
- **Bug de navegación**: Reescritura del sistema de transición de pantallas

---

## [1.0.0-alpha] - 2026-09-28

### Añadido
- Interfaz gráfica moderna con CustomTkinter en modo oscuro
- Pantalla de selección de unidad o carpeta personalizada
- Algoritmo de escaneo profundo basado en firmas mágicas
- Ejecución del escaneo en segundo plano (threading)
- Pantalla de progreso con controles de pausa/cancelación
- Pantalla de resultados con explorador de doble panel
- Sistema de selección múltiple de archivos con checkboxes
- Indicador de salud de archivos (Buena, Regular, Mala)
- Botón fijo "Recuperar seleccionados" con diálogo de confirmación
