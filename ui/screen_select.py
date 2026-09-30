"""
Pantalla 1: Selección de unidad o carpeta
Permite al usuario elegir dónde realizar el escaneo profundo
Compatible con Linux (Fedora) y Windows
"""

import os
import platform
import customtkinter as ctk
import psutil
from pathlib import Path
from typing import Callable, Optional


def detectar_sistema() -> str:
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


def es_usuario_admin() -> bool:
    """
    Verifica si el usuario tiene privilegios de administrador.
    
    En Linux: verifica si es root (UID 0)
    En Windows: verifica si pertenece al grupo de administradores
    
    Returns:
        True si tiene privilegios elevados, False en caso contrario
    """
    sistema = detectar_sistema()
    
    if sistema == "Linux" or sistema == "Darwin":
        try:
            return os.getuid() == 0
        except AttributeError:
            return False
    elif sistema == "Windows":
        try:
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except (ImportError, AttributeError):
            return False
    return False


class PantallaSeleccion(ctk.CTkFrame):
    """
    Pantalla de selección de unidad o carpeta.
    
    Muestra las unidades disponibles y permite seleccionar una carpeta
    personalizada para iniciar el escaneo profundo.
    """

    def __init__(self, parent, controlador, on_iniciar_escaneo: Callable):
        super().__init__(parent, fg_color="transparent")

        self.controlador = controlador
        self.on_iniciar_escaneo = on_iniciar_escaneo
        self.ruta_seleccionada = ctk.StringVar(value="")
        self.es_carpeta_personalizada = False

        # Configurar grid
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Crear elementos de la interfaz
        self._crear_encabezado()
        self._crear_banner_permisos()
        self._crear_contenido()
        self._crear_boton_inicio()

    def _crear_encabezado(self):
        """Crea el encabezado con el logo y título."""
        frame_header = ctk.CTkFrame(self, fg_color="transparent")
        frame_header.grid(row=0, column=0, sticky="ew", pady=(0, 20))

        # Logo/Icono
        label_icono = ctk.CTkLabel(
            frame_header,
            text="🕷️",
            font=ctk.CTkFont(size=64)
        )
        label_icono.pack(pady=(20, 10))

        # Título
        label_titulo = ctk.CTkLabel(
            frame_header,
            text="SpiderRecovery",
            font=ctk.CTkFont(size=36, weight="bold"),
            text_color="#4FC3F7"
        )
        label_titulo.pack()

        # Subtítulo
        label_subtitulo = ctk.CTkLabel(
            frame_header,
            text="Recupera tus archivos eliminados con tecnología de escaneo profundo",
            font=ctk.CTkFont(size=14),
            text_color="#888888"
        )
        label_subtitulo.pack(pady=(5, 0))

    def _crear_banner_permisos(self):
        """
        Crea un banner de advertencia si no se tienen privilegios de administrador.
        
        El mensaje varía según el sistema operativo:
        - Linux: recomienda usar sudo
        - Windows: recomienda ejecutar como Administrador
        """
        sistema = detectar_sistema()
        es_admin = es_usuario_admin()

        if not es_admin:
            # Banner de advertencia en rojo
            frame_banner = ctk.CTkFrame(self, fg_color="#2D1B1B", corner_radius=8)
            frame_banner.grid(row=1, column=0, sticky="ew", padx=40, pady=(0, 10))

            # Mensaje específico según el sistema operativo
            if sistema == "Linux":
                mensaje = "⚠️ Se requieren permisos de administrador (sudo) para escanear discos físicos."
            elif sistema == "Windows":
                mensaje = "⚠️ Se requieren permisos de administrador para escanear discos físicos. Ejecute como Administrador."
            else:
                mensaje = "⚠️ Se requieren privilegios elevados para escanear discos físicos."

            label_banner = ctk.CTkLabel(
                frame_banner,
                text=mensaje,
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color="#FF6B6B",
                anchor="center"
            )
            label_banner.pack(fill="x", padx=20, pady=12)

            # Ajustar el grid para el contenido principal
            self.grid_rowconfigure(2, weight=1)
        else:
            # Sin banner, el contenido principal ocupa la fila 1
            self.grid_rowconfigure(2, weight=1)

    def _crear_contenido(self):
        """Crea el contenido principal con el menú desplegable de unidades."""
        frame_contenido = ctk.CTkFrame(self, fg_color="#1E1E1E", corner_radius=16)
        frame_contenido.grid(row=2, column=0, sticky="nsew", padx=40, pady=10)
        frame_contenido.grid_rowconfigure(1, weight=0)
        frame_contenido.grid_rowconfigure(2, weight=1)
        frame_contenido.grid_columnconfigure(0, weight=1)

        # Título de la sección
        label_seccion = ctk.CTkLabel(
            frame_contenido,
            text="Selecciona una unidad o partición para escanear",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#CCCCCC"
        )
        label_seccion.grid(row=0, column=0, sticky="w", padx=20, pady=(20, 10))

        # Obtener unidades del sistema (multiplataforma)
        self.unidades = self._obtener_unidades()
        self.opciones_unidades = [f"{nombre} — {espacio}" for ruta, nombre, espacio in self.unidades]

        # Menú desplegable de unidades
        self.menu_unidades = ctk.CTkOptionMenu(
            frame_contenido,
            values=self.opciones_unidades if self.opciones_unidades else ["No se encontraron unidades"],
            font=ctk.CTkFont(size=14),
            height=45,
            corner_radius=10,
            fg_color="#2D2D2D",
            button_color="#4FC3F7",
            button_hover_color="#29B6F6",
            dropdown_fg_color="#2D2D2D",
            dropdown_hover_color="#3D3D3D",
            dropdown_text_color="#CCCCCC",
            text_color="#CCCCCC",
            command=self._al_seleccionar_unidad
        )
        self.menu_unidades.grid(row=1, column=0, sticky="ew", padx=20, pady=10)
        if self.opciones_unidades:
            self.menu_unidades.set(self.opciones_unidades[0])

        # Información de la unidad seleccionada
        self.frame_info = ctk.CTkFrame(frame_contenido, fg_color="transparent")
        self.frame_info.grid(row=2, column=0, sticky="nsew", padx=20, pady=10)
        self.frame_info.grid_columnconfigure(0, weight=1)

        # Botón para seleccionar carpeta personalizada
        frame_carpeta = ctk.CTkFrame(frame_contenido, fg_color="transparent")
        frame_carpeta.grid(row=3, column=0, sticky="ew", padx=20, pady=(10, 20))

        self.boton_carpeta = ctk.CTkButton(
            frame_carpeta,
            text="📁 Examinar carpeta...",
            font=ctk.CTkFont(size=13),
            height=40,
            corner_radius=10,
            fg_color="#2D2D2D",
            hover_color="#3D3D3D",
            command=self._seleccionar_carpeta
        )
        self.boton_carpeta.pack(side="left")

        # Etiqueta de ruta seleccionada
        self.label_ruta = ctk.CTkLabel(
            frame_carpeta,
            text="",
            font=ctk.CTkFont(size=12),
            text_color="#888888"
        )
        self.label_ruta.pack(side="left", padx=(15, 0))

        # Actualizar información inicial
        self._actualizar_info_unidad()

    def _actualizar_info_unidad(self):
        """Actualiza la información de la unidad seleccionada."""
        # Limpiar frame de información
        for widget in self.frame_info.winfo_children():
            widget.destroy()

        # Si se seleccionó una carpeta personalizada, mostrar su ruta
        if self.es_carpeta_personalizada:
            ruta = self.ruta_seleccionada.get()
            tarjeta = ctk.CTkFrame(self.frame_info, fg_color="#2D2D2D", corner_radius=12)
            tarjeta.pack(fill="x", padx=5, pady=5)
            tarjeta.grid_columnconfigure(0, weight=1)

            label_icono = ctk.CTkLabel(
                tarjeta,
                text="📁",
                font=ctk.CTkFont(size=24)
            )
            label_icono.pack(fill="x", padx=15, pady=(15, 5))

            label_nombre = ctk.CTkLabel(
                tarjeta,
                text="Carpeta personalizada",
                font=ctk.CTkFont(size=16, weight="bold"),
                text_color="#4FC3F7",
                anchor="w"
            )
            label_nombre.pack(fill="x", padx=15)

            label_ruta = ctk.CTkLabel(
                tarjeta,
                text=f"Ruta: {ruta}",
                font=ctk.CTkFont(size=12),
                text_color="#888888",
                anchor="w"
            )
            label_ruta.pack(fill="x", padx=15, pady=(5, 15))
            return

        # Mostrar información de la unidad del sistema
        indice = self._obtener_indice_seleccionado()
        if indice is None or indice >= len(self.unidades):
            return

        ruta, nombre, espacio = self.unidades[indice]

        # Tarjeta de información
        tarjeta = ctk.CTkFrame(self.frame_info, fg_color="#2D2D2D", corner_radius=12)
        tarjeta.pack(fill="x", padx=5, pady=5)
        tarjeta.grid_columnconfigure(0, weight=1)

        label_nombre = ctk.CTkLabel(
            tarjeta,
            text=f"💾 {nombre}",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#FFFFFF",
            anchor="w"
        )
        label_nombre.pack(fill="x", padx=15, pady=(15, 5))

        label_ruta = ctk.CTkLabel(
            tarjeta,
            text=f"Ruta: {ruta}",
            font=ctk.CTkFont(size=12),
            text_color="#888888",
            anchor="w"
        )
        label_ruta.pack(fill="x", padx=15)

        label_espacio = ctk.CTkLabel(
            tarjeta,
            text=f"Espacio: {espacio}",
            font=ctk.CTkFont(size=12),
            text_color="#66BB6A",
            anchor="w"
        )
        label_espacio.pack(fill="x", padx=15, pady=(5, 15))

    def _obtener_indice_seleccionado(self) -> Optional[int]:
        """Obtiene el índice de la unidad seleccionada en el menú."""
        seleccion = self.menu_unidades.get()
        for i, opcion in enumerate(self.opciones_unidades):
            if opcion == seleccion:
                return i
        return None

    def _al_seleccionar_unidad(self, seleccion: str):
        """Maneja el cambio de selección en el menú desplegable."""
        self.es_carpeta_personalizada = False
        self._actualizar_info_unidad()
        indice = self._obtener_indice_seleccionado()
        if indice is not None and indice < len(self.unidades):
            ruta = self.unidades[indice][0]
            self.ruta_seleccionada.set(ruta)
            self.label_ruta.configure(text=f"Ruta: {ruta}")

    def actualizar_unidades(self):
        """
        Re-escanea las unidades del sistema y actualiza el menú desplegable.
        
        Este método se llama cada vez que se muestra la pantalla de selección
        para asegurar que la lista de unidades esté siempre actualizada.
        """
        # Obtener unidades actualizadas del sistema (multiplataforma)
        self.unidades = self._obtener_unidades()
        self.opciones_unidades = [f"{nombre} — {espacio}" for ruta, nombre, espacio in self.unidades]

        # Actualizar el menú desplegable
        if self.opciones_unidades:
            self.menu_unidades.configure(values=self.opciones_unidades)
            self.menu_unidades.set(self.opciones_unidades[0])
        else:
            self.menu_unidades.configure(values=["No se encontraron unidades"])
            self.menu_unidades.set("No se encontraron unidades")

        # Actualizar la información de la unidad seleccionada
        self._actualizar_info_unidad()

        # Actualizar la ruta seleccionada
        self._al_seleccionar_unidad(self.menu_unidades.get())

    def _obtener_unidades(self) -> list[tuple[str, str, str]]:
        """
        Obtiene las unidades disponibles según el sistema operativo.
        
        En Linux: usa psutil para obtener particiones montadas.
        En Windows: genera rutas físicas para cada letra de unidad disponible.
        """
        sistema = detectar_sistema()
        
        if sistema == "Windows":
            return self._obtener_unidades_windows()
        else:
            return self._obtener_unidades_linux()

    def _obtener_unidades_linux(self) -> list[tuple[str, str, str]]:
        """Obtiene las unidades montadas en Linux usando psutil."""
        unidades = []

        try:
            # Obtener todas las particiones del sistema
            particiones = psutil.disk_partitions(all=False)

            for particion in particiones:
                punto_montaje = particion.mountpoint
                dispositivo = particion.device
                tipo_fs = particion.fstype

                # Filtrar rutas del sistema irrelevantes
                if punto_montaje in ["/boot", "/dev", "/proc", "/sys", "/run", "/boot/efi"]:
                    continue

                # Obtener información de espacio
                try:
                    uso = psutil.disk_usage(punto_montaje)
                    total_gb = uso.total / (1024 ** 3)
                    libre_gb = uso.free / (1024 ** 3)
                    espacio = f"{total_gb:.1f} GB total • {libre_gb:.1f} GB libre"
                except (OSError, PermissionError):
                    espacio = "Espacio no disponible"

                # Nombre descriptivo
                if punto_montaje == "/":
                    nombre = "Disco del sistema (raíz)"
                elif punto_montaje.startswith("/home"):
                    nombre = f"Datos de usuario ({punto_montaje})"
                elif punto_montaje.startswith(("/mnt", "/media")):
                    nombre = f"Unidad externa ({punto_montaje})"
                else:
                    nombre = f"Unidad ({punto_montaje})"

                unidades.append((punto_montaje, nombre, espacio))

        except Exception as e:
            print(f"Error obteniendo unidades con psutil: {e}")

        # Si no se encontraron unidades, añadir opción de carpeta personalizada
        if not unidades:
            unidades.append(("~", "Carpeta personalizada", "Selecciona una carpeta específica"))

        return unidades

    def _obtener_unidades_windows(self) -> list[tuple[str, str, str]]:
        """
        Obtiene las unidades de Windows y las mapea a rutas físicas.
        
        Cada unidad se convierte a su ruta de dispositivo físico:
        - D: -> \\\\.\\D:
        - C: -> \\\\.\\PhysicalDrive0 (para el disco del sistema)
        """
        unidades = []

        try:
            # Obtener todas las unidades de disco disponible
            particiones = psutil.disk_partitions(all=False)

            for particion in particiones:
                punto_montaje = particion.mountpoint  # Ej: "C:\\", "D:\\"
                dispositivo = particion.device
                tipo_fs = particion.fstype

                # Obtener la letra de la unidad
                letra = punto_montaje.rstrip("\\").rstrip(":")

                # Generar ruta física de Windows
                ruta_fisica = f"\\\\.\\{letra}:"

                # Obtener información de espacio
                try:
                    uso = psutil.disk_usage(punto_montaje)
                    total_gb = uso.total / (1024 ** 3)
                    libre_gb = uso.free / (1024 ** 3)
                    espacio = f"{total_gb:.1f} GB total • {libre_gb:.1f} GB libre"
                except (OSError, PermissionError):
                    espacio = "Espacio no disponible"

                # Nombre descriptivo
                if letra.upper() == "C":
                    nombre = "Disco del sistema (C:)"
                else:
                    nombre = f"Unidad ({letra}:)"

                unidades.append((ruta_fisica, nombre, espacio))

        except Exception as e:
            print(f"Error obteniendo unidades de Windows: {e}")

        # Si no se encontraron unidades, añadir opción de carpeta personalizada
        if not unidades:
            unidades.append(("~", "Carpeta personalizada", "Selecciona una carpeta específica"))

        return unidades

    def _seleccionar_carpeta(self):
        """Abre un diálogo para seleccionar una carpeta personalizada."""
        from tkinter import filedialog

        carpeta = filedialog.askdirectory(
            title="Selecciona una carpeta para escanear",
            mustexist=True
        )

        if carpeta:
            self.es_carpeta_personalizada = True
            self.ruta_seleccionada.set(carpeta)
            self.label_ruta.configure(text=f"Ruta: {carpeta}")
            # Actualizar la tarjeta de información para mostrar la carpeta
            self._actualizar_info_unidad()

    def _crear_boton_inicio(self):
        """Crea el botón principal de inicio de escaneo."""
        frame_boton = ctk.CTkFrame(self, fg_color="transparent")
        frame_boton.grid(row=3, column=0, sticky="ew", pady=(10, 20))

        self.boton_iniciar = ctk.CTkButton(
            frame_boton,
            text="🔍 Iniciar Escaneo Profundo",
            font=ctk.CTkFont(size=16, weight="bold"),
            height=55,
            corner_radius=14,
            fg_color="#4FC3F7",
            hover_color="#29B6F6",
            text_color="#000000",
            command=self._iniciar_escaneo
        )
        self.boton_iniciar.pack(fill="x", padx=40)

    def _iniciar_escaneo(self):
        """
        Inicia el escaneo con la ruta seleccionada.
        
        Si se seleccionó una unidad de disco, resuelve la ruta al dispositivo
        de bloque real (ej: /dev/sda1) antes de iniciar el escaneo.
        """
        ruta = self.ruta_seleccionada.get()

        if not ruta or ruta == "~":
            # Mostrar error si no hay ruta seleccionada
            self._mostrar_error("Por favor, selecciona una unidad o carpeta para escanear")
            return

        # Expandir ~ a la ruta del home
        ruta = str(Path(ruta).expanduser())

        # Si es una carpeta personalizada, iniciar escaneo directamente
        if self.es_carpeta_personalizada:
            self.on_iniciar_escaneo(ruta)
            return

        # Si es una unidad del sistema, resolver al dispositivo de bloque real
        from core.scanner import resolver_ruta_dispositivo
        ruta_resuelta = resolver_ruta_dispositivo(ruta)
        
        if ruta_resuelta != ruta:
            print(f"[RESOLUCIÓN] Unidad '{ruta}' resuelta a dispositivo: '{ruta_resuelta}'")
            ruta = ruta_resuelta

        # Llamar al callback
        self.on_iniciar_escaneo(ruta)

    def _mostrar_error(self, mensaje: str):
        """Muestra un mensaje de error temporal."""
        dialog = ctk.CTkToplevel(self)
        dialog.title("Error")
        dialog.geometry("400x150")
        dialog.transient(self)
        dialog.grab_set()

        label = ctk.CTkLabel(
            dialog,
            text=mensaje,
            font=ctk.CTkFont(size=14),
            text_color="#FF6B6B",
            wraplength=350
        )
        label.pack(expand=True)

        boton = ctk.CTkButton(
            dialog,
            text="Aceptar",
            command=dialog.destroy,
            fg_color="#4FC3F7",
            text_color="#000000"
        )
        boton.pack(pady=15)
