"""Pruebas de recuperación GIF (GIF87a/GIF89a) de SpiderRecovery.

Fixtures sintéticos construidos a mano (sin dependencias externas) y,
cuando Pillow está disponible en el intérprete del sistema, fixtures
reales vía subproceso. Nunca usa /dev/*, discos reales ni volúmenes
externos salvo /dev/shm como destino temporal (tmpfs).
"""

import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.recover import RecuperadorArchivos
from core.scanner import ArchivoEncontrado

DEST_TMPFS = Path("/dev/shm")


def _subbloques(*datos: bytes) -> bytes:
    """Secuencia de sub-bloques: cada uno con longitud y datos, terminada en 0."""
    partes = []
    for d in datos:
        assert len(d) <= 255
        partes.append(bytes([len(d)]) + d)
    partes.append(b"\x00")
    return b"".join(partes)


def _tabla(colores: int, semilla: bytes = b"\xAA") -> bytes:
    """Tabla de colores: `colores` entradas RGB."""
    return b"".join(bytes([semilla[i % len(semilla)]] * 3) for i in range(colores))


def _gif_esqueleto(cabecera=b"GIF89a", ancho=1, alto=1, packed_lsd=0x00,
                   gct=b"", cuerpo=b"", bg=0, aspecto=0) -> bytes:
    lsd = struct.pack("<HHB BB".replace(" ", ""), ancho, alto, packed_lsd, bg, aspecto)
    return cabecera + lsd + gct + cuerpo + b"\x3B"


def _descriptor(izq=0, sup=0, ancho=1, alto=1, packed=0x00) -> bytes:
    return b"\x2C" + struct.pack("<HHHHB".replace(" ", ""), izq, sup, ancho, alto, packed)


def _lzw_min() -> bytes:
    return b"\x02"  # tamaño mínimo LZW válido (2..8)


def _datos_imagen(*bloques: bytes) -> bytes:
    return _lzw_min() + _subbloques(*bloques)


def _gif_minimo(cabecera=b"GIF89a") -> bytes:
    cuerpo = _descriptor() + _datos_imagen(b"\x44\x01")
    return _gif_esqueleto(cabecera=cabecera, cuerpo=cuerpo)


def _gif_con_gct() -> bytes:
    gct = _tabla(2)
    packed = 0x80 | 0x00  # GCT presente, 2^(0+1)=2 colores
    cuerpo = _descriptor() + _datos_imagen(b"\x44\x01")
    return _gif_esqueleto(packed_lsd=packed, gct=gct, cuerpo=cuerpo)


def _gif_con_lct() -> bytes:
    cuerpo = _descriptor(packed=0x80 | 0x00) + _tabla(2) + _datos_imagen(b"\x44\x01")
    return _gif_esqueleto(cuerpo=cuerpo)


def _ext_comentario(texto: bytes) -> bytes:
    return b"\x21\xFE" + _subbloques(texto)


def _ext_app(datos11: bytes = b"NETSCAPE2.0", extra: bytes = b"\x03\x01\x00\x00") -> bytes:
    return b"\x21\xFF" + bytes([len(datos11)]) + datos11 + _subbloques(extra)


def _ext_gce() -> bytes:
    return b"\x21\xF9\x04" + b"\x00\x00\x00\x00" + b"\x00"


def _ext_texto() -> bytes:
    fijo = struct.pack("<HHHHBBBB", 0, 0, 8, 8, 8, 8, 0, 0)
    return b"\x21\x01" + bytes([12]) + fijo + _subbloques(b"hola")


def _gif_animado() -> bytes:
    gct = _tabla(2)
    packed = 0x80 | 0x00
    frame1 = _descriptor() + _datos_imagen(b"\x44\x01")
    frame2 = _ext_gce() + _descriptor(izq=1, sup=1, ancho=2, alto=2, packed=0x80) + \
        _tabla(2) + _datos_imagen(b"\x44\x02\x55")
    return _gif_esqueleto(packed_lsd=packed, gct=gct, cuerpo=frame1 + frame2)


