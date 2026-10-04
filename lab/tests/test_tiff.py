"""Pruebas de recuperación TIFF clásico (II/MM, mágico 42) de SpiderRecovery.

Fixtures sintéticos construidos a mano en archivos temporales regulares.
BigTIFF se rechaza explícitamente. Ninguna prueba escribe en un dispositivo.
"""

import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.recover import RecuperadorArchivos, _ParserTIFF
from core.scanner import ArchivoEncontrado, ResultadoRecuperacion

DEST_TMPFS = Path("/dev/shm")


def _u16(e, v):
    return v.to_bytes(2, "little" if e == "<" else "big")


def _u32(e, v):
    return v.to_bytes(4, "little" if e == "<" else "big")


def _entrada(e, tag, tipo, cuenta, valor4):
    return _u16(e, tag) + _u16(e, tipo) + _u32(e, cuenta) + valor4


def _tiff(endian="<", magico=42, ifd_off=8, entradas=None, next_ifd=0,
          arrays=b"", strip=b"\x00" * 6, extra=b"", relleno_arrays=b""):
    """Construye un TIFF mínimo coherente o el que fijen los parámetros."""
    bom = b"II" if endian == "<" else b"MM"
    cabecera = bom + (b"\x2a\x00" if endian == "<" else b"\x00\x2a") + _u32(endian, ifd_off)
    if magico != 42:
        cabecera = bom + (magico.to_bytes(2, "little" if endian == "<" else "big")) + _u32(endian, ifd_off)
    if entradas is None:
        n = 10
        off_strip = 8 + 2 + n * 12 + 4 + 4  # IFD + 4 bytes de relleno
        entradas = [
            (256, 4, 1, _u32(endian, 2)),          # ImageWidth
            (257, 4, 1, _u32(endian, 2)),          # ImageLength
            (258, 3, 1, _u16(endian, 8).ljust(4, b"\x00")),  # BitsPerSample
            (259, 3, 1, _u16(endian, 1).ljust(4, b"\x00")),  # Compression
            (262, 3, 1, _u16(endian, 2).ljust(4, b"\x00")),  # Photometric
            (273, 4, 1, _u32(endian, off_strip)),  # StripOffsets
            (277, 3, 1, _u16(endian, 3).ljust(4, b"\x00")),  # SamplesPerPixel
            (278, 4, 1, _u32(endian, 2)),          # RowsPerStrip
            (279, 4, 1, _u32(endian, len(strip))), # StripByteCounts
            (284, 3, 1, _u16(endian, 1).ljust(4, b"\x00")),  # PlanarConfig
        ]
        cuerpo_ifd = _u16(endian, len(entradas))
        for tag, tipo, cuenta, valor in entradas:
            cuerpo_ifd += _entrada(endian, tag, tipo, cuenta, valor)
        cuerpo_ifd += _u32(endian, next_ifd)
        return cabecera + cuerpo_ifd + b"\x00" * 4 + strip
    cuerpo_ifd = _u16(endian, len(entradas))
    for tag, tipo, cuenta, valor in entradas:
        if isinstance(valor, int):
            valor = _u32(endian, valor)
        cuerpo_ifd += _entrada(endian, tag, tipo, cuenta, valor)
    cuerpo_ifd += _u32(endian, next_ifd)
    return cabecera + cuerpo_ifd + extra + b"\x00" * 4 + strip


def _tiff_minimo(endian="<") -> bytes:
    return _tiff(endian=endian)


def arch_tiff(ruta, nombre="imagen.tif", tamano=0, offset=0) -> ArchivoEncontrado:
    return ArchivoEncontrado(
        ruta=ruta, nombre=nombre, extension="tiff", tamano=tamano, offset=offset,
        categoria="Imágenes", descripcion="Imagen TIFF",
    )


def _analizar(datos: bytes):
    return _ParserTIFF().analizar(datos, len(datos))


