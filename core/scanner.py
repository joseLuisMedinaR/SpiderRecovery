"""
Escáner de archivos eliminados con algoritmo de escaneo profundo (File Carving)
Implementa acceso real a dispositivos de bloque y detección por firmas mágicas
Compatible con Linux (Fedora) y Windows
"""

import os
import platform
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from core.signatures import FIRMAS_ARCHIVOS, obtener_categoria, obtener_descripcion


class EstadoEscaneo(Enum):
    """Estados posibles del escaneo."""
    DETENIDO = "detenido"
    ESCANEANDO = "escaneando"
    PAUSADO = "pausado"
    COMPLETADO = "completado"
    CANCELADO = "cancelado"


@dataclass
class ArchivoEncontrado:
    """Representa un archivo encontrado durante el escaneo."""
    ruta: str
    nombre: str
    extension: str
    tamano: int
    offset: int  # Posición en el disco donde se encontró
    categoria: str
    descripcion: str
    salud: str = "Desconocida"  # Buena, Regular, Mala
    seleccionado: bool = False
    carpeta_original: str = ""  # Ruta de la carpeta original estimada


@dataclass
class ProgresoEscaneo:
    """Información del progreso del escaneo."""
    estado: EstadoEscaneo = EstadoEscaneo.DETENIDO
    bytes_leidos: int = 0
    bytes_totales: int = 0
    archivos_encontrados: int = 0
    velocidad: float = 0.0  # bytes/segundo
    tiempo_transcurrido: float = 0.0
    ruta_actual: str = ""
    tiempo_restante: int = 0  # segundos restantes estimados


# Diccionario de firmas mágicas para File Carving
# Formato: (firma_inicio_bytes, fin_bytes_opcional, extensión, categoría, descripción)
FIRMAS_CARVING = [
    # Imágenes
    (b"\xff\xd8\xff", b"\xff\xd9", "jpg", "Imágenes", "Imagen JPEG"),
    (b"\x89PNG\r\n\x1a\n", b"IEND\xaeB`\x82", "png", "Imágenes", "Imagen PNG"),
    (b"GIF87a", b"\x00\x3b", "gif", "Imágenes", "Imagen GIF"),
    (b"GIF89a", b"\x00\x3b", "gif", "Imágenes", "Imagen GIF"),
    (b"BM", None, "bmp", "Imágenes", "Imagen BMP"),

    # Documentos
    (b"%PDF-", b"%%EOF", "pdf", "Documentos", "Documento PDF"),

    # Comprimidos y documentos Office
    (b"PK\x03\x04", None, "zip", "Comprimidos", "Archivo ZIP/DOCX/PPTX"),

    # Audio/Video
    (b"ID3", None, "mp3", "Multimedia", "Audio MP3"),
    (b"\xff\xfb", None, "mp3", "Multimedia", "Audio MP3"),
    (b"RIFF", None, "wav", "Multimedia", "Audio WAV/AVI"),
    (b"\x00\x00\x00\x18ftyp", None, "mp4", "Multimedia", "Video MP4"),

    # Sistema
    (b"\x7fELF", None, "elf", "Sistema", "Binario ELF"),
    (b"MZ", None, "exe", "Sistema", "Ejecutable Windows"),

    # Bases de datos
    (b"SQLite format 3\x00", None, "db", "Bases de datos", "Base de datos SQLite"),
]