def _gif_completo() -> bytes:
    gct = _tabla(4)
    packed = 0x80 | 0x01  # 2^(1+1)=4 colores
    body = (
        _ext_gce()
        + _ext_app()
        + _ext_comentario(b"comentario")
        + _ext_texto()
        + _descriptor() + _datos_imagen(b"\x44\x01")
        + _ext_comentario(b"entre frames")
        + _descriptor(packed=0x80 | 0x00) + _tabla(2) + _datos_imagen(b"\x44\x03")
    )
    return _gif_esqueleto(packed_lsd=packed, gct=gct, cuerpo=body)


def arch_gif(ruta: str, nombre="imagen.gif", tamano=0, offset=0) -> ArchivoEncontrado:
    return ArchivoEncontrado(
        ruta=ruta, nombre=nombre, extension="gif", tamano=tamano, offset=offset,
        categoria="Imágenes", descripcion="Imagen GIF",
    )


def _recuperar(fixture: bytes, tmp_path: Path, nombre="imagen.gif"):
    src_dir = tmp_path / "src"
    src_dir.mkdir(exist_ok=True)
    fuente = src_dir / nombre
    fuente.write_bytes(fixture)
    dest = tempfile.mkdtemp(dir=DEST_TMPFS if DEST_TMPFS.is_dir() else None)
    entrada = arch_gif(str(fuente), nombre=nombre, tamano=len(fixture))
    r = RecuperadorArchivos()
    resultado = r.recuperar_archivos([entrada], dest)
    return entrada, resultado, Path(dest)


class TestGifValido(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _ok_exacto(self, fixture: bytes, nombre="imagen.gif"):
        entrada, (rec, fal), dest = _recuperar(fixture, self.base, nombre)
        self.assertEqual((rec, fal), (1, 0))
        self.assertTrue(entrada.tamano_exacto)
        from core.scanner import ResultadoRecuperacion
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.AMBIGUO)
        self.assertTrue(entrada.evidencia.estructura_validada)
        self.assertEqual(entrada.evidencia.bytes_escritos, len(fixture))
        salidas = list(dest.rglob("*.gif"))
        self.assertEqual(len(salidas), 1)
        self.assertEqual(salidas[0].read_bytes(), fixture)
        import shutil; shutil.rmtree(dest, ignore_errors=True)

    def test_gif87a_minimo(self):
        self._ok_exacto(_gif_minimo(b"GIF87a"), "a87.gif")

    def test_gif89a_minimo(self):
        self._ok_exacto(_gif_minimo(b"GIF89a"), "a89.gif")

    def test_con_tabla_global(self):
        self._ok_exacto(_gif_con_gct(), "gct.gif")

    def test_con_tabla_local(self):
        self._ok_exacto(_gif_con_lct(), "lct.gif")

    def test_con_extensiones(self):
        fixture = _gif_esqueleto(
            cuerpo=_ext_gce() + _descriptor() + _datos_imagen(b"\x44\x01"))
        self._ok_exacto(fixture, "ext.gif")

    def test_animado_varios_frames(self):
        self._ok_exacto(_gif_animado(), "anim.gif")

    def test_comentarios_app_gce(self):
        fixture = _gif_esqueleto(
            cuerpo=_ext_app() + _ext_comentario(b"hola") + _ext_gce() +
            _descriptor() + _datos_imagen(b"\x44\x01"))
        self._ok_exacto(fixture, "exts.gif")

    def test_extension_texto_plano(self):
        fixture = _gif_esqueleto(
            cuerpo=_ext_texto() + _descriptor() + _datos_imagen(b"\x44\x01"))
        self._ok_exacto(fixture, "txt.gif")

    def test_completo_todas_las_extensiones(self):
        self._ok_exacto(_gif_completo(), "full.gif")

    def test_termina_exactamente_en_trailer(self):
        fixture = _gif_minimo()
        self.assertEqual(fixture[-1], 0x3B)
        self._ok_exacto(fixture)

    def test_trailer_seguido_de_basura_no_se_incluye(self):
        fixture = _gif_minimo() + b"RUIDO-NO-DEL-GIF" * 10
        entrada, (rec, fal), dest = _recuperar(fixture, self.base)
        self.assertEqual((rec, fal), (1, 0))
        salidas = list(dest.rglob("*.gif"))
        self.assertEqual(salidas[0].read_bytes(), _gif_minimo())
        self.assertEqual(entrada.evidencia.bytes_escritos, len(_gif_minimo()))
        import shutil; shutil.rmtree(dest, ignore_errors=True)

    def test_trailer_falso_dentro_de_tabla_no_termina(self):
        # La GCT contiene 0x3B; el parser debe consumirla por longitud y
        # terminar solo en el trailer real.
        gct = bytes([0x3B, 0x00, 0x00, 0x3B, 0x11, 0x22])  # 2 colores, incluye 0x3B
        packed = 0x80 | 0x00
        cuerpo = _descriptor() + _datos_imagen(b"\x44\x01")
        fixture = _gif_esqueleto(packed_lsd=packed, gct=gct, cuerpo=cuerpo)
        self._ok_exacto(fixture, "falso.gif")

    def test_trailer_falso_dentro_de_datos_imagen_no_termina(self):
        datos = b"\x44\x3B\x3B\x00"  # contiene 0x3B dentro del payload LZW
        cuerpo = _descriptor() + _datos_imagen(datos)
        fixture = _gif_esqueleto(cuerpo=cuerpo)
        self._ok_exacto(fixture, "falso2.gif")

    def test_gif_real_con_pil_si_disponible(self):
        import shutil, subprocess
        python = shutil.which("python3")
        rutas = []
        if python:
            script = self.base / "_gengif.py"
            script.write_text(
                "from PIL import Image\n"
                f"Image.new('P', (8, 8)).save(r'{self.base}/real.gif', 'GIF')\n"
            )
            try:
                subprocess.run([python, str(script)], check=True,
                               capture_output=True, timeout=60)
                rutas = [self.base / "real.gif"]
            except Exception:
                rutas = []
            try:
                script.unlink()
            except OSError:
                pass
        if not rutas:
            self.skipTest("PIL del sistema no disponible para generar GIF real")
        datos = rutas[0].read_bytes()
        self._ok_exacto(datos, "real.gif")