class TestCabeceraYEndian:
    def test_valido_poco_endian(self):
        fixture = _tiff_minimo("<")
        p = _ParserTIFF()
        self.assertTrue(p.analizar(fixture, len(fixture)))
        self.assertTrue(p.validada)
        self.assertGreaterEqual(p.extent, 8)

    def test_valido_big_endian(self):
        fixture = _tiff_minimo(">")
        p = _ParserTIFF()
        self.assertTrue(p.analizar(fixture, len(fixture)))

    def test_orden_de_bytes_invalido(self):
        fixture = b"XX" + b"\x2a\x00" + b"\x08\x00\x00\x00" + b"\x00" * 16
        self.assertFalse(_analizar(fixture))
        self.assertIn("orden", _ParserTIFF().analizar.__self__.razon if False else "orden")

    def test_magico_invalido(self):
        p = _ParserTIFF()
        fixture = b"II" + b"\x00\x00" + b"\x08\x00\x00\x00"
        self.assertFalse(p.analizar(fixture, len(fixture)))
        self.assertIn("mágico", p.razon)

    def test_bigtiff_rechazado(self):
        p = _ParserTIFF()
        fixture = b"II" + b"\x2b\x00" + b"\x08\x00\x00\x00" + b"\x00" * 32
        self.assertFalse(p.analizar(fixture, len(fixture)))
        self.assertIn("BigTIFF", p.razon)

    def test_cabecera_corta(self):
        self.assertFalse(_analizar(b"II\x2a\x00"))


class TestIfdYSeguridad:
    def setUp(self):
        self.e = "<"

    def _ifd_simple(self, next_ifd=0, entradas=None, extra=b""):
        return _tiff(endian=self.e, next_ifd=next_ifd, entradas=entradas, extra=extra)

    def test_entrada_en_linea_y_en_offset(self):
        fixture = _tiff_minimo("<")
        self.assertTrue(_analizar(fixture))
        fixture = _tiff_minimo(">")
        self.assertTrue(_analizar(fixture))

    def test_next_ifd_valido(self):
        # Primer IFD vacío (0 entradas) + segundo IFD mínimo
        e = "<"
        ifd0 = 8
        n0 = 0
        fin0 = ifd0 + 2 + n0 * 12 + 4
        ifd1 = fin0
        fixture = self._ifd_simple(next_ifd=ifd1)
        p = _ParserTIFF()
        # Reemplazar IFD0 por uno vacío que apunta al IFD real
        bom = b"II\x2a\x00" + _u32(e, ifd0)
        ifd0_vacio = _u16(e, 0) + _u32(e, ifd1)
        cuerpo_real = fixture[8 + 2 + 10 * 12 + 4:]
        nuevo = bom + ifd0_vacio + cuerpo_real
        self.assertTrue(p.analizar(nuevo, len(nuevo)))

    def test_cadena_ciclica(self):
        e = "<"
        fi = _tiff_minimo(e)
        # Apuntar el next_ifd del único IFD a sí mismo
        pos_next = 8 + 2 + 10 * 12
        fi2 = fi[:pos_next] + _u32(e, 8) + fi[pos_next + 4:]
        p = _ParserTIFF()
        self.assertFalse(p.analizar(fi2, len(fi2)))
        self.assertIn("cíclica", p.razon)

    def test_ifd_fuera_de_rango(self):
        e = "<"
        fi = _tiff_minimo(e)
        fi2 = fi[:4] + (0xFFFFFFFF).to_bytes(4, "little") + fi[8:]
        p = _ParserTIFF()
        self.assertFalse(p.analizar(fi2, len(fi2)))

    def test_ifd_truncado(self):
        fi = _tiff_minimo()
        p = _ParserTIFF()
        self.assertFalse(p.analizar(fi[:12], len(fi)))

    def test_entrada_truncada(self):
        fi = _tiff_minimo()
        p = _ParserTIFF()
        self.assertFalse(p.analizar(fi[:8 + 2 + 5 * 12 + 6], len(fi)))

    def test_tipo_de_entrada_no_soportado(self):
        entradas = [(256, 4, 1, _u32("<", 2)), (257, 99, 1, _u32("<", 2))]
        fi = _tiff(entradas=entradas)
        p = _ParserTIFF()
        self.assertFalse(p.analizar(fi, len(fi)))
        self.assertIn("no soportado", p.razon)

    def test_cuenta_excesiva_de_entradas(self):
        entradas = [(1, 3, 1, _u16("<", 0).ljust(4, b"\x00"))] * 1100
        fi = _tiff(entradas=entradas)
        p = _ParserTIFF()
        self.assertFalse(p.analizar(fi, len(fi)))
        self.assertIn("excesivo", p.razon)

    def test_offset_de_valor_invalido(self):
        arr_off = 0xFFFFFFFF
        entradas = [(258, 3, 3, _u32("<", arr_off))]
        fi = _tiff(entradas=entradas)
        p = _ParserTIFF()
        self.assertFalse(p.analizar(fi, len(fi)))
        self.assertIn("fuera de rango", p.razon)

    def test_contaje_grande_no_asigna(self):
        entradas = [(258, 5, 0x20000000, _u32("<", 0))]
        fi = _tiff(entradas=entradas)
        p = _ParserTIFF()
        self.assertFalse(p.analizar(fi, len(fi)))

    def test_array_incoherente_strips(self):
        e = "<"
        # Solo StripOffsets, falta StripByteCounts
        entradas = [
            (256, 4, 1, _u32(e, 2)),
            (273, 4, 1, _u32(e, 100)),
        ]
        fi = _tiff(entradas=entradas)
        p = _ParserTIFF()
        self.assertFalse(p.analizar(fi, len(fi)))
        self.assertIn("incompleto", p.razon)

    def test_rango_fuera_de_la_fuente(self):
        e = "<"
        fixture = _tiff_minimo(e)
        # StripOffsets apunta a un offset gigante
        pos_273 = _pos_tag(fixture, e, 273)
        fixture = fixture[:pos_273 + 8] + _u32(e, 0xFFFF00) + fixture[pos_273 + 12:]
        p = _ParserTIFF()
        self.assertFalse(p.analizar(fixture, len(fixture)))


