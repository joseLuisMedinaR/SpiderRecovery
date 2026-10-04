#!/usr/bin/env python3
"""Generador de ficheros sintéticos para el laboratorio de SpiderRecovery.

Crea ficheros pequeños y deterministas con las cabeceras que el escáner
real de SpiderRecovery reconoce (ver core/scanner.py, FIRMAS_CARVING).

Uso:
    python lab/fixtures/generate_images.py --out <directorio> [--size N] [--dry-run]

Reglas de seguridad:
  * Nunca sobrescribe ficheros existentes.
  * Crea el directorio de salida solo con --out explícito.
  * No escribe en discos físicos, volúmenes externos ni rutas del sistema.
  * Los ficheros son válidos solo en cuanto a su firma de cabecera; NO son
    ficheros reales conformes a estándar.
"""

import argparse
import os
import sys
from pathlib import Path

# Cabeceras soportadas por core/scanner.py (FIRMAS_CARVING)
CABECERAS = {
    "jpg": (b"\xff\xd8\xff\xe0", b"\xff\xd9"),
    "png": (b"\x89PNG\r\n\x1a\n", b"IEND\xaeB`\x82"),
    "gif": (b"GIF89a", b"\x00\x3b"),
    "bmp": (b"BM", None),
    "pdf": (b"%PDF-1.4\n", b"%%EOF"),
    "zip": (b"PK\x03\x04", None),
    "mp3": (b"ID3\x04\x00", None),
    "wav": (b"RIFF", None),
    "mp4": (b"\x00\x00\x00\x18ftypmp42", None),
    "elf": (b"\x7fELF", None),
    "exe": (b"MZ", None),
    "db": (b"SQLite format 3\x00", None),
}

MALFORMADOS = {
    "jpg_truncado": b"\xff\xd8",          # cabecera incompleta
    "png_incompleto": b"\x89PN",          # cabecera incompleta
    "pdf_sin_eofine": b"%PDF-1.4\nsin fin",  # sin marcador de fin
    "aleatorio": bytes(range(64)),        # sin firma conocida
    "vacio": b"",
}


def construir(nombre: str, cabecera: bytes, fin: bytes | None, size: int) -> bytes:
    cuerpo = bytearray()
    cuerpo.extend(cabecera)
    relleno = max(0, size - len(cabecera) - (len(fin) if fin else 0))
    cuerpo.extend(b"\x00" * relleno)
    if fin:
        cuerpo.extend(fin)
    return bytes(cuerpo)


def main() -> int:
    ap = argparse.ArgumentParser(description="Generador de fixtures sintéticos")
    ap.add_argument("--out", required=True, help="Directorio de salida (se crea si no existe)")
    ap.add_argument("--size", type=int, default=64, help="Tamaño del cuerpo para fixtures con firma")
    ap.add_argument("--dry-run", action="store_true", help="Solo muestra lo que haría")
    args = ap.parse_args()

    salida = Path(args.out).resolve()
    if not str(salida).startswith(("/tmp", str(Path.home() / "tmp"), os.getcwd())):
        print(f"[ERROR] Ruta de salida no controlada: {salida}")
        return 2

    if not args.dry_run:
        salida.mkdir(parents=True, exist_ok=True)

    for ext, (cabecera, fin) in CABECERAS.items():
        ruta = salida / f"firma_valida.{ext}"
        if ruta.exists():
            print(f"[OMITIR] Ya existe: {ruta}")
            continue
        datos = construir(ext, cabecera, fin, args.size)
        if args.dry_run:
            print(f"[DRY-RUN] Crearía {ruta} ({len(datos)} bytes)")
        else:
            ruta.write_bytes(datos)
            print(f"Creado: {ruta} ({len(datos)} bytes)")

    for nombre in MALFORMADOS:
        ruta = salida / f"malformado_{nombre}"
        if ruta.exists():
            print(f"[OMITIR] Ya existe: {ruta}")
            continue
        datos = MALFORMADOS[nombre]
        if args.dry_run:
            print(f"[DRY-RUN] Crearía {ruta} ({len(datos)} bytes)")
        else:
            ruta.write_bytes(datos)
            print(f"Creado: {ruta} ({len(datos)} bytes)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