class TestGifMalformado(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _estimado(self, fixture: bytes, nombre="imagen.gif"):
        from core.scanner import ResultadoRecuperacion
        entrada, (rec, fal), dest = _recuperar(fixture, self.base, nombre)
        # La extracción devuelve prefijo + ESTIMADO, nunca éxito completo.
        self.assertEqual(fal, 0)
        self.assertFalse(entrada.tamano_exacto)
        self.assertIsNotNone(entrada.evidencia)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.ESTIMADO)
        import shutil; shutil.rmtree(dest, ignore_errors=True)

    def test_firma_invalida(self):
        self._estimado(b"NOTA GIF" + b"\x00" * 32)

    def test_version_no_soportada(self):
        cuerpo = _descriptor() + _datos_imagen(b"\x44\x01")
        self._estimado(_gif_esqueleto(cabecera=b"GIF85a", cuerpo=cuerpo))

    def test_cabecera_truncada(self):
        self._estimado(b"GIF89")

    def test_lsd_truncado(self):
        self._estimado(b"GIF89a" + b"\x01\x00\x01")

    def test_tabla_global_truncada(self):
        fixture = _gif_con_gct()[:8]  # corta dentro de la GCT
        self._estimado(fixture)

    def test_tabla_local_truncada(self):
        fixture = _gif_con_lct()
        # Localizar el descriptor y cortar dentro de la LCT (2*3=6 bytes)
        idx = fixture.index(b"\x2C") + 1 + 9
        self._estimado(fixture[:idx + 3])

    def test_descriptor_truncado(self):
        fixture = _gif_minimo()
        idx = fixture.index(b"\x2C")
        self._estimado(fixture[:idx + 4])

    def test_extension_truncada(self):
        fixture = _gif_esqueleto(cuerpo=_ext_gce() + _descriptor() + _datos_imagen(b"\x44\x01"))
        idx = fixture.index(b"\x21")
        self._estimado(fixture[:idx + 4])

    def test_subbloques_sin_terminador(self):
        # Falta el 0x00 final de la secuencia de sub-bloques de imagen
        sin_fin = _lzw_min() + bytes([4]) + b"\x44\x01\x02\x03"
        cuerpo = _descriptor() + sin_fin  # sin terminador ni trailer
        fixture = _gif_esqueleto(cuerpo=cuerpo)[:-1]  # sin trailer además
        self._estimado(fixture)

    def test_sin_trailer(self):
        fixture = _gif_minimo()[:-1]
        self._estimado(fixture)

    def test_introducer_invalido(self):
        cuerpo = b"\x99" + _descriptor() + _datos_imagen(b"\x44\x01")
        self._estimado(_gif_esqueleto(cuerpo=cuerpo))

    def test_gce_con_longitud_fija_incorrecta(self):
        mala = b"\x21\xF9\x05" + b"\x00" * 5 + b"\x00"
        cuerpo = mala + _descriptor() + _datos_imagen(b"\x44\x01")
        self._estimado(_gif_esqueleto(cuerpo=cuerpo))

    def test_app_con_longitud_fija_incorrecta(self):
        mala = b"\x21\xFF\x0A" + b"X" * 10 + _subbloques(b"")
        cuerpo = mala + _descriptor() + _datos_imagen(b"\x44\x01")
        self._estimado(_gif_esqueleto(cuerpo=cuerpo))

    def test_texto_con_longitud_fija_incorrecta(self):
        mala = b"\x21\x01\x0B" + b"X" * 11 + _subbloques(b"abc")
        cuerpo = mala + _descriptor() + _datos_imagen(b"\x44\x01")
        self._estimado(_gif_esqueleto(cuerpo=cuerpo))

    def test_lzw_minimo_fuera_de_rango(self):
        cuerpo = _descriptor() + b"\x01" + _subbloques(b"\x44\x01")
        self._estimado(_gif_esqueleto(cuerpo=cuerpo))

    def test_dimensiones_nulas(self):
        cuerpo = _descriptor(ancho=0, alto=0) + _datos_imagen(b"\x44\x01")
        self._estimado(_gif_esqueleto(cuerpo=cuerpo))

    def test_pantalla_dimensiones_nulas(self):
        self._estimado(_gif_esqueleto(ancho=0, alto=5, cuerpo=b"\x3B")[:-1] + b"")

    def test_vacio(self):
        entrada, (rec, fal), dest = _recuperar(b"", self.base)
        self.assertEqual((rec, fal), (0, 1))  # fuente vacía -> FALLIDO
        import shutil; shutil.rmtree(dest, ignore_errors=True)

    def test_solo_trailer_sin_imagen(self):
        self._estimado(b"GIF89a" + struct.pack("<HHB BB".replace(" ", ""), 1, 1, 0, 0, 0) + b"\x3B")

    def test_dimensiones_grandes_no_asignan_memoria(self):
        # Pantalla enorme + tabla declarada grande: el parser no reserva
        # memoria según esas cifras; el límite lo pone el tope operativo.
        fixture = _gif_esqueleto(ancho=0xFFFF, alto=0xFFFF, packed_lsd=0xFF,
                                 gct=_tabla(2) * 128)
        fixture = fixture[:len(fixture) - 1]  # sin trailer
        self._estimado(fixture)

    def test_descriptor_dimensiones_nulas(self):
        cuerpo = _descriptor(ancho=5, alto=0) + _datos_imagen(b"\x44\x01")
        self._estimado(_gif_esqueleto(cuerpo=cuerpo))


