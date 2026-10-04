"""Pruebas de recuperación de SpiderRecovery con ficheros sintéticos.

Nunca usa /dev/*, discos reales ni volúmenes externos. Los destinos se
ubican en /dev/shm (tmpfs), que suele ser un dispositivo distinto del /tmp.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.recover import RecuperadorArchivos
from core.scanner import ArchivoEncontrado

DEST_TMPFS = Path("/dev/shm")


def _segmento(tipo: bytes, contenido: bytes) -> bytes:
    longitud = len(contenido) + 2
    return b"\xff" + tipo + longitud.to_bytes(2, "big") + contenido


def _jpeg_sintetico(datos_scan: bytes = b"") -> bytes:
    """JPEG sintético estructuralmente plausible: SOI + APP + SOS + datos + EOI."""
    app = _segmento(b"\xe0", b"JFIF\x00fake")
    sos = _segmento(b"\xda", b"\x01\x01\x00\x00\x3f\x00")
    scan = datos_scan.replace(b"\xff", b"\xff\x00")
    return b"\xff\xd8" + app + sos + scan + b"\xff\xd9"


def _jpeg_sin_eoi(datos_scan: bytes = b"") -> bytes:
    app = _segmento(b"\xe0", b"JFIF\x00fake")
    sos = _segmento(b"\xda", b"\x01\x01\x00\x00\x3f\x00")
    return b"\xff\xd8" + app + sos + datos_scan.replace(b"\xff", b"\xff\x00")


def arch(ruta: str, nombre="foto.jpg") -> ArchivoEncontrado:
    return ArchivoEncontrado(
        ruta=ruta, nombre=nombre, extension="jpg", tamano=10, offset=0,
        categoria="Imágenes", descripcion="Imagen JPEG",
    )


# --- Fixtures PNG sintéticos (sin PIL; construidos a mano) -------------------

import struct
import zlib

_PNG_IHDR = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
_PNG_IDAT = zlib.compress(b"\x00\x00\x00\x00")  # filtro 0 + RGB negro


def _chunk_png(tipo: bytes, datos: bytes, longitud_declarada: int = None,
               crc_forzada: int = None) -> bytes:
    """Construye un chunk PNG con CRC correcta salvo que se fuerce otra."""
    longitud = len(datos) if longitud_declarada is None else longitud_declarada
    crc = zlib.crc32(tipo + datos) & 0xFFFFFFFF if crc_forzada is None else crc_forzada
    return struct.pack(">I", longitud) + tipo + datos + struct.pack(">I", crc)


def _png_sintetico(datos_idat: bytes = None, con_iend: bool = True) -> bytes:
    idat = datos_idat if datos_idat is not None else _PNG_IDAT
    partes = [b"\x89PNG\r\n\x1a\n", _chunk_png(b"IHDR", _PNG_IHDR)]
    if idat is not None:
        partes.append(_chunk_png(b"IDAT", idat))
    if con_iend:
        partes.append(_chunk_png(b"IEND", b""))
    return b"".join(partes)


def _ihdr_png(ancho=1, alto=1, profundidad=8, color=2, compresion=0,
              filtro=0, entrelazado=0) -> bytes:
    """Construye un payload IHDR de 13 bytes con los campos indicados."""
    return struct.pack(">IIBBBBB", ancho, alto, profundidad, color,
                       compresion, filtro, entrelazado)


def _png_con_chunks(chunks, con_firma=True) -> bytes:
    """Construye un PNG concatenando chunks (tipo, datos) con CRC correcta."""
    firma = b"\x89PNG\r\n\x1a\n" if con_firma else b""
    return firma + b"".join(_chunk_png(t, d) for t, d in chunks)


def arch_png(ruta: str, nombre="imagen.png", tamano=0, offset=0) -> ArchivoEncontrado:
    return ArchivoEncontrado(
        ruta=ruta, nombre=nombre, extension="png", tamano=tamano, offset=offset,
        categoria="Imágenes", descripcion="Imagen PNG",
    )


# --- Fixtures BMP sintéticos (sin PIL; construidos a mano) -------------------

def _stride_bmp(ancho: int, bpp: int) -> int:
    """Fila de un BMP alineada a múltiplo de 4 bytes."""
    return ((bpp * ancho + 31) // 32) * 4


def _bmp_sintetico(ancho=2, alto=2, bpp=24, compresion=0, planes=1,
                   tam_declarado=None, tam_imagen=None, offset_datos=None,
                   dib_size=40, ext_dib=b"", metadatos=b"", firma=b"BM",
                   pixel_bytes=None, tam_pixel_extra=0) -> bytes:
    """Construye un BMP sintético configurable.

    Por defecto calcula tamaños coherentes y rellena los píxeles con 0.
    Pasar `pixel_bytes` para truncar/alterar los datos declarados.
    """
    fila = _stride_bmp(ancho, bpp)
    pixel_size = fila * abs(alto) + tam_pixel_extra
    cabecera_dib_total = dib_size
    if offset_datos is None:
        offset_datos = 14 + cabecera_dib_total + len(metadatos)
    total = offset_datos + pixel_size
    if tam_declarado is None:
        tam_declarado = total
    if tam_imagen is None:
        tam_imagen = pixel_size
    cabecera_archivo = firma + struct.pack("<IHHI", tam_declarado, 0, 0, offset_datos)
    dib = struct.pack("<IiiHHIIiiII", dib_size, ancho, alto, planes, bpp, compresion,
                      tam_imagen, 0, 0, 0, 0)
    relleno_dib = ext_dib
    if pixel_bytes is None:
        pixel_bytes = b"\x00" * pixel_size
    return cabecera_archivo + dib + relleno_dib + metadatos + pixel_bytes


def arch_bmp(ruta: str, nombre="imagen.bmp", tamano=0, offset=0) -> ArchivoEncontrado:
    return ArchivoEncontrado(
        ruta=ruta, nombre=nombre, extension="bmp", tamano=tamano, offset=offset,
        categoria="Imágenes", descripcion="Imagen BMP",
    )


class TestRecuperacion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src_dir = Path(self.tmp.name)
        self.src = self.src_dir / "origen.jpg"
        self.src.write_bytes(_jpeg_sintetico(b"\x42" * 32))
        self.dest = tempfile.TemporaryDirectory(dir=DEST_TMPFS if DEST_TMPFS.is_dir() else None)

    def tearDown(self):
        self.tmp.cleanup()
        self.dest.cleanup()

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_recuperacion_preserva_bytes(self):
        r = RecuperadorArchivos()
        recuperados, fallidos = r.recuperar_archivos([arch(str(self.src))], self.dest.name)
        salida = Path(self.dest.name) / "Imágenes" / "foto.jpg"
        self.assertEqual((recuperados, fallidos), (1, 0))
        self.assertTrue(salida.is_file())
        self.assertGreaterEqual(salida.stat().st_size, self.src.stat().st_size)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_preexistente_no_se_sobrescribe(self):
        previo = Path(self.dest.name) / "Imágenes" / "foto.jpg"
        previo.parent.mkdir(parents=True, exist_ok=True)
        previo.write_bytes(b"ORIGINAL")
        r = RecuperadorArchivos()
        recuperados, fallidos = r.recuperar_archivos([arch(str(self.src))], self.dest.name)
        self.assertEqual((recuperados, fallidos), (0, 1))
        self.assertEqual(previo.read_bytes(), b"ORIGINAL")

    def test_origen_inexistente_reporta_fallo(self):
        r = RecuperadorArchivos()
        recuperados, fallidos = r.recuperar_archivos([arch("/ruta/que/no/existe.jpg")], self.dest.name)
        self.assertEqual((recuperados, fallidos), (0, 1))
        self.assertEqual(list(Path(self.dest.name).rglob("*.jpg")), [])

    def test_origen_vacio(self):
        vacio = self.src_dir / "vacio.jpg"
        vacio.write_bytes(b"")
        r = RecuperadorArchivos()
        recuperados, fallidos = r.recuperar_archivos([arch(str(vacio), "vacio.jpg")], self.dest.name)
        self.assertEqual((recuperados, fallidos), (0, 1))

    def test_origen_en_mismo_dispositivo_es_rechazado(self):
        # Destino en el mismo /tmp que el origen -> AUD-005 debe rechazar
        dest2 = tempfile.TemporaryDirectory(dir=self.src_dir)
        try:
            r = RecuperadorArchivos()
            recuperados, fallidos = r.recuperar_archivos([arch(str(self.src))], dest2.name)
            self.assertEqual((recuperados, fallidos), (0, 1))
            self.assertEqual(list(Path(dest2.name).rglob("*.jpg")), [])
        finally:
            dest2.cleanup()

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_integridad_jpeg_byte_a_byte(self):
        """Flujo completo: escaneo de carpeta -> selección -> recuperación.

        Verifica si el motor reproduce los bytes exactos del fixture.
        No asume que el fixture sea un JPEG conforme a estándar.
        """
        import hashlib

        fixture = _jpeg_sintetico(b"SPIDER-LAB-PAYLOAD-0123456789" * 8)
        fuente = self.src_dir / "muestra.jpg"
        fuente.write_bytes(fixture)

        from core.scanner import EscaneoProfundo
        escaner = EscaneoProfundo()
        escaner.iniciar(str(self.src_dir))
        escaner._hilo.join(timeout=5)
        encontrados = escaner.obtener_archivos()

        detectados = [a for a in encontrados if a.nombre == "muestra.jpg"]
        print(f"\n[INTEGRIDAD] detectados: {len(detectados)} (duplicados: {len(detectados) > 1})")
        self.assertEqual(len(detectados), 1, f"detecciones inesperadas: {len(detectados)}")

        r = RecuperadorArchivos()
        recuperados, fallidos = r.recuperar_archivos(detectados, self.dest.name)
        print(f"[INTEGRIDAD] recuperados={recuperados} fallidos={fallidos}")
        self.assertEqual((recuperados, fallidos), (1, 0))

        salida = Path(self.dest.name) / "Imágenes" / "archivo_recuperado_0001.jpg"
        # El nombre puede diferir; localizar el primer .jpg creado
        jpgs = list(Path(self.dest.name).rglob("*.jpg"))
        self.assertEqual(len(jpgs), 1)
        recuperado = jpgs[0].read_bytes()

        h_orig = hashlib.sha256(fixture).hexdigest()
        h_rec = hashlib.sha256(recuperado).hexdigest()
        print(f"[INTEGRIDAD] original={len(fixture)}B sha256={h_orig}")
        print(f"[INTEGRIDAD] recuperado={len(recuperado)}B sha256={h_rec}")
        print(f"[INTEGRIDAD] igualdad byte a byte: {fixture == recuperado}")
        print(f"[INTEGRIDAD] bytes extra al final: {len(recuperado) > len(fixture)}")

        self.assertEqual(fixture, recuperado, "El contenido recuperado no es byte a byte igual al original")
        self.assertEqual(len(fixture), len(recuperado), "Tamaño recuperado distinto del original")

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_firma_embebida_no_detectada_en_modo_carpeta(self):
        """El escaneo de carpeta solo inspecciona cabeceras (16 bytes).

        Un JPEG embebido en offset distinto de 0 NO se detecta: limitación
        documentada del modo carpeta (core/scanner.py:_escanear_carpeta).
        """
        from core.scanner import EscaneoProfundo

        prefijo = b"\xAA" * 128
        fixture = _jpeg_sintetico(b"EMBEBIDO-JPEG-PAYLOAD" * 4)
        sufijo = b"\xBB" * 96
        embebido = self.src_dir / "contenedor.bin"

        EMB_OFFSET = len(prefijo)
        embebido.write_bytes(prefijo + fixture + sufijo)

        escaner = EscaneoProfundo()
        escaner.iniciar(str(self.src_dir))
        escaner._hilo.join(timeout=5)
        detectados = [a for a in escaner.obtener_archivos() if a.nombre == "contenedor.bin"]
        print(f"\n[EMBEBIDO] detecciones en modo carpeta: {len(detectados)} (esperado 0 por limitación de API)")
        self.assertEqual(detectados, [])

        # Recuperación directa por offset conocido (API pública ArchivoEncontrado):
        # el extractor lee desde el offset sin validar que el tamaño sea exacto.
        import hashlib
        from core.scanner import ArchivoEncontrado as AE
        entrada = AE(ruta=str(embebido), nombre="muestra.jpg", extension="jpg",
                     tamano=len(fixture), offset=EMB_OFFSET, categoria="Imágenes",
                     descripcion="Imagen JPEG")
        r = RecuperadorArchivos()
        recuperados, fallidos = r.recuperar_archivos([entrada], self.dest.name)
        self.assertEqual((recuperados, fallidos), (1, 0))
        jpgs = list(Path(self.dest.name).rglob("*.jpg"))
        self.assertEqual(len(jpgs), 1)
        recuperado = jpgs[0].read_bytes()
        print(f"[EMBEBIDO] offset={EMB_OFFSET} recuperado={len(recuperado)}B "
              f"sha256_rec={hashlib.sha256(recuperado).hexdigest()[:16]}… "
              f"sha256_fixture={hashlib.sha256(fixture).hexdigest()[:16]}…")
        print(f"[EMBEBIDO] prefijo extra ausente: {not recuperado.startswith(prefijo)}; "
              f"bytes extra de sufijo: {len(recuperado) - len(fixture)}")
        # Tras la corrección JPEG, el extractor recorta exactamente en FF D9.
        self.assertEqual(recuperado, fixture)
        self.assertTrue(entrada.tamano_exacto)
        from core.scanner import ResultadoRecuperacion
        self.assertIsNotNone(entrada.evidencia)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.AMBIGUO)
        self.assertEqual(entrada.evidencia.bytes_escritos, len(jpgs[0].read_bytes()))

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_jpeg_mayor_a_8mib_resultado_honesto(self):
        """JPEG sin EOI de 9 MiB: ya no se trunca a 8 MiB; sigue ESTIMADO."""
        grande = _jpeg_sin_eoi(b"\x00" * (9 * 1024 * 1024))
        fuente = self.src_dir / "grande.jpg"
        fuente.write_bytes(grande)

        from core.scanner import ArchivoEncontrado as AE, ResultadoRecuperacion
        entrada = AE(ruta=str(fuente), nombre="grande.jpg", extension="jpg",
                     tamano=len(grande), offset=0, categoria="Imágenes",
                     descripcion="Imagen JPEG")
        r = RecuperadorArchivos()
        recuperados, fallidos = r.recuperar_archivos([entrada], self.dest.name)
        self.assertEqual((recuperados, fallidos), (1, 0))
        self.assertFalse(entrada.tamano_exacto)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.ESTIMADO)
        jpgs = list(Path(self.dest.name).rglob("*.jpg"))
        # 9 MiB > 8 MiB: ya NO se trunca por el límite de formato
        self.assertEqual(jpgs[0].stat().st_size, len(grande))
        self.assertEqual(entrada.evidencia.bytes_escritos, jpgs[0].stat().st_size)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_jpeg_mayor_a_8mib_con_eoi(self):
        """JPEG >8 MiB con EOI dentro del presupuesto: no se trunca a 8 MiB."""
        fixture = _jpeg_sintetico(b"\x11" * (9 * 1024 * 1024))
        fuente = self.src_dir / "grande_eoi.jpg"
        fuente.write_bytes(fixture)

        from core.scanner import ArchivoEncontrado as AE, ResultadoRecuperacion
        entrada = AE(ruta=str(fuente), nombre="grande_eoi.jpg", extension="jpg",
                     tamano=len(fixture), offset=0, categoria="Imágenes",
                     descripcion="Imagen JPEG")
        r = RecuperadorArchivos()
        recuperados, fallidos = r.recuperar_archivos([entrada], self.dest.name)
        self.assertEqual((recuperados, fallidos), (1, 0))
        jpgs = list(Path(self.dest.name).rglob("*.jpg"))
        self.assertEqual(jpgs[0].read_bytes(), fixture)
        self.assertTrue(entrada.tamano_exacto)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.AMBIGUO)

    def test_cancelacion_sin_resultado_completo(self):
        """Cancelar antes de recuperar no debe producir evidencia COMPLETO."""
        fuente = self.src_dir / "cancel.jpg"
        fuente.write_bytes(_jpeg_sintetico(b"\x00" * 64))
        from core.scanner import ArchivoEncontrado as AE
        entrada = AE(ruta=str(fuente), nombre="cancel.jpg", extension="jpg",
                     tamano=70, offset=0, categoria="Imágenes", descripcion="Imagen JPEG")
        r = RecuperadorArchivos()
        # Cancelar durante el procesamiento del segundo archivo
        llamadas = {"n": 0}
        def _cb(i, n, nombre):
            llamadas["n"] += 1
            if llamadas["n"] >= 2:
                r.cancelar()
        r.registrar_callback_progreso(_cb)
        entrada2 = AE(ruta=str(fuente), nombre="cancel2.jpg", extension="jpg",
                      tamano=70, offset=0, categoria="Imágenes", descripcion="Imagen JPEG")
        recuperados, fallidos = r.recuperar_archivos([entrada, entrada2], self.dest.name)
        # El archivo en curso al cancelar debe quedar PARCIAL/cancelado,
        # y nunca existir como "completo". El segundo puede quedar PARCIAL
        # (bytes_escritos=0, salida eliminada) pero nunca COMPLETO.
        from core.scanner import ResultadoRecuperacion
        for entrada_i in (entrada, entrada2):
            if entrada_i.evidencia is not None:
                self.assertNotEqual(entrada_i.evidencia.resultado, ResultadoRecuperacion.COMPLETO_ESTRUCTURAL)
        jpgs = list(Path(self.dest.name).rglob("*.jpg"))
        self.assertLessEqual(len(jpgs), 1)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_fallo_de_escritura_no_reporta_exito(self):
        """Un error al crear el destino exclusivo produce FALLIDO, no éxito."""
        fixture = _jpeg_sintetico(b"PAYLOAD" * 8)
        fuente = self.src_dir / "fallo.jpg"
        fuente.write_bytes(fixture)

        from core.scanner import ArchivoEncontrado as AE, ResultadoRecuperacion
        entrada = AE(ruta=str(fuente), nombre="fallo.jpg", extension="jpg",
                     tamano=len(fixture), offset=0, categoria="Imágenes", descripcion="Imagen JPEG")
        r = RecuperadorArchivos()

        import builtins
        from unittest import mock
        real_open = builtins.open
        def fake_open(path, mode="r", *a, **k):
            if mode == "xb" and str(path).endswith(".jpg"):
                raise OSError("disco lleno simulado")
            return real_open(path, mode, *a, **k)

        with mock.patch.object(builtins, "open", fake_open):
            recuperados, fallidos = r.recuperar_archivos([entrada], self.dest.name)
        self.assertEqual((recuperados, fallidos), (0, 1))
        self.assertIsNotNone(entrada.evidencia)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.FALLIDO)
        self.assertEqual(entrada.evidencia.bytes_escritos, 0)
        self.assertEqual(list(Path(self.dest.name).rglob("*.jpg")), [])

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_ffd9_dentro_de_segmento_app_se_ignora(self):
        """Un FF D9 dentro del contenido de segmento APP no es fin de archivo."""
        app_malicioso = _segmento(b"\xe1", b"\xff\xd9" + b"thumbnail-falso")
        sos = _segmento(b"\xda", b"\x01\x01\x00\x00\x3f\x00")
        fixture = b"\xff\xd8" + app_malicioso + sos + b"\x10" * 32 + b"\xff\xd9"
        fuente = self.src_dir / "app.jpg"
        fuente.write_bytes(fixture)

        from core.scanner import ArchivoEncontrado as AE
        entrada = AE(ruta=str(fuente), nombre="app.jpg", extension="jpg",
                     tamano=len(fixture), offset=0, categoria="Imágenes", descripcion="Imagen JPEG")
        r = RecuperadorArchivos()
        recuperados, fallidos = r.recuperar_archivos([entrada], self.dest.name)
        self.assertEqual((recuperados, fallidos), (1, 0))
        jpgs = list(Path(self.dest.name).rglob("*.jpg"))
        self.assertEqual(jpgs[0].read_bytes(), fixture)
        self.assertTrue(entrada.tamano_exacto)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_byte_stuffing_ff00(self):
        """FF 00 dentro de datos de imagen no es marcador de fin."""
        scan = b"\x11\x22\xff\x00\x33" + b"\x44" * 16
        fixture = _jpeg_sintetico(scan)
        fuente = self.src_dir / "stuff.jpg"
        fuente.write_bytes(fixture)

        from core.scanner import ArchivoEncontrado as AE
        entrada = AE(ruta=str(fuente), nombre="stuff.jpg", extension="jpg",
                     tamano=len(fixture), offset=0, categoria="Imágenes", descripcion="Imagen JPEG")
        r = RecuperadorArchivos()
        r.recuperar_archivos([entrada], self.dest.name)
        jpgs = list(Path(self.dest.name).rglob("*.jpg"))
        self.assertEqual(jpgs[0].read_bytes(), fixture)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_marcadores_reinicio(self):
        scan = b"\xff\xd0" + b"\xAA" * 8 + b"\xff\xd3" + b"\xBB" * 8
        fixture = _jpeg_sintetico(scan)
        fuente = self.src_dir / "rst.jpg"
        fuente.write_bytes(fixture)

        from core.scanner import ArchivoEncontrado as AE
        entrada = AE(ruta=str(fuente), nombre="rst.jpg", extension="jpg",
                     tamano=len(fixture), offset=0, categoria="Imágenes", descripcion="Imagen JPEG")
        r = RecuperadorArchivos()
        r.recuperar_archivos([entrada], self.dest.name)
        jpgs = list(Path(self.dest.name).rglob("*.jpg"))
        self.assertEqual(jpgs[0].read_bytes(), fixture)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_multiples_sos_progresivo(self):
        app = _segmento(b"\xe0", b"JFIF\x00fake")
        sos1 = _segmento(b"\xda", b"\x01\x01\x00\x00\x3f\x00")
        sos2 = _segmento(b"\xda", b"\x01\x01\x00\x00\x3f\x00")
        fixture = b"\xff\xd8" + app + sos1 + b"\x10" * 16 + sos2 + b"\x20" * 16 + b"\xff\xd9"
        fuente = self.src_dir / "prog.jpg"
        fuente.write_bytes(fixture)

        from core.scanner import ArchivoEncontrado as AE
        entrada = AE(ruta=str(fuente), nombre="prog.jpg", extension="jpg",
                     tamano=len(fixture), offset=0, categoria="Imágenes", descripcion="Imagen JPEG")
        r = RecuperadorArchivos()
        r.recuperar_archivos([entrada], self.dest.name)
        jpgs = list(Path(self.dest.name).rglob("*.jpg"))
        self.assertEqual(jpgs[0].read_bytes(), fixture)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_longitud_invalida_no_es_exacto(self):
        """Un segmento con longitud < 2 no produce límite exacto."""
        fixture = b"\xff\xd8" + b"\xff\xe0" + b"\x00\x01" + b"\x00" * 16 + b"\xff\xd9"
        fuente = self.src_dir / "badlen.jpg"
        fuente.write_bytes(fixture)

        from core.scanner import ArchivoEncontrado as AE, ResultadoRecuperacion
        entrada = AE(ruta=str(fuente), nombre="badlen.jpg", extension="jpg",
                     tamano=len(fixture), offset=0, categoria="Imágenes", descripcion="Imagen JPEG")
        r = RecuperadorArchivos()
        recuperados, fallidos = r.recuperar_archivos([entrada], self.dest.name)
        self.assertEqual((recuperados, fallidos), (1, 0))
        self.assertFalse(entrada.tamano_exacto)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.ESTIMADO)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_limite_operativo_se_respeta_exactamente(self):
        """Con TAMANO_MAX_ARCHIVO reducido, no se lee ni escribe más allá."""
        from unittest import mock
        from core.scanner import EscaneoProfundo
        fixture = _jpeg_sin_eoi(b"\x00" * (2 * 1024 * 1024))
        fuente = self.src_dir / "limite.jpg"
        fuente.write_bytes(fixture)

        from core.scanner import ArchivoEncontrado as AE, ResultadoRecuperacion
        entrada = AE(ruta=str(fuente), nombre="limite.jpg", extension="jpg",
                     tamano=len(fixture), offset=0, categoria="Imágenes", descripcion="Imagen JPEG")
        r = RecuperadorArchivos()
        with mock.patch.object(EscaneoProfundo, "TAMANO_MAX_ARCHIVO", 1024 * 1024):
            recuperados, fallidos = r.recuperar_archivos([entrada], self.dest.name)
        self.assertEqual((recuperados, fallidos), (1, 0))
        jpgs = list(Path(self.dest.name).rglob("*.jpg"))
        self.assertLessEqual(jpgs[0].stat().st_size, 1024 * 1024)
        self.assertEqual(entrada.evidencia.limite_alcanzado, "tamano_maximo")
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.ESTIMADO)

    def _generar_jpegs_referencia(self, carpeta: Path) -> dict:
        """Genera JPEGs reales con PIL del sistema (no con el venv).

        Devuelve rutas o {} si PIL no está disponible (test omitido).
        """
        import subprocess, shutil
        python = shutil.which("python3")
        if python is None:
            return {}
        script = carpeta / "_gen.py"
        script.write_text(
            "from PIL import Image\n"
            f"img = Image.new('RGB', (64, 64), (10, 120, 200))\n"
            f"img.save(r'{carpeta}/baseline.jpg', 'JPEG')\n"
            f"img.save(r'{carpeta}/progresivo.jpg', 'JPEG', progressive=True)\n"
        )
        try:
            subprocess.run([python, str(script)], check=True, capture_output=True, timeout=60)
        except Exception:
            return {}
        finally:
            try:
                script.unlink()
            except OSError:
                pass
        rutas = {
            "baseline": carpeta / "baseline.jpg",
            "progresivo": carpeta / "progresivo.jpg",
        }
        return {k: v for k, v in rutas.items() if v.is_file()}

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_jpeg_real_baseline_bytes_exactos(self):
        """JPEG real baseline: extracción byte-exacta hasta EOI, sin 'completo'."""
        rutas = self._generar_jpegs_referencia(self.src_dir)
        if not rutas:
            self.skipTest("PIL del sistema no disponible para generar JPEG real")
        fuente = rutas["baseline"]

        from core.scanner import ArchivoEncontrado as AE, ResultadoRecuperacion
        esperado = fuente.read_bytes()
        entrada = AE(ruta=str(fuente), nombre="baseline.jpg", extension="jpg",
                     tamano=len(esperado), offset=0, categoria="Imágenes", descripcion="Imagen JPEG")
        r = RecuperadorArchivos()
        recuperados, fallidos = r.recuperar_archivos([entrada], self.dest.name)
        self.assertEqual((recuperados, fallidos), (1, 0))
        jpgs = list(Path(self.dest.name).rglob("*.jpg"))
        recuperado = jpgs[0].read_bytes()
        print(f"\n[REAL] baseline: original={len(esperado)}B recuperado={len(recuperado)}B "
              f"iguales={recuperado == esperado} resultado={entrada.evidencia.resultado}")
        self.assertEqual(recuperado, esperado)
        self.assertTrue(entrada.tamano_exacto)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.AMBIGUO)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_jpeg_real_progresivo_bytes_exactos(self):
        """JPEG real progresivo (múltiples escaneos): extracción byte-exacta."""
        rutas = self._generar_jpegs_referencia(self.src_dir)
        if not rutas or "progresivo" not in rutas:
            self.skipTest("PIL del sistema no disponible para generar JPEG progresivo")
        fuente = rutas["progresivo"]

        from core.scanner import ArchivoEncontrado as AE, ResultadoRecuperacion
        esperado = fuente.read_bytes()
        entrada = AE(ruta=str(fuente), nombre="progresivo.jpg", extension="jpg",
                     tamano=len(esperado), offset=0, categoria="Imágenes", descripcion="Imagen JPEG")
        r = RecuperadorArchivos()
        recuperados, fallidos = r.recuperar_archivos([entrada], self.dest.name)
        self.assertEqual((recuperados, fallidos), (1, 0))
        jpgs = list(Path(self.dest.name).rglob("*.jpg"))
        recuperado = jpgs[0].read_bytes()
        print(f"[REAL] progresivo: original={len(esperado)}B recuperado={len(recuperado)}B "
              f"iguales={recuperado == esperado} resultado={entrada.evidencia.resultado}")
        self.assertEqual(recuperado, esperado)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.AMBIGUO)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_jpeg_real_truncado_sin_eoi(self):
        """JPEG real truncado: resultado estimado honesto, nunca 'completo'."""
        rutas = self._generar_jpegs_referencia(self.src_dir)
        if not rutas:
            self.skipTest("PIL del sistema no disponible para generar JPEG real")
        original = rutas["baseline"].read_bytes()
        truncado = original[: len(original) * 6 // 10]
        fuente = self.src_dir / "truncado.jpg"
        fuente.write_bytes(truncado)

        from core.scanner import ArchivoEncontrado as AE, ResultadoRecuperacion
        entrada = AE(ruta=str(fuente), nombre="truncado.jpg", extension="jpg",
                     tamano=len(truncado), offset=0, categoria="Imágenes", descripcion="Imagen JPEG")
        r = RecuperadorArchivos()
        recuperados, fallidos = r.recuperar_archivos([entrada], self.dest.name)
        self.assertEqual((recuperados, fallidos), (1, 0))
        self.assertFalse(entrada.tamano_exacto)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.ESTIMADO)
        jpgs = list(Path(self.dest.name).rglob("*.jpg"))
        print(f"[REAL] truncado: original={len(original)}B truncado={len(truncado)}B "
              f"resultado={entrada.evidencia.resultado} bytes_escritos={entrada.evidencia.bytes_escritos}")
        self.assertEqual(entrada.evidencia.bytes_escritos, jpgs[0].stat().st_size)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_eoi_en_todas_las_posiciones_de_borde(self):
        """EOI cruzando el límite de 64 KiB en varias posiciones."""
        app = _segmento(b"\xe0", b"xxxx")
        sos = _segmento(b"\xda", b"\x01\x01\x00\x00\x3f\x00")
        prefijo = 2 + len(app) + len(sos)
        for objetivo_ff in (65533, 65534, 65535, 65536, 65537):
            scan_len = objetivo_ff - prefijo
            fixture = b"\xff\xd8" + app + sos + b"\x00" * scan_len + b"\xff\xd9"
            fuente = self.src_dir / f"borde_{objetivo_ff}.jpg"
            fuente.write_bytes(fixture)

            dest_i = tempfile.mkdtemp(dir=DEST_TMPFS)
            try:
                from core.scanner import ArchivoEncontrado as AE
                entrada = AE(ruta=str(fuente), nombre=f"borde_{objetivo_ff}.jpg", extension="jpg",
                             tamano=len(fixture), offset=0, categoria="Imágenes", descripcion="Imagen JPEG")
                r = RecuperadorArchivos()
                r.recuperar_archivos([entrada], dest_i)
                jpgs = list(Path(dest_i).rglob("*.jpg"))
                self.assertEqual(len(jpgs), 1)
                self.assertEqual(jpgs[0].read_bytes(), fixture,
                                 f"EOI en offset {objetivo_ff}: bytes no idénticos")
                self.assertTrue(entrada.tamano_exacto, f"EOI en {objetivo_ff}: no se marcó exacto")
            finally:
                import shutil; shutil.rmtree(dest_i, ignore_errors=True)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_segmento_termina_exactamente_en_borde_de_bloque(self):
        """Un segmento APP que termina justo en el límite de 64 KiB."""
        app1 = _segmento(b"\xe0", b"JFIF\x00fake")
        # app2 debe terminar exactamente en el byte 65536
        contenido_app2_len = 65536 - (2 + len(app1)) - 4
        app2 = _segmento(b"\xe1", b"\x77" * contenido_app2_len)
        sos = _segmento(b"\xda", b"\x01\x01\x00\x00\x3f\x00")
        fixture = b"\xff\xd8" + app1 + app2 + sos + b"\x30" * 16 + b"\xff\xd9"
        fuente = self.src_dir / "borde_app.jpg"
        fuente.write_bytes(fixture)

        from core.scanner import ArchivoEncontrado as AE
        entrada = AE(ruta=str(fuente), nombre="borde_app.jpg", extension="jpg",
                     tamano=len(fixture), offset=0, categoria="Imágenes", descripcion="Imagen JPEG")
        r = RecuperadorArchivos()
        r.recuperar_archivos([entrada], self.dest.name)
        jpgs = list(Path(self.dest.name).rglob("*.jpg"))
        self.assertEqual(jpgs[0].read_bytes(), fixture)
        self.assertTrue(entrada.tamano_exacto)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_limite_operativo_a_mitad_de_marcador(self):
        """El tope operativo cae en medio del marcador EOI: no se marca exacto."""
        app = _segmento(b"\xe0", b"JFIF\x00fake")
        sos = _segmento(b"\xda", b"\x01\x01\x00\x00\x3f\x00")
        fixture = b"\xff\xd8" + app + sos + b"\x00" * 64 + b"\xff\xd9"
        offset_eoi = len(fixture) - 2
        fuente = self.src_dir / "limite_marcador.jpg"
        fuente.write_bytes(fixture)

        from unittest import mock
        from core.scanner import EscaneoProfundo, ArchivoEncontrado as AE, ResultadoRecuperacion
        entrada = AE(ruta=str(fuente), nombre="limite_marcador.jpg", extension="jpg",
                     tamano=len(fixture), offset=0, categoria="Imágenes", descripcion="Imagen JPEG")
        r = RecuperadorArchivos()
        # Tope que deja el FF de EOI leído pero NO el D9
        with mock.patch.object(EscaneoProfundo, "TAMANO_MAX_ARCHIVO", offset_eoi + 1):
            r.recuperar_archivos([entrada], self.dest.name)
        self.assertFalse(entrada.tamano_exacto)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.ESTIMADO)
        self.assertEqual(entrada.evidencia.limite_alcanzado, "tamano_maximo")
        jpgs = list(Path(self.dest.name).rglob("*.jpg"))
        self.assertEqual(jpgs[0].stat().st_size, offset_eoi + 1)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_lectura_corta_no_corrompe(self):
        """Reads que devuelven pocos bytes no rompen la salida."""
        fixture = _jpeg_sintetico(b"\x12" * 4096)
        fuente = self.src_dir / "corta.jpg"
        fuente.write_bytes(fixture)

        import builtins
        from unittest import mock
        real_open = builtins.open

        class LecturaCorta:
            def __init__(self, f): self._f = f
            def read(self, n=-1): return self._f.read(min(n if n > 0 else 7, 7))
            def seek(self, o, w=0): return self._f.seek(o, w)
            def write(self, b): return self._f.write(b)
            def __enter__(self): return self
            def __exit__(self, *a): return self._f.__exit__(*a)

        def fake_open(path, mode="r", *a, **k):
            f = real_open(path, mode, *a, **k)
            if mode == "rb" and str(path).endswith("corta.jpg"):
                return LecturaCorta(f)
            return f

        from core.scanner import ArchivoEncontrado as AE
        entrada = AE(ruta=str(fuente), nombre="corta.jpg", extension="jpg",
                     tamano=len(fixture), offset=0, categoria="Imágenes", descripcion="Imagen JPEG")
        r = RecuperadorArchivos()
        with mock.patch.object(builtins, "open", fake_open):
            r.recuperar_archivos([entrada], self.dest.name)
        jpgs = list(Path(self.dest.name).rglob("*.jpg"))
        self.assertEqual(jpgs[0].read_bytes(), fixture)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_error_de_lectura_inyectado(self):
        """Un OSError a mitad de lectura produce FALLIDO y limpia el parcial."""
        fixture = _jpeg_sin_eoi(b"\x34" * (70 * 1024))
        fuente = self.src_dir / "eolectura.jpg"
        fuente.write_bytes(fixture)

        import builtins
        from unittest import mock
        real_open = builtins.open

        class FallaTrasN:
            def __init__(self, f): self._f = f; self._n = 0
            def read(self, n=-1):
                self._n += 1
                if self._n >= 2:
                    raise OSError("disco con errores simulado")
                return self._f.read(n)
            def seek(self, o, w=0): return self._f.seek(o, w)
            def __enter__(self): return self
            def __exit__(self, *a): return self._f.__exit__(*a)

        def fake_open(path, mode="r", *a, **k):
            f = real_open(path, mode, *a, **k)
            if mode == "rb" and str(path).endswith("eolectura.jpg"):
                return FallaTrasN(f)
            return f

        from core.scanner import ArchivoEncontrado as AE, ResultadoRecuperacion
        entrada = AE(ruta=str(fuente), nombre="eolectura.jpg", extension="jpg",
                     tamano=len(fixture), offset=0, categoria="Imágenes", descripcion="Imagen JPEG")
        r = RecuperadorArchivos()
        with mock.patch.object(builtins, "open", fake_open):
            recuperados, fallidos = r.recuperar_archivos([entrada], self.dest.name)
        self.assertEqual((recuperados, fallidos), (0, 1))
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.FALLIDO)
        self.assertEqual(entrada.evidencia.bytes_escritos, 0)
        self.assertEqual(list(Path(self.dest.name).rglob("*.jpg")), [])

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_cancelacion_tras_primer_bloque(self):
        """Cancelar justo después del primer bloque de 64 KiB: PARCIAL y limpieza."""
        fixture = _jpeg_sin_eoi(b"\x00" * (128 * 1024))
        fuente = self.src_dir / "cancel_borde.jpg"
        fuente.write_bytes(fixture)

        import builtins
        from unittest import mock
        real_open = builtins.open

        r = RecuperadorArchivos()

        class CancelaTrasPrimero:
            def __init__(self, f): self._f = f; self._n = 0
            def read(self, n=-1):
                self._n += 1
                datos = self._f.read(n)
                if self._n == 1:
                    r.cancelar()
                return datos
            def seek(self, o, w=0): return self._f.seek(o, w)
            def __enter__(self): return self
            def __exit__(self, *a): return self._f.__exit__(*a)

        def fake_open(path, mode="r", *a, **k):
            f = real_open(path, mode, *a, **k)
            if mode == "rb" and str(path).endswith("cancel_borde.jpg"):
                return CancelaTrasPrimero(f)
            return f

        from core.scanner import ArchivoEncontrado as AE, ResultadoRecuperacion
        entrada = AE(ruta=str(fuente), nombre="cancel_borde.jpg", extension="jpg",
                     tamano=len(fixture), offset=0, categoria="Imágenes", descripcion="Imagen JPEG")
        with mock.patch.object(builtins, "open", fake_open):
            recuperados, fallidos = r.recuperar_archivos([entrada], self.dest.name)
        from core.scanner import ResultadoRecuperacion as RR
        self.assertIn(entrada.evidencia.resultado, (RR.PARCIAL,))
        self.assertEqual(entrada.evidencia.bytes_escritos, 0)
        self.assertEqual(list(Path(self.dest.name).rglob("*.jpg")), [])

    def test_memoria_acotada_en_jpeg_grande(self):
        """El payload JPEG no se acumula en memoria proporcionalmente al tamaño."""
        import tracemalloc
        grande = _jpeg_sintetico(b"\xAB" * (16 * 1024 * 1024))
        fuente = self.src_dir / "grande_mem.jpg"
        fuente.write_bytes(grande)

        from core.scanner import ArchivoEncontrado as AE
        entrada = AE(ruta=str(fuente), nombre="grande_mem.jpg", extension="jpg",
                     tamano=len(grande), offset=0, categoria="Imágenes", descripcion="Imagen JPEG")
        r = RecuperadorArchivos()
        dest_shm = tempfile.mkdtemp(dir=DEST_TMPFS) if DEST_TMPFS.is_dir() else tempfile.mkdtemp()
        try:
            tracemalloc.start()
            snapshot_antes = tracemalloc.take_snapshot()
            recuperados, fallidos = r.recuperar_archivos([entrada], dest_shm)
            actual, pico = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        finally:
            import shutil; shutil.rmtree(dest_shm, ignore_errors=True)

        self.assertEqual((recuperados, fallidos), (1, 0))
        # Con 16 MiB de fixture, el pico de Python debe ser muy inferior
        self.assertLess(pico, 8 * 1024 * 1024, f"pico de memoria alto: {pico} bytes")

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_estado_no_jpeg_es_estimado(self):
        """Formatos sin validador estructural nunca se marcan como completos."""
        fuente = self.src_dir / "datos.bin"
        fuente.write_bytes(b"PK\x03\x04" + b"\x11" * 128)
        from core.scanner import ArchivoEncontrado as AE, ResultadoRecuperacion
        entrada = AE(ruta=str(fuente), nombre="datos.zip", extension="zip",
                     tamano=132, offset=0, categoria="Comprimidos", descripcion="Archivo ZIP")
        r = RecuperadorArchivos()
        recuperados, fallidos = r.recuperar_archivos([entrada], self.dest.name)
        self.assertEqual((recuperados, fallidos), (1, 0))
        self.assertIsNotNone(entrada.evidencia)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.ESTIMADO)
        self.assertIsNone(entrada.evidencia.estructura_validada)
        zips = list(Path(self.dest.name).rglob("*.zip"))
        self.assertEqual(entrada.evidencia.bytes_escritos, zips[0].stat().st_size)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_bytes_escritos_coinciden_con_archivo(self):
        """Tras éxito, bytes_escritos debe igualar el tamaño real del archivo."""
        fixture = _jpeg_sintetico(b"PAYLOAD" * 16)
        fuente = self.src_dir / "exacto.jpg"
        fuente.write_bytes(fixture)
        from core.scanner import ArchivoEncontrado as AE
        entrada = AE(ruta=str(fuente), nombre="exacto.jpg", extension="jpg",
                     tamano=len(fixture), offset=0, categoria="Imágenes", descripcion="Imagen JPEG")
        r = RecuperadorArchivos()
        recuperados, fallidos = r.recuperar_archivos([entrada], self.dest.name)
        self.assertEqual((recuperados, fallidos), (1, 0))
        jpgs = list(Path(self.dest.name).rglob("*.jpg"))
        self.assertEqual(entrada.evidencia.bytes_escritos, jpgs[0].stat().st_size)
        self.assertEqual(jpgs[0].read_bytes(), fixture)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_jpeg_con_bytes_sobrantes(self):
        """JPEG con FF D9 conocido y bytes al final: debe terminar en FF D9."""
        fixture = _jpeg_sintetico(b"TRAILING-TEST-PAYLOAD" * 4)
        fuente = self.src_dir / "con_sobrante.jpg"
        fuente.write_bytes(fixture + b"\xCC" * 64)

        from core.scanner import EscaneoProfundo
        escaner = EscaneoProfundo()
        escaner.iniciar(str(self.src_dir))
        escaner._hilo.join(timeout=5)
        detectados = [a for a in escaner.obtener_archivos() if a.nombre == "con_sobrante.jpg"]
        self.assertEqual(len(detectados), 1)

        r = RecuperadorArchivos()
        recuperados, fallidos = r.recuperar_archivos(detectados, self.dest.name)
        self.assertEqual((recuperados, fallidos), (1, 0))
        jpgs = list(Path(self.dest.name).rglob("*.jpg"))
        self.assertEqual(len(jpgs), 1)
        recuperado = jpgs[0].read_bytes()
        self.assertEqual(recuperado, fixture)
        self.assertTrue(detectados[0].tamano_exacto)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_jpeg_ffd9_partido_entre_buffers(self):
        """FF D9 straddling the 64 KiB read window must still be found."""
        # Estructura válida cuyo marcador FF D9 cruza el límite de 64 KiB
        app = _segmento(b"\xe0", b"JFIF\x00fake")
        sos = _segmento(b"\xda", b"\x01\x01\x00\x00\x3f\x00")
        prefijo_len = 2 + len(app) + len(sos)
        scan_len = (64 * 1024 - 1) - prefijo_len  # FF de EOI queda en el byte 65535
        fixture = b"\xff\xd8" + app + sos + b"\x00" * scan_len + b"\xff\xd9" + b"\xEE" * 32
        fuente = self.src_dir / "partido.jpg"
        fuente.write_bytes(fixture)

        from core.scanner import ArchivoEncontrado as AE
        entrada = AE(ruta=str(fuente), nombre="partido.jpg", extension="jpg",
                     tamano=len(fixture) - 32, offset=0, categoria="Imágenes",
                     descripcion="Imagen JPEG")
        r = RecuperadorArchivos()
        recuperados, fallidos = r.recuperar_archivos([entrada], self.dest.name)
        self.assertEqual((recuperados, fallidos), (1, 0))
        jpgs = list(Path(self.dest.name).rglob("*.jpg"))
        recuperado = jpgs[0].read_bytes()
        esperado = fixture[:-32]
        self.assertEqual(recuperado, esperado)
        self.assertTrue(entrada.tamano_exacto)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_jpeg_sin_ffd9_no_es_exacto(self):
        """JPEG sin FF D9: no debe marcarse como recuperación exacta."""
        fixture = _jpeg_sin_eoi(b"SIN-FIN" * 16 + b"\xAA" * 64)
        fuente = self.src_dir / "sin_fin.jpg"
        fuente.write_bytes(fixture)

        from core.scanner import ArchivoEncontrado as AE
        entrada = AE(ruta=str(fuente), nombre="sin_fin.jpg", extension="jpg",
                     tamano=len(fixture), offset=0, categoria="Imágenes",
                     descripcion="Imagen JPEG")
        r = RecuperadorArchivos()
        recuperados, fallidos = r.recuperar_archivos([entrada], self.dest.name)
        self.assertEqual((recuperados, fallidos), (1, 0))
        self.assertFalse(entrada.tamano_exacto)
        jpgs = list(Path(self.dest.name).rglob("*.jpg"))
        # Sin marcador, se conserva la extracción parcial estimada (hasta EOF/máximo)
        self.assertEqual(jpgs[0].read_bytes(), fixture)


class TestRecuperacionPNG(unittest.TestCase):
    """Pruebas del parser/stream PNG: válidos, truncados, CRC, longitudes,
    orden, E/S parcial, fronteras de bloque, cancelación y tope operativo."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src_dir = Path(self.tmp.name)
        self.dest = tempfile.TemporaryDirectory(dir=DEST_TMPFS if DEST_TMPFS.is_dir() else None)

    def tearDown(self):
        self.tmp.cleanup()
        self.dest.cleanup()

    def _recuperar(self, fixture_bytes, nombre="imagen.png", **kw):
        fuente = self.src_dir / nombre
        fuente.write_bytes(fixture_bytes)
        from core.scanner import ArchivoEncontrado as AE
        entrada = AE(ruta=str(fuente), nombre=nombre, extension="png",
                     tamano=len(fixture_bytes), offset=kw.get("offset", 0),
                     categoria="Imágenes", descripcion="Imagen PNG")
        r = RecuperadorArchivos()
        resultado = r.recuperar_archivos([entrada], self.dest.name)
        return entrada, resultado

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_valido_bytes_exactos(self):
        """PNG válido: extracción byte-exacta, estructura validada, AMBIGUO."""
        from core.scanner import ResultadoRecuperacion
        fixture = _png_sintetico()
        entrada, (recuperados, fallidos) = self._recuperar(fixture)
        self.assertEqual((recuperados, fallidos), (1, 0))
        self.assertTrue(entrada.tamano_exacto)
        self.assertIsNotNone(entrada.evidencia)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.AMBIGUO)
        self.assertTrue(entrada.evidencia.estructura_validada)
        pngs = list(Path(self.dest.name).rglob("*.png"))
        self.assertEqual(len(pngs), 1)
        self.assertEqual(pngs[0].read_bytes(), fixture)
        self.assertEqual(entrada.evidencia.bytes_escritos, pngs[0].stat().st_size)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_truncado_sin_iend_estimado(self):
        """PNG sin IEND: límite no demostrado, resultado ESTIMADO."""
        from core.scanner import ResultadoRecuperacion
        fixture = _png_sintetico(con_iend=False)
        entrada, (recuperados, fallidos) = self._recuperar(fixture, "trunc.png")
        self.assertEqual((recuperados, fallidos), (1, 0))
        self.assertFalse(entrada.tamano_exacto)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.ESTIMADO)
        pngs = list(Path(self.dest.name).rglob("*.png"))
        self.assertEqual(pngs[0].read_bytes(), fixture)
        self.assertEqual(entrada.evidencia.bytes_escritos, pngs[0].stat().st_size)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_crc_invalida_no_es_exacto(self):
        """CRC de IDAT incorrecta: estructura inválida, ESTIMADO."""
        from core.scanner import ResultadoRecuperacion
        partes = [
            b"\x89PNG\r\n\x1a\n",
            _chunk_png(b"IHDR", _PNG_IHDR),
            _chunk_png(b"IDAT", _PNG_IDAT, crc_forzada=0xDEADBEEF),
            _chunk_png(b"IEND", b""),
        ]
        fixture = b"".join(partes)
        entrada, (recuperados, fallidos) = self._recuperar(fixture, "crc.png")
        self.assertEqual((recuperados, fallidos), (1, 0))
        self.assertFalse(entrada.tamano_exacto)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.ESTIMADO)
        self.assertEqual(entrada.evidencia.limite_alcanzado, "estructura_invalida")

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_crc_iend_invalida_no_cierra(self):
        """Una CRC inválida en IEND impide darlo por válido."""
        partes = [
            b"\x89PNG\r\n\x1a\n",
            _chunk_png(b"IHDR", _PNG_IHDR),
            _chunk_png(b"IDAT", _PNG_IDAT),
            _chunk_png(b"IEND", b"", crc_forzada=0x00000000),
        ]
        entrada, (recuperados, fallidos) = self._recuperar(b"".join(partes), "crc_iend.png")
        self.assertEqual((recuperados, fallidos), (1, 0))
        self.assertFalse(entrada.tamano_exacto)
        self.assertEqual(entrada.evidencia.limite_alcanzado, "estructura_invalida")

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_longitud_con_bit_reservado(self):
        """Una longitud con el bit superior activo se rechaza sin asignar memoria."""
        from core.scanner import ResultadoRecuperacion
        fixture = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 0x80000001) + b"IHDR" + b"\x00" * 13
        entrada, (recuperados, fallidos) = self._recuperar(fixture, "bit.png")
        self.assertEqual((recuperados, fallidos), (1, 0))
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.ESTIMADO)
        self.assertEqual(entrada.evidencia.limite_alcanzado, "estructura_invalida")
        # Solo la firma + la longitud (12 bytes) resultan consumidos.
        self.assertLessEqual(entrada.evidencia.bytes_escritos, 12)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_longitud_gigante_no_asigna_memoria(self):
        """Una longitud declarada de 1 GiB con datos mínimos no reserva memoria."""
        import tracemalloc
        # IHDR declara 1 GiB de datos pero el fichero termina inmediatamente:
        # el parser debe consumir hasta EOF sin reservar 1 GiB.
        fixture = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 0x40000000) + b"IHDR"
        fuente = self.src_dir / "gigante.png"
        fuente.write_bytes(fixture)
        from core.scanner import ArchivoEncontrado as AE
        entrada = AE(ruta=str(fuente), nombre="gigante.png", extension="png",
                     tamano=len(fixture), offset=0, categoria="Imágenes",
                     descripcion="Imagen PNG")
        r = RecuperadorArchivos()
        tracemalloc.start()
        r.recuperar_archivos([entrada], self.dest.name)
        _, pico = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertLess(pico, 4 * 1024 * 1024, f"pico de memoria alto: {pico} bytes")

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_iend_antes_de_idat_invalido(self):
        """IEND sin IDAT previo no es un cierre válido."""
        fixture = b"\x89PNG\r\n\x1a\n" + _chunk_png(b"IHDR", _PNG_IHDR) + _chunk_png(b"IEND", b"")
        entrada, (recuperados, fallidos) = self._recuperar(fixture, "iend_temprano.png")
        self.assertEqual((recuperados, fallidos), (1, 0))
        self.assertFalse(entrada.tamano_exacto)
        self.assertEqual(entrada.evidencia.limite_alcanzado, "estructura_invalida")

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_iend_longitud_no_cero_invalido(self):
        """IEND con longitud distinta de 0 no es válido."""
        fixture = (b"\x89PNG\r\n\x1a\n" + _chunk_png(b"IHDR", _PNG_IHDR)
                   + _chunk_png(b"IDAT", _PNG_IDAT) + _chunk_png(b"IEND", b"\x00"))
        entrada, (recuperados, fallidos) = self._recuperar(fixture, "iend_len.png")
        self.assertEqual((recuperados, fallidos), (1, 0))
        self.assertFalse(entrada.tamano_exacto)
        self.assertEqual(entrada.evidencia.limite_alcanzado, "estructura_invalida")

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_ignora_bytes_tras_iend(self):
        """Los bytes posteriores al IEND no forman parte del archivo recuperado."""
        fixture = _png_sintetico()
        entrada, (recuperados, fallidos) = self._recuperar(fixture + b"\xCC" * 128, "sobrante.png")
        self.assertEqual((recuperados, fallidos), (1, 0))
        pngs = list(Path(self.dest.name).rglob("*.png"))
        self.assertEqual(pngs[0].read_bytes(), fixture)
        self.assertTrue(entrada.tamano_exacto)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_iend_cruza_frontera_de_64kib(self):
        """El chunk IEND repartido entre dos ventanas de 64 KiB se detecta igual."""
        base = 8 + (4 + 4 + len(_PNG_IHDR) + 4)  # firma + IHDR
        # Posición de inicio del chunk IEND tras un IDAT de relleno.
        inicio_iend = 65533
        relleno_idat = inicio_iend - base - 12  # 12 = cabecera+CRC del propio IDAT
        idat_grande = b"\x00" * relleno_idat
        fixture = (b"\x89PNG\r\n\x1a\n" + _chunk_png(b"IHDR", _PNG_IHDR)
                   + _chunk_png(b"IDAT", idat_grande) + _chunk_png(b"IEND", b""))
        self.assertEqual(len(fixture) - 12, inicio_iend)
        entrada, (recuperados, fallidos) = self._recuperar(fixture, "borde.png")
        self.assertEqual((recuperados, fallidos), (1, 0))
        pngs = list(Path(self.dest.name).rglob("*.png"))
        self.assertEqual(pngs[0].read_bytes(), fixture)
        self.assertTrue(entrada.tamano_exacto)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_lectura_corta_no_corrompe(self):
        """Lecturas que devuelven pocos bytes no rompen la salida PNG."""
        fixture = _png_sintetico()
        fuente = self.src_dir / "corta.png"
        fuente.write_bytes(fixture)

        import builtins
        from unittest import mock
        real_open = builtins.open

        class LecturaCorta:
            def __init__(self, f): self._f = f
            def read(self, n=-1): return self._f.read(min(n if n > 0 else 7, 7))
            def seek(self, o, w=0): return self._f.seek(o, w)
            def write(self, b): return self._f.write(b)
            def __enter__(self): return self
            def __exit__(self, *a): return self._f.__exit__(*a)

        def fake_open(path, mode="r", *a, **k):
            f = real_open(path, mode, *a, **k)
            if mode == "rb" and str(path).endswith("corta.png"):
                return LecturaCorta(f)
            return f

        from core.scanner import ArchivoEncontrado as AE
        entrada = AE(ruta=str(fuente), nombre="corta.png", extension="png",
                     tamano=len(fixture), offset=0, categoria="Imágenes", descripcion="Imagen PNG")
        r = RecuperadorArchivos()
        with mock.patch.object(builtins, "open", fake_open):
            recuperados, fallidos = r.recuperar_archivos([entrada], self.dest.name)
        self.assertEqual((recuperados, fallidos), (1, 0))
        pngs = list(Path(self.dest.name).rglob("*.png"))
        self.assertEqual(pngs[0].read_bytes(), fixture)
        self.assertTrue(entrada.tamano_exacto)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_cancelacion_tras_primer_bloque(self):
        """Cancelar tras el primer bloque deja PARCIAL, bytes_escritos=0 y limpia."""
        # Sin IEND para forzar al menos dos ventanas de lectura.
        fixture = _png_sintetico(datos_idat=b"\x00" * (128 * 1024), con_iend=False)
        fuente = self.src_dir / "cancel.png"
        fuente.write_bytes(fixture)

        import builtins
        from unittest import mock
        real_open = builtins.open
        r = RecuperadorArchivos()

        class CancelaTrasPrimero:
            def __init__(self, f): self._f = f; self._n = 0
            def read(self, n=-1):
                self._n += 1
                datos = self._f.read(n)
                if self._n == 1:
                    r.cancelar()
                return datos
            def seek(self, o, w=0): return self._f.seek(o, w)
            def __enter__(self): return self
            def __exit__(self, *a): return self._f.__exit__(*a)

        def fake_open(path, mode="r", *a, **k):
            f = real_open(path, mode, *a, **k)
            if mode == "rb" and str(path).endswith("cancel.png"):
                return CancelaTrasPrimero(f)
            return f

        from core.scanner import ArchivoEncontrado as AE, ResultadoRecuperacion
        entrada = AE(ruta=str(fuente), nombre="cancel.png", extension="png",
                     tamano=len(fixture), offset=0, categoria="Imágenes", descripcion="Imagen PNG")
        with mock.patch.object(builtins, "open", fake_open):
            r.recuperar_archivos([entrada], self.dest.name)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.PARCIAL)
        self.assertEqual(entrada.evidencia.bytes_escritos, 0)
        self.assertEqual(list(Path(self.dest.name).rglob("*.png")), [])

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_limite_operativo_respetado(self):
        """El tope operativo se respeta y se reporta como tamano_maximo."""
        from unittest import mock
        from core.scanner import EscaneoProfundo, ResultadoRecuperacion
        fixture = _png_sintetico(datos_idat=b"\x00" * (2 * 1024 * 1024), con_iend=False)
        fuente = self.src_dir / "limite.png"
        fuente.write_bytes(fixture)
        from core.scanner import ArchivoEncontrado as AE
        entrada = AE(ruta=str(fuente), nombre="limite.png", extension="png",
                     tamano=len(fixture), offset=0, categoria="Imágenes", descripcion="Imagen PNG")
        r = RecuperadorArchivos()
        with mock.patch.object(EscaneoProfundo, "TAMANO_MAX_ARCHIVO", 1024 * 1024):
            recuperados, fallidos = r.recuperar_archivos([entrada], self.dest.name)
        self.assertEqual((recuperados, fallidos), (1, 0))
        pngs = list(Path(self.dest.name).rglob("*.png"))
        self.assertLessEqual(pngs[0].stat().st_size, 1024 * 1024)
        self.assertEqual(entrada.evidencia.limite_alcanzado, "tamano_maximo")
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.ESTIMADO)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_error_de_lectura_limpia_parcial(self):
        """Un OSError a mitad de lectura produce FALLIDO y limpia el parcial."""
        fixture = _png_sintetico(datos_idat=b"\x00" * (70 * 1024), con_iend=False)
        fuente = self.src_dir / "eopng.png"
        fuente.write_bytes(fixture)

        import builtins
        from unittest import mock
        real_open = builtins.open

        class FallaTrasN:
            def __init__(self, f): self._f = f; self._n = 0
            def read(self, n=-1):
                self._n += 1
                if self._n >= 2:
                    raise OSError("disco con errores simulado")
                return self._f.read(n)
            def seek(self, o, w=0): return self._f.seek(o, w)
            def __enter__(self): return self
            def __exit__(self, *a): return self._f.__exit__(*a)

        def fake_open(path, mode="r", *a, **k):
            f = real_open(path, mode, *a, **k)
            if mode == "rb" and str(path).endswith("eopng.png"):
                return FallaTrasN(f)
            return f

        from core.scanner import ArchivoEncontrado as AE, ResultadoRecuperacion
        entrada = AE(ruta=str(fuente), nombre="eopng.png", extension="png",
                     tamano=len(fixture), offset=0, categoria="Imágenes", descripcion="Imagen PNG")
        r = RecuperadorArchivos()
        with mock.patch.object(builtins, "open", fake_open):
            recuperados, fallidos = r.recuperar_archivos([entrada], self.dest.name)
        self.assertEqual((recuperados, fallidos), (0, 1))
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.FALLIDO)
        self.assertEqual(entrada.evidencia.bytes_escritos, 0)
        self.assertEqual(list(Path(self.dest.name).rglob("*.png")), [])

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_no_sobrescribe_preexistente(self):
        """La creación exclusiva de PNG no sobrescribe un archivo preexistente."""
        fixture = _png_sintetico()
        previo = Path(self.dest.name) / "Imágenes" / "imagen.png"
        previo.parent.mkdir(parents=True, exist_ok=True)
        previo.write_bytes(b"ORIGINAL")
        entrada, (recuperados, fallidos) = self._recuperar(fixture)
        self.assertEqual((recuperados, fallidos), (0, 1))
        self.assertEqual(previo.read_bytes(), b"ORIGINAL")

    # --- Validación estructural endurecida (IDAT consecutivos, IHDR) --------

    def _rechazo_esperado(self, fixture, nombre):
        """Comprueba que un PNG inválido queda ESTIMADO/estructura_invalida."""
        from core.scanner import ResultadoRecuperacion
        entrada, (recuperados, fallidos) = self._recuperar(fixture, nombre)
        self.assertEqual((recuperados, fallidos), (1, 0))
        self.assertFalse(entrada.tamano_exacto)
        self.assertIsNotNone(entrada.evidencia)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.ESTIMADO)
        self.assertEqual(entrada.evidencia.limite_alcanzado, "estructura_invalida")
        return entrada

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_idat_multiple_consecutivo_valido(self):
        """Varios IDAT consecutivos forman un PNG válido byte-exacto."""
        from core.scanner import ResultadoRecuperacion
        fixture = _png_con_chunks([
            (b"IHDR", _ihdr_png()),
            (b"IDAT", b"\x00\x11\x22"),
            (b"IDAT", b"\x33\x44\x55\x66"),
            (b"IEND", b""),
        ])
        entrada, (recuperados, fallidos) = self._recuperar(fixture, "idat_multi.png")
        self.assertEqual((recuperados, fallidos), (1, 0))
        self.assertTrue(entrada.tamano_exacto)
        self.assertTrue(entrada.evidencia.estructura_validada)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.AMBIGUO)
        pngs = list(Path(self.dest.name).rglob("*.png"))
        self.assertEqual(pngs[0].read_bytes(), fixture)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_auxiliar_antes_de_idat_valido(self):
        """Un chunk auxiliar (gAMA) antes de IDAT no invalida el PNG."""
        fixture = _png_con_chunks([
            (b"IHDR", _ihdr_png()),
            (b"gAMA", b"\x00\x01\x86\xa0"),
            (b"IDAT", _PNG_IDAT),
            (b"IEND", b""),
        ])
        entrada, (recuperados, fallidos) = self._recuperar(fixture, "gama.png")
        self.assertEqual((recuperados, fallidos), (1, 0))
        self.assertTrue(entrada.tamano_exacto)
        pngs = list(Path(self.dest.name).rglob("*.png"))
        self.assertEqual(pngs[0].read_bytes(), fixture)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_auxiliar_entre_idat_es_rechazado(self):
        """IDAT - auxiliar - IDAT se rechaza (IDAT no consecutivos)."""
        fixture = _png_con_chunks([
            (b"IHDR", _ihdr_png()),
            (b"IDAT", b"\x11\x22"),
            (b"tEXt", b"clave\x00valor"),
            (b"IDAT", b"\x33\x44"),
            (b"IEND", b""),
        ])
        self._rechazo_esperado(fixture, "idat_cortado.png")

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_auxiliar_tras_idat_antes_de_iend_valido(self):
        """Un auxiliar tras el último IDAT y antes de IEND es válido."""
        fixture = _png_con_chunks([
            (b"IHDR", _ihdr_png()),
            (b"IDAT", _PNG_IDAT),
            (b"tEXt", b"clave\x00valor"),
            (b"IEND", b""),
        ])
        entrada, (recuperados, fallidos) = self._recuperar(fixture, "texto.png")
        self.assertEqual((recuperados, fallidos), (1, 0))
        self.assertTrue(entrada.tamano_exacto)
        pngs = list(Path(self.dest.name).rglob("*.png"))
        self.assertEqual(pngs[0].read_bytes(), fixture)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_ancho_cero_rechazado(self):
        self._rechazo_esperado(_png_con_chunks([
            (b"IHDR", _ihdr_png(ancho=0, alto=5)),
            (b"IDAT", _PNG_IDAT), (b"IEND", b""),
        ]), "ancho0.png")

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_alto_cero_rechazado(self):
        self._rechazo_esperado(_png_con_chunks([
            (b"IHDR", _ihdr_png(ancho=5, alto=0)),
            (b"IDAT", _PNG_IDAT), (b"IEND", b""),
        ]), "alto0.png")

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_profundidad_invalida_para_color_rechazada(self):
        # color 2 (RGB) solo admite 8 o 16; 4 es inválido.
        self._rechazo_esperado(_png_con_chunks([
            (b"IHDR", _ihdr_png(profundidad=4, color=2)),
            (b"IDAT", _PNG_IDAT), (b"IEND", b""),
        ]), "prof_inv.png")

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_color_type_invalido_rechazado(self):
        # 1 no es un tipo de color PNG permitido.
        self._rechazo_esperado(_png_con_chunks([
            (b"IHDR", _ihdr_png(profundidad=8, color=1)),
            (b"IDAT", _PNG_IDAT), (b"IEND", b""),
        ]), "color_inv.png")

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_compresion_invalida_rechazada(self):
        self._rechazo_esperado(_png_con_chunks([
            (b"IHDR", _ihdr_png(compresion=1)),
            (b"IDAT", _PNG_IDAT), (b"IEND", b""),
        ]), "comp.png")

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_filtro_invalido_rechazado(self):
        self._rechazo_esperado(_png_con_chunks([
            (b"IHDR", _ihdr_png(filtro=1)),
            (b"IDAT", _PNG_IDAT), (b"IEND", b""),
        ]), "filtro.png")

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_entrelazado_invalido_rechazado(self):
        self._rechazo_esperado(_png_con_chunks([
            (b"IHDR", _ihdr_png(entrelazado=2)),
            (b"IDAT", _PNG_IDAT), (b"IEND", b""),
        ]), "inter2.png")

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_entrelazado_uno_valido(self):
        """Entrelazado Adam7 (1) es válido."""
        fixture = _png_con_chunks([
            (b"IHDR", _ihdr_png(entrelazado=1)),
            (b"IDAT", _PNG_IDAT), (b"IEND", b""),
        ])
        entrada, (recuperados, fallidos) = self._recuperar(fixture, "adam7.png")
        self.assertEqual((recuperados, fallidos), (1, 0))
        self.assertTrue(entrada.tamano_exacto)

    def test_png_matriz_profundidad_color(self):
        """Toda combinación válida de profundidad/tipo de color se acepta y
        toda combinación inválida se rechaza (sin decodificar la imagen)."""
        validas = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8),
                   4: (8, 16), 6: (8, 16)}
        for color in (0, 2, 3, 4, 6, 1, 5, 7):
            for prof in (1, 2, 4, 8, 16):
                valida = prof in validas.get(color, ())
                chunks = [(b"IHDR", _ihdr_png(1, 1, prof, color))]
                if color == 3:
                    chunks.append((b"PLTE", b"\x00\x00\x00"))
                chunks += [(b"IDAT", _PNG_IDAT), (b"IEND", b"")]
                fixture = _png_con_chunks(chunks)
                nombre = f"c{color}_p{prof}.png"
                if valida:
                    entrada, (rec, fal) = self._recuperar(fixture, nombre)
                    self.assertEqual((rec, fal), (1, 0), f"color={color} prof={prof}")
                    self.assertTrue(entrada.tamano_exacto, f"color={color} prof={prof}")
                else:
                    entrada = self._rechazo_esperado(fixture, nombre)
                    self.assertFalse(entrada.tamano_exacto)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_ihdr_con_crc_mala_no_valida_semantica(self):
        """Con CRC de IHDR inválida no se llega a validar su semántica."""
        from core.scanner import ResultadoRecuperacion
        ihdr_chunk = _chunk_png(b"IHDR", _ihdr_png(), crc_forzada=0x12345678)
        fixture = b"\x89PNG\r\n\x1a\n" + ihdr_chunk + _chunk_png(b"IDAT", _PNG_IDAT) + _chunk_png(b"IEND", b"")
        entrada = self._rechazo_esperado(fixture, "ihdr_crc.png")
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.ESTIMADO)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_ihdr_valido_pero_datos_absurdos_no_falla(self):
        """La validación no es decodificación: dimensiones grandes con CRC válida pasan
        mientras el framing sea correcto (honestidad: sigue AMBIGUO)."""
        from core.scanner import ResultadoRecuperacion
        fixture = _png_con_chunks([
            (b"IHDR", _ihdr_png(ancho=0xFFFF, alto=0xFFFF, profundidad=8, color=2)),
            (b"IDAT", _PNG_IDAT), (b"IEND", b""),
        ])
        entrada, (recuperados, fallidos) = self._recuperar(fixture, "grande.png")
        self.assertEqual((recuperados, fallidos), (1, 0))
        self.assertTrue(entrada.tamano_exacto)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.AMBIGUO)
        self.assertTrue(entrada.evidencia.estructura_validada)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_regresion_jpeg_inalterado_por_cambios_png(self):
        """El endurecimiento PNG no altera la recuperación JPEG existente."""
        from core.scanner import ResultadoRecuperacion
        fixture = _jpeg_sintetico(b"REGRESION-JPEG-PAYLOAD" * 8)
        fuente = self.src_dir / "regresion.jpg"
        fuente.write_bytes(fixture)
        from core.scanner import ArchivoEncontrado as AE
        entrada = AE(ruta=str(fuente), nombre="regresion.jpg", extension="jpg",
                     tamano=len(fixture), offset=0, categoria="Imágenes",
                     descripcion="Imagen JPEG")
        r = RecuperadorArchivos()
        recuperados, fallidos = r.recuperar_archivos([entrada], self.dest.name)
        self.assertEqual((recuperados, fallidos), (1, 0))
        jpgs = list(Path(self.dest.name).rglob("*.jpg"))
        self.assertEqual(jpgs[0].read_bytes(), fixture)
        self.assertTrue(entrada.tamano_exacto)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.AMBIGUO)

    def _generar_pngs_referencia(self, carpeta: Path) -> dict:
        """Genera PNG reales con PIL del sistema (no con el venv).

        Devuelve rutas o {} si PIL no está disponible (test omitido).
        """
        import subprocess, shutil
        python = shutil.which("python3")
        if python is None:
            return {}
        script = carpeta / "_genpng.py"
        script.write_text(
            "from PIL import Image\n"
            f"Image.new('RGB', (128, 128), (10, 120, 200)).save(r'{carpeta}/rgb.png', 'PNG')\n"
            f"Image.new('RGBA', (64, 64), (1, 2, 3, 4)).save(r'{carpeta}/rgba.png', 'PNG', optimize=True)\n"
            f"Image.new('L', (256, 256), 128).save(r'{carpeta}/interlazado.png', 'PNG', interlace=True)\n"
        )
        try:
            subprocess.run([python, str(script)], check=True, capture_output=True, timeout=60)
        except Exception:
            return {}
        finally:
            try:
                script.unlink()
            except OSError:
                pass
        rutas = {
            "rgb": carpeta / "rgb.png",
            "rgba": carpeta / "rgba.png",
            "interlazado": carpeta / "interlazado.png",
        }
        return {k: v for k, v in rutas.items() if v.is_file()}

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_png_real_bytes_exactos(self):
        """PNG reales (RGB, RGBA, entrelazado): extracción byte-exacta y AMBIGUO."""
        from core.scanner import ResultadoRecuperacion
        rutas = self._generar_pngs_referencia(self.src_dir)
        if not rutas:
            self.skipTest("PIL del sistema no disponible para generar PNG real")
        for clave, fuente in rutas.items():
            esperado = fuente.read_bytes()
            from core.scanner import ArchivoEncontrado as AE
            entrada = AE(ruta=str(fuente), nombre=fuente.name, extension="png",
                         tamano=len(esperado), offset=0, categoria="Imágenes",
                         descripcion="Imagen PNG")
            r = RecuperadorArchivos()
            dest_i = tempfile.mkdtemp(dir=DEST_TMPFS)
            try:
                recuperados, fallidos = r.recuperar_archivos([entrada], dest_i)
                self.assertEqual((recuperados, fallidos), (1, 0), f"{clave}: fallo")
                pngs = list(Path(dest_i).rglob("*.png"))
                self.assertEqual(len(pngs), 1, f"{clave}: salidas inesperadas")
                recuperado = pngs[0].read_bytes()
                print(f"\n[PNG REAL] {clave}: original={len(esperado)}B "
                      f"recuperado={len(recuperado)}B iguales={recuperado == esperado} "
                      f"resultado={entrada.evidencia.resultado}")
                self.assertEqual(recuperado, esperado, f"{clave}: bytes no idénticos")
                self.assertTrue(entrada.tamano_exacto, f"{clave}: no exacto")
                self.assertTrue(entrada.evidencia.estructura_validada, f"{clave}: no validada")
                self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.AMBIGUO)
            finally:
                import shutil; shutil.rmtree(dest_i, ignore_errors=True)