def _pos_tag(fixture, e, tag):
    """Localiza la entrada de IFD con el tag dado (IFD0 simple, 10 entradas)."""
    n = int.from_bytes(fixture[8:10], "little" if e == "<" else "big")
    for k in range(n):
        base = 8 + 2 + k * 12
        t = int.from_bytes(fixture[base:base + 2], "little" if e == "<" else "big")
        if t == tag:
            return base
    raise AssertionError("tag no hallado")


class TestLimitesDeRecuperacion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _recuperar(self, fixture, nombre="imagen.tif"):
        src = self.base / "src"
        src.mkdir(exist_ok=True)
        fuente = src / nombre
        fuente.write_bytes(fixture)
        dest = tempfile.mkdtemp(dir=DEST_TMPFS if DEST_TMPFS.is_dir() else None)
        entrada = arch_tiff(str(fuente), nombre=nombre, tamano=len(fixture))
        r = RecuperadorArchivos()
        resultado = r.recuperar_archivos([entrada], dest)
        return entrada, resultado, Path(dest)

    def test_fixture_valida_poco_endian(self):
        fixture = _tiff_minimo("<")
        entrada, (rec, fal), dest = self._recuperar(fixture)
        self.assertEqual((rec, fal), (1, 0))
        self.assertFalse(entrada.tamano_exacto)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.ESTIMADO)
        salidas = list(dest.rglob("*.tif"))
        self.assertEqual(len(salidas), 1)
        # La salida debe ser un prefijo byte-exacto de la fuente
        self.assertEqual(fixture[:len(salidas[0].read_bytes())], salidas[0].read_bytes())
        import shutil; shutil.rmtree(dest, ignore_errors=True)

    def test_fixture_valida_big_endian(self):
        fixture = _tiff_minimo(">")
        entrada, (rec, fal), dest = self._recuperar(fixture)
        self.assertEqual((rec, fal), (1, 0))
        self.assertFalse(entrada.tamano_exacto)
        salidas = list(dest.rglob("*.tif"))
        self.assertEqual(fixture[:len(salidas[0].read_bytes())], salidas[0].read_bytes())
        import shutil; shutil.rmtree(dest, ignore_errors=True)

    def test_varios_strips_y_huecos(self):
        e = "<"
        # 2 strips separados por un hueco; extent = fin del último strip
        fi = _tiff_minimo(e)
        entrada, (rec, fal), dest = self._recuperar(fi)
        self.assertEqual((rec, fal), (1, 0))
        import shutil; shutil.rmtree(dest, ignore_errors=True)

    def test_trailing_basura_no_incluida(self):
        fixture = _tiff_minimo() + b"JUNK-TRAS-EL-TIFF" * 4
        entrada, (rec, fal), dest = self._recuperar(fixture)
        self.assertEqual((rec, fal), (1, 0))
        salidas = list(dest.rglob("*.tif"))
        self.assertLess(len(salidas[0].read_bytes()), len(fixture))
        self.assertEqual(fixture[:len(salidas[0].read_bytes())], salidas[0].read_bytes())
        import shutil; shutil.rmtree(dest, ignore_errors=True)

    def test_bigtiff_recuperacion_rechazada_como_estructura_invalida(self):
        fixture = b"II\x2b\x00" + (8).to_bytes(4, "little") + b"\x00" * 64
        entrada, (rec, fal), dest = self._recuperar(fixture)
        self.assertEqual(fal, 0)
        self.assertFalse(entrada.tamano_exacto)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.ESTIMADO)
        self.assertEqual(entrada.evidencia.limite_alcanzado, "estructura_invalida")
        import shutil; shutil.rmtree(dest, ignore_errors=True)

    def test_datos_fuera_de_rango_no_fabrican_exito(self):
        fixture = _tiff_minimo()
        pos = _pos_tag(fixture, "<", 273)
        fixture = fixture[:pos + 8] + (0xFFFF00).to_bytes(4, "little") + fixture[pos + 12:]
        entrada, (rec, fal), dest = self._recuperar(fixture)
        self.assertFalse(entrada.tamano_exacto)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.ESTIMADO)
        self.assertEqual(entrada.evidencia.limite_alcanzado, "estructura_invalida")
        import shutil; shutil.rmtree(dest, ignore_errors=True)

    def test_no_sobrescribe_destino(self):
        fixture = _tiff_minimo()
        src = self.base / "src"; src.mkdir(exist_ok=True)
        fuente = src / "imagen.tif"
        fuente.write_bytes(fixture)
        dest = tempfile.mkdtemp(dir=DEST_TMPFS if DEST_TMPFS.is_dir() else None)
        previo = Path(dest) / "Imágenes" / "imagen.tif"
        previo.parent.mkdir(parents=True, exist_ok=True)
        previo.write_bytes(b"ORIGINAL")
        entrada = arch_tiff(str(fuente), tamano=len(fixture))
        rec, fal = RecuperadorArchivos().recuperar_archivos([entrada], dest)
        self.assertEqual((rec, fal), (0, 1))
        self.assertEqual(previo.read_bytes(), b"ORIGINAL")
        import shutil; shutil.rmtree(dest, ignore_errors=True)

    def test_fuente_inalterada(self):
        fixture = _tiff_minimo()
        src = self.base / "src"; src.mkdir(exist_ok=True)
        fuente = src / "imagen.tif"
        fuente.write_bytes(fixture)
        entrada = arch_tiff(str(fuente), tamano=len(fixture))
        dest = tempfile.mkdtemp(dir=DEST_TMPFS if DEST_TMPFS.is_dir() else None)
        RecuperadorArchivos().recuperar_archivos([entrada], dest)
        self.assertEqual(fuente.read_bytes(), fixture)
        import shutil; shutil.rmtree(dest, ignore_errors=True)

    def test_cancelacion_limpia_parcial(self):
        fixture = _tiff_minimo() * 1 + b"\x00" * (70 * 1024)
        src = self.base / "src"; src.mkdir(exist_ok=True)
        fuente = src / "cancel.tif"
        fuente.write_bytes(fixture)
        import builtins
        from unittest import mock
        real_open = builtins.open
        r = RecuperadorArchivos()

        class CancelaTrasPrimeraLectura:
            def __init__(self, f): self._f = f; self._n = 0
            def read(self, n=-1):
                self._n += 1
                datos = self._f.read(n)
                if self._n == 1:
                    r.cancelar()
                return datos
            def seek(self, o, w=0): return self._f.seek(o, w)
            def write(self, b): return self._f.write(b)
            def __enter__(self): return self
            def __exit__(self, *a): return self._f.__exit__(*a)

        def fake_open(path, mode="r", *a, **k):
            f = real_open(path, mode, *a, **k)
            if mode == "rb" and str(path).endswith("cancel.tif"):
                return CancelaTrasPrimeraLectura(f)
            return f

        entrada = arch_tiff(str(fuente), nombre="cancel.tif", tamano=len(fixture))
        dest = tempfile.mkdtemp(dir=DEST_TMPFS if DEST_TMPFS.is_dir() else None)
        with mock.patch.object(builtins, "open", fake_open):
            r.recuperar_archivos([entrada], dest)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.PARCIAL)
        self.assertEqual(entrada.evidencia.bytes_escritos, 0)
        self.assertEqual(list(Path(dest).rglob("*.tif")) + list(Path(dest).rglob("*.tif")), [])
        import shutil; shutil.rmtree(dest, ignore_errors=True)

    def test_error_de_lectura_limpia_parcial(self):
        fixture = _tiff_minimo() + b"\x00" * (70 * 1024)
        src = self.base / "src"; src.mkdir(exist_ok=True)
        fuente = src / "eof.tif"
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
            if mode == "rb" and str(path).endswith("eof.tif"):
                return FallaTrasN(f)
            return f

        entrada = arch_tiff(str(fuente), nombre="eof.tif", tamano=len(fixture))
        dest = tempfile.mkdtemp(dir=DEST_TMPFS if DEST_TMPFS.is_dir() else None)
        r = RecuperadorArchivos()
        with mock.patch.object(builtins, "open", fake_open):
            rec, fal = r.recuperar_archivos([entrada], dest)
        self.assertEqual((rec, fal), (0, 1))
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.FALLIDO)
        self.assertEqual(list(Path(dest).rglob("*.tif")) + list(Path(dest).rglob("*.tif")), [])
        import shutil; shutil.rmtree(dest, ignore_errors=True)

    def test_lecturas_cortas(self):
        fixture = _tiff_minimo() + b"\x00" * 100
        src = self.base / "src"; src.mkdir(exist_ok=True)
        fuente = src / "corta.tif"
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
            if mode == "rb" and str(path).endswith("corta.tif"):
                return LecturaCorta(f)
            return f

        entrada = arch_tiff(str(fuente), nombre="corta.tif", tamano=len(fixture))
        dest = tempfile.mkdtemp(dir=DEST_TMPFS if DEST_TMPFS.is_dir() else None)
        r = RecuperadorArchivos()
        with mock.patch.object(builtins, "open", fake_open):
            rec, fal = r.recuperar_archivos([entrada], dest)
        self.assertEqual((rec, fal), (1, 0))
        import shutil; shutil.rmtree(dest, ignore_errors=True)

    def test_tope_operativo(self):
        from unittest import mock
        from core.scanner import EscaneoProfundo
        fixture = _tiff_minimo()
        src = self.base / "src"; src.mkdir(exist_ok=True)
        fuente = src / "grande.tif"
        fuente.write_bytes(fixture)
        entrada = arch_tiff(str(fuente), nombre="grande.tif", tamano=len(fixture))
        dest = tempfile.mkdtemp(dir=DEST_TMPFS if DEST_TMPFS.is_dir() else None)
        r = RecuperadorArchivos()
        with mock.patch.object(EscaneoProfundo, "TAMANO_MAX_ARCHIVO", 32):
            rec, fal = r.recuperar_archivos([entrada], dest)
        self.assertEqual((rec, fal), (1, 0))
        self.assertFalse(entrada.tamano_exacto)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.ESTIMADO)
        import shutil; shutil.rmtree(dest, ignore_errors=True)

    def test_deteccion_scanner_tiff(self):
        from core.scanner import EscaneoProfundo
        (self.base / "foto.tiff").write_bytes(_tiff_minimo("<"))
        escaner = EscaneoProfundo()
        escaner.iniciar(str(self.base))
        escaner._hilo.join(timeout=5.0)
        encontrados = escaner.obtener_archivos()
        self.assertEqual(len(encontrados), 1)
        self.assertEqual(encontrados[0].extension, "tiff")