class TestGifSeguridadYOperacion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src_dir = Path(self.tmp.name)
        self.dest = tempfile.TemporaryDirectory(dir=DEST_TMPFS if DEST_TMPFS.is_dir() else None)

    def tearDown(self):
        self.tmp.cleanup()
        self.dest.cleanup()

    def _recuperar(self, fixture: bytes, nombre="imagen.gif"):
        fuente = self.src_dir / nombre
        fuente.write_bytes(fixture)
        entrada = arch_gif(str(fuente), nombre=nombre, tamano=len(fixture))
        r = RecuperadorArchivos()
        resultado = r.recuperar_archivos([entrada], self.dest.name)
        return entrada, resultado, r

    def test_lectura_corta_no_corrompe(self):
        fixture = _gif_completo()
        fuente = self.src_dir / "corta.gif"
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
            if mode == "rb" and str(path).endswith("corta.gif"):
                return LecturaCorta(f)
            return f

        entrada = arch_gif(str(fuente), nombre="corta.gif", tamano=len(fixture))
        r = RecuperadorArchivos()
        with mock.patch.object(builtins, "open", fake_open):
            rec, fal = r.recuperar_archivos([entrada], self.dest.name)
        self.assertEqual((rec, fal), (1, 0))
        salidas = list(Path(self.dest.name).rglob("*.gif"))
        self.assertEqual(salidas[0].read_bytes(), fixture)
        self.assertTrue(entrada.tamano_exacto)

    def test_error_de_lectura_limpia_parcial(self):
        fuente = self.src_dir / "eogif.gif"
        # Cabecera válida + datos de imagen largos e incompletos (>64 KiB,
        # sin terminadores ni trailer): fuerza una 2ª lectura.
        carga = b"".join(bytes([255]) + b"\x44" * 255 for _ in range(300))
        cuerpo = _descriptor() + _lzw_min() + carga
        fixture = _gif_esqueleto(cuerpo=cuerpo)[:-1]
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
            if mode == "rb" and str(path).endswith("eogif.gif"):
                return FallaTrasN(f)
            return f

        from core.scanner import ResultadoRecuperacion
        entrada = arch_gif(str(fuente), nombre="eogif.gif", tamano=len(fixture))
        r = RecuperadorArchivos()
        with mock.patch.object(builtins, "open", fake_open):
            rec, fal = r.recuperar_archivos([entrada], self.dest.name)
        self.assertEqual((rec, fal), (0, 1))
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.FALLIDO)
        self.assertEqual(entrada.evidencia.bytes_escritos, 0)
        self.assertEqual(list(Path(self.dest.name).rglob("*.gif")), [])

    def test_cancelacion_limpia_parcial(self):
        carga = b"".join(bytes([255]) + b"\x44" * 255 for _ in range(300))
        cuerpo = _descriptor() + _lzw_min() + carga
        fixture = _gif_esqueleto(cuerpo=cuerpo)[:-1]  # sin trailer, >64 KiB
        fuente = self.src_dir / "cancel.gif"
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
            if mode == "rb" and str(path).endswith("cancel.gif"):
                return CancelaTrasPrimero(f)
            return f

        from core.scanner import ResultadoRecuperacion
        entrada = arch_gif(str(fuente), nombre="cancel.gif", tamano=len(fixture))
        with mock.patch.object(builtins, "open", fake_open):
            r.recuperar_archivos([entrada], self.dest.name)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.PARCIAL)
        self.assertEqual(entrada.evidencia.bytes_escritos, 0)
        self.assertEqual(list(Path(self.dest.name).rglob("*.gif")), [])

    def test_no_sobrescribe_preexistente(self):
        fixture = _gif_minimo()
        previo = Path(self.dest.name) / "Imágenes" / "imagen.gif"
        previo.parent.mkdir(parents=True, exist_ok=True)
        previo.write_bytes(b"ORIGINAL")
        entrada, (rec, fal), _ = self._recuperar(fixture)
        self.assertEqual((rec, fal), (0, 1))
        self.assertEqual(previo.read_bytes(), b"ORIGINAL")

    def test_no_incluye_bytes_posteriores(self):
        fixture = _gif_minimo()
        entrada, (rec, fal), _ = self._recuperar(fixture + b"\xDD" * 100)
        self.assertEqual((rec, fal), (1, 0))
        salidas = list(Path(self.dest.name).rglob("*.gif"))
        self.assertEqual(salidas[0].read_bytes(), fixture)
        self.assertTrue(entrada.tamano_exacto)

    def test_tope_operativo(self):
        from unittest import mock
        from core.scanner import EscaneoProfundo, ResultadoRecuperacion
        fixture = _gif_completo()
        fuente = self.src_dir / "grande.gif"
        fuente.write_bytes(fixture * 4)
        entrada = arch_gif(str(fuente), nombre="grande.gif", tamano=len(fixture) * 4)
        r = RecuperadorArchivos()
        with mock.patch.object(EscaneoProfundo, "TAMANO_MAX_ARCHIVO", 32):
            rec, fal = r.recuperar_archivos([entrada], self.dest.name)
        self.assertEqual((rec, fal), (1, 0))
        self.assertFalse(entrada.tamano_exacto)
        self.assertEqual(entrada.evidencia.limite_alcanzado, "tamano_maximo")
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.ESTIMADO)

    def test_evidencia_consistente(self):
        fixture = _gif_completo()
        entrada, (rec, fal), _ = self._recuperar(fixture)
        self.assertEqual((rec, fal), (1, 0))
        salidas = list(Path(self.dest.name).rglob("*.gif"))
        self.assertEqual(entrada.evidencia.bytes_escritos, salidas[0].stat().st_size)
        self.assertEqual(len(fixture), salidas[0].stat().st_size)
        detalle = entrada.evidencia.detalle.lower()
        self.assertIn("lzw", detalle)

    def test_fuente_inalterada(self):
        fixture = _gif_completo()
        fuente = self.src_dir / "src.gif"
        fuente.write_bytes(fixture)
        entrada = arch_gif(str(fuente), nombre="src.gif", tamano=len(fixture))
        RecuperadorArchivos().recuperar_archivos([entrada], self.dest.name)
        self.assertEqual(fuente.read_bytes(), fixture)

    def test_integracion_scanner(self):
        from core.scanner import EscaneoProfundo
        fixture = _gif_completo()
        (self.src_dir / "real.gif").write_bytes(fixture)
        escaner = EscaneoProfundo()
        escaner.iniciar(str(self.src_dir))
        escaner._hilo.join(timeout=5.0)
        encontrados = escaner.obtener_archivos()
        self.assertEqual(len(encontrados), 1)
        self.assertEqual(encontrados[0].extension, "gif")
        rec, fal = RecuperadorArchivos().recuperar_archivos(encontrados, self.dest.name)
        self.assertEqual((rec, fal), (1, 0))
        salidas = list(Path(self.dest.name).rglob("*.gif"))
        self.assertEqual(salidas[0].read_bytes(), fixture)


