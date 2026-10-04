"""
Módulo de recuperación de archivos
Gestiona la restauración de archivos seleccionados con extracción real de payload
"""

import os
import platform
from pathlib import Path
from typing import Callable, Optional

from core.scanner import (
    ArchivoEncontrado,
    EscaneoProfundo,
    EvidenciaRecuperacion,
    ResultadoRecuperacion,
    obtener_tamano_maximo,
    es_ruta_dispositivo_bloque,
)


class _ParserPNG:
    """
    Parser estructural incremental y conservador de PNG.

    Valida la firma de 8 bytes, el encuadre de chunks
    (longitud de 4 bytes BE, tipo de 4 bytes, datos, CRC-32),
    el orden IHDR primero, la semántica de los campos del IHDR,
    la consecutividad de los IDAT, al menos un IDAT, un IEND de
    longitud 0 como último chunk, y la CRC-32 estándar de cada chunk
    (zlib).

    La memoria es acotada: nunca se reserva memoria proporcional a la
    longitud declarada por un chunk. Los datos se recorren en rodajas
    del bloque de entrada, de modo que una longitud enorme o maliciosa
    no provoca asignaciones grandes. Solo se retiene el IHDR (13 bytes
    fijos) para validar su semántica.

    No decodifica píxeles ni descomprime zlib. Un IEND con todas las
    CRC válidas es evidencia estructural, no prueba de integridad del
    original.
    """

    _SIG = 0
    _LEN = 1
    _TIPO = 2
    _DATA = 3
    _CRC = 4

    _FIRMA = b"\x89PNG\r\n\x1a\n"

    # Límite de longitud permitido por el estándar PNG (bit superior 0).
    _LEN_MAXIMA = 0x7FFFFFFF

    # Profundidades de bits válidas por tipo de color (PNG 2ª ed., tabla 11.1).
    #   0 = escala de grises, 2 = color verdadero, 3 = indexado,
    #   4 = grises con alfa, 6 = color verdadero con alfa.
    _PROFUNDIDADES_POR_COLOR = {
        0: (1, 2, 4, 8, 16),
        2: (8, 16),
        3: (1, 2, 4, 8),
        4: (8, 16),
        6: (8, 16),
    }

    def __init__(self):
        self.estado = self._SIG
        self.pos_sig = 0
        self.len_buf = bytearray()
        self.tipo_buf = bytearray()
        self.crc_buf = bytearray()
        self.restante_data = 0
        self.crc_calculada = 0
        self.tipo_actual = b""
        self.chunks = 0
        self.visto_ihdr = False
        self.visto_idat = False
        self.idat_terminado = False  # ya apareció un chunk no-IDAT tras IDAT
        self.ihdr_buf = bytearray()  # 13 bytes fijos del payload IHDR
        self.ihdr_validado = False
        self.eoi = False  # IEND válido encontrado
        self.invalido = False
        self.razon = ""
        self.crc_mala = False

    def _invalidar(self, razon: str):
        self.invalido = True
        self.razon = razon

    def _tipo_valido(self, tipo: bytes) -> bool:
        """El tipo de chunk debe ser 4 letras ASCII y el bit reservado 0."""
        for c in tipo:
            if not ((0x41 <= c <= 0x5A) or (0x61 <= c <= 0x7A)):
                self._invalidar("Tipo de chunk no alfabético")
                return False
        if tipo[2] & 0x20:
            self._invalidar("Bit reservado activo en el tipo de chunk")
            return False
        return True

    def _validar_ihdr(self) -> bool:
        """Valida la semántica de los 13 bytes del payload IHDR.

        No decodifica píxeles. Comprueba ancho/alto distintos de cero,
        tipo de color permitido, profundidad de bits compatible con el
        tipo de color, y métodos de compresión, filtro y entrelazado
        dentro de los valores permitidos por el estándar.
        """
        datos = bytes(self.ihdr_buf)
        self.ihdr_buf.clear()
        if len(datos) != 13:
            self._invalidar("IHDR con longitud distinta de 13")
            return False
        ancho, alto = int.from_bytes(datos[0:4], "big"), int.from_bytes(datos[4:8], "big")
        profundidad, tipo_color, compresion, filtro, entrelazado = datos[8:13]
        if ancho == 0 or alto == 0:
            self._invalidar("IHDR con ancho o alto igual a cero")
            return False
        if tipo_color not in self._PROFUNDIDADES_POR_COLOR:
            self._invalidar(f"Tipo de color PNG no permitido: {tipo_color}")
            return False
        if profundidad not in self._PROFUNDIDADES_POR_COLOR[tipo_color]:
            self._invalidar(
                f"Profundidad de bits {profundidad} no válida para el tipo de color {tipo_color}"
            )
            return False
        if compresion != 0:
            self._invalidar(f"Método de compresión PNG no permitido: {compresion}")
            return False
        if filtro != 0:
            self._invalidar(f"Método de filtro PNG no permitido: {filtro}")
            return False
        if entrelazado not in (0, 1):
            self._invalidar(f"Método de entrelazado PNG no permitido: {entrelazado}")
            return False
        self.ihdr_validado = True
        return True

    def _validar_orden(self, tipo: bytes) -> bool:
        """Comprueba la posición del chunk dentro de la secuencia PNG.

        Reglas aplicadas:
        - El primer chunk debe ser IHDR (uno solo).
        - Los IDAT deben ser consecutivos: una vez que aparece un chunk
          no-IDAT tras el primer IDAT, ningún IDAT posterior es válido.
        - IEND exige al menos un IDAT previo y longitud 0.
        - Los chunks auxiliares (p. ej. PLTE, tRNS, gAMA) se aceptan en
          cualquier posición válida sin rechazar PNG legítimos.
        """
        if self.chunks == 0 and tipo != b"IHDR":
            self._invalidar("El primer chunk no es IHDR")
            return False
        if tipo == b"IHDR":
            if self.visto_ihdr:
                self._invalidar("IHDR duplicado")
                return False
            if self.restante_data != 13:
                self._invalidar("IHDR con longitud distinta de 13")
                return False
            self.visto_ihdr = True
        elif tipo == b"IDAT":
            if not self.visto_ihdr:
                self._invalidar("IDAT antes de IHDR")
                return False
            if self.idat_terminado:
                self._invalidar("IDAT no consecutivo: apareció tras un chunk no-IDAT")
                return False
            self.visto_idat = True
        elif tipo == b"IEND":
            if not self.visto_ihdr:
                self._invalidar("IEND antes de IHDR")
                return False
            if not self.visto_idat:
                self._invalidar("IEND sin datos IDAT")
                return False
            if self.restante_data != 0:
                self._invalidar("IEND con longitud distinta de 0")
                return False
        else:
            # Chunk auxiliar (o desconocido): tras el primer IDAT cierra la
            # secuencia de IDAT. No se rechaza por sí mismo.
            if self.visto_idat:
                self.idat_terminado = True
        return True

    def _chunk_completado(self):
        # El IHDR se valida semánticamente una vez leídos sus 13 bytes y
        # verificada su CRC. Si es inválido, no se cuenta como chunk válido.
        if self.tipo_actual == b"IHDR" and not self._validar_ihdr():
            return
        self.chunks += 1
        if self.tipo_actual == b"IEND":
            self.eoi = True
            return
        self.estado = self._LEN

    def alimentar(self, data: bytes) -> int:
        """
        Procesa un bloque y devuelve cuántos bytes consumió antes de
        detenerse por IEND o por estructura inválida. Todos los bytes
        consumidos pertenecen al candidato PNG y pueden escribirse tal cual.
        """
        import zlib

        i = 0
        n = len(data)
        while i < n and not self.eoi and not self.invalido:
            estado = self.estado
            if estado == self._SIG:
                while i < n and self.pos_sig < 8:
                    if data[i] != self._FIRMA[self.pos_sig]:
                        self._invalidar("Firma PNG incorrecta")
                        break
                    self.pos_sig += 1
                    i += 1
                if self.invalido:
                    break
                if self.pos_sig == 8:
                    self.estado = self._LEN
            elif estado == self._LEN:
                tomar = min(4 - len(self.len_buf), n - i)
                self.len_buf += data[i:i + tomar]
                i += tomar
                if len(self.len_buf) == 4:
                    longitud = int.from_bytes(self.len_buf, "big")
                    self.len_buf.clear()
                    if longitud > self._LEN_MAXIMA:
                        self._invalidar("Longitud de chunk con bit reservado activo")
                        break
                    self.restante_data = longitud
                    self.estado = self._TIPO
            elif estado == self._TIPO:
                tomar = min(4 - len(self.tipo_buf), n - i)
                self.tipo_buf += data[i:i + tomar]
                i += tomar
                if len(self.tipo_buf) == 4:
                    tipo = bytes(self.tipo_buf)
                    self.tipo_buf.clear()
                    if not self._tipo_valido(tipo):
                        break
                    self.tipo_actual = tipo
                    self.crc_calculada = zlib.crc32(tipo) & 0xFFFFFFFF
                    if not self._validar_orden(tipo):
                        break
                    self.estado = self._CRC if self.restante_data == 0 else self._DATA
            elif estado == self._DATA:
                tomar = min(self.restante_data, n - i)
                if self.tipo_actual == b"IHDR" and len(self.ihdr_buf) < 13:
                    # Solo el IHDR se retiene (13 bytes fijos) para validar
                    # su semántica; nunca según longitudes no confiables.
                    self.ihdr_buf += data[i:i + min(tomar, 13 - len(self.ihdr_buf))]
                self.crc_calculada = zlib.crc32(
                    data[i:i + tomar], self.crc_calculada
                ) & 0xFFFFFFFF
                self.restante_data -= tomar
                i += tomar
                if self.restante_data == 0:
                    self.estado = self._CRC
            elif estado == self._CRC:
                tomar = min(4 - len(self.crc_buf), n - i)
                self.crc_buf += data[i:i + tomar]
                i += tomar
                if len(self.crc_buf) == 4:
                    declarada = int.from_bytes(self.crc_buf, "big")
                    self.crc_buf.clear()
                    if declarada != self.crc_calculada:
                        self.crc_mala = True
                        self._invalidar("CRC de chunk inválida")
                        break
                    self._chunk_completado()
        return i