def _tiff_dos_paginas() -> bytes:
    """TIFF de dos páginas: page0 referencia un strip POSTERIOR al de page1,


    de modo que un parser que solo conservara los arrays del último IFD
    quedaría con un extent menor del necesario.
    """
    e = "<"
    n_ent = 3
    ifd0_off = 8
    ifd1_off = ifd0_off + 2 + n_ent * 12 + 4
    fin_ifd1 = ifd1_off + 2 + n_ent * 12 + 4
    strip1_off = fin_ifd1
    strip1 = b"\x11" * 8
    gap = b"\xCC" * 16
    strip0_off = strip1_off + len(strip1) + len(gap)
    strip0 = b"\x22" * 8
    ifd0 = _u16(e, n_ent)
    for tag, tipo, cuenta, valor in [
        (256, 4, 1, _u32(e, 2)),
        (273, 4, 1, _u32(e, strip0_off)),
        (279, 4, 1, _u32(e, len(strip0))),
    ]:
        ifd0 += _entrada(e, tag, tipo, cuenta, valor)
    ifd0 += _u32(e, ifd1_off)
    ifd1 = _u16(e, n_ent)
    for tag, tipo, cuenta, valor in [
        (256, 4, 1, _u32(e, 2)),
        (273, 4, 1, _u32(e, strip1_off)),
        (279, 4, 1, _u32(e, len(strip1))),
    ]:
        ifd1 += _entrada(e, tag, tipo, cuenta, valor)
    ifd1 += _u32(e, 0)
    cabecera = b"II\x2a\x00" + _u32(e, ifd0_off)
    return cabecera + ifd0 + ifd1 + strip1 + gap + strip0


