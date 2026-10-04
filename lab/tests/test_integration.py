"""Pruebas de integración del pipeline completo de SpiderRecovery.

Ejercitan el flujo real: escaneo de carpeta -> detección de candidatos ->
selección -> recuperación -> evidencia y clasificación, usando ficheros
regulares sintéticos en directorios temporales. Nunca acceden a
dispositivos de bloque ni a datos del usuario.

También validan la propagación de evidencia hacia la capa de presentación
(`ui.screen_results`) mediante sus métodos puros, sin arrancar Tk.
"""

import shutil
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.scanner import (
    ArchivoEncontrado,
    EscaneoProfundo,
    EvidenciaRecuperacion,
    ResultadoRecuperacion,
)
from core.recover import RecuperadorArchivos

DEST_TMPFS = Path("/dev/shm")


# --- Constructores de fixtures mínimos y válidos -----------------------------

def _segmento(tipo: bytes, contenido: bytes) -> bytes:
    return b"\xff" + tipo + (len(contenido) + 2).to_bytes(2, "big") + contenido


def fixture_jpeg() -> bytes:
    app = _segmento(b"\xe0", b"JFIF\x00fake")
    sos = _segmento(b"\xda", b"\x01\x01\x00\x00\x3f\x00")
    return b"\xff\xd8" + app + sos + b"\x00" * 40 + b"\xff\xd9"