# Tamaños máximos de extracción por tipo de archivo (en bytes)
# Estos valores son generosos para asegurar la extracción completa del payload
TAMANOS_MAXIMOS_CARVING = {
    # Imágenes: 8 MB máximo
    "jpg": 8 * 1024 * 1024,      # 8 MB
    "png": 8 * 1024 * 1024,      # 8 MB
    "gif": 8 * 1024 * 1024,      # 8 MB
    "bmp": 8 * 1024 * 1024,      # 8 MB
    
    # Documentos: 15 MB máximo
    "pdf": 15 * 1024 * 1024,     # 15 MB
    "docx": 15 * 1024 * 1024,    # 15 MB
    "xlsx": 15 * 1024 * 1024,    # 15 MB
    "pptx": 15 * 1024 * 1024,    # 15 MB
    "zip": 15 * 1024 * 1024,     # 15 MB
    
    # Multimedia: 50 MB máximo
    "mp3": 50 * 1024 * 1024,     # 50 MB
    "wav": 50 * 1024 * 1024,     # 50 MB
    "mp4": 50 * 1024 * 1024,     # 50 MB
    "avi": 50 * 1024 * 1024,     # 50 MB
    
    # Sistema: 10 MB máximo
    "elf": 10 * 1024 * 1024,     # 10 MB
    "exe": 10 * 1024 * 1024,     # 10 MB
    
    # Bases de datos: 20 MB máximo
    "db": 20 * 1024 * 1024,      # 20 MB
    
    # Default para tipos desconocidos
    "default": 10 * 1024 * 1024,  # 10 MB
}


def obtener_tamano_maximo(extension: str) -> int:
    """
    Obtiene el tamaño máximo de extracción para un tipo de archivo.
    
    Args:
        extension: Extensión del archivo (sin punto)
        
    Returns:
        Tamaño máximo en bytes
    """
    return TAMANOS_MAXIMOS_CARVING.get(extension.lower(), TAMANOS_MAXIMOS_CARVING["default"])


def detectar_sistema_operativo() -> str:
    """
    Detecta el sistema operativo del host.
    
    Returns:
        "Linux", "Windows", "Darwin" (macOS), u "Desconocido"
    """
    sistema = platform.system()
    if sistema == "Linux":
        return "Linux"
    elif sistema == "Windows":
        return "Windows"
    elif sistema == "Darwin":
        return "Darwin"
    else:
        return "Desconocido"


def es_ruta_dispositivo_bloque(ruta: str) -> bool:
    """
    Determina si la ruta es un dispositivo de bloque válido.
    
    En Linux: /dev/sdX, /dev/nvmeX, etc.
    En Windows: \\\\.\\PhysicalDrive0, \\\\.\\D:, etc.
    """
    sistema = detectar_sistema_operativo()
    ruta_str = str(ruta)

    if sistema == "Linux":
        # Verificar si es un dispositivo de bloque en Linux
        try:
            if ruta_str.startswith("/dev/"):
                return os.path.exists(ruta_str) and os.stat(ruta_str).st_mode & 0o170000 == 0o060000
        except (OSError, PermissionError):
            return False
    elif sistema == "Windows":
        # Verificar si es un dispositivo físico o lógico en Windows
        if ruta_str.startswith("\\\\.\\PhysicalDrive") or ruta_str.startswith("\\\\.\\"):
            return True

    return False


def obtener_ruta_fisica_windows(letra_unidad: str) -> str:
    """
    Convierte una letra de unidad de Windows a su ruta física.
    
    Args:
        letra_unidad: Letra de la unidad (ej: "D:", "C:")
    
    Returns:
        Ruta física del dispositivo (ej: "\\\\.\\D:")
    """
    # Asegurar que la letra esté en formato correcto
    letra = letra_unidad.rstrip(":").upper()
    return f"\\\\.\\{letra}:"


