# Laboratorio local de pruebas — SpiderRecovery

Entorno sintético, reproducible y seguro para inspeccionar el motor de
SpiderRecovery sin tocar dispositivos físicos, sin root y sin GUI.

## Contenido

| Archivo | Responsabilidad |
|---|---|
| `diagnostics/environment.py` | Informa de Python, SO, imports y archivos esperados. No instala nada ni toca dispositivos. |
| `fixtures/generate_images.py` | Genera ficheros pequeños con cabeceras reconocidas por `core/scanner.py` y variantes malformadas. Nunca sobrescribe, soporta `--dry-run` y `--size`. |
| `tests/test_scanner.py` | Pruebas del escaneo en modo carpeta con fixtures temporales (firmas reconocidas, desconocidas, vacías, truncadas, carpeta vacía). |
| `tests/test_recovery.py` | Pruebas de `RecuperadorArchivos`: preservación de bytes, no sobrescritura de preexistentes, origen inexistente, origen vacío, rechazo mismo dispositivo (AUD-005). |

## Comandos (desde la raíz del repositorio)

```bash
# Entorno virtual del proyecto
source venv/bin/activate

# Diagnóstico
python lab/diagnostics/environment.py

# Generar fixtures (no sobrescribe; --dry-run para previsualizar)
python lab/fixtures/generate_images.py --out /tmp/fx_lab --dry-run
python lab/fixtures/generate_images.py --out /tmp/fx_lab

# Pruebas
python lab/tests/test_scanner.py
python lab/tests/test_recovery.py
```

## Resultados esperados

- Diagnóstico: imprime versiones y `OK`/`FALTA` por archivo; exit 0.
- Fixtures: crea `firma_valida.<ext>` y `malformado_<tipo>` en el directorio indicado.
- Tests: `Ran N tests ... OK`; exit 0.

## Cómo interpretar fallos

- `ImportError` de `customtkinter`/`psutil`: faltan dependencias en el venv; no instalar desde este laboratorio sin autorización.
- `FAILED` en tests: leer el traceback; puede indicar un bug real (documentarlo, no tocar el código de producción).
- `ModuleNotFoundError` al ejecutar tests: ejecutar desde la raíz del repositorio.

## Limitaciones de seguridad

- No usar nunca `/dev/*`, discos reales, volúmenes externos ni documentos del usuario.
- Los destinos de recuperación se ubican en `/dev/shm` (tmpfs) cuando se comprueba el rechazo por dispositivo; si no existe, el test usa el directorio temporal por defecto.
- Ningún test requiere root, red ni GUI.

## Limitaciones conocidas del motor (según código)

- El modo carpeta solo clasifica por cabecera de 16 bytes (`core/scanner.py:_escanear_carpeta`); no recupera eliminados.
- El carving real requiere dispositivo de bloque `/dev/*`; no hay soporte de imagen de disco.
- Tamaños estimados por marcador de fin dentro del mismo bloque de 64 KiB (`_determinar_tamano_archivo`).
- `recover.py` extrae hasta el máximo por tipo (8–50 MB), no el tamaño detectado.
- Firmas ambiguas (`RIFF`, `PK\x03\x04`, `BM`, `MZ`, `\xff\xfb`) sin subtipificación ni validación estructural.

## Implementado vs futuro

- **Implementado y verificado en esta fase:** diagnósticos, generador de fixtures, tests de scanner (modo carpeta) y tests de recuperación sintética. Resultados obtenidos el 2026-10-03: 7/7 en scanner, 6/6 en recovery (`OK`), incluida una prueba de integridad JPEG sintética por flujo completo (escaneo → recuperación) con igualdad byte a byte confirmada (sha256 idéntico, sin bytes extra ni duplicados). Nota: la fidelidad byte a byte solo se demostró en modo archivo regular (el extractor lee desde el offset 0 con el tamaño máximo por tipo); NO valida carving sobre dispositivo de bloque.
- **Futuro (no implementado):** carving sobre dispositivo de bloque vía loop device, imágenes sintéticas, pruebas de cancelación y de UI, métricas de rendimiento.