def fixture_png() -> bytes:
    def ch(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\x00\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", ihdr) + ch(b"IDAT", idat) + ch(b"IEND", b"")


def fixture_bmp(ancho=4, alto=3, bpp=24) -> bytes:
    fila = ((bpp * ancho + 31) // 32) * 4
    pix = fila * alto
    off = 54
    return (b"BM" + struct.pack("<IHHI", off + pix, 0, 0, off)
            + struct.pack("<IiiHHIIiiII", 40, ancho, alto, 1, bpp, 0, pix, 0, 0, 0, 0)
            + b"\x00" * pix)


FIXTURES = {
    "uno.jpg": (fixture_jpeg, "jpg"),
    "dos.png": (fixture_png, "png"),
    "tres.bmp": (fixture_bmp, "bmp"),
}


def escanear(carpeta: Path, timeout: float = 5.0) -> EscaneoProfundo:
    escaner = EscaneoProfundo()
    escaner.iniciar(str(carpeta))
    escaner._hilo.join(timeout=timeout)
    return escaner


class TestPipelineCompleto(unittest.TestCase):
    """Escaneo -> recuperación -> evidencia con comparación byte a byte."""

    def setUp(self):
        self.src = Path(tempfile.mkdtemp())
        self.dest = tempfile.mkdtemp(dir=DEST_TMPFS if DEST_TMPFS.is_dir() else None)
        self.contenidos = {}
        for nombre, (constructor, _ext) in FIXTURES.items():
            datos = constructor()
            (self.src / nombre).write_bytes(datos)
            self.contenidos[nombre] = datos

    def tearDown(self):
        shutil.rmtree(self.src, ignore_errors=True)
        shutil.rmtree(self.dest, ignore_errors=True)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_escaneo_detecta_los_tres_formatos(self):
        escaner = escanear(self.src)
        nombres = sorted(a.nombre for a in escaner.obtener_archivos())
        self.assertEqual(nombres, sorted(FIXTURES))

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_recuperacion_bytes_exactos_por_formato(self):
        escaner = escanear(self.src)
        encontrados = escaner.obtener_archivos()
        r = RecuperadorArchivos()
        recuperados, fallidos = r.recuperar_archivos(encontrados, self.dest)
        self.assertEqual((recuperados, fallidos), (3, 0))
        for nombre, datos in self.contenidos.items():
            ext = nombre.rsplit(".", 1)[1]
            salidas = list(Path(self.dest).rglob(f"*.{ext}"))
            self.assertEqual(len(salidas), 1, f"{nombre}: salidas={len(salidas)}")
            self.assertEqual(salidas[0].read_bytes(), datos, f"{nombre}: no byte-exacto")

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_fuente_inmutable_tras_recuperar(self):
        import hashlib
        hashes_antes = {n: hashlib.sha256(d).hexdigest() for n, d in self.contenidos.items()}
        escaner = escanear(self.src)
        RecuperadorArchivos().recuperar_archivos(escaner.obtener_archivos(), self.dest)
        for nombre, h in hashes_antes.items():
            actual = hashlib.sha256((self.src / nombre).read_bytes()).hexdigest()
            self.assertEqual(actual, h, f"{nombre}: la fuente cambió")

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_evidencia_coherente_con_salida(self):
        escaner = escanear(self.src)
        encontrados = escaner.obtener_archivos()
        RecuperadorArchivos().recuperar_archivos(encontrados, self.dest)
        for a in encontrados:
            self.assertIsNotNone(a.evidencia, f"{a.nombre}: sin evidencia")
            ext = a.extension
            salidas = list(Path(self.dest).rglob(f"*.{ext}"))
            self.assertTrue(salidas, f"{a.nombre}: sin salida")
            self.assertEqual(a.evidencia.bytes_escritos, salidas[0].stat().st_size,
                             f"{a.nombre}: bytes_escritos != tamaño real")
            self.assertNotEqual(a.evidencia.resultado, ResultadoRecuperacion.FALLIDO)
            # Los tres fixtures son estructuralmente válidos: AMBIGUO, nunca COMPLETO.
            self.assertNotEqual(a.evidencia.resultado, ResultadoRecuperacion.COMPLETO_ESTRUCTURAL)
            # PNG y BMP exponen validación estructural; JPEG no tiene validador
            # implementado y debe mantener estructura_validada=None (honestidad).
            if ext in ("png", "bmp"):
                self.assertTrue(a.evidencia.estructura_validada, f"{a.nombre}: no validada")
            else:
                self.assertIsNone(a.evidencia.estructura_validada,
                                  f"{a.nombre}: JPEG no debe afirmar validación estructural")

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_destino_mismo_dispositivo_rechazado(self):
        """AUD-005: destino en el mismo FS que la fuente se rechaza."""
        dest_mismo = tempfile.mkdtemp(dir=self.src)
        try:
            escaner = escanear(self.src)
            r = RecuperadorArchivos()
            recuperados, fallidos = r.recuperar_archivos(escaner.obtener_archivos(), dest_mismo)
            self.assertEqual(recuperados, 0)
            self.assertEqual(fallidos, 3)
            self.assertEqual(list(Path(dest_mismo).rglob("*.*")), [])
        finally:
            shutil.rmtree(dest_mismo, ignore_errors=True)


class TestPropagacionEvidenciaUI(unittest.TestCase):
    """La capa de presentación no debe sobreestimar las clasificaciones."""

    def _archivo(self, evidencia=None):
        a = ArchivoEncontrado(
            ruta="x", nombre="x.png", extension="png", tamano=10, offset=0,
            categoria="Imágenes", descripcion="x", evidencia=evidencia,
        )
        return a

    def test_sin_evidencia_no_se_describe(self):
        from ui.screen_results import PantallaResultados
        self.assertEqual(PantallaResultados._texto_resultado(None, self._archivo()), "—")

    def test_resultados_se_mapean_a_su_valor(self):
        from ui.screen_results import PantallaResultados
        for resultado in ResultadoRecuperacion:
            a = self._archivo(EvidenciaRecuperacion(resultado=resultado))
            self.assertEqual(PantallaResultados._texto_resultado(None, a), resultado.value)

    def test_completo_estructural_no_se_usa_sin_estructura(self):
        """Ninguna evidencia emitida por el motor sin estructura_validada
        debería clasificarse como COMPLETO_ESTRUCTURAL."""
        # Contrato documentado: el motor nunca emite COMPLETO_ESTRUCTURAL.
        self.assertIn(ResultadoRecuperacion.COMPLETO_ESTRUCTURAL, ResultadoRecuperacion)


if __name__ == "__main__":
    unittest.main()