class _ParserBMP:
    """
    Parser estructural incremental y conservador de BMP de Windows.

    Valida la firma "BM", el tamaño de la cabecera DIB (variantes
    BITMAPINFOHEADER y sus extensiones V2/V3/V4/V5), las dimensiones
    (ancho positivo, alto distinto de cero, incluido el caso top-down
    con alto negativo), los planos, la profundidad de bits, la
    compresión (solo BI_RGB sin comprimir), el desplazamiento a los
    datos de píxel, el tamaño declarado del archivo, el tamaño de
    imagen declarado y el tamaño calculado con filas alineadas a 4 bytes.

    La memoria es acotada: solo se retienen los bytes de la cabecera
    (<= 138 bytes) para validarlos; los datos de píxel no se acumulan.
    El límite se deriva aritméticamente de los campos validados; nunca
    se reserva memoria según tamaños no confiables.

    No decodifica los píxeles. Un límite y una cabecera coherentes son
    evidencia estructural, no prueba de integridad del original.
    """

    _CAB = 0
    _DATOS = 1

    _FIRMA = b"BM"
    # Variantes de cabecera DIB de Windows soportadas: todas comparten los
    # primeros 40 bytes con BITMAPINFOHEADER.
    _DIB_SOPORTADOS = (40, 52, 56, 108, 124)
    _BPP_SOPORTADOS = (1, 4, 8, 16, 24, 32)
    _COMPRESION_SOPORTADA = 0  # BI_RGB (sin comprimir)
    # Mínimo para leer firma (2) + cabecera de archivo (12) + tamaño DIB (4).
    _MIN_CABECERA = 18

    def __init__(self):
        self.estado = self._CAB
        self.buf = bytearray()
        self._cabecera_objetivo = self._MIN_CABECERA
        self._dib_leido = False
        self.dib_size = 0
        self.tamano_total = 0
        self.restante_datos = 0
        self.fin = False
        self.invalido = False
        self.razon = ""

    def _invalidar(self, razon: str):
        self.invalido = True
        self.razon = razon

    def _procesar_cabecera(self) -> bool:
        """Primera fase: lee el tamaño DIB. Segunda: valida los campos."""
        b = bytes(self.buf)
        if not self._dib_leido:
            if b[0:2] != self._FIRMA:
                self._invalidar("Firma BMP incorrecta")
                return False
            dib = int.from_bytes(b[14:18], "little")
            if dib not in self._DIB_SOPORTADOS:
                self._invalidar(f"Tamaño de cabecera DIB no soportado: {dib}")
                return False
            self.dib_size = dib
            self._dib_leido = True
            self._cabecera_objetivo = 14 + dib
            return True
        return self._validar_campos(b)

    def _validar_campos(self, b: bytes) -> bool:
        tam_declarado = int.from_bytes(b[2:6], "little")
        offset_datos = int.from_bytes(b[10:14], "little")
        ancho = int.from_bytes(b[18:22], "little", signed=True)
        alto = int.from_bytes(b[22:26], "little", signed=True)
        planes = int.from_bytes(b[26:28], "little")
        bpp = int.from_bytes(b[28:30], "little")
        compresion = int.from_bytes(b[30:34], "little")
        tam_imagen = int.from_bytes(b[34:38], "little")
        cabecera_total = 14 + self.dib_size

        if ancho <= 0:
            self._invalidar("Ancho BMP no positivo")
            return False
        if alto == 0 or alto == -(2 ** 31):
            self._invalidar("Alto BMP inválido")
            return False
        if planes != 1:
            self._invalidar(f"Planos BMP no soportados: {planes}")
            return False
        if bpp not in self._BPP_SOPORTADOS:
            self._invalidar(f"Bits por píxel no soportados: {bpp}")
            return False
        if compresion != self._COMPRESION_SOPORTADA:
            self._invalidar(f"Compresión BMP no soportada: {compresion}")
            return False
        if offset_datos < cabecera_total:
            self._invalidar("Desplazamiento a datos de píxel dentro de la cabecera")
            return False

        # Fila alineada a DWORD (múltiplo de 4 bytes). Aritmética entera,
        # sin asignar memoria según las dimensiones declaradas.
        alto_abs = abs(alto)
        stride = ((bpp * ancho + 31) // 32) * 4
        pixel_size = stride * alto_abs
        if pixel_size <= 0:
            self._invalidar("Tamaño de datos de píxel nulo")
            return False
        if tam_imagen != 0 and tam_imagen != pixel_size:
            self._invalidar("biSizeImage contradictorio con las dimensiones")
            return False
        total = offset_datos + pixel_size
        if tam_declarado != 0 and tam_declarado != total:
            self._invalidar("Tamaño de archivo declarado contradictorio")
            return False
        if offset_datos >= total or total <= cabecera_total:
            self._invalidar("Límite BMP no posterior a la cabecera")
            return False

        self.tamano_total = total
        self.restante_datos = total - cabecera_total
        self.estado = self._DATOS
        return True

    def alimentar(self, data: bytes) -> int:
        """
        Procesa un bloque y devuelve cuántos bytes consumió antes de
        alcanzar el límite validado o de detectar estructura inválida.
        Todos los bytes consumidos pertenecen al candidato BMP.
        """
        i = 0
        n = len(data)
        while i < n and not self.fin and not self.invalido:
            if self.estado == self._CAB:
                faltan = self._cabecera_objetivo - len(self.buf)
                tomar = min(faltan, n - i)
                self.buf += data[i:i + tomar]
                i += tomar
                if len(self.buf) == self._cabecera_objetivo:
                    if not self._procesar_cabecera():
                        break
                    if self.estado == self._CAB:
                        # Ya se conoce el tamaño DIB; seguir acumulando.
                        continue
            elif self.estado == self._DATOS:
                tomar = min(self.restante_datos, n - i)
                self.restante_datos -= tomar
                i += tomar
                if self.restante_datos == 0:
                    self.fin = True
        return i


class _ParserJPEG:
    """
    Parser estructural incremental y conservador de JPEG.

    Solo determina si la secuencia de marcadores es plausible y dónde
    terminaría el límite del archivo (EOI). No decodifica imagen.

    Estados: ESPERA_SOI, ESPERA_FF, TIPO, LEN_HI, LEN_LO, CONTENIDO,
    DATOS, DATOS_FF. El sub-estado `post_contenido` decide si tras un
    segmento con longitud volvemos a ESPERA_FF (cabecera) o a DATOS
    (tras un SOS dentro de datos de escaneo).
    """

    _ESPERA_SOI = 0
    _ESPERA_FF = 1
    _TIPO = 2
    _LEN_HI = 3
    _LEN_LO = 4
    _CONTENIDO = 5
    _DATOS = 6
    _DATOS_FF = 7

    def __init__(self):
        self.estado = self._ESPERA_SOI
        self.tipo = 0
        self.post_contenido = self._ESPERA_FF
        self.restante = 0
        self.len_hi = 0
        self.soi_visto = False
        self.eoi = False
        self.invalido = False
        self.razon = ""

    def _es_marcador_sin_longitud(self, t: int) -> bool:
        return t in (0x01,) or 0xD0 <= t <= 0xD7

    def alimentar(self, data: bytes) -> int:
        """
        Procesa el bloque y devuelve cuántos bytes consumió antes de
        detenerse por EOI o estructura inválida. Todos los bytes
        consumidos pertenecen al JPEG y pueden escribirse tal cual.
        """
        consumido = 0
        for b in data:
            if self.eoi or self.invalido:
                break
            consumido += 1
            if self.estado == self._ESPERA_SOI:
                if not self.soi_visto:
                    if b != 0xFF:
                        self.invalido, self.razon = True, "Se esperaba SOI (FF D8)"
                        break
                    self.soi_visto = True
                else:
                    if b != 0xD8:
                        self.invalido, self.razon = True, "Se esperaba D8 tras FF"
                        break
                    self.estado = self._ESPERA_FF
            elif self.estado == self._ESPERA_FF:
                if b != 0xFF:
                    self.invalido, self.razon = True, "Se esperaba 0xFF de marcador"
                    break
                self.estado = self._TIPO
            elif self.estado == self._TIPO:
                if b == 0xFF:
                    # FF de relleno antes del tipo
                    continue
                t = b
                if t == 0xD9:
                    self.eoi = True
                    break
                if t == 0xD8:
                    self.invalido, self.razon = True, "SOI duplicado inesperado"
                    break
                if self._es_marcador_sin_longitud(t):
                    self.estado = self._ESPERA_FF
                    continue
                if t == 0xDA:
                    self.tipo = t
                    self.post_contenido = self._DATOS
                else:
                    self.tipo = t
                    self.post_contenido = self._ESPERA_FF
                self.estado = self._LEN_HI
            elif self.estado == self._LEN_HI:
                self.len_hi = b
                self.estado = self._LEN_LO
            elif self.estado == self._LEN_LO:
                n = (self.len_hi << 8) | b
                if n < 2:
                    self.invalido, self.razon = True, f"Longitud de segmento inválida: {n}"
                    break
                self.restante = n - 2
                if self.restante == 0:
                    self.estado = self.post_contenido
                else:
                    self.estado = self._CONTENIDO
            elif self.estado == self._CONTENIDO:
                self.restante -= 1
                if self.restante == 0:
                    self.estado = self.post_contenido
            elif self.estado == self._DATOS:
                if b == 0xFF:
                    self.estado = self._DATOS_FF
            elif self.estado == self._DATOS_FF:
                if b == 0x00:
                    # Byte stuffing: dato, no marcador
                    self.estado = self._DATOS
                elif 0xD0 <= b <= 0xD7:
                    # Marcador de reinicio
                    self.estado = self._DATOS
                elif b == 0xD9:
                    self.eoi = True
                    break
                elif b == 0xDA:
                    self.tipo = b
                    self.post_contenido = self._DATOS
                    self.estado = self._LEN_HI
                elif b == 0xFF:
                    # Relleno de FF repetidos
                    continue
                elif b == 0xD8:
                    self.invalido, self.razon = True, "SOI dentro de datos de escaneo"
                    break
                else:
                    # Otro marcador con longitud dentro de datos de escaneo:
                    # se tolera; tras su contenido se reanudan datos
                    self.tipo = b
                    self.post_contenido = self._DATOS
                    self.estado = self._LEN_HI
        return consumido


class _ParserGIF:
    """
    Parser estructural incremental y conservador de GIF87a/GIF89a.

    Valida la cabecera de versión, el Logical Screen Descriptor, la Tabla
    Global de Colores (si el packed de la pantalla la indica), y recorre
    el cuerpo interpretando descriptores de imagen (0x2C) con sus Tablas
    Locales de Colores, extensiones (0x21) con sus introductores y
    longitudes de sub-bloque, los datos de imagen (mínimo LZW + secuencia
    de sub-bloques terminada en 0x00) y el trailer 0x3B.

    La memoria es acotada: las Tablas de Colores, los descriptores de
    imagen y los sub-bloques se atraviesan contando bytes, nunca
    acumulando su contenido; solo se retiene un contador de cada bloque
    en curso. Las dimensiones de la pantalla se validan sintácticamente
    pero no se usan para reservar memoria.

    Estructura de extensión: introducer 0x21, etiqueta de 1 byte, y una
    secuencia de (tamaño, datos[tamaño]) que termina con tamaño 0. Las
    extensiones con cabecera fija (GCE tamaño 4, Application tamaño 11,
    Plain Text tamaño 12) validan esa longitud fija en su primer bloque
    y después continúan con la secuencia de sub-bloques. Los datos de
    imagen usan la misma secuencia de sub-bloques. Un 0x3B dentro de
    cualquier payload de datos NO termina el parseo porque el parser no
    busca bytes, sino que cuadra las longitudes declaradas.

    No decodifica LZW ni píxeles: un trailer válido es evidencia
    estructural, no prueba de integridad del original.
    """

    _CABECERA = 0      # firma "GIF87a"/"GIF89a"
    _LSD = 1           # Logical Screen Descriptor (7 bytes)
    _GCT = 2           # Global Color Table (si aplica)
    _BLOQUE = 3        # siguiente introducer: 0x21, 0x2C o 0x3B
    _EXT_LABEL = 4     # etiqueta de extensión
    _EXT_PRIMER_BLOQUE = 5
    _EXT_BLOQUE = 6    # tamaño de sub-bloque de extensión
    _EXT_DATOS = 7     # datos de sub-bloque de extensión
    _IMG_DESC = 8      # Image Descriptor (9 bytes)
    _LCT = 9           # Local Color Table (si aplica)
    _LZW = 10          # mínimo código LZW (1 byte, 2..8)
    _IMG_BLOQUE = 11   # tamaño de sub-bloque de datos de imagen
    _IMG_DATOS = 12    # datos de sub-bloque de imagen

    # Introductores de bloque
    _EXT = 0x21
    _SEP_IMAGEN = 0x2C
    _TRAILER = 0x3B

    # Etiquetas de extensión
    _ETIQUETA_GCE = 0xF9   # Graphic Control Extension: primer bloque fijo de 4
    _ETIQUETA_APP = 0xFF   # Application Extension: primer bloque fijo de 11
    _ETIQUETA_TEXTO = 0x01  # Plain Text Extension: primer bloque fijo de 12
    _ETIQUETA_COMENTARIO = 0xFE  # Comment Extension: sub-bloques directos

    _FIXTO = {_ETIQUETA_GCE: 4, _ETIQUETA_APP: 11, _ETIQUETA_TEXTO: 12}

    def __init__(self):
        self.estado = self._CABECERA
        self.buf = bytearray()          # buffers pequeños de cabecera/LSD/desc
        self._objetivo = 6              # bytes esperados en el estado actual
        self.restante = 0               # bytes de tabla/datos por consumir
        self.etiqueta = 0
        self._primer_bloque_ext = True
        self._bloques_ext_ok = False
        self.imagenes = 0
        self.extensiones = 0
        self.vio_trailer = False
        self.fin = False
        self.invalido = False
        self.razon = ""

    def _invalidar(self, razon: str):
        self.invalido = True
        self.razon = razon

    def _tabla_tamano(self, bits: int) -> int:
        """Tamaño en bytes de una tabla de colores de 2^(bits+1) entradas."""
        return 3 * (1 << (bits + 1))

    def _procesar_cabecera(self) -> bool:
        if bytes(self.buf) not in (b"GIF87a", b"GIF89a"):
            self._invalidar("Firma GIF incorrecta o versión no soportada")
            return False
        self.estado = self._LSD
        self._objetivo = 7
        self.buf.clear()
        return True

    def _procesar_lsd(self) -> bool:
        b = bytes(self.buf)
        self.buf.clear()
        ancho = int.from_bytes(b[0:2], "little")
        alto = int.from_bytes(b[2:4], "little")
        packed = b[4]
        if ancho == 0 or alto == 0:
            self._invalidar("Dimensiones de pantalla nulas")
            return False
        if packed & 0x80:
            self.estado = self._GCT
            self.restante = self._tabla_tamano(packed & 0x07)
        else:
            self.estado = self._BLOQUE
        return True

    def _procesar_descriptor_imagen(self) -> bool:
        b = bytes(self.buf)
        self.buf.clear()
        ancho = int.from_bytes(b[4:6], "little")
        alto = int.from_bytes(b[6:8], "little")
        packed = b[8]
        if ancho == 0 or alto == 0:
            self._invalidar("Descriptor de imagen con dimensiones nulas")
            return False
        self.imagenes += 1
        if packed & 0x80:
            self.estado = self._LCT
            self.restante = self._tabla_tamano(packed & 0x07)
        else:
            self.estado = self._LZW
        return True

    def alimentar(self, data: bytes) -> int:
        """
        Procesa un bloque y devuelve cuántos bytes consumió antes de
        alcanzar el trailer, detectar estructura inválida o agotar el
        bloque. Todos los bytes consumidos pertenecen al GIF.
        """
        i = 0
        n = len(data)
        while i < n and not self.fin and not self.invalido:
            estado = self.estado
            if estado in (self._CABECERA, self._LSD, self._IMG_DESC):
                faltan = self._objetivo - len(self.buf)
                tomar = min(faltan, n - i)
                self.buf += data[i:i + tomar]
                i += tomar
                if len(self.buf) == self._objetivo:
                    if estado == self._CABECERA:
                        if not self._procesar_cabecera():
                            break
                    elif estado == self._LSD:
                        if not self._procesar_lsd():
                            break
                    else:
                        if not self._procesar_descriptor_imagen():
                            break
            elif estado in (self._GCT, self._LCT):
                tomar = min(self.restante, n - i)
                self.restante -= tomar
                i += tomar
                if self.restante == 0:
                    self.estado = self._BLOQUE if estado == self._GCT else self._LZW
            elif estado == self._BLOQUE:
                intro = data[i]
                i += 1
                if intro == self._EXT:
                    self.estado = self._EXT_LABEL
                elif intro == self._SEP_IMAGEN:
                    self.estado = self._IMG_DESC
                    self._objetivo = 9
                    self.buf.clear()
                elif intro == self._TRAILER:
                    if self.imagenes == 0:
                        self._invalidar("Trailer sin ningún descriptor de imagen")
                        break
                    self.vio_trailer = True
                    self.fin = True
                else:
                    self._invalidar(f"Introductor de bloque no válido: 0x{intro:02X}")
                    break
            elif estado == self._EXT_LABEL:
                self.etiqueta = data[i]
                i += 1
                self.extensiones += 1
                self._primer_bloque_ext = True
                self._bloques_ext_ok = False
                self.estado = self._EXT_BLOQUE
            elif estado == self._EXT_BLOQUE:
                tam = data[i]
                i += 1
                if tam == 0:
                    # Terminador de sub-bloques de la extensión
                    self.estado = self._BLOQUE
                else:
                    fijado = self._FIXTO.get(self.etiqueta)
                    if self._primer_bloque_ext and fijado is not None and tam != fijado:
                        self._invalidar(
                            f"Extensión 0x{self.etiqueta:02X} con cabecera fija "
                            f"de {fijado} bytes, declarada {tam}"
                        )
                        break
                    self._primer_bloque_ext = False
                    self._bloques_ext_ok = True
                    self.restante = tam
                    self.estado = self._EXT_DATOS
            elif estado == self._EXT_DATOS:
                tomar = min(self.restante, n - i)
                self.restante -= tomar
                i += tomar
                if self.restante == 0:
                    self.estado = self._EXT_BLOQUE
            elif estado == self._LZW:
                minimo = data[i]
                i += 1
                if not (2 <= minimo <= 8):
                    self._invalidar(f"Tamaño mínimo de código LZW no permitido: {minimo}")
                    break
                self.estado = self._IMG_BLOQUE
            elif estado == self._IMG_BLOQUE:
                tam = data[i]
                i += 1
                if tam == 0:
                    self.estado = self._BLOQUE
                else:
                    self.restante = tam
                    self.estado = self._IMG_DATOS
            elif estado == self._IMG_DATOS:
                tomar = min(self.restante, n - i)
                self.restante -= tomar
                i += tomar
                if self.restante == 0:
                    self.estado = self._IMG_BLOQUE
        return i


class RecuperadorArchivos:
    """
    Gestiona la recuperación de archivos seleccionados.
    
    Implementa extracción real de payload desde dispositivos de bloque,
    leyendo los bytes directamente del offset donde se encontró el header.
    """

    def __init__(self):
        self._progreso_callback: Callable = None
        self._cancelar = False

    def registrar_callback_progreso(self, callback: Callable):
        """Registra callback para reportar progreso."""
        self._progreso_callback = callback

    def cancelar(self):
        """Cancela la recuperación en curso."""
        self._cancelar = True

    def recuperar_archivos(
        self,
        archivos: list[ArchivoEncontrado],
        destino: str
    ) -> tuple[int, int]:
        """
        Recupera los archivos seleccionados al destino especificado.
        
        Implementa extracción real de payload:
        1. Abre el dispositivo de bloque en modo binario ('rb')
        2. Seek al offset donde se encontró el header
        3. Lee el payload completo (hasta footer o tamaño máximo)
        4. Escribe al archivo destino en modo binario ('wb')
        
        Args:
            archivos: Lista de archivos a recuperar
            destino: Ruta de destino
            
        Returns:
            Tuple (archivos_recuperados, archivos_fallidos)
        """
        self._cancelar = False
        destino_path = Path(destino)
        destino_path.mkdir(parents=True, exist_ok=True)

        recuperados = 0
        fallidos = 0

        for i, archivo in enumerate(archivos):
            if self._cancelar:
                break

            try:
                # Notificar progreso
                if self._progreso_callback:
                    self._progreso_callback(i + 1, len(archivos), archivo.nombre)

                # Crear estructura de carpetas por tipo
                carpeta_tipo = destino_path / archivo.categoria
                carpeta_tipo.mkdir(exist_ok=True)

                # Ruta de destino
                ruta_destino = carpeta_tipo / archivo.nombre

                # AUD-005: validar dispositivo antes de extraer y de escribir
                if not self._destino_distinto_del_origen(archivo, ruta_destino.parent):
                    print(f"[ERROR] Destino en el mismo dispositivo que el origen: {ruta_destino}")
                    archivo.evidencia = EvidenciaRecuperacion(
                        tamano_detectado=archivo.tamano,
                        resultado=ResultadoRecuperacion.FALLIDO,
                        detalle="Destino en el mismo dispositivo que el origen",
                    )
                    fallidos += 1
                    continue
                if ruta_destino.exists():
                    print(f"[ERROR] El archivo ya existe, no se sobrescribirá: {ruta_destino}")
                    archivo.evidencia = EvidenciaRecuperacion(
                        tamano_detectado=archivo.tamano,
                        resultado=ResultadoRecuperacion.FALLIDO,
                        detalle="El archivo ya existe en destino; no se sobrescribió",
                    )
                    fallidos += 1
                    continue

                # Extraer payload real desde el dispositivo
                extension_actual = archivo.extension.lower()
                if extension_actual in ("jpg", "png", "bmp", "gif"):
                    # JPEG/PNG/BMP: extracción incremental con memoria acotada
                    ruta_origen = self._extraer_ruta_dispositivo(archivo.ruta)
                    if not ruta_origen:
                        print(f"[ERROR] No se pudo determinar dispositivo para {archivo.nombre}")
                        archivo.evidencia = EvidenciaRecuperacion(
                            tamano_detectado=archivo.tamano,
                            resultado=ResultadoRecuperacion.FALLIDO,
                            detalle="No se pudo determinar la ruta de origen",
                        )
                        fallidos += 1
                        continue
                    extractores = {
                        "jpg": self._extraer_jpeg_stream,
                        "png": self._extraer_png_stream,
                        "bmp": self._extraer_bmp_stream,
                        "gif": self._extraer_gif_stream,
                    }
                    extractor = extractores[extension_actual]
                    try:
                        resultado_stream = extractor(
                            ruta_origen, archivo.offset, ruta_destino,
                            EscaneoProfundo.TAMANO_MAX_ARCHIVO, archivo
                        )
                    except FileExistsError:
                        print(f"[ERROR] El archivo apareció durante la recuperación, no se sobrescribirá: {ruta_destino}")
                        archivo.evidencia = EvidenciaRecuperacion(
                            tamano_detectado=archivo.tamano,
                            resultado=ResultadoRecuperacion.FALLIDO,
                            detalle="El archivo apareció durante la recuperación",
                        )
                        resultado_stream = None
                    if resultado_stream is None:
                        fallidos += 1
                    elif resultado_stream[0] == 0:
                        # Fuente vacía: considerar fallo sin reportar éxito
                        try:
                            if ruta_destino.exists():
                                ruta_destino.unlink()
                        except OSError:
                            pass
                        archivo.evidencia = EvidenciaRecuperacion(
                            tamano_detectado=archivo.tamano,
                            resultado=ResultadoRecuperacion.FALLIDO,
                            detalle="La fuente no produjo bytes (vacía o ilegible)",
                        )
                        fallidos += 1
                    else:
                        recuperados += 1
                        bytes_escritos, limite = resultado_stream
                        archivo.evidencia = self._construir_evidencia(archivo, bytes_escritos, limite)
                    continue

                payload_extraido = self._extraer_payload(archivo)

                if payload_extraido and len(payload_extraido) > 0:
                    # AUD-005: creación exclusiva ('xb') para evitar sobrescrituras y carreras.
                    # Nunca se usa "wb" sobre una ruta existente.
                    try:
                        with open(ruta_destino, "xb") as f:
                            f.write(payload_extraido)
                        recuperados += 1
                        archivo.evidencia = self._construir_evidencia(archivo, len(payload_extraido))
                    except FileExistsError:
                        print(f"[ERROR] El archivo apareció durante la recuperación, no se sobrescribirá: {ruta_destino}")
                        archivo.evidencia = EvidenciaRecuperacion(
                            tamano_detectado=archivo.tamano,
                            resultado=ResultadoRecuperacion.FALLIDO,
                            detalle="El archivo apareció durante la recuperación",
                        )
                        fallidos += 1
                    except Exception as e:
                        # Solo se elimina el archivo que ESTA recuperación creó; nunca uno preexistente
                        try:
                            if ruta_destino.exists():
                                ruta_destino.unlink()
                        except OSError:
                            pass
                        print(f"[ERROR] Enviando payload a disco falló {archivo.nombre}: {e}")
                        archivo.evidencia = EvidenciaRecuperacion(
                            tamano_detectado=archivo.tamano,
                            resultado=ResultadoRecuperacion.FALLIDO,
                            bytes_escritos=0,
                            detalle=f"Error de escritura: {e}",
                        )
                        fallidos += 1
                else:
                    print(f"[ADVERTENCIA] No se pudo extraer payload para {archivo.nombre}")
                    archivo.evidencia = EvidenciaRecuperacion(
                        tamano_detectado=archivo.tamano,
                        resultado=ResultadoRecuperacion.FALLIDO,
                        detalle="No se pudo extraer el payload",
                    )
                    fallidos += 1

            except Exception as e:
                print(f"[ERROR] Error recuperando {archivo.nombre}: {e}")
                fallidos += 1

        return recuperados, fallidos

    def _construir_evidencia(
        self,
        archivo: ArchivoEncontrado,
        bytes_escritos: int,
        limite_alcanzado: Optional[str] = None
    ) -> EvidenciaRecuperacion:
        """Construye la evidencia según el resultado de la extracción.

        Nunca se afirma completitud por encontrar un marcador: COMPLETO_ESTRUCTURAL
        exige validación estructural (hoy no existe para ningún formato).
        """
        if archivo.extension.lower() == "jpg" and archivo.tamano_exacto is False:
            if limite_alcanzado == "estructura_invalida":
                return EvidenciaRecuperacion(
                    tamano_detectado=archivo.tamano,
                    limite_exacto=False,
                    estructura_validada=None,
                    bytes_escritos=bytes_escritos,
                    resultado=ResultadoRecuperacion.ESTIMADO,
                    limite_alcanzado="estructura_invalida",
                    detalle="Estructura JPEG inválida o insuficiente; límite no demostrado",
                )
            return EvidenciaRecuperacion(
                tamano_detectado=archivo.tamano,
                limite_exacto=False,
                estructura_validada=None,
                bytes_escritos=bytes_escritos,
                resultado=ResultadoRecuperacion.ESTIMADO,
                limite_alcanzado=limite_alcanzado or "fin_fuente",
                detalle="JPEG sin marcador EOI dentro del límite de recursos de 100 MiB",
            )
        if archivo.extension.lower() == "jpg" and archivo.tamano_exacto is True:
            return EvidenciaRecuperacion(
                tamano_detectado=archivo.tamano,
                limite_exacto=True,
                estructura_validada=None,  # sin validador JPEG implementado
                bytes_escritos=bytes_escritos,
                resultado=ResultadoRecuperacion.AMBIGUO,
                limite_alcanzado=limite_alcanzado or "marcador_fin",
                detalle="Límite por EOI tras estructura plausibles interpretada parcialmente (sin decodificar la imagen)",
            )
        if archivo.extension.lower() == "png":
            return self._construir_evidencia_png(archivo, bytes_escritos, limite_alcanzado)
        if archivo.extension.lower() == "bmp":
            return self._construir_evidencia_bmp(archivo, bytes_escritos, limite_alcanzado)
        if archivo.extension.lower() == "gif":
            return self._construir_evidencia_gif(archivo, bytes_escritos, limite_alcanzado)
        # Formatos sin validación estructural implementada: siempre estimado
        return EvidenciaRecuperacion(
            tamano_detectado=archivo.tamano,
            limite_exacto=None,
            estructura_validada=None,
            bytes_escritos=bytes_escritos,
            resultado=ResultadoRecuperacion.ESTIMADO,
            limite_alcanzado="fin_fuente_o_maximo",
            detalle="Sin validador estructural para este formato; tamaño acotado por máximo",
        )

    def _construir_evidencia_png(
        self,
        archivo: ArchivoEncontrado,
        bytes_escritos: int,
        limite_alcanzado: Optional[str]
    ) -> EvidenciaRecuperacion:
        """
        Evidencia honesta para PNG.

        Un IEND con encuadre y CRCs válidas demuestra el límite del
        candidato y valida su estructura de chunks, pero NO prueba que el
        contenido original esté intacto (no se decodifica el flujo zlib ni
        los filtros). Por eso el resultado es AMBIGUO, nunca COMPLETO.
        """
        if archivo.tamano_exacto is True:
            return EvidenciaRecuperacion(
                tamano_detectado=archivo.tamano,
                limite_exacto=True,
                estructura_validada=True,
                bytes_escritos=bytes_escritos,
                resultado=ResultadoRecuperacion.AMBIGUO,
                limite_alcanzado=limite_alcanzado or "marcador_fin",
                detalle="IEND con encuadre y CRC-32 válidos; estructura de chunks validada. No se decodificó la imagen: la integridad del contenido no está verificada.",
            )
        detalle = (
            "Estructura PNG inválida o insuficiente; límite no demostrado"
            if limite_alcanzado == "estructura_invalida"
            else "PNG sin IEND válido dentro del límite operativo; límite no demostrado"
        )
        return EvidenciaRecuperacion(
            tamano_detectado=archivo.tamano,
            limite_exacto=False,
            estructura_validada=None,
            bytes_escritos=bytes_escritos,
            resultado=ResultadoRecuperacion.ESTIMADO,
            limite_alcanzado=limite_alcanzado or "fin_fuente",
            detalle=detalle,
        )

    def _construir_evidencia_bmp(
        self,
        archivo: ArchivoEncontrado,
        bytes_escritos: int,
        limite_alcanzado: Optional[str]
    ) -> EvidenciaRecuperacion:
        """
        Evidencia honesta para BMP.

        Un límite derivado de la cabecera validada demuestra la extensión
        declarada del candidato y valida su estructura de cabecera, pero NO
        prueba que los píxeles originales estén intactos ni que no exista
        fragmentación. Por eso el resultado es AMBIGUO, nunca COMPLETO.
        """
        if archivo.tamano_exacto is True:
            return EvidenciaRecuperacion(
                tamano_detectado=archivo.tamano,
                limite_exacto=True,
                estructura_validada=True,
                bytes_escritos=bytes_escritos,
                resultado=ResultadoRecuperacion.AMBIGUO,
                limite_alcanzado=limite_alcanzado or "limite_declarado",
                detalle="Límite derivado de la cabecera BMP (tamaño declarado y filas alineadas a 4 bytes). No se decodificaron los píxeles: la integridad del contenido no está verificada.",
            )
        detalle = (
            "Estructura BMP inválida o insuficiente; límite no demostrado"
            if limite_alcanzado == "estructura_invalida"
            else "BMP sin límite declarado alcanzable dentro del tope operativo; límite no demostrado"
        )
        return EvidenciaRecuperacion(
            tamano_detectado=archivo.tamano,
            limite_exacto=False,
            estructura_validada=None,
            bytes_escritos=bytes_escritos,
            resultado=ResultadoRecuperacion.ESTIMADO,
            limite_alcanzado=limite_alcanzado or "fin_fuente",
            detalle=detalle,
        )

    def _construir_evidencia_gif(
        self,
        archivo: ArchivoEncontrado,
        bytes_escritos: int,
        limite_alcanzado: Optional[str]
    ) -> EvidenciaRecuperacion:
        """
        Evidencia honesta para GIF.

        Un trailer 0x3B alcanzado tras una secuencia estructuralmente
        válida de bloques demuestra el límite del candidato y valida su
        estructura de cabecera, tablas, descriptores y extensiones, pero
        NO prueba que los píxeles originales estén intactos (no se
        decodifica el flujo LZW). Por eso el resultado es AMBIGUO, nunca
        COMPLETO.
        """
        if archivo.tamano_exacto is True:
            return EvidenciaRecuperacion(
                tamano_detectado=archivo.tamano,
                limite_exacto=True,
                estructura_validada=True,
                bytes_escritos=bytes_escritos,
                resultado=ResultadoRecuperacion.AMBIGUO,
                limite_alcanzado=limite_alcanzado or "marcador_fin",
                detalle="Trailer GIF alcanzado tras estructura de bloques válida; tablas y extensiones consistentes. No se decodificó el flujo LZW: la integridad del contenido no está verificada.",
            )
        detalle = (
            "Estructura GIF inválida o insuficiente; límite no demostrado"
            if limite_alcanzado == "estructura_invalida"
            else "GIF sin trailer válido dentro del límite operativo; límite no demostrado"
        )
        return EvidenciaRecuperacion(
            tamano_detectado=archivo.tamano,
            limite_exacto=False,
            estructura_validada=None,
            bytes_escritos=bytes_escritos,
            resultado=ResultadoRecuperacion.ESTIMADO,
            limite_alcanzado=limite_alcanzado or "fin_fuente",
            detalle=detalle,
        )

    def _extraer_payload(self, archivo: ArchivoEncontrado) -> Optional[bytes]:
        """
        Extrae el payload completo de un archivo desde el dispositivo de bloque.
        
        Args:
            archivo: Archivo con información de offset y ruta del dispositivo
            
        Returns:
            Bytes del payload extraído, o None si falla
        """
        # Obtener tamaño máximo según tipo de archivo
        tamano_maximo = obtener_tamano_maximo(archivo.extension)

        # Extraer la ruta del dispositivo desde la ruta del archivo
        # Formato esperado: "/dev/sdX#offset=12345" o "\\\\.\\D:#offset=12345"
        ruta_dispositivo = self._extraer_ruta_dispositivo(archivo.ruta)
        offset = archivo.offset
        
        if not ruta_dispositivo:
            print(f"[ERROR] No se pudo determinar dispositivo para {archivo.nombre}")
            return None
        
        try:
            # Verificar si es un dispositivo de bloque
            if es_ruta_dispositivo_bloque(ruta_dispositivo):
                return self._extraer_desde_dispositivo(ruta_dispositivo, offset, tamano_maximo)
            else:
                # Si es un archivo regular, leer directamente
                return self._extraer_desde_archivo(ruta_dispositivo, offset, tamano_maximo)
                
        except PermissionError:
            print(f"[ERROR] Permiso denegado para leer {ruta_dispositivo}. Ejecute con sudo/Administrador.")
            return None
        except Exception as e:
            print(f"[ERROR] Extrayendo payload de {archivo.nombre}: {e}")
            return None

    def _extraer_ruta_dispositivo(self, ruta_archivo: str) -> Optional[str]:
        """
        Extrae la ruta del dispositivo desde la ruta del archivo.
        
        Args:
            ruta_archivo: Ruta en formato "/dev/sdX#offset=12345"
            
        Returns:
            Ruta del dispositivo o None
        """
        if "#offset=" in ruta_archivo:
            return ruta_archivo.split("#offset=")[0]
        return ruta_archivo

    def _destino_distinto_del_origen(self, archivo: ArchivoEncontrado, carpeta_destino: Path) -> bool:
        """
        Devuelve True solo si se puede afirmar con seguridad que el destino
        está en un dispositivo distinto del origen del archivo recuperado.

        - Origen regular (POSIX): compara st_dev del archivo origen con el de la
          carpeta destino (dispositivo de filesystem).
        - Origen bloque montado (POSIX): compara st_dev del punto de montaje del
          dispositivo (vía psutil) con el de la carpeta destino.
        - Windows: compara la letra de unidad; orígenes PhysicalDriveN sin letra
          se consideran indeterminados.
        - Si no se puede establecer la identidad con seguridad, devuelve False
          (fallo seguro).

        Comparar st_dev NO garantiza distinto dispositivo físico en todos los
        casos (varias particiones pueden compartir disco físico); esta
        verificación es sobre el dispositivo de filesystem y es deliberada:
        es la frontera de seguridad trazable desde Python de forma estándar.
        """
        ruta_origen = self._extraer_ruta_dispositivo(archivo.ruta)
        if not ruta_origen or platform.system() == "Windows":
            return self._destino_distinto_windows(ruta_origen, carpeta_destino)
        return self._destino_distinto_posix(ruta_origen, carpeta_destino)

    def _destino_distinto_posix(self, ruta_origen: str, carpeta_destino: Path) -> bool:
        try:
            dev_destino = self._st_dev_de(carpeta_destino)
            if dev_destino is None:
                return False
            if es_ruta_dispositivo_bloque(ruta_origen):
                dev_origen = self._st_dev_dispositivo_desde_montaje(ruta_origen)
            else:
                dev_origen = os.stat(ruta_origen).st_dev
            if dev_origen is None:
                return False
            return dev_origen != dev_destino
        except (OSError, PermissionError):
            return False

    def _st_dev_de(self, ruta: Path) -> Optional[int]:
        """st_dev de la carpeta destino; si no existe, del ancestro más cercano."""
        actual = ruta
        while True:
            try:
                return os.stat(actual).st_dev
            except (OSError, PermissionError):
                if actual.parent == actual:
                    return None
                actual = actual.parent

    def _st_dev_dispositivo_desde_montaje(self, ruta_dispositivo: str) -> Optional[int]:
        """st_dev del filesystem del dispositivo, localizando su punto de montaje."""
        try:
            import psutil
            for particion in psutil.disk_partitions(all=True):
                if particion.device == ruta_dispositivo and particion.mountpoint:
                    try:
                        return os.stat(particion.mountpoint).st_dev
                    except (OSError, PermissionError):
                        continue
        except Exception:
            pass
        return None

    def _destino_distinto_windows(self, ruta_origen: Optional[str], carpeta_destino: Path) -> bool:
        try:
            letra_destino = Path(carpeta_destino).drive.upper()
            if not letra_destino:
                return False
            if not ruta_origen:
                return False
            origen = ruta_origen
            if origen.startswith("\\\\.\\"):
                resto = origen[4:]
                if len(resto) >= 2 and resto[1] == ":":
                    letra_origen = resto[:2].upper()
                    return letra_origen != letra_destino
                # PhysicalDriveN u otro: indeterminado
                return False
            letra_origen = Path(origen).drive.upper()
            if not letra_origen:
                return False
            return letra_origen != letra_destino
        except Exception:
            return False

    def _extraer_jpeg_stream(
        self,
        ruta_origen: str,
        offset: int,
        ruta_destino: Path,
        tamano_maximo: int,
        archivo: ArchivoEncontrado
    ) -> Optional[tuple[int, str]]:
        """
        Extrae un JPEG escribiendo incrementalmente en el destino exclusivo.

        - Ventana de lectura de 64 KiB; parser incremental byte a byte.
        - Nunca escribe más allá del primer EOI estructuralmente plausible ni
          del `tamano_maximo` de recursos (100 MiB).
        - Memoria acotada: solo la ventana de lectura.

        Returns:
            (bytes_escritos, limite_alcanzado) o None si falló/canceló.
        """
        ventana = 64 * 1024
        parser = _ParserJPEG()
        retenido = 0
        leido = 0

        def _limpiar_parcial():
            try:
                if ruta_destino.exists():
                    ruta_destino.unlink()
            except OSError:
                pass

        try:
            with open(ruta_origen, "rb") as src, open(ruta_destino, "xb") as dst:
                src.seek(offset)
                while leido < tamano_maximo:
                    if self._cancelar:
                        _limpiar_parcial()
                        archivo.evidencia = EvidenciaRecuperacion(
                            tamano_detectado=archivo.tamano,
                            resultado=ResultadoRecuperacion.PARCIAL,
                            bytes_escritos=0,
                            detalle="Cancelado durante la escritura; salida parcial eliminada",
                            limite_alcanzado="cancelado",
                        )
                        return None
                    bloque = src.read(min(ventana, tamano_maximo - leido))
                    if not bloque:
                        break
                    leido += len(bloque)
                    consumido = parser.alimentar(bloque)
                    if consumido > 0:
                        dst.write(bloque[:consumido])
                        retenido += consumido
                    if parser.eoi:
                        archivo.tamano_exacto = True
                        return retenido, "marcador_fin"
                    if parser.invalido:
                        archivo.tamano_exacto = False
                        print(f"[ADVERTENCIA] Estructura JPEG no interpretable: {parser.razon}")
                        return retenido, "estructura_invalida"
                archivo.tamano_exacto = False
                limite = "tamano_maximo" if leido >= tamano_maximo else "fin_fuente"
                return retenido, limite
        except FileExistsError:
            archivo.evidencia = EvidenciaRecuperacion(
                tamano_detectado=archivo.tamano,
                resultado=ResultadoRecuperacion.FALLIDO,
                detalle="El archivo ya existía en destino",
            )
            raise
        except Exception as e:
            _limpiar_parcial()
            archivo.evidencia = EvidenciaRecuperacion(
                tamano_detectado=archivo.tamano,
                resultado=ResultadoRecuperacion.FALLIDO,
                bytes_escritos=0,
                detalle=f"Error de E/S: {e}",
                limite_alcanzado="error",
            )
            print(f"[ERROR] Extrayendo JPEG de {archivo.nombre}: {e}")
            return None

    def _extraer_png_stream(
        self,
        ruta_origen: str,
        offset: int,
        ruta_destino: Path,
        tamano_maximo: int,
        archivo: ArchivoEncontrado
    ) -> Optional[tuple[int, str]]:
        """
        Extrae un PNG escribiendo incrementalmente en el destino exclusivo.

        - Ventana de lectura de 64 KiB; parser incremental por estados.
        - Solo se escriben bytes consumidos por el parser (encuadre de
          chunks con IHDR, al menos un IDAT, CRC-32 válidas e IEND final).
        - Nunca se escribe más allá del IEND válido ni del `tamano_maximo`
          de recursos (100 MiB). Memoria acotada: solo la ventana de lectura;
          el parser no reserva memoria según longitudes declaradas.
        - Si el IEND cruza el límite del tope operativo no se alcanza a
          consumir su CRC: la salida queda con el prefijo leído y se
          reporta el tope, nunca completitud.

        Returns:
            (bytes_escritos, limite_alcanzado) o None si falló/canceló.
        """
        ventana = 64 * 1024
        parser = _ParserPNG()
        escrito = 0
        leido = 0

        def _limpiar_parcial():
            try:
                if ruta_destino.exists():
                    ruta_destino.unlink()
            except OSError:
                pass

        try:
            with open(ruta_origen, "rb") as src, open(ruta_destino, "xb") as dst:
                src.seek(offset)
                while leido < tamano_maximo:
                    if self._cancelar:
                        _limpiar_parcial()
                        archivo.evidencia = EvidenciaRecuperacion(
                            tamano_detectado=archivo.tamano,
                            resultado=ResultadoRecuperacion.PARCIAL,
                            bytes_escritos=0,
                            detalle="Cancelado durante la escritura; salida parcial eliminada",
                            limite_alcanzado="cancelado",
                        )
                        return None
                    bloque = src.read(min(ventana, tamano_maximo - leido))
                    if not bloque:
                        break
                    leido += len(bloque)
                    consumido = parser.alimentar(bloque)
                    if consumido > 0:
                        dst.write(bloque[:consumido])
                        escrito += consumido
                    if parser.eoi:
                        archivo.tamano_exacto = True
                        return escrito, "marcador_fin"
                    if parser.invalido:
                        archivo.tamano_exacto = False
                        print(f"[ADVERTENCIA] Estructura PNG no interpretable: {parser.razon}")
                        return escrito, "estructura_invalida"
                archivo.tamano_exacto = False
                limite = "tamano_maximo" if leido >= tamano_maximo else "fin_fuente"
                return escrito, limite
        except FileExistsError:
            archivo.evidencia = EvidenciaRecuperacion(
                tamano_detectado=archivo.tamano,
                resultado=ResultadoRecuperacion.FALLIDO,
                detalle="El archivo ya existía en destino",
            )
            raise
        except Exception as e:
            _limpiar_parcial()
            archivo.evidencia = EvidenciaRecuperacion(
                tamano_detectado=archivo.tamano,
                resultado=ResultadoRecuperacion.FALLIDO,
                bytes_escritos=0,
                detalle=f"Error de E/S: {e}",
                limite_alcanzado="error",
            )
            print(f"[ERROR] Extrayendo PNG de {archivo.nombre}: {e}")
            return None

    def _extraer_bmp_stream(
        self,
        ruta_origen: str,
        offset: int,
        ruta_destino: Path,
        tamano_maximo: int,
        archivo: ArchivoEncontrado
    ) -> Optional[tuple[int, str]]:
        """
        Extrae un BMP escribiendo incrementalmente en el destino exclusivo.

        - Ventana de lectura de 64 KiB; parser incremental por estados.
        - El parser valida la cabecera (firma, tamaño DIB, dimensiones,
          planos, profundidad, compresión, offset y tamaños coherentes) y
          deriva el límite del archivo de forma aritmética.
        - Nunca se escribe más allá del límite validado ni del
          `tamano_maximo` de recursos (100 MiB). Memoria acotada: solo la
          ventana de lectura y <=138 bytes de cabecera.
        - Si el tope operativo corta antes del límite declarado, la salida
          queda con el prefijo leído y se reporta el tope, nunca completitud.

        Returns:
            (bytes_escritos, limite_alcanzado) o None si falló/canceló.
        """
        ventana = 64 * 1024
        parser = _ParserBMP()
        escrito = 0
        leido = 0

        def _limpiar_parcial():
            try:
                if ruta_destino.exists():
                    ruta_destino.unlink()
            except OSError:
                pass

        try:
            with open(ruta_origen, "rb") as src, open(ruta_destino, "xb") as dst:
                src.seek(offset)
                while leido < tamano_maximo:
                    if self._cancelar:
                        _limpiar_parcial()
                        archivo.evidencia = EvidenciaRecuperacion(
                            tamano_detectado=archivo.tamano,
                            resultado=ResultadoRecuperacion.PARCIAL,
                            bytes_escritos=0,
                            detalle="Cancelado durante la escritura; salida parcial eliminada",
                            limite_alcanzado="cancelado",
                        )
                        return None
                    bloque = src.read(min(ventana, tamano_maximo - leido))
                    if not bloque:
                        break
                    leido += len(bloque)
                    consumido = parser.alimentar(bloque)
                    if consumido > 0:
                        dst.write(bloque[:consumido])
                        escrito += consumido
                    if parser.fin:
                        archivo.tamano_exacto = True
                        return escrito, "limite_declarado"
                    if parser.invalido:
                        archivo.tamano_exacto = False
                        print(f"[ADVERTENCIA] Estructura BMP no interpretable: {parser.razon}")
                        return escrito, "estructura_invalida"
                archivo.tamano_exacto = False
                limite = "tamano_maximo" if leido >= tamano_maximo else "fin_fuente"
                return escrito, limite
        except FileExistsError:
            archivo.evidencia = EvidenciaRecuperacion(
                tamano_detectado=archivo.tamano,
                resultado=ResultadoRecuperacion.FALLIDO,
                detalle="El archivo ya existía en destino",
            )
            raise
        except Exception as e:
            _limpiar_parcial()
            archivo.evidencia = EvidenciaRecuperacion(
                tamano_detectado=archivo.tamano,
                resultado=ResultadoRecuperacion.FALLIDO,
                bytes_escritos=0,
                detalle=f"Error de E/S: {e}",
                limite_alcanzado="error",
            )
            print(f"[ERROR] Extrayendo BMP de {archivo.nombre}: {e}")
            return None

    def _extraer_gif_stream(
        self,
        ruta_origen: str,
        offset: int,
        ruta_destino: Path,
        tamano_maximo: int,
        archivo: ArchivoEncontrado
    ) -> Optional[tuple[int, str]]:
        """
        Extrae un GIF escribiendo incrementalmente en el destino exclusivo.

        - Ventana de lectura de 64 KiB; parser incremental por estados.
        - El parser cuadra cabecera, descriptor lógico, tablas de color,
          descriptores de imagen, extensiones y sub-bloques hasta el
          trailer 0x3B; nunca busca el trailer "a ciegas".
        - Nunca se escribe más allá del trailer válido ni del
          `tamano_maximo` de recursos (100 MiB). Memoria acotada: solo la
          ventana de lectura; tablas y sub-bloques se atraviesan contando
          bytes (sin buffers proporcionales a dimensiones declaradas).

        Returns:
            (bytes_escritos, limite_alcanzado) o None si falló/canceló.
        """
        ventana = 64 * 1024
        parser = _ParserGIF()
        escrito = 0
        leido = 0

        def _limpiar_parcial():
            try:
                if ruta_destino.exists():
                    ruta_destino.unlink()
            except OSError:
                pass

        try:
            with open(ruta_origen, "rb") as src, open(ruta_destino, "xb") as dst:
                src.seek(offset)
                while leido < tamano_maximo:
                    if self._cancelar:
                        _limpiar_parcial()
                        archivo.evidencia = EvidenciaRecuperacion(
                            tamano_detectado=archivo.tamano,
                            resultado=ResultadoRecuperacion.PARCIAL,
                            bytes_escritos=0,
                            detalle="Cancelado durante la escritura; salida parcial eliminada",
                            limite_alcanzado="cancelado",
                        )
                        return None
                    bloque = src.read(min(ventana, tamano_maximo - leido))
                    if not bloque:
                        break
                    leido += len(bloque)
                    consumido = parser.alimentar(bloque)
                    if consumido > 0:
                        dst.write(bloque[:consumido])
                        escrito += consumido
                    if parser.fin:
                        archivo.tamano_exacto = True
                        return escrito, "marcador_fin"
                    if parser.invalido:
                        archivo.tamano_exacto = False
                        print(f"[ADVERTENCIA] Estructura GIF no interpretable: {parser.razon}")
                        return escrito, "estructura_invalida"
                archivo.tamano_exacto = False
                limite = "tamano_maximo" if leido >= tamano_maximo else "fin_fuente"
                return escrito, limite
        except FileExistsError:
            archivo.evidencia = EvidenciaRecuperacion(
                tamano_detectado=archivo.tamano,
                resultado=ResultadoRecuperacion.FALLIDO,
                detalle="El archivo ya existía en destino",
            )
            raise
        except Exception as e:
            _limpiar_parcial()
            archivo.evidencia = EvidenciaRecuperacion(
                tamano_detectado=archivo.tamano,
                resultado=ResultadoRecuperacion.FALLIDO,
                bytes_escritos=0,
                detalle=f"Error de E/S: {e}",
                limite_alcanzado="error",
            )
            print(f"[ERROR] Extrayendo GIF de {archivo.nombre}: {e}")
            return None

    def _extraer_desde_dispositivo(
        self,
        ruta_dispositivo: str,
        offset: int,
        tamano_maximo: int
    ) -> Optional[bytes]:
        """
        Extrae payload desde un dispositivo de bloque en modo binario.
        
        Args:
            ruta_dispositivo: Ruta del dispositivo (/dev/sdX o \\\\.\\D:)
            offset: Offset donde se encontró el header
            tamano_maximo: Tamaño máximo a leer
            
        Returns:
            Payload extraído o None
        """
        # Leer en bloques de 64KB para uso eficiente de RAM
        buffer_size = 64 * 1024
        payload = bytearray()
        
        with open(ruta_dispositivo, "rb") as dispositivo:
            # Seek al offset donde se encontró el header
            dispositivo.seek(offset)
            
            # Leer hasta tamaño máximo o fin del dispositivo
            bytes_restantes = tamano_maximo
            
            while bytes_restantes > 0:
                # Determinar tamaño del bloque actual
                bloque_size = min(buffer_size, bytes_restantes)
                
                # Leer bloque en modo binario
                bloque = dispositivo.read(bloque_size)
                
                if not bloque:
                    # Fin del dispositivo alcanzado
                    break
                
                payload.extend(bloque)
                bytes_restantes -= len(bloque)
        
        return bytes(payload) if payload else None

    def _extraer_desde_archivo(
        self,
        ruta_archivo: str,
        offset: int,
        tamano_maximo: int
    ) -> Optional[bytes]:
        """
        Extrae payload desde un archivo regular en modo binario.
        
        Args:
            ruta_archivo: Ruta del archivo
            offset: Offset donde se encontró el header
            tamano_maximo: Tamaño máximo a leer
            
        Returns:
            Payload extraído o None
        """
        payload = bytearray()
        
        with open(ruta_archivo, "rb") as f:
            f.seek(offset)
            payload = f.read(tamano_maximo)
        
        return bytes(payload) if payload else None