def resolver_ruta_dispositivo(ruta: str) -> str:
    """
    Resuelve la ruta real del dispositivo de bloque.
    
    Si la ruta es un punto de montaje (ej: /home, /), intenta resolver
    al dispositivo de bloque real (ej: /dev/sda1, /dev/nvme0n1p1).
    
    Args:
        ruta: Ruta seleccionada por el usuario
        
    Returns:
        Ruta del dispositivo de bloque o la ruta original si no se puede resolver
    """
    sistema = detectar_sistema_operativo()
    
    if sistema == "Linux":
        # Si ya es un dispositivo de bloque, retornarlo directamente
        if ruta.startswith("/dev/") and es_ruta_dispositivo_bloque(ruta):
            return ruta
        
        # Intentar resolver el dispositivo desde el punto de montaje
        try:
            # Usar findmnt para obtener el dispositivo real
            import subprocess
            resultado = subprocess.run(
                ["findmnt", "-n", "-o", "SOURCE", "--target", ruta],
                capture_output=True,
                text=True,
                timeout=5
            )
            if resultado.returncode == 0:
                dispositivo = resultado.stdout.strip()
                if dispositivo and dispositivo.startswith("/dev/"):
                    print(f"[DEPURACIÓN] Punto de montaje '{ruta}' resuelto a dispositivo: '{dispositivo}'")
                    return dispositivo
        except Exception as e:
            print(f"[DEPURACIÓN] Error resolviendo dispositivo para '{ruta}': {e}")
    
    # Si no se puede resolver, retornar la ruta original
    return ruta