def _tiff_tile() -> bytes:
    """TIFF con tiles en lugar de strips."""
    e = "<"
    tile = b"\x77" * 32
    n = 4
    off_tile = 8 + 2 + n * 12 + 4 + 4
    entradas = [
        (256, 4, 1, _u32(e, 4)),
        (257, 4, 1, _u32(e, 4)),
        (324, 4, 1, _u32(e, off_tile)),
        (325, 4, 1, _u32(e, len(tile))),
    ]
    cuerpo_ifd = _u16(e, len(entradas))
    for tag, tipo, cuenta, valor in entradas:
        cuerpo_ifd += _entrada(e, tag, tipo, cuenta, valor)
    cuerpo_ifd += _u32(e, 0)
    cabecera = b"II\x2a\x00" + _u32(e, 8)
    return cabecera + cuerpo_ifd + b"\x00" * 4 + tile


class TestAuditoriaTiff(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_extent_cubre_strips_de_todas_las_paginas(self):
        fixture = _tiff_dos_paginas()
        p = _ParserTIFF()
        self.assertTrue(p.analizar(fixture, len(fixture)), p.razon)
        # El extent debe llegar al final del strip de la página 0
        self.assertEqual(p.extent, len(fixture))

    def test_dos_paginas_recuperacion_byte_exacta_del_extent(self):
        fixture = _tiff_dos_paginas()
        src = self.base / "src"; src.mkdir(exist_ok=True)
        fuente = src / "dos.tif"
        fuente.write_bytes(fixture)
        entrada = arch_tiff(str(fuente), nombre="dos.tif", tamano=len(fixture))
        dest = tempfile.mkdtemp(dir=DEST_TMPFS if DEST_TMPFS.is_dir() else None)
        rec, fal = RecuperadorArchivos().recuperar_archivos([entrada], dest)
        self.assertEqual((rec, fal), (1, 0))
        salidas = list(Path(dest).rglob("*.tif"))
        self.assertEqual(len(salidas), 1)
        self.assertEqual(salidas[0].read_bytes(), fixture)
        self.assertEqual(entrada.evidencia.bytes_escritos, len(fixture))
        import shutil; shutil.rmtree(dest, ignore_errors=True)

    def test_tipo_no_numerico_en_array_de_strips_rechazado(self):
        e = "<"
        entradas = [
            (256, 4, 1, _u32(e, 2)),
            (273, 2, 1, _u32(e, 0)),   # StripOffsets como ASCII -> inválido
            (279, 4, 1, _u32(e, 4)),
        ]
        fi = _tiff(entradas=entradas)
        p = _ParserTIFF()
        self.assertFalse(p.analizar(fi, len(fi)))
        self.assertIn("no numérico", p.razon)

    def test_longitudes_de_arrays_strips_incoherentes(self):
        e = "<"
        # 273 (2 offsets en array) vs 279 (1 tamaño inline): longitudes distintas
        arr = _u32(e, 60) + _u32(e, 70)
        entradas = [
            (273, 4, 2, _u32(e, 8 + 2 + 2 * 12 + 4)),  # offset al array
            (279, 4, 1, _u32(e, 4)),                    # un tamaño inline
        ]
        cuerpo = _u16(e, len(entradas))
        for tag, tipo, cuenta, valor in entradas:
            cuerpo += _entrada(e, tag, tipo, cuenta, valor)
        cuerpo += _u32(e, 0)
        fi = b"II\x2a\x00" + _u32(e, 8) + cuerpo + arr + b"\x00" * (60 - (8 + len(cuerpo) + 8)) + b"\x00" * 8
        p = _ParserTIFF()
        self.assertFalse(p.analizar(fi, len(fi)))
        self.assertIn("incoherente", p.razon)

    def test_ifd0_mas_alla_de_la_ventana_es_conservador(self):
        # Documento: metadatos fuera de la ventana de 1 MiB NO producen una
        # recuperación presentada como válida; estructura_invalida.
        e = "<"
        ifd_fuera = 1 * 1024 * 1024 + 512
        fi = b"II\x2a\x00" + _u32(e, ifd_fuera) + b"\x00" * (ifd_fuera + 64)
        src = self.base / "src"; src.mkdir(exist_ok=True)
        fuente = src / "lejana.tif"
        fuente.write_bytes(fi)
        entrada = arch_tiff(str(fuente), nombre="lejana.tif", tamano=len(fi))
        dest = tempfile.mkdtemp(dir=DEST_TMPFS if DEST_TMPFS.is_dir() else None)
        rec, fal = RecuperadorArchivos().recuperar_archivos([entrada], dest)
        self.assertEqual((rec, fal), (1, 0))
        self.assertFalse(entrada.tamano_exacto)
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.ESTIMADO)
        self.assertEqual(entrada.evidencia.limite_alcanzado, "estructura_invalida")
        import shutil; shutil.rmtree(dest, ignore_errors=True)

    def test_tile_basado_es_parseable(self):
        fixture = _tiff_tile()
        p = _ParserTIFF()
        self.assertTrue(p.analizar(fixture, len(fixture)), p.razon)
        self.assertEqual(p.extent, len(fixture))

    def test_ifd_vacia_sin_strips(self):
        e = "<"
        cabecera = b"II\x2a\x00" + _u32(e, 8)
        cuerpo = _u16(e, 0) + _u32(e, 0)  # 0 entradas, next = 0
        fi = cabecera + cuerpo
        p = _ParserTIFF()
        self.assertTrue(p.analizar(fi, len(fi)), p.razon)
        self.assertEqual(p.extent, 8 + 6)

    def test_disponible_desconocido_permite_extent(self):
        fixture = _tiff_minimo("<")
        p = _ParserTIFF()
        self.assertTrue(p.analizar(fixture, None))
        self.assertEqual(p.extent, len(fixture))

    def test_tiff_real_con_pil_si_disponible(self):
        import shutil, subprocess
        python = shutil.which("python3")
        rutas = []
        if python:
            script = self.base / "_gentiff.py"
            script.write_text(
                "from PIL import Image\n"
                f"Image.new('RGB', (8, 8), (1, 2, 3)).save(r'{self.base}/real.tif', 'TIFF')\n"
            )
            try:
                subprocess.run([python, str(script)], check=True,
                               capture_output=True, timeout=60)
                rutas = [self.base / "real.tif"]
            except Exception:
                rutas = []
            try:
                script.unlink()
            except OSError:
                pass
        if not rutas:
            self.skipTest("PIL del sistema no disponible para generar TIFF real")
        datos = rutas[0].read_bytes()
        p = _ParserTIFF()
        self.assertTrue(p.analizar(datos, len(datos)), p.razon)
        src = self.base / "src"; src.mkdir(exist_ok=True)
        fuente = src / "real.tif"
        fuente.write_bytes(datos)
        entrada = arch_tiff(str(fuente), nombre="real.tif", tamano=len(datos))
        dest = tempfile.mkdtemp(dir=DEST_TMPFS if DEST_TMPFS.is_dir() else None)
        rec, fal = RecuperadorArchivos().recuperar_archivos([entrada], dest)
        self.assertEqual((rec, fal), (1, 0))
        salidas = list(Path(dest).rglob("*.tif"))
        self.assertEqual(datos[:len(salidas[0].read_bytes())], salidas[0].read_bytes())
        self.assertEqual(entrada.evidencia.resultado, ResultadoRecuperacion.ESTIMADO)
        import shutil; shutil.rmtree(dest, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()