class TestRegresionFormatos(unittest.TestCase):
    """La integración GIF no debe alterar JPEG, PNG ni BMP."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src_dir = Path(self.tmp.name)
        self.dest = tempfile.TemporaryDirectory(dir=DEST_TMPFS if DEST_TMPFS.is_dir() else None)

    def tearDown(self):
        self.tmp.cleanup()
        self.dest.cleanup()

    def test_jpeg_png_bmp_inalterados(self):
        import struct as st, zlib
        jpeg = b"\xff\xd8" + b"\xff\xe0\x00\x0bJFIF\x00fake" + b"\xff\xda\x00\x04\x01\x01" + b"\x00" * 8 + b"\xff\xd9"
        ihdr = st.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        def ch(t, d):
            return st.pack(">I", len(d)) + t + d + st.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        png = b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", ihdr) + ch(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) + ch(b"IEND", b"")
        fila = 8; alto = 2
        bmp = (b"BM" + st.pack("<IHHI", 54 + fila * alto, 0, 0, 54)
               + st.pack("<IiiHHIIiiII", 40, 2, alto, 1, 24, 0, fila * alto, 0, 0, 0, 0)
               + b"\x00" * fila * alto)
        casos = [("r.jpg", "jpg", jpeg), ("r.png", "png", png), ("r.bmp", "bmp", bmp)]
        for nombre, ext, fixture in casos:
            fuente = self.src_dir / nombre
            fuente.write_bytes(fixture)
            entrada = ArchivoEncontrado(
                ruta=str(fuente), nombre=nombre, extension=ext, tamano=len(fixture),
                offset=0, categoria="Imágenes", descripcion="Imagen")
            rec, fal = RecuperadorArchivos().recuperar_archivos([entrada], self.dest.name)
            self.assertEqual((rec, fal), (1, 0), f"{ext}: fallo")
            salidas = list(Path(self.dest.name).rglob(f"*.{ext}"))
            self.assertEqual(salidas[0].read_bytes(), fixture, f"{ext}: bytes")
            self.assertTrue(entrada.tamano_exacto, f"{ext}: no exacto")


if __name__ == "__main__":
    unittest.main()
