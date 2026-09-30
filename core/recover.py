"""
Módulo de recuperación de archivos
Gestiona la restauración de archivos seleccionados con extracción real de payload
"""

import os
import platform
from pathlib import Path
from typing import Callable

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

                # Extraer payload real desde el dispositivo
                payload_extraido = self._extraer_payload(archivo)

                if payload_extraido and len(payload_extraido) > 0:
                    # Escribir payload en modo binario estricto
                    with open(ruta_destino, "wb") as f:
                        f.write(payload_extraido)
                    recuperados += 1
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