class TestRecuperacionBMP(unittest.TestCase):
    """Pruebas del parser/stream BMP: cabeceras, dimensiones, alineación,
    top-down, metadatos, rechazos, E/S parcial, cancelación y tope."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src_dir = Path(self.tmp.name)
        self.dest = tempfile.TemporaryDirectory(dir=DEST_TMPFS if DEST_TMPFS.is_dir() else None)

    def tearDown(self):
        self.tmp.cleanup()
        self.dest.cleanup()

    def _recuperar(self, fixture_bytes, nombre="imagen.bmp", **kw):
        fuente = self.src_dir / nombre
        fuente.write_bytes(fixture_bytes)
        from core.scanner import ArchivoEncontrado as AE
        entrada = AE(ruta=str(fuente), nombre=nombre, extension="bmp",
                     tamano=len(fixture_bytes), offset=kw.get("offset", 0),
                     categoria="Imágenes", descripcion="Imagen BMP")
        r = RecuperadorArchivos()
        resultado = r.recuperar_archivos([entrada], self.dest.name)
        return entrada, resultado

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_bmp_valido_bytes_exactos(self):
        """BMP 24bpp válido: extracción byte-exacta, AMBIGUO y estructura validada."""
        from core.scanner import ResultadoRecuperacion
        fixture = _bmp_sintetico(ancho=2, alto=2, bpp=24)
        entrada, (recuperados, fallidos) = self._recuperar(fixture)
        self.assertEqual((recuperados, fallidos), (1, 0))
        self.assertTrue(entrada.tamano_exacto)
        self.assertIsNotNone(entrada.evidencia)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.AMBIGUO)
        self.assertTrue(entrada.evidencia.estructura_validada)
        self.assertEqual(entrada.evidencia.limite_alcanzado, "limite_declarado")
        bmps = list(Path(self.dest.name).rglob("*.bmp"))
        self.assertEqual(len(bmps), 1)
        self.assertEqual(bmps[0].read_bytes(), fixture)
        self.assertEqual(entrada.evidencia.bytes_escritos, bmps[0].stat().st_size)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_bmp_minimo_1bpp(self):
        """BMP mínimo 1bpp 1x1: fila alineada a 4 bytes."""
        fixture = _bmp_sintetico(ancho=1, alto=1, bpp=1)
        # fila = ((1*1+31)//32)*4 = 4 bytes
        self.assertEqual(_stride_bmp(1, 1), 4)
        entrada, (recuperados, fallidos) = self._recuperar(fixture, "min.bmp")
        self.assertEqual((recuperados, fallidos), (1, 0))
        self.assertTrue(entrada.tamano_exacto)
        bmps = list(Path(self.dest.name).rglob("*.bmp"))
        self.assertEqual(bmps[0].read_bytes(), fixture)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_bmp_profundidades_y_alineacion(self):
        """Varias profundidades con cálculo de stride correcto."""
        for bpp in (1, 4, 8, 16, 24, 32):
            for ancho in (1, 3, 5, 7, 9):
                fixture = _bmp_sintetico(ancho=ancho, alto=3, bpp=bpp)
                dest_i = tempfile.mkdtemp(dir=DEST_TMPFS)
                try:
                    from core.scanner import ArchivoEncontrado as AE
                    fuente = self.src_dir / f"b{bpp}_a{ancho}.bmp"
                    fuente.write_bytes(fixture)
                    entrada = AE(ruta=str(fuente), nombre=fuente.name, extension="bmp",
                                 tamano=len(fixture), offset=0, categoria="Imágenes",
                                 descripcion="Imagen BMP")
                    r = RecuperadorArchivos()
                    recuperados, fallidos = r.recuperar_archivos([entrada], dest_i)
                    self.assertEqual((recuperados, fallidos), (1, 0), f"bpp={bpp} ancho={ancho}")
                    bmps = list(Path(dest_i).rglob("*.bmp"))
                    self.assertEqual(bmps[0].read_bytes(), fixture, f"bpp={bpp} ancho={ancho}")
                    self.assertTrue(entrada.tamano_exacto)
                finally:
                    import shutil; shutil.rmtree(dest_i, ignore_errors=True)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_bmp_top_down_alto_negativo(self):
        """Alto negativo (top-down) es válido."""
        fixture = _bmp_sintetico(ancho=3, alto=-4, bpp=24)
        entrada, (recuperados, fallidos) = self._recuperar(fixture, "topdown.bmp")
        self.assertEqual((recuperados, fallidos), (1, 0))
        self.assertTrue(entrada.tamano_exacto)
        bmps = list(Path(self.dest.name).rglob("*.bmp"))
        self.assertEqual(bmps[0].read_bytes(), fixture)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_bmp_metadatos_antes_de_pixeles(self):
        """Metadatos legítimos entre DIB y píxeles via offset declarado."""
        fixture = _bmp_sintetico(ancho=2, alto=2, bpp=24, metadatos=b"\xAB" * 14)
        entrada, (recuperados, fallidos) = self._recuperar(fixture, "meta.bmp")
        self.assertEqual((recuperados, fallidos), (1, 0))
        self.assertTrue(entrada.tamano_exacto)
        bmps = list(Path(self.dest.name).rglob("*.bmp"))
        self.assertEqual(bmps[0].read_bytes(), fixture)

    def _bmp_con_paleta(self, ancho, alto, bpp, entradas_paleta, tam_imagen):
        """Construye un BMP con tabla de colores entre la cabecera y los píxeles."""
        fila = _stride_bmp(ancho, bpp)
        pix = fila * alto
        pal = entradas_paleta * 4
        offset = 14 + 40 + pal
        total = offset + pix
        cabecera = b"BM" + struct.pack("<IHHI", total, 0, 0, offset)
        dib = struct.pack("<IiiHHIIiiII", 40, ancho, alto, 1, bpp, 0,
                          tam_imagen, 0, 0, 0, 0)
        return cabecera + dib + b"\x00" * pal + b"\x00" * pix

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_bmp_paleta_y_sizeimage_cero(self):
        """Paletas 1/4/8 bpp y biSizeImage=0 (permitido en BI_RGB)."""
        casos = [
            (8, 5, 1, 2, 0),
            (8, 5, 4, 16, 0),
            (8, 5, 8, 256, 0),
        ]
        for ancho, alto, bpp, entradas, sizeimg in casos:
            fixture = self._bmp_con_paleta(ancho, alto, bpp, entradas, sizeimg)
            dest_i = tempfile.mkdtemp(dir=DEST_TMPFS)
            try:
                from core.scanner import ArchivoEncontrado as AE
                fuente = self.src_dir / f"pal{bpp}.bmp"
                fuente.write_bytes(fixture)
                entrada = AE(ruta=str(fuente), nombre=fuente.name, extension="bmp",
                             tamano=len(fixture), offset=0, categoria="Imágenes",
                             descripcion="Imagen BMP")
                r = RecuperadorArchivos()
                recuperados, fallidos = r.recuperar_archivos([entrada], dest_i)
                self.assertEqual((recuperados, fallidos), (1, 0), f"bpp={bpp}")
                bmps = list(Path(dest_i).rglob("*.bmp"))
                self.assertEqual(bmps[0].read_bytes(), fixture, f"bpp={bpp}")
                self.assertTrue(entrada.tamano_exacto, f"bpp={bpp}: no exacto")
            finally:
                import shutil; shutil.rmtree(dest_i, ignore_errors=True)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_bmp_variantes_cabecera_dib(self):
        """Cabeceras DIB 40/52/56/108/124 (rellenadas) son aceptadas."""
        casos = [(40, 0), (52, 12), (56, 16), (108, 68), (124, 84)]
        for dib, ext_len in casos:
            fixture = _bmp_sintetico(ancho=2, alto=2, bpp=24, dib_size=dib,
                                     ext_dib=b"\x00" * ext_len)
            dest_i = tempfile.mkdtemp(dir=DEST_TMPFS)
            try:
                from core.scanner import ArchivoEncontrado as AE
                fuente = self.src_dir / f"dib{dib}.bmp"
                fuente.write_bytes(fixture)
                entrada = AE(ruta=str(fuente), nombre=fuente.name, extension="bmp",
                             tamano=len(fixture), offset=0, categoria="Imágenes",
                             descripcion="Imagen BMP")
                r = RecuperadorArchivos()
                recuperados, fallidos = r.recuperar_archivos([entrada], dest_i)
                self.assertEqual((recuperados, fallidos), (1, 0), f"DIB={dib}")
                bmps = list(Path(dest_i).rglob("*.bmp"))
                self.assertEqual(bmps[0].read_bytes(), fixture, f"DIB={dib}")
                self.assertTrue(entrada.tamano_exacto, f"DIB={dib}: no exacto")
            finally:
                import shutil; shutil.rmtree(dest_i, ignore_errors=True)

    # --- Rechazos conservadores -------------------------------------------

    def _rechazo_esperado(self, fixture, nombre):
        from core.scanner import ResultadoRecuperacion
        entrada, (recuperados, fallidos) = self._recuperar(fixture, nombre)
        self.assertEqual((recuperados, fallidos), (1, 0))
        self.assertFalse(entrada.tamano_exacto)
        self.assertIsNotNone(entrada.evidencia)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.ESTIMADO)
        self.assertEqual(entrada.evidencia.limite_alcanzado, "estructura_invalida")
        return entrada

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_bmp_firma_invalida_rechazada(self):
        self._rechazo_esperado(_bmp_sintetico(firma=b"ZZ"), "firma.bmp")

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_bmp_cabecera_archivo_truncada(self):
        """Menos de 14+4 bytes: no se puede validar; ESTIMADO."""
        from core.scanner import ResultadoRecuperacion
        fixture = b"BM" + b"\x00" * 10  # 12 bytes, < 18
        entrada, (recuperados, fallidos) = self._recuperar(fixture, "trunc_hdr.bmp")
        self.assertEqual((recuperados, fallidos), (1, 0))
        self.assertFalse(entrada.tamano_exacto)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.ESTIMADO)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_bmp_dib_truncado(self):
        """Tamaño DIB declarado soportado pero datos de cabecera insuficientes."""
        from core.scanner import ResultadoRecuperacion
        # firma + tam.archivo + reservado + offset + tam.DIB=40, pero sin los 40 bytes
        fixture = b"BM" + struct.pack("<IHHI", 100, 0, 0, 54) + struct.pack("<I", 40) + b"\x00" * 5
        entrada, (recuperados, fallidos) = self._recuperar(fixture, "trunc_dib.bmp")
        self.assertEqual((recuperados, fallidos), (1, 0))
        self.assertFalse(entrada.tamano_exacto)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.ESTIMADO)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_bmp_dib_no_soportado_rechazado(self):
        self._rechazo_esperado(
            _bmp_sintetico(ancho=2, alto=2, bpp=24, dib_size=12,
                           ext_dib=b"", pixel_bytes=b"\x00" * 16),
            "dib12.bmp")

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_bmp_ancho_cero_rechazado(self):
        self._rechazo_esperado(_bmp_sintetico(ancho=0, alto=2, bpp=24), "ancho0.bmp")

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_bmp_alto_cero_rechazado(self):
        self._rechazo_esperado(_bmp_sintetico(ancho=2, alto=0, bpp=24), "alto0.bmp")

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_bmp_planos_invalidos_rechazado(self):
        self._rechazo_esperado(_bmp_sintetico(planes=2), "planos.bmp")

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_bmp_bpp_no_soportado_rechazado(self):
        self._rechazo_esperado(_bmp_sintetico(ancho=2, alto=2, bpp=7), "bpp7.bmp")

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_bmp_compresion_no_soportada_rechazada(self):
        self._rechazo_esperado(_bmp_sintetico(compresion=1), "comp1.bmp")

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_bmp_offset_imposible_rechazado(self):
        """Offset de píxeles menor que la cabecera: rechazado."""
        fixture = _bmp_sintetico(ancho=2, alto=2, bpp=24, offset_datos=10,
                                 pixel_bytes=b"\x00" * 16)
        self._rechazo_esperado(fixture, "offset.bmp")

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_bmp_tamano_declarado_contradictorio_rechazado(self):
        self._rechazo_esperado(_bmp_sintetico(tam_declarado=999), "tam_dec.bmp")

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_bmp_tamano_imagen_contradictorio_rechazado(self):
        self._rechazo_esperado(_bmp_sintetico(tam_imagen=999), "tam_img.bmp")

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_bmp_pixeles_truncados_estimado(self):
        """Píxeles declarados pero ausentes: no se alcanza el límite; ESTIMADO."""
        from core.scanner import ResultadoRecuperacion
        fixture = _bmp_sintetico(ancho=4, alto=4, bpp=24, pixel_bytes=b"\x00" * 10)
        entrada, (recuperados, fallidos) = self._recuperar(fixture, "pix_trunc.bmp")
        self.assertEqual((recuperados, fallidos), (1, 0))
        self.assertFalse(entrada.tamano_exacto)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.ESTIMADO)
        # Los bytes escritos igualan el tamaño real del fixture, sin fabricar.
        bmps = list(Path(self.dest.name).rglob("*.bmp"))
        self.assertEqual(entrada.evidencia.bytes_escritos, bmps[0].stat().st_size)
        self.assertEqual(bmps[0].stat().st_size, len(fixture))

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_bmp_pixel_declarado_excede_tope_operativo(self):
        """Si el tamaño declarado supera el tope, no se marca exacto."""
        from unittest import mock
        from core.scanner import EscaneoProfundo, ResultadoRecuperacion
        fixture = _bmp_sintetico(ancho=8, alto=8, bpp=24)
        fuente = self.src_dir / "grande.bmp"
        fuente.write_bytes(fixture)
        from core.scanner import ArchivoEncontrado as AE
        entrada = AE(ruta=str(fuente), nombre="grande.bmp", extension="bmp",
                     tamano=len(fixture), offset=0, categoria="Imágenes",
                     descripcion="Imagen BMP")
        r = RecuperadorArchivos()
        with mock.patch.object(EscaneoProfundo, "TAMANO_MAX_ARCHIVO", 32):
            recuperados, fallidos = r.recuperar_archivos([entrada], self.dest.name)
        self.assertEqual((recuperados, fallidos), (1, 0))
        self.assertFalse(entrada.tamano_exacto)
        self.assertEqual(entrada.evidencia.limite_alcanzado, "tamano_maximo")
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.ESTIMADO)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_bmp_lectura_corta_no_corrompe(self):
        """Lecturas que devuelven pocos bytes no rompen la salida BMP."""
        fixture = _bmp_sintetico(ancho=8, alto=8, bpp=24)
        fuente = self.src_dir / "corta.bmp"
        fuente.write_bytes(fixture)

        import builtins
        from unittest import mock
        real_open = builtins.open

        class LecturaCorta:
            def __init__(self, f): self._f = f
            def read(self, n=-1): return self._f.read(min(n if n > 0 else 7, 7))
            def seek(self, o, w=0): return self._f.seek(o, w)
            def write(self, b): return self._f.write(b)
            def __enter__(self): return self
            def __exit__(self, *a): return self._f.__exit__(*a)

        def fake_open(path, mode="r", *a, **k):
            f = real_open(path, mode, *a, **k)
            if mode == "rb" and str(path).endswith("corta.bmp"):
                return LecturaCorta(f)
            return f

        from core.scanner import ArchivoEncontrado as AE
        entrada = AE(ruta=str(fuente), nombre="corta.bmp", extension="bmp",
                     tamano=len(fixture), offset=0, categoria="Imágenes", descripcion="Imagen BMP")
        r = RecuperadorArchivos()
        with mock.patch.object(builtins, "open", fake_open):
            recuperados, fallidos = r.recuperar_archivos([entrada], self.dest.name)
        self.assertEqual((recuperados, fallidos), (1, 0))
        bmps = list(Path(self.dest.name).rglob("*.bmp"))
        self.assertEqual(bmps[0].read_bytes(), fixture)
        self.assertTrue(entrada.tamano_exacto)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_bmp_error_de_lectura_limpia_parcial(self):
        """Un OSError a mitad de lectura produce FALLIDO y limpia el parcial."""
        fixture = _bmp_sintetico(ancho=256, alto=256, bpp=24)  # > 64 KiB
        fuente = self.src_dir / "eobmp.bmp"
        fuente.write_bytes(fixture)

        import builtins
        from unittest import mock
        real_open = builtins.open

        class FallaTrasN:
            def __init__(self, f): self._f = f; self._n = 0
            def read(self, n=-1):
                self._n += 1
                if self._n >= 2:
                    raise OSError("disco con errores simulado")
                return self._f.read(n)
            def seek(self, o, w=0): return self._f.seek(o, w)
            def __enter__(self): return self
            def __exit__(self, *a): return self._f.__exit__(*a)

        def fake_open(path, mode="r", *a, **k):
            f = real_open(path, mode, *a, **k)
            if mode == "rb" and str(path).endswith("eobmp.bmp"):
                return FallaTrasN(f)
            return f

        from core.scanner import ArchivoEncontrado as AE, ResultadoRecuperacion
        entrada = AE(ruta=str(fuente), nombre="eobmp.bmp", extension="bmp",
                     tamano=len(fixture), offset=0, categoria="Imágenes", descripcion="Imagen BMP")
        r = RecuperadorArchivos()
        with mock.patch.object(builtins, "open", fake_open):
            recuperados, fallidos = r.recuperar_archivos([entrada], self.dest.name)
        self.assertEqual((recuperados, fallidos), (0, 1))
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.FALLIDO)
        self.assertEqual(entrada.evidencia.bytes_escritos, 0)
        self.assertEqual(list(Path(self.dest.name).rglob("*.bmp")), [])

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_bmp_cancelacion_tras_primer_bloque(self):
        """Cancelar tras el primer bloque deja PARCIAL, 0 bytes y limpia."""
        fixture = _bmp_sintetico(ancho=256, alto=256, bpp=24)  # > 64 KiB
        fuente = self.src_dir / "cancel.bmp"
        fuente.write_bytes(fixture)

        import builtins
        from unittest import mock
        real_open = builtins.open
        r = RecuperadorArchivos()

        class CancelaTrasPrimero:
            def __init__(self, f): self._f = f; self._n = 0
            def read(self, n=-1):
                self._n += 1
                datos = self._f.read(n)
                if self._n == 1:
                    r.cancelar()
                return datos
            def seek(self, o, w=0): return self._f.seek(o, w)
            def __enter__(self): return self
            def __exit__(self, *a): return self._f.__exit__(*a)

        def fake_open(path, mode="r", *a, **k):
            f = real_open(path, mode, *a, **k)
            if mode == "rb" and str(path).endswith("cancel.bmp"):
                return CancelaTrasPrimero(f)
            return f

        from core.scanner import ArchivoEncontrado as AE, ResultadoRecuperacion
        entrada = AE(ruta=str(fuente), nombre="cancel.bmp", extension="bmp",
                     tamano=len(fixture), offset=0, categoria="Imágenes", descripcion="Imagen BMP")
        with mock.patch.object(builtins, "open", fake_open):
            r.recuperar_archivos([entrada], self.dest.name)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.PARCIAL)
        self.assertEqual(entrada.evidencia.bytes_escritos, 0)
        self.assertEqual(list(Path(self.dest.name).rglob("*.bmp")), [])

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_bmp_no_sobrescribe_preexistente(self):
        """La creación exclusiva de BMP no sobrescribe un archivo preexistente."""
        fixture = _bmp_sintetico(ancho=2, alto=2, bpp=24)
        previo = Path(self.dest.name) / "Imágenes" / "imagen.bmp"
        previo.parent.mkdir(parents=True, exist_ok=True)
        previo.write_bytes(b"ORIGINAL")
        entrada, (recuperados, fallidos) = self._recuperar(fixture)
        self.assertEqual((recuperados, fallidos), (0, 1))
        self.assertEqual(previo.read_bytes(), b"ORIGINAL")

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_bmp_no_incluye_bytes_posteriores(self):
        """Los bytes tras el límite declarado no forman parte de la salida."""
        fixture = _bmp_sintetico(ancho=2, alto=2, bpp=24)
        entrada, (recuperados, fallidos) = self._recuperar(fixture + b"\xDD" * 100, "extra.bmp")
        self.assertEqual((recuperados, fallidos), (1, 0))
        bmps = list(Path(self.dest.name).rglob("*.bmp"))
        self.assertEqual(bmps[0].read_bytes(), fixture)
        self.assertTrue(entrada.tamano_exacto)

    def _generar_bmps_referencia(self, carpeta: Path) -> dict:
        """Genera BMP reales con PIL del sistema, si está disponible."""
        import subprocess, shutil
        python = shutil.which("python3")
        if python is None:
            return {}
        script = carpeta / "_genbmp.py"
        script.write_text(
            "from PIL import Image\n"
            f"Image.new('RGB', (32, 16), (10, 120, 200)).save(r'{carpeta}/rgb.bmp', 'BMP')\n"
            f"Image.new('RGBA', (16, 16), (1, 2, 3, 4)).save(r'{carpeta}/rgba.bmp', 'BMP')\n"
            f"Image.new('L', (9, 5), 128).save(r'{carpeta}/gray.bmp', 'BMP')\n"
        )
        try:
            subprocess.run([python, str(script)], check=True, capture_output=True, timeout=60)
        except Exception:
            return {}
        finally:
            try:
                script.unlink()
            except OSError:
                pass
        rutas = {
            "rgb": carpeta / "rgb.bmp",
            "rgba": carpeta / "rgba.bmp",
            "gray": carpeta / "gray.bmp",
        }
        return {k: v for k, v in rutas.items() if v.is_file()}

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_bmp_real_bytes_exactos(self):
        """BMP reales de PIL: extracción byte-exacta y AMBIGUO."""
        from core.scanner import ResultadoRecuperacion
        rutas = self._generar_bmps_referencia(self.src_dir)
        if not rutas:
            self.skipTest("PIL del sistema no disponible para generar BMP real")
        for clave, fuente in rutas.items():
            esperado = fuente.read_bytes()
            from core.scanner import ArchivoEncontrado as AE
            entrada = AE(ruta=str(fuente), nombre=fuente.name, extension="bmp",
                         tamano=len(esperado), offset=0, categoria="Imágenes",
                         descripcion="Imagen BMP")
            r = RecuperadorArchivos()
            dest_i = tempfile.mkdtemp(dir=DEST_TMPFS)
            try:
                recuperados, fallidos = r.recuperar_archivos([entrada], dest_i)
                self.assertEqual((recuperados, fallidos), (1, 0), f"{clave}: fallo")
                bmps = list(Path(dest_i).rglob("*.bmp"))
                self.assertEqual(len(bmps), 1, f"{clave}: salidas inesperadas")
                recuperado = bmps[0].read_bytes()
                print(f"\n[BMP REAL] {clave}: original={len(esperado)}B "
                      f"recuperado={len(recuperado)}B iguales={recuperado == esperado} "
                      f"resultado={entrada.evidencia.resultado}")
                self.assertEqual(recuperado, esperado, f"{clave}: bytes no idénticos")
                self.assertTrue(entrada.tamano_exacto, f"{clave}: no exacto")
                self.assertTrue(entrada.evidencia.estructura_validada, f"{clave}: no validada")
                self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.AMBIGUO)
            finally:
                import shutil; shutil.rmtree(dest_i, ignore_errors=True)

    @unittest.skipUnless(DEST_TMPFS.is_dir(), "requiere /dev/shm como filesystem distinto")
    def test_regresion_jpeg_png_inalterados(self):
        """Confirma que los cambios BMP no alteran JPEG ni PNG."""
        from core.scanner import ResultadoRecuperacion, ArchivoEncontrado as AE
        casos = [
            ("r.jpg", "jpg", _jpeg_sintetico(b"JPEG" * 16)),
            ("r.png", "png", _png_sintetico()),
        ]
        for nombre, ext, fixture in casos:
            fuente = self.src_dir / nombre
            fuente.write_bytes(fixture)
            entrada = AE(ruta=str(fuente), nombre=nombre, extension=ext,
                         tamano=len(fixture), offset=0, categoria="Imágenes",
                         descripcion="Imagen")
            r = RecuperadorArchivos()
            dest_i = tempfile.mkdtemp(dir=DEST_TMPFS)
            try:
                recuperados, fallidos = r.recuperar_archivos([entrada], dest_i)
                self.assertEqual((recuperados, fallidos), (1, 0), f"{ext}: fallo")
                salidas = list(Path(dest_i).rglob(f"*.{ext}"))
                self.assertEqual(salidas[0].read_bytes(), fixture, f"{ext}: bytes")
                self.assertTrue(entrada.tamano_exacto, f"{ext}: no exacto")
                self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.AMBIGUO)
            finally:
                import shutil; shutil.rmtree(dest_i, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
