"""Pruebas del escáner de SpiderRecovery (modo carpeta) con fixtures sintéticos.

No requiere dispositivos físicos, root, red ni GUI. Usa directorios
temporales y se limpia al finalizar.
"""

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.scanner import EscaneoProfundo, EstadoEscaneo

CABECERAS_RECONOCIDAS = {
    "foto.jpg": b"\xff\xd8\xff\xe0" + b"\x00" * 32 + b"\xff\xd9",
    "doc.pdf": b"%PDF-1.4\n" + b"\x00" * 32 + b"%%EOF",
    "anim.gif": b"GIF89a" + b"\x00" * 32 + b"\x00\x3b",
}


def escanear(carpeta: Path, timeout: float = 5.0):
    escaner = EscaneoProfundo()
    escaner.iniciar(str(carpeta))
    escaner._hilo.join(timeout=timeout)
    return escaner


class TestEscaneoCarpeta(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_firma_reconocida_es_detectada(self):
        (self.base / "foto.jpg").write_bytes(CABECERAS_RECONOCIDAS["foto.jpg"])
        escaner = escanear(self.base)
        encontrados = escaner.obtener_archivos()
        self.assertEqual(escaner.progreso.estado, EstadoEscaneo.COMPLETADO)
        self.assertEqual(len(encontrados), 1)
        self.assertEqual(encontrados[0].extension, "jpg")

    def test_multiples_firmas(self):
        for nombre, datos in CABECERAS_RECONOCIDAS.items():
            (self.base / nombre).write_bytes(datos)
        escaner = escanear(self.base)
        extensiones = {a.extension for a in escaner.obtener_archivos()}
        self.assertEqual(extensiones, {"jpg", "pdf", "gif"})

    def test_cabecera_desconocida_no_es_detectada(self):
        (self.base / "datos.bin").write_bytes(bytes(range(64)))
        escaner = escanear(self.base)
        self.assertEqual(escaner.obtener_archivos(), [])

    def test_fichero_vacio_no_es_detectado(self):
        (self.base / "vacio.jpg").write_bytes(b"")
        escaner = escanear(self.base)
        self.assertEqual(escaner.obtener_archivos(), [])

    def test_cabecera_truncada_no_es_detectada(self):
        (self.base / "truncado.jpg").write_bytes(b"\xff\xd8")
        escaner = escanear(self.base)
        self.assertEqual(escaner.obtener_archivos(), [])

    def test_carpeta_vacia(self):
        escaner = escanear(self.base)
        self.assertEqual(escaner.progreso.estado, EstadoEscaneo.COMPLETADO)
        self.assertEqual(escaner.obtener_archivos(), [])

    def test_ruta_actual_y_conteo(self):
        (self.base / "foto.jpg").write_bytes(CABECERAS_RECONOCIDAS["foto.jpg"])
        escaner = escanear(self.base)
        self.assertEqual(escaner.progreso.archivos_encontrados, 1)

    def test_cabecera_png_es_detectada(self):
        """La firma PNG completa (8 bytes) se clasifica como imagen PNG."""
        (self.base / "imagen.png").write_bytes(
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
        )
        escaner = escanear(self.base)
        encontrados = escaner.obtener_archivos()
        self.assertEqual(len(encontrados), 1)
        self.assertEqual(encontrados[0].extension, "png")
        self.assertEqual(encontrados[0].categoria, "Imágenes")

    def test_cabecera_bmp_es_detectada(self):
        """La firma BMP (BM) se clasifica como imagen BMP."""
        (self.base / "imagen.bmp").write_bytes(b"BM" + b"\x00" * 62)
        escaner = escanear(self.base)
        encontrados = escaner.obtener_archivos()
        self.assertEqual(len(encontrados), 1)
        self.assertEqual(encontrados[0].extension, "bmp")
        self.assertEqual(encontrados[0].categoria, "Imágenes")


if __name__ == "__main__":
    unittest.main()
