#!/usr/bin/env python3
"""Diagnóstico del entorno del laboratorio de pruebas de SpiderRecovery.

Solo informa; no instala paquetes, no accede a dispositivos, no modifica
archivos del proyecto y no requiere privilegios de administrador.
"""

import os
import platform
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]

DEPENDENCIAS = ["customtkinter", "psutil"]

ARCHIVOS_ESPERADOS = [
    "main.py",
    "VERSION",
    "requirements.txt",
    "core/scanner.py",
    "core/recover.py",
    "core/signatures.py",
    "ui/app.py",
    "ui/screen_select.py",
    "ui/screen_progress.py",
    "ui/screen_results.py",
    "lab/README.md",
]


def main() -> int:
    print(f"Python: {sys.version.split()[0]}")
    print(f"Ejecutable: {sys.executable}")
    print(f"SO: {platform.system()} ({platform.release()})")
    print(f"Plataforma: {platform.platform()}")
    print(f"Raíz del proyecto: {RAIZ}")
    print(f"Directorio de trabajo: {os.getcwd()}")

    for paquete in DEPENDENCIAS:
        try:
            mod = __import__(paquete)
            print(f"Import {paquete}: OK ({getattr(mod, '__version__', '?')})")
        except ImportError as e:
            print(f"Import {paquete}: FALLO -> {e}")

    for rel in ARCHIVOS_ESPERADOS:
        ruta = RAIZ / rel
        print(f"Archivo {rel}: {'OK' if ruta.is_file() else 'FALTA'}")

    print(f"Directorio lab/: {'OK' if (RAIZ / 'lab').is_dir() else 'FALTA'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
