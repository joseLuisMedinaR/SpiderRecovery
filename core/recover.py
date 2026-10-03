"""
Módulo de recuperación de archivos
Gestiona la restauración de archivos seleccionados con extracción real de payload
"""

import os
import platform
from pathlib import Path
from typing import Callable, Optional

from core.scanner import ArchivoEncontrado, obtener_tamano_maximo, es_ruta_dispositivo_bloque


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
                    fallidos += 1
                    continue
                if ruta_destino.exists():
                    print(f"[ERROR] El archivo ya existe, no se sobrescribirá: {ruta_destino}")
                    fallidos += 1
                    continue

                # Extraer payload real desde el dispositivo
                payload_extraido = self._extraer_payload(archivo)

                if payload_extraido and len(payload_extraido) > 0:
                    # AUD-005: creación exclusiva ('xb') para evitar sobrescrituras y carreras.
                    # Nunca se usa "wb" sobre una ruta existente.
                    try:
                        with open(ruta_destino, "xb") as f:
                            f.write(payload_extraido)
                        recuperados += 1
                    except FileExistsError:
                        print(f"[ERROR] El archivo apareció durante la recuperación, no se sobrescribirá: {ruta_destino}")
                        fallidos += 1
                    except Exception as e:
                        # Solo se elimina el archivo que ESTA recuperación creó; nunca uno preexistente
                        try:
                            if ruta_destino.exists():
                                ruta_destino.unlink()
                        except OSError:
                            pass
                        print(f"[ERROR] Enviando payload a disco falló {archivo.nombre}: {e}")
                        fallidos += 1
                else:
                    print(f"[ADVERTENCIA] No se pudo extraer payload para {archivo.nombre}")
                    fallidos += 1

            except Exception as e:
                print(f"[ERROR] Error recuperando {archivo.nombre}: {e}")
                fallidos += 1

        return recuperados, fallidos

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