class EscaneoProfundo:
    """
    Escáner profundo de archivos eliminados mediante File Carving.
    
    Abre dispositivos de bloque en modo binario y busca firmas mágicas
    para recuperar archivos eliminados cuyos datos aún persisten en el disco.
    Compatible con Linux y Windows.
    """

    # Tamaño del bloque de lectura (64 KB para uso eficiente de RAM)
    TAMANO_BLOQUE = 64 * 1024

    # Tamaño máximo de archivo a recuperar (100 MB)
    TAMANO_MAX_ARCHIVO = 100 * 1024 * 1024

    def __init__(self):
        self.progreso = ProgresoEscaneo()
        self.archivos_encontrados: list[ArchivoEncontrado] = []
        self._hilo: Optional[threading.Thread] = None
        self._evento_pausa = threading.Event()
        self._evento_cancelar = threading.Event()
        self._evento_pausa.set()  # Iniciar sin pausa
        self._callbacks: list[Callable] = []
        self._lock = threading.Lock()
        self._contador_archivos = 0  # Para generar nombres únicos
        self.sistema_operativo = detectar_sistema_operativo()
        self.ruta_dispositivo: str = ""  # Ruta del dispositivo en escaneo

    def registrar_callback(self, callback: Callable):
        """Registra una función a llamar cuando hay actualizaciones."""
        self._callbacks.append(callback)

    def _notificar(self):
        """Notifica a todos los callbacks registrados."""
        for callback in self._callbacks:
            try:
                callback(self.progreso, self.archivos_encontrados)
            except Exception:
                pass

    def iniciar(self, ruta: str):
        """Inicia el escaneo en un hilo separado."""
        if self._hilo and self._hilo.is_alive():
            return

        self.progreso = ProgresoEscaneo(estado=EstadoEscaneo.ESCANEANDO)
        self.archivos_encontrados = []
        self._contador_archivos = 0
        self._evento_pausa.set()
        self._evento_cancelar.clear()

        self._hilo = threading.Thread(
            target=self._escanear,
            args=(ruta,),
            daemon=True,
            name="HiloEscaneo"
        )
        self._hilo.start()

    def pausar(self):
        """Pausa el escaneo actual."""
        if self.progreso.estado == EstadoEscaneo.ESCANEANDO:
            self.progreso.estado = EstadoEscaneo.PAUSADO
            self._evento_pausa.clear()
            self._notificar()

    def reanudar(self):
        """Reanuda el escaneo pausado."""
        if self.progreso.estado == EstadoEscaneo.PAUSADO:
            self.progreso.estado = EstadoEscaneo.ESCANEANDO
            self._evento_pausa.set()
            self._notificar()

    def cancelar(self):
        """Cancela el escaneo actual."""
        self._evento_cancelar.set()
        self._evento_pausa.set()  # Asegurar que no esté pausado
        self.progreso.estado = EstadoEscaneo.CANCELADO
        self._notificar()

    def detener_y_guardar(self):
        """
        Detiene el escaneo actual y retorna los archivos encontrados hasta el momento.
        
        Este método permite una "parada parcial" donde el usuario puede ver los
        resultados obtenidos hasta el momento sin esperar a que termine el escaneo completo.
        
        Returns:
            Lista de archivos encontrados hasta el momento de la detención
        """
        print(f"\n[PARCIAL] Deteniendo escaneo y guardando resultados parciales...")
        
        # Señalar al hilo que debe detenerse
        self._evento_cancelar.set()
        self._evento_pausa.set()  # Asegurar que no esté pausado
        
        # Actualizar estado
        self.progreso.estado = EstadoEscaneo.CANCELADO
        
        # Notificar a la UI
        self._notificar()
        
        # Retornar copia de los archivos encontrados hasta el momento
        archivos_parciales = self.obtener_archivos()
        print(f"[PARCIAL] Se encontraron {len(archivos_parciales)} archivos antes de detener")
        
        return archivos_parciales

    def _escanear(self, ruta: str):
        """
        Algoritmo principal de escaneo profundo (File Carving).
        
        Determina si la ruta es un dispositivo de bloque o una carpeta,
        y aplica el método de escaneo correspondiente según el SO.
        """
        inicio_tiempo = time.time()
        ruta_obj = Path(ruta)

        self.progreso.ruta_actual = ruta

        print(f"\n{'='*60}")
        print(f"[INICIO] Escaneo profundo iniciado")
        print(f"[INICIO] Sistema operativo: {self.sistema_operativo}")
        print(f"[INICIO] Ruta seleccionada: {ruta}")
        print(f"{'='*60}\n")

        try:
            # Determinar si es un dispositivo de bloque o una carpeta
            if es_ruta_dispositivo_bloque(ruta):
                # Escaneo real de dispositivo de bloque
                print(f"[DISPOSITIVO] Detectado dispositivo de bloque: {ruta}")
                self._escanear_dispositivo(ruta, inicio_tiempo)
            elif ruta_obj.is_dir():
                # Escaneo de carpeta (archivos eliminados recientemente)
                print(f"[CARPETA] Escaneando carpeta: {ruta}")
                self._escanear_carpeta(ruta_obj, inicio_tiempo)
            else:
                # Archivo individual
                print(f"[ARCHIVO] Escaneando archivo individual: {ruta}")
                self._escanear_archivo_individual(ruta_obj, inicio_tiempo)

        except PermissionError:
            sistema = self.sistema_operativo
            if sistema == "Linux":
                print(f"[ERROR] Permiso denegado: {ruta}. Ejecute con sudo para acceder a dispositivos de bloque.")
            elif sistema == "Windows":
                print(f"[ERROR] Permiso denegado: {ruta}. Ejecute como Administrador para acceder a dispositivos físicos.")
            else:
                print(f"[ERROR] Permiso denegado: {ruta}. Ejecute con privilegios elevados.")
        except Exception as e:
            print(f"[ERROR] Error durante el escaneo: {e}")
            import traceback
            traceback.print_exc()

        finally:
            self.progreso.tiempo_transcurrido = time.time() - inicio_tiempo
            if self.progreso.estado != EstadoEscaneo.CANCELADO:
                self.progreso.estado = EstadoEscaneo.COMPLETADO
            self._notificar()
            
            print(f"\n{'='*60}")
            print(f"[FIN] Escaneo completado")
            print(f"[FIN] Archivos encontrados: {len(self.archivos_encontrados)}")
            print(f"[FIN] Bytes leídos: {self.progreso.bytes_leidos}")
            print(f"[FIN] Tiempo transcurrido: {self.progreso.tiempo_transcurrido:.2f} segundos")
            print(f"{'='*60}\n")

    def _obtener_tamano_dispositivo(self, ruta: str) -> int:
        """
        Obtiene el tamaño total del dispositivo de bloque según el sistema operativo.
        
        En Linux: usa ioctl BLKGETSIZE64 para obtener el tamaño real del dispositivo.
        En Windows: usa seek(0, 2) + tell() para obtener el tamaño del dispositivo.
        
        Args:
            ruta: Ruta del dispositivo de bloque
            
        Returns:
            Tamaño total en bytes
        """
        sistema = detectar_sistema_operativo()
        
        if sistema == "Linux":
            return self._obtener_tamano_linux(ruta)
        elif sistema == "Windows":
            return self._obtener_tamano_windows(ruta)
        else:
            # Para otros sistemas, intentar el método genérico
            return self._obtener_tamano_generico(ruta)

    def _obtener_tamano_linux(self, ruta: str) -> int:
        """
        Obtiene el tamaño de un dispositivo de bloque en Linux usando ioctl.
        
        Usa el comando BLKGETSIZE64 (0x80081254) para obtener el tamaño real
        del dispositivo, evitando la restricción de 0 bytes en algunos kernels.
        
        Args:
            ruta: Ruta del dispositivo (ej: /dev/sda1)
            
        Returns:
            Tamaño total en bytes
        """
        try:
            import struct
            import fcntl
            
            with open(ruta, "rb") as f:
                # BLKGETSIZE64 = 0x80081254
                # Este ioctl devuelve el tamaño del dispositivo en bytes (uint64)
                size = fcntl.ioctl(f.fileno(), 0x80081254, struct.pack('Q', 0))
                total_bytes = struct.unpack('Q', size)[0]
                
                if total_bytes > 0:
                    print(f"[LINUX] Tamaño obtenido vía ioctl BLKGETSIZE64: {total_bytes} bytes")
                    return total_bytes
                else:
                    print(f"[LINUX] ioctl devolvió 0, usando método alternativo")
                    
        except Exception as e:
            print(f"[LINUX] Error con ioctl: {e}, usando método alternativo")
        
        # Método alternativo: seek al final
        try:
            with open(ruta, "rb") as f:
                f.seek(0, 2)
                total_bytes = f.tell()
                print(f"[LINUX] Tamaño obtenido vía seek: {total_bytes} bytes")
                return total_bytes
        except Exception as e:
            print(f"[LINUX] Error con seek: {e}")
            return 100 * 1024 * 1024 * 1024  # 100 GB por defecto

    def _obtener_tamano_windows(self, ruta: str) -> int:
        """
        Obtiene el tamaño de un dispositivo físico en Windows.
        
        Usa seek(0, 2) + tell() para obtener el tamaño del dispositivo Win32.
        
        Args:
            ruta: Ruta del dispositivo (ej: \\\\.\\PhysicalDrive0)
            
        Returns:
            Tamaño total en bytes
        """
        try:
            with open(ruta, "rb") as f:
                f.seek(0, 2)
                total_bytes = f.tell()
                print(f"[WINDOWS] Tamaño obtenido vía seek: {total_bytes} bytes")
                return total_bytes
        except Exception as e:
            print(f"[WINDOWS] Error obteniendo tamaño: {e}")
            return 100 * 1024 * 1024 * 1024  # 100 GB por defecto

    def _obtener_tamano_generico(self, ruta: str) -> int:
        """
        Método genérico para obtener el tamaño de un dispositivo.
        
        Args:
            ruta: Ruta del dispositivo
            
        Returns:
            Tamaño total en bytes
        """
        try:
            tamano_total = os.path.getsize(ruta)
            print(f"[GENÉRICO] Tamaño obtenido: {tamano_total} bytes")
            return tamano_total
        except (OSError, PermissionError):
            print(f"[GENÉRICO] No se pudo obtener el tamaño. Usando valor por defecto: 100 GB")
            return 100 * 1024 * 1024 * 1024  # 100 GB por defecto

    def _escanear_dispositivo(self, ruta: str, inicio_tiempo: float):
        """
        Escaneo real de dispositivo de bloque mediante File Carving.
        
        Lee el dispositivo en bloques de 64KB y busca firmas mágicas
        para identificar y extraer archivos eliminados.
        """
        # Obtener tamaño del dispositivo según el sistema operativo
        tamano_total = self._obtener_tamano_dispositivo(ruta)
        
        print(f"[DISPOSITIVO] Tamaño total: {tamano_total} bytes ({tamano_total / (1024**3):.2f} GB)")

        self.progreso.bytes_totales = tamano_total
        self.ruta_dispositivo = ruta  # Guardar referencia para el carving

        print(f"[DISPOSITIVO] Abriendo dispositivo en modo binario: {ruta}")
        
        with open(ruta, "rb") as dispositivo:
            offset = 0
            buffer_previo = b""  # Para firmas que cruzan límites de bloque
            bloque_numero = 0

            while offset < tamano_total:
                # Verificar si se canceló
                if self._evento_cancelar.is_set():
                    print(f"[CANCELADO] Escaneo cancelado por el usuario en offset {offset}")
                    return

                # Manejar pausa
                self._evento_pausa.wait()

                # Leer bloque de 64KB
                bloque = dispositivo.read(self.TAMANO_BLOQUE)
                if not bloque:
                    print(f"[FIN] Fin del dispositivo alcanzado en offset {offset}")
                    break

                bloque_numero += 1
                
                # Depuración: mostrar progreso cada 100 bloques
                if bloque_numero % 100 == 0:
                    print(f"[DEPURACIÓN] Analizando bloque #{bloque_numero}... Total bytes leídos: {offset + len(bloque)}")

                # Combinar con buffer previo para detectar firmas en límites
                datos = buffer_previo + bloque

                # Buscar firmas mágicas en el bloque
                archivos_en_bloque = self._buscar_firmas_en_bloque(datos, offset - len(buffer_previo), inicio_tiempo)
                
                if archivos_en_bloque > 0:
                    print(f"[¡ENCONTRADO!] {archivos_en_bloque} archivo(s) encontrado(s) en bloque #{bloque_numero} (offset: {offset})")

                # Guardar los últimos bytes para el siguiente bloque
                # (máximo tamaño de firma de inicio)
                buffer_previo = bloque[-16:] if len(bloque) >= 16 else bloque

                # Actualizar progreso
                self.progreso.bytes_leidos = offset + len(bloque)
                self.progreso.ruta_actual = f"{ruta} (offset: {offset})"

                # Calcular velocidad y tiempo restante
                tiempo_actual = time.time() - inicio_tiempo
                if tiempo_actual > 0:
                    self.progreso.velocidad = self.progreso.bytes_leidos / tiempo_actual
                    self.progreso.tiempo_transcurrido = tiempo_actual
                    
                    # Calcular tiempo restante estimado
                    bytes_restantes = self.progreso.bytes_totales - self.progreso.bytes_leidos
                    if self.progreso.velocidad > 0 and bytes_restantes > 0:
                        self.progreso.tiempo_restante = int(bytes_restantes / self.progreso.velocidad)
                    else:
                        self.progreso.tiempo_restante = 0

                # Notificar actualización
                self._notificar()

                offset += len(bloque)

    def _buscar_firmas_en_bloque(self, datos: bytes, offset_base: int, inicio_tiempo: float) -> int:
        """
        Busca firmas mágicas en un bloque de datos.
        
        Cuando encuentra una firma de inicio, intenta localizar la firma de fin
        para determinar el tamaño del archivo.
        
        Args:
            datos: Bytes del bloque a analizar
            offset_base: Offset base del bloque en el dispositivo
            inicio_tiempo: Tiempo de inicio del escaneo
            
        Returns:
            Número de archivos encontrados en este bloque
        """
        archivos_encontrados = 0
        
        for firma_inicio, firma_fin, extension, categoria, descripcion in FIRMAS_CARVING:
            # Buscar todas las ocurrencias de la firma de inicio en el bloque
            posicion = 0
            while True:
                idx = datos.find(firma_inicio, posicion)
                if idx == -1:
                    break

                # Calcular offset absoluto en el dispositivo
                offset_absoluto = offset_base + idx

                try:
                    # Determinar tamaño del archivo
                    tamano = self._determinar_tamano_archivo(
                        datos, idx, firma_inicio, firma_fin, offset_absoluto
                    )

                    # Crear registro del archivo encontrado
                    # Usar la ruta del dispositivo guardada o un nombre lógico como respaldo
                    ruta_archivo = self.ruta_dispositivo if self.ruta_dispositivo else f"Recuperado_{offset_absoluto}"
                    
                    self._registrar_archivo(
                        extension=extension,
                        categoria=categoria,
                        descripcion=descripcion,
                        tamano=tamano,
                        offset=offset_absoluto,
                        ruta=ruta_archivo
                    )
                    
                    archivos_encontrados += 1

                except Exception as e:
                    # Si falla el registro de este archivo, continuar con el siguiente
                    print(f"[ADVERTENCIA] Error registrando archivo en offset {offset_absoluto}: {e}")
                    continue

                # Continuar buscando después de esta firma
                posicion = idx + len(firma_inicio)
        
        return archivos_encontrados

    def _determinar_tamano_archivo(
        self,
        datos: bytes,
        idx_inicio: int,
        firma_inicio: bytes,
        firma_fin: Optional[bytes],
        offset_absoluto: int
    ) -> int:
        """
        Determina el tamaño del archivo encontrado.
        
        Si hay firma de fin, la busca en los datos. Si no, usa un tamaño estimado.
        """
        if firma_fin:
            # Buscar la firma de fin después de la de inicio
            idx_fin = datos.find(firma_fin, idx_inicio + len(firma_inicio))
            if idx_fin != -1:
                tamano = idx_fin + len(firma_fin) - idx_inicio
                return min(tamano, self.TAMANO_MAX_ARCHIVO)

        # Si no hay firma de fin o no se encontró, usar tamaño estimado
        # Buscar el próximo bloque de ceros (indicador de fin de datos)
        tamano_estimado = self._buscar_fin_de_datos(datos, idx_inicio + len(firma_inicio))
        return min(tamano_estimado, self.TAMANO_MAX_ARCHIVO)

    def _buscar_fin_de_datos(self, datos: bytes, inicio: int) -> int:
        """
        Busca el fin de los datos del archivo buscando un bloque de ceros.
        
        Retorna el tamaño estimado del archivo.
        """
        # Buscar un bloque de 512 bytes de ceros (común en discos formateados)
        bloque_ceros = b"\x00" * 512
        idx = datos.find(bloque_ceros, inicio)
        if idx != -1:
            return idx - inicio

        # Si no se encuentra, usar un tamaño por defecto razonable
        return min(len(datos) - inicio, 1024 * 1024)  # 1 MB por defecto

    def _registrar_archivo(
        self,
        extension: str,
        categoria: str,
        descripcion: str,
        tamano: int,
        offset: int,
        ruta: str
    ):
        """Registra un archivo encontrado en la lista de resultados."""
        with self._lock:
            self._contador_archivos += 1
            nombre = f"archivo_recuperado_{self._contador_archivos:04d}.{extension}"

            archivo = ArchivoEncontrado(
                ruta=f"{ruta}#offset={offset}",
                nombre=nombre,
                extension=extension,
                tamano=tamano,
                offset=offset,
                categoria=categoria,
                descripcion=descripcion,
                salud=self._evaluar_salud(tamano),
                carpeta_original="Recuperado del disco"
            )

            self.archivos_encontrados.append(archivo)
            self.progreso.archivos_encontrados = len(self.archivos_encontrados)

    def _escanear_carpeta(self, ruta: Path, inicio_tiempo: float):
        """
        Escaneo de carpeta buscando archivos con firmas mágicas.
        
        Útil para encontrar archivos eliminados recientemente en carpetas de usuario.
        """
        self.progreso.bytes_totales = self._calcular_tamano_total(ruta)
        
        print(f"[CARPETA] Tamaño total a escanear: {self.progreso.bytes_totales} bytes")

        for item in ruta.rglob("*"):
            if self._evento_cancelar.is_set():
                return

            self._evento_pausa.wait()

            try:
                if item.is_file():
                    tamano = item.stat().st_size
                    self.progreso.bytes_leidos += tamano
                    self.progreso.ruta_actual = str(item)

                    # Leer primeros bytes para identificar tipo
                    with open(item, "rb") as f:
                        header = f.read(16)

                    # Identificar por firma mágica
                    extension = self._identificar_por_firma(header)
                    if extension:
                        archivo = ArchivoEncontrado(
                            ruta=str(item),
                            nombre=item.name,
                            extension=extension,
                            tamano=tamano,
                            offset=0,
                            categoria=obtener_categoria(extension),
                            descripcion=obtener_descripcion(extension),
                            salud=self._evaluar_salud(tamano),
                            carpeta_original=str(item.parent)
                        )
                        with self._lock:
                            self.archivos_encontrados.append(archivo)
                            self.progreso.archivos_encontrados = len(self.archivos_encontrados)

                    # Calcular velocidad
                    tiempo_actual = time.time() - inicio_tiempo
                    if tiempo_actual > 0:
                        self.progreso.velocidad = self.progreso.bytes_leidos / tiempo_actual
                        self.progreso.tiempo_transcurrido = tiempo_actual

                    self._notificar()

            except (OSError, PermissionError):
                continue

    def _escanear_archivo_individual(self, ruta: Path, inicio_tiempo: float):
        """Escanea un archivo individual."""
        try:
            tamano = ruta.stat().st_size
            self.progreso.bytes_totales = tamano
            self.progreso.bytes_leidos = tamano

            with open(ruta, "rb") as f:
                header = f.read(16)

            extension = self._identificar_por_firma(header)
            if extension:
                archivo = ArchivoEncontrado(
                    ruta=str(ruta),
                    nombre=ruta.name,
                    extension=extension,
                    tamano=tamano,
                    offset=0,
                    categoria=obtener_categoria(extension),
                    descripcion=obtener_descripcion(extension),
                    salud=self._evaluar_salud(tamano),
                    carpeta_original=str(ruta.parent)
                )
                with self._lock:
                    self.archivos_encontrados.append(archivo)
                    self.progreso.archivos_encontrados = 1

            self._notificar()

        except (OSError, PermissionError) as e:
            print(f"[ERROR] Error escaneando archivo individual: {e}")

    def _calcular_tamano_total(self, ruta: Path) -> int:
        """Calcula el tamaño total de los archivos en la ruta."""
        total = 0
        try:
            if ruta.is_file():
                return ruta.stat().st_size
            for item in ruta.rglob("*"):
                if item.is_file():
                    try:
                        total += item.stat().st_size
                    except (OSError, PermissionError):
                        continue
        except (OSError, PermissionError):
            pass
        return total if total > 0 else 1  # Evitar división por cero

    def _identificar_por_firma(self, header: bytes) -> Optional[str]:
        """Identifica el tipo de archivo por sus primeros bytes."""
        for firma_inicio, _, extension, _, _ in FIRMAS_CARVING:
            if header.startswith(firma_inicio):
                return extension
        return None

    def _evaluar_salud(self, tamano: int) -> str:
        """
        Evalúa la salud del archivo encontrado.
        
        En un escaneo real, un tamaño muy pequeño puede indicar corrupción.
        """
        if tamano == 0:
            return "Mala"
        elif tamano < 1024:
            return "Regular"
        elif tamano > self.TAMANO_MAX_ARCHIVO:
            return "Regular"  # Archivo muy grande, posiblemente corrupto
        else:
            return "Buena"

    def obtener_archivos(self) -> list[ArchivoEncontrado]:
        """Obtiene la lista de archivos encontrados (thread-safe)."""
        with self._lock:
            return self.archivos_encontrados.copy()
