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
    el orden IHDR primero, un único IEND de longitud 0, y la
    CRC-32 estándar de cada chunk (zlib).

    No decodifica píxeles. Un IEND estructuralmente plausible y
    CRCs válidas son solo evidencia estructural, no prueba de
    completitud del original.
    """

    _SIG = 0
    _LEN = 1
    _TIPO = 2
    _DATA = 3
    _CRC = 4
    _FIN = 5

    _FIRMA = b"\x89PNG\r\n\x1a\n"

    def __init__(self):
        self.estado = self._SIG
        self.pos_sig = 0
        self.len_buf = bytearray()
        self.tipo_buf = bytearray()
        self.crc_buf = bytearray()
        self.restante_data = 0
        self.crc_calculada = 0
        self.chunks = 0
        self.visto_ihdr = False
        self.eoi = False  # IEND válido encontrado
        self.invalido = False
        self.razon = ""
        self.crc_mala = False

    def alimentar(self, data: bytes) -> int:
        """Procesa un bloque; devuelve cuántos bytes consumió."""
        import zlib
        consumido = 0
        for b in data:
            if self.eoi or self.invalido:
                break
            consumido += 1
            if self.estado == self._SIG:
                if b != self._FIRMA[self.pos_sig]:
                    self.invalido, self.razon = True, "Firma PNG incorrecta"
                    break
                self.pos_sig += 1
                if self.pos_sig == 8:
                    self.estado = self._LEN
            elif self.estado == self._LEN:
                self.len_buf.append(b)
                if len(self.len_buf) == 4:
                    longitud = int.from_bytes(self.len_buf, "big")
                    if longitud & 0x80000000:
                        self.invalido, self.razon = True, "Longitud con bit reservado"
                        break
                    self.restante_data = longitud
                    self.len_buf.clear()
                    self.estado = self._TIPO
            elif self.estado == self._TIPO:
                self.tipo_buf.append(b)
                if len(self.tipo_buf) == 4:
                    tipo = bytes(self.tipo_buf)
                    if self.chunks == 0 and tipo != b"IHDR":
                        self.invalido, self.razon = True, "El primer chunk no es IHDR"
                        break
                    if tipo == b"IHDR":
                        if self.visto_ihdr:
                            self.invalido, self.razon = True, "IHDR duplicado"
                            break
                        if self.restante_data != 13:
                            self.invalido, self.razon = True, "IHDR con longitud distinta de 13"
                            break
                        self.visto_ihdr = True
                    if tipo == b"IEND" and self.restante_data != 0:
                        self.invalido, self.razon = True, "IEND con longitud distinta de 0"
                        break
                    self.crc_calculada = zlib.crc32(tipo) & 0xFFFFFFFF
                    self.tipo_buf.clear()
                    self.estado = self._DATA if self.restante_data > 0 else self._CRC
            elif self.estado == self._DATA:
                self.crc_calculada = zlib.crc32(bytes([b]), self.crc_calculada) & 0xFFFFFFFF
                self.restante_data -= 1
                if self.restante_data == 0:
                    self.estado = self._CRC
            elif self.estado == self._CRC:
                self.crc_buf.append(b)
                if len(self.crc_buf) == 4:
                    declarada = int.from_bytes(self.crc_buf, "big")
                    self.crc_buf.clear()
                    if declarada != self.crc_calculada:
                        self.crc_mala = True
                        self.invalido, self.razon = True, "CRC de chunk inválida"
                        break
                    self.chunks += 1
                    # IEND ya consumido: fin plausible
                    if self.chunks > 0 and self.estado != self._FIN:
                        pass
                    # Detectar IEND por tipo (última tipo_buf ya limpia): usar flag
                    self.estado = self._LEN
                    # Marcar fin si este chunk fue IEND (se detecta abajo)
                    # (tipo del chunk actual guardado en crc de tipo anterior)
                    # Simpler: comprobar con el último tipo visto
                    # (se almacena en _ultimo_tipo)
                    # -> ver abajo
                    self._ultimo_tipo_ok = getattr(self, "_ultimo_tipo", b"")
                    if getattr(self, "_ultimo_tipo", b"") == b"IEND":
                        self.eoi = True
                        break
            # Guardar el tipo actual para la detección de IEND
            if self.estado == self._DATA or (self.estado == self._CRC and self.chunks == 0):
                self._ultimo_tipo = bytes(self.tipo_buf) if self.tipo_buf else getattr(self, "_ultimo_tipo", b"")
        return consumido


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
                if archivo.extension.lower() == "jpg":
                    # JPEG: extracción incremental con memoria acotada
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
                    try:
                        resultado_jpeg = self._extraer_jpeg_stream(
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
                        resultado_jpeg = None
                    if resultado_jpeg is None:
                        fallidos += 1
                    elif resultado_jpeg[0] == 0:
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
                        bytes_escritos, limite = resultado_jpeg
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
