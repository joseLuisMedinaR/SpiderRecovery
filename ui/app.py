"""
Ventana principal de la aplicación
Gestiona la navegación entre pantallas
"""

import os
import customtkinter as ctk
from typing import Optional

from core.scanner import EscaneoProfundo, EstadoEscaneo
from ui.screen_select import PantallaSeleccion
from ui.screen_progress import PantallaProgreso
from ui.screen_results import PantallaResultados


def leer_version() -> str:
    """
    Lee la versión actual desde el archivo VERSION.
    
    Returns:
        Cadena con la versión, o "desconocida" si no se puede leer.
    """
    try:
        ruta_version = os.path.join(os.path.dirname(os.path.dirname(__file__)), "VERSION")
        with open(ruta_version, "r", encoding="utf-8") as f:
            return f.read().strip()
    except (OSError, IOError):
        return "desconocida"


class AppPrincipal(ctk.CTk):
    """
    Ventana principal de SpiderRecovery.
    
    Gestiona la navegación entre las tres pantallas:
    1. Selección de unidad/carpeta
    2. Progreso del escaneo
    3. Resultados y recuperación
    """

    def __init__(self):
        super().__init__()

        # Configuración de la ventana
        self.title("SpiderRecovery - Recuperación de Archivos")
        self.geometry("1100x700")
        self.minsize(900, 600)

        # Configurar grid principal
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)  # Fila para la etiqueta de versión
        self.grid_columnconfigure(0, weight=1)

        # Inicializar el escáner
        self.escanner = EscaneoProfundo()
        self.ruta_seleccionada: Optional[str] = None

        # Crear contenedor principal de pantallas
        self.contenedor = ctk.CTkFrame(self, fg_color="transparent")
        self.contenedor.grid(row=0, column=0, sticky="nsew", padx=20, pady=20)
        self.contenedor.grid_rowconfigure(0, weight=1)
        self.contenedor.grid_columnconfigure(0, weight=1)

        # Diccionario de pantallas
        self.pantallas = {}
        self.pantalla_actual: Optional[ctk.CTkFrame] = None

        # Inicializar TODAS las pantallas dentro del contenedor usando grid
        self._inicializar_pantallas()

        # Mostrar pantalla de selección al iniciar
        self.mostrar_pantalla("seleccion")

        # Etiqueta de versión en la esquina inferior derecha
        self._crear_etiqueta_version()

    def _crear_etiqueta_version(self):
        """Crea una etiqueta discreta con la versión en la esquina inferior derecha."""
        version = leer_version()

        frame_version = ctk.CTkFrame(self, fg_color="transparent")
        frame_version.grid(row=1, column=0, sticky="se", padx=15, pady=10)

        self.label_version = ctk.CTkLabel(
            frame_version,
            text=f"Versión: {version}",
            font=ctk.CTkFont(size=10),
            text_color="#666666"
        )
        self.label_version.pack()

    def actualizar_version(self):
        """Actualiza la etiqueta de versión leyendo el archivo VERSION."""
        version = leer_version()
        if hasattr(self, "label_version"):
            self.label_version.configure(text=f"Versión: {version}")

    def _inicializar_pantallas(self):
        """
        Crea todas las pantallas de la aplicación.
        
        Todas las pantallas se posicionan en el mismo lugar (row=0, column=0)
        dentro del contenedor usando grid con sticky="nsew".
        """
        # Crear cada pantalla dentro del contenedor
        self.pantallas["seleccion"] = PantallaSeleccion(
            self.contenedor,
            self,
            on_iniciar_escaneo=self._iniciar_escaneo
        )
        self.pantallas["progreso"] = PantallaProgreso(
            self.contenedor,
            self,
            on_escaneo_completado=self._escaneo_completado,
            on_cancelar=self._cancelar_escaneo,
            on_ver_parciales=self._ver_resultados_parciales
        )
        self.pantallas["resultados"] = PantallaResultados(
            self.contenedor,
            self,
            on_volver=self._volver_al_inicio
        )

        # Posicionar TODAS las pantallas en el mismo lugar usando grid
        for pantalla in self.pantallas.values():
            pantalla.grid(row=0, column=0, sticky="nsew")

    def mostrar_pantalla(self, nombre: str):
        """
        Muestra la pantalla especificada y oculta todas las demás.
        
        Este método garantiza que:
        1. Todas las pantallas se oculten con grid_remove()
        2. Solo la pantalla solicitada sea visible con grid() y lift()
        3. Se fuerce la actualización del layout con update_idletasks()
        
        Args:
            nombre: Nombre de la pantalla a mostrar ("seleccion", "progreso", "resultados")
        """
        # Paso 1: Ocultar TODAS las pantallas
        for pantalla in self.pantallas.values():
            pantalla.grid_remove()

        # Paso 2: Mostrar solo la pantalla solicitada
        if nombre in self.pantallas:
            self.pantalla_actual = self.pantallas[nombre]
            self.pantalla_actual.grid(row=0, column=0, sticky="nsew")
            self.pantalla_actual.lift()  # Asegurar que esté al frente

        # Paso 3: Forzar la actualización del layout
        self.update_idletasks()

        # Paso 4: Si volvemos a la pantalla de selección, actualizar las unidades
        if nombre == "seleccion":
            self.pantallas["seleccion"].actualizar_unidades()

    def _iniciar_escaneo(self, ruta: str):
        """Inicia el escaneo de la ruta seleccionada."""
        self.ruta_seleccionada = ruta

        # Configurar escáner
        self.escanner.registrar_callback(
            self.pantallas["progreso"].publicar_actualizacion
        )

        # Cambiar a pantalla de progreso
        self.mostrar_pantalla("progreso")

        # Iniciar escaneo en segundo plano
        self.escanner.iniciar(ruta)

    def _escaneo_completado(self):
        """
        Se llama cuando el escaneo se completa.
        
        Usa self.after() para dar tiempo al mainloop de Tkinter a procesar
        el layout correctamente antes de transicionar (evita crash X11 en Linux).
        """
        # Forzar actualización del layout antes de transicionar
        self.update_idletasks()
        
        # Pequeño delay para estabilizar el layout en Linux X11
        self.after(150, self._transicionar_a_resultados)

    def _transicionar_a_resultados(self):
        """
        Transiciona a la pantalla de resultados de forma segura.
        
        Protocolo de 4 pasos para evitar el crash X_CreatePixmap (0x0):
        1. Mostrar pantalla vacía
        2. Forzar mapeo con update_idletasks() + update()
        3. Cargar resultados de forma asíncrona
        4. Confirmar estabilidad final
        """
        # Paso 1: Obtener archivos (sin cargar en UI aún)
        archivos = self.escanner.obtener_archivos()
        
        # Paso 2: Mostrar pantalla de resultados vacía
        self.mostrar_pantalla("resultados")
        
        # Paso 3: Forzar mapeo completo del frame
        # update_idletasks() procesa eventos de layout pendientes
        # update() procesa todos los eventos pendientes incluyendo redraw
        self.update_idletasks()
        self.update()
        
        # Paso 4: Programar carga asíncrona segura
        # El delay de 150ms garantiza que X11 haya asignado dimensiones reales
        self.after(150, lambda: self._cargar_resultados_final(archivos))

    def _cargar_resultados_final(self, archivos):
        """
        Carga final de resultados después de que el frame esté mapeado.
        
        Args:
            archivos: Lista de archivos a cargar
        """
        try:
            # Cargar resultados (internamente usa after(150, ...) adicional)
            self.pantallas["resultados"].cargar_resultados(archivos)
            
            # Forzar actualización final
            self.update_idletasks()
            
        except Exception as e:
            print(f"[ERROR] Error en carga final de resultados: {e}")
            import traceback
            traceback.print_exc()

    def _cancelar_escaneo(self):
        """
        Cancela el escaneo actual de forma segura.
        
        Usa self.after() para evitar crashes X11 durante transiciones rápidas.
        """
        self.escanner.cancelar()
        self.update_idletasks()
        self.after(100, lambda: self.mostrar_pantalla("seleccion"))

    def _ver_resultados_parciales(self):
        """
        Detiene el escaneo actual y muestra los resultados parciales de forma segura.
        
        Este método es llamado cuando el usuario hace clic en
        "Ver archivos encontrados" durante el escaneo.
        Usa self.after() para dar tiempo al mainloop a procesar el layout.
        """
        # Forzar actualización del layout antes de detener
        self.update_idletasks()
        
        # Pequeño delay para estabilizar en Linux X11
        self.after(150, self._cargar_resultados_parciales)

    def _cargar_resultados_parciales(self):
        """
        Carga y muestra los resultados parciales de forma segura.
        
        Usa el mismo protocolo de 4 pasos que _transicionar_a_resultados
        para evitar el crash X_CreatePixmap (0x0).
        """
        # Paso 1: Detener escaneo y obtener archivos
        archivos_parciales = self.escanner.detener_y_guardar()
        
        # Paso 2: Mostrar pantalla de resultados vacía
        self.mostrar_pantalla("resultados")
        
        # Paso 3: Forzar mapeo completo del frame
        self.update_idletasks()
        self.update()
        
        # Paso 4: Programar carga asíncrona segura
        self.after(150, lambda: self._cargar_resultados_final(archivos_parciales))

    def _volver_al_inicio(self):
        """
        Vuelve a la pantalla de selección de forma segura.
        
        Reinicia el escáner, limpia la ruta seleccionada y actualiza
        la lista de unidades disponibles.
        Usa self.after() para evitar crashes X11.
        """
        # Forzar actualización del layout
        self.update_idletasks()
        
        # Reiniciar el escáner para el próximo escaneo
        self.escanner = EscaneoProfundo()
        self.ruta_seleccionada = None

        # Pequeño delay para estabilizar en Linux X11
        self.after(100, lambda: self.mostrar_pantalla("seleccion"))
