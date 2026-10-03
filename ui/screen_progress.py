"""
Pantalla 2: Progreso del escaneo
Muestra el progreso en tiempo real con controles de pausa/cancelación
"""

import queue

import customtkinter as ctk
from typing import Callable, Optional

from core.scanner import ProgresoEscaneo, EstadoEscaneo, ArchivoEncontrado


class PantallaProgreso(ctk.CTkFrame):
    """
    Pantalla de progreso del escaneo.
    
    Muestra una barra de progreso, contadores de archivos encontrados,
    velocidad de escaneo y controles para pausar o cancelar.
    """

    def __init__(
        self,
        parent,
        controlador,
        on_escaneo_completado: Callable,
        on_cancelar: Callable,
        on_ver_parciales: Optional[Callable] = None
    ):
        super().__init__(parent, fg_color="transparent")

        self.controlador = controlador
        self.on_escaneo_completado = on_escaneo_completado
        self.on_cancelar = on_cancelar
        self.on_ver_parciales = on_ver_parciales

        # Variables de estado
        self.escaneo_activo = True
        self._callback_registrado = False

        # Cola thread-safe para transferir actualizaciones del hilo worker al hilo UI
        self._cola_ui: queue.Queue = queue.Queue()
        self._id_polling: Optional[str] = None
        self._iniciar_polling()

        # Configurar grid
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Crear elementos
        self._crear_encabezado()
        self._crear_barra_progreso()
        self._crear_estadisticas()
        self._crear_controles()

    def _crear_encabezado(self):
        """Crea el encabezado de la pantalla."""
        frame_header = ctk.CTkFrame(self, fg_color="transparent")
        frame_header.grid(row=0, column=0, sticky="ew", pady=(20, 10))

        # Icono animado (estático en esta versión)
        self.label_icono = ctk.CTkLabel(
            frame_header,
            text="🔍",
            font=ctk.CTkFont(size=48)
        )
        self.label_icono.pack()

        # Título
        label_titulo = ctk.CTkLabel(
            frame_header,
            text="Escaneando en progreso...",
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color="#4FC3F7"
        )
        label_titulo.pack(pady=(10, 5))

        # Ruta actual
        self.label_ruta = ctk.CTkLabel(
            frame_header,
            text="",
            font=ctk.CTkFont(size=12),
            text_color="#888888"
        )
        self.label_ruta.pack()

    def _crear_barra_progreso(self):
        """Crea la barra de progreso principal con información secundaria."""
        frame_progreso = ctk.CTkFrame(self, fg_color="transparent")
        frame_progreso.grid(row=1, column=0, sticky="ew", padx=60, pady=20)

        # Barra de progreso
        self.barra_progreso = ctk.CTkProgressBar(
            frame_progreso,
            height=20,
            corner_radius=10,
            fg_color="#2D2D2D",
            progress_color="#4FC3F7"
        )
        self.barra_progreso.pack(fill="x")
        self.barra_progreso.set(0)

        # Porcentaje
        self.label_porcentaje = ctk.CTkLabel(
            frame_progreso,
            text="0%",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#4FC3F7"
        )
        self.label_porcentaje.pack(pady=(10, 0))

        # Fila de información secundaria (velocidad, tiempo restante, procesado)
        frame_info_secundaria = ctk.CTkFrame(frame_progreso, fg_color="transparent")
        frame_info_secundaria.pack(fill="x", pady=(15, 0))

        # Variable para la información secundaria
        self.info_secundaria = ctk.StringVar(value="Velocidad: 0 MB/s  |  Tiempo restante: Calculando...  |  Procesado: 0 B / 0 B")

        self.label_info_secundaria = ctk.CTkLabel(
            frame_info_secundaria,
            textvariable=self.info_secundaria,
            font=ctk.CTkFont(size=11),
            text_color="#8A8A8F"
        )
        self.label_info_secundaria.pack()

    def _crear_estadisticas(self):
        """Crea el panel de estadísticas en tiempo real."""
        frame_stats = ctk.CTkFrame(self, fg_color="#1E1E1E", corner_radius=16)
        frame_stats.grid(row=2, column=0, sticky="ew", padx=60, pady=10)
        frame_stats.grid_columnconfigure((0, 1, 2), weight=1)

        # Archivos encontrados
        frame_archivos = ctk.CTkFrame(frame_stats, fg_color="transparent")
        frame_archivos.grid(row=0, column=0, padx=20, pady=20)

        self.label_num_archivos = ctk.CTkLabel(
            frame_archivos,
            text="0",
            font=ctk.CTkFont(size=32, weight="bold"),
            text_color="#4FC3F7"
        )
        self.label_num_archivos.pack()

        label_titulo_archivos = ctk.CTkLabel(
            frame_archivos,
            text="Archivos encontrados",
            font=ctk.CTkFont(size=12),
            text_color="#888888"
        )
        label_titulo_archivos.pack()

        # Velocidad
        frame_velocidad = ctk.CTkFrame(frame_stats, fg_color="transparent")
        frame_velocidad.grid(row=0, column=1, padx=20, pady=20)

        self.label_velocidad = ctk.CTkLabel(
            frame_velocidad,
            text="0 MB/s",
            font=ctk.CTkFont(size=32, weight="bold"),
            text_color="#66BB6A"
        )
        self.label_velocidad.pack()

        label_titulo_velocidad = ctk.CTkLabel(
            frame_velocidad,
            text="Velocidad de escaneo",
            font=ctk.CTkFont(size=12),
            text_color="#888888"
        )
        label_titulo_velocidad.pack()

        # Tiempo transcurrido
        frame_tiempo = ctk.CTkFrame(frame_stats, fg_color="transparent")
        frame_tiempo.grid(row=0, column=2, padx=20, pady=20)

        self.label_tiempo = ctk.CTkLabel(
            frame_tiempo,
            text="00:00",
            font=ctk.CTkFont(size=32, weight="bold"),
            text_color="#FFA726"
        )
        self.label_tiempo.pack()

        label_titulo_tiempo = ctk.CTkLabel(
            frame_tiempo,
            text="Tiempo transcurrido",
            font=ctk.CTkFont(size=12),
            text_color="#888888"
        )
        label_titulo_tiempo.pack()

    def _crear_controles(self):
        """
        Crea los botones de control.
        
        Incluye:
        - Pausar/Reanudar: Pausa o reanuda el escaneo
        - Ver archivos encontrados: Detiene y muestra resultados parciales
        - Cancelar: Cancela el escaneo con opción de ver parciales
        """
        frame_controles = ctk.CTkFrame(self, fg_color="transparent")
        frame_controles.grid(row=3, column=0, sticky="ew", padx=60, pady=20)

        # Botón Pausar/Reanudar
        self.boton_pausa = ctk.CTkButton(
            frame_controles,
            text="⏸️ Pausar",
            font=ctk.CTkFont(size=14, weight="bold"),
            width=140,
            height=45,
            corner_radius=12,
            fg_color="#FFA726",
            hover_color="#FF9800",
            text_color="#000000",
            command=self._alternar_pausa
        )
        self.boton_pausa.pack(side="left", padx=(0, 10))

        # Botón Ver archivos encontrados (parcial)
        self.boton_ver_parciales = ctk.CTkButton(
            frame_controles,
            text="🔍 Ver archivos encontrados",
            font=ctk.CTkFont(size=13, weight="bold"),
            width=200,
            height=45,
            corner_radius=12,
            fg_color="#4FC3F7",
            hover_color="#29B6F6",
            text_color="#000000",
            command=self._ver_parciales
        )
        self.boton_ver_parciales.pack(side="left", padx=(0, 10))

        # Botón Cancelar
        self.boton_cancelar = ctk.CTkButton(
            frame_controles,
            text="❌ Cancelar",
            font=ctk.CTkFont(size=14, weight="bold"),
            width=140,
            height=45,
            corner_radius=12,
            fg_color="#EF5350",
            hover_color="#E53935",
            text_color="#FFFFFF",
            command=self._cancelar
        )
        self.boton_cancelar.pack(side="right")

    def publicar_actualizacion(self, progreso: ProgresoEscaneo, archivos: list[ArchivoEncontrado]):
        """
        Callback invocado desde el hilo worker del escáner.

        No toca widgets ni variables de Tkinter; solo encola la actualización
        para que el hilo principal la procese. Colapsa la cola conservando
        únicamente las actualizaciones más recientes para evitar crecimiento
        ilimitado; el último estado siempre queda registrado.
        """
        try:
            self._cola_ui.put_nowait((progreso, archivos))
            while self._cola_ui.qsize() > 2:
                try:
                    self._cola_ui.get_nowait()
                except queue.Empty:
                    break
        except Exception:
            pass

    def _iniciar_polling(self):
        """Programa el drenaje periódico de la cola en el hilo principal."""
        try:
            self._id_polling = self.after(100, self._procesar_cola)
        except Exception:
            self._id_polling = None

    def _procesar_cola(self):
        """Aplica en el hilo principal las actualizaciones pendientes."""
        try:
            while True:
                progreso, archivos = self._cola_ui.get_nowait()
                self.actualizar_progreso(progreso, archivos)
        except queue.Empty:
            pass
        except Exception:
            # No interrumpir el bucle de polling por un error puntual
            pass
        self._iniciar_polling()

    def actualizar_progreso(
        self,
        progreso: ProgresoEscaneo,
        archivos: list[ArchivoEncontrado]
    ):
        """
        Callback llamado cuando el escáner actualiza su progreso.
        
        Args:
            progreso: Estado actual del progreso
            archivos: Lista actual de archivos encontrados
        """
        # Actualizar barra de progreso
        if progreso.bytes_totales > 0:
            porcentaje = progreso.bytes_leidos / progreso.bytes_totales
            self.barra_progreso.set(min(porcentaje, 1.0))
            self.label_porcentaje.configure(text=f"{int(porcentaje * 100)}%")

        # Actualizar estadísticas
        self.label_num_archivos.configure(text=str(progreso.archivos_encontrados))

        # Formatear velocidad
        velocidad_mb = progreso.velocidad / (1024 * 1024)
        self.label_velocidad.configure(text=f"{velocidad_mb:.1f} MB/s")

        # Formatear tiempo transcurrido
        minutos = int(progreso.tiempo_transcurrido) // 60
        segundos = int(progreso.tiempo_transcurrido) % 60
        self.label_tiempo.configure(text=f"{minutos:02d}:{segundos:02d}")

        # Actualizar información secundaria (velocidad, tiempo restante, procesado)
        self._actualizar_info_secundaria(progreso)

        # Actualizar ruta actual
        if progreso.ruta_actual:
            self.label_ruta.configure(text=f"Escaneando: {progreso.ruta_actual}")

        # Resetear botón de pausa cuando el escaneo está activo
        if progreso.estado == EstadoEscaneo.ESCANEANDO:
            self.boton_pausa.configure(text="⏸️ Pausar", state="normal")

        # Estado intermedio de cancelación: bloquear controles hasta finalizar
        if progreso.estado == EstadoEscaneo.CANCELANDO:
            self.boton_pausa.configure(state="disabled")
            self.boton_cancelar.configure(state="disabled")
            self.boton_ver_parciales.configure(state="disabled")
            self.label_ruta.configure(text="Cancelando escaneo...")

        # Cancelación finalizada: deshabilitar controles
        if progreso.estado == EstadoEscaneo.CANCELADO:
            self.escaneo_activo = False
            self.boton_pausa.configure(state="disabled")
            self.boton_cancelar.configure(state="disabled")
            self.boton_ver_parciales.configure(state="disabled")

        # Verificar si el escaneo terminó
        if progreso.estado == EstadoEscaneo.COMPLETADO:
            self.escaneo_activo = False
            self.boton_pausa.configure(state="disabled")
            self.boton_cancelar.configure(state="disabled")
            # Forzar actualización del layout antes de transición (estabilidad X11)
            self.update_idletasks()
            # Pequeño delay para mostrar el 100% y estabilizar en Linux X11
            self.after(1000, self.on_escaneo_completado)

    def _actualizar_info_secundaria(self, progreso: ProgresoEscaneo):
        """
        Actualiza la fila de información secundaria con velocidad, tiempo restante y progreso.
        
        Args:
            progreso: Estado actual del progreso
        """
        # Formatear velocidad
        velocidad_mb = progreso.velocidad / (1024 * 1024)
        texto_velocidad = f"Velocidad: {velocidad_mb:.1f} MB/s"

        # Formatear tiempo restante
        if progreso.estado == EstadoEscaneo.PAUSADO:
            texto_tiempo = "Tiempo restante: Pausado"
        elif progreso.tiempo_restante == 0 and progreso.velocidad == 0:
            texto_tiempo = "Tiempo restante: Calculando..."
        else:
            horas = progreso.tiempo_restante // 3600
            minutos = (progreso.tiempo_restante % 3600) // 60
            segundos = progreso.tiempo_restante % 60
            texto_tiempo = f"Tiempo restante: {horas:02d}:{minutos:02d}:{segundos:02d}"

        # Formatear bytes procesados
        bytes_leidos = self._formatear_bytes(progreso.bytes_leidos)
        bytes_totales = self._formatear_bytes(progreso.bytes_totales)
        texto_procesado = f"Procesado: {bytes_leidos} / {bytes_totales}"

        # Actualizar la etiqueta
        self.info_secundaria.set(f"{texto_velocidad}  |  {texto_tiempo}  |  {texto_procesado}")

    def _formatear_bytes(self, bytes_valor: int) -> str:
        """
        Formatea bytes a una cadena legible (B, KB, MB, GB, TB).
        
        Args:
            bytes_valor: Valor en bytes
            
        Returns:
            Cadena formateada (ej: "45.2 GB")
        """
        if bytes_valor < 1024:
            return f"{bytes_valor} B"
        elif bytes_valor < 1024 ** 2:
            return f"{bytes_valor / 1024:.1f} KB"
        elif bytes_valor < 1024 ** 3:
            return f"{bytes_valor / (1024 ** 2):.1f} MB"
        elif bytes_valor < 1024 ** 4:
            return f"{bytes_valor / (1024 ** 3):.1f} GB"
        else:
            return f"{bytes_valor / (1024 ** 4):.1f} TB"

    def _alternar_pausa(self):
        """
        Alterna entre pausar y reanudar el escaneo de forma segura.
        
        Incluye update_idletasks() para estabilidad en Linux X11.
        """
        escanner = self.controlador.escanner

        if escanner.progreso.estado == EstadoEscaneo.ESCANEANDO:
            escanner.pausar()
            self.boton_pausa.configure(text="▶️ Reanudar")
            # Forzar actualización del layout (estabilidad X11)
            self.update_idletasks()
        elif escanner.progreso.estado == EstadoEscaneo.PAUSADO:
            escanner.reanudar()
            self.boton_pausa.configure(text="⏸️ Pausar")
            # Forzar actualización del layout (estabilidad X11)
            self.update_idletasks()

    def _ver_parciales(self):
        """
        Detiene el escaneo y muestra los resultados parciales de forma segura.
        
        Llama al callback on_ver_parciales que debe estar conectado
        a la lógica de transición en la pantalla de resultados.
        Incluye update_idletasks() para estabilidad en Linux X11.
        """
        # Forzar actualización del layout antes de detener (estabilidad X11)
        self.update_idletasks()
        
        if self.on_ver_parciales:
            self.on_ver_parciales()

    def _cancelar(self):
        """
        Cancela el escaneo actual con opción de ver resultados parciales.
        
        Muestra un diálogo preguntando si desea descartar todo o ver
        los archivos encontrados hasta el momento.
        """
        # Crear diálogo de confirmación
        dialog = ctk.CTkToplevel(self)
        dialog.title("Cancelar escaneo")
        dialog.geometry("450x200")
        dialog.transient(self)
        dialog.grab_set()

        # Contenido
        label_titulo = ctk.CTkLabel(
            dialog,
            text="¿Desea ver los resultados parciales?",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#4FC3F7"
        )
        label_titulo.pack(pady=(20, 10))

        label_info = ctk.CTkLabel(
            dialog,
            text="El escaneo aún no ha terminado.\nPuede ver los archivos encontrados hasta ahora\no cancelar completamente.",
            font=ctk.CTkFont(size=12),
            text_color="#CCCCCC",
            justify="center"
        )
        label_info.pack()

        # Botones
        frame_botones = ctk.CTkFrame(dialog, fg_color="transparent")
        frame_botones.pack(pady=20)

        boton_descartar = ctk.CTkButton(
            frame_botones,
            text="Descartar todo",
            width=140,
            fg_color="#EF5350",
            hover_color="#E53935",
            text_color="#FFFFFF",
            command=lambda: self._confirmar_cancelacion(dialog, ver_parciales=False)
        )
        boton_descartar.pack(side="left", padx=10)

        boton_ver = ctk.CTkButton(
            frame_botones,
            text="Ver parciales",
            width=140,
            fg_color="#4FC3F7",
            hover_color="#29B6F6",
            text_color="#000000",
            command=lambda: self._confirmar_cancelacion(dialog, ver_parciales=True)
        )
        boton_ver.pack(side="left", padx=10)

    def _confirmar_cancelacion(self, dialog: ctk.CTkToplevel, ver_parciales: bool):
        """
        Confirma la cancelación y ejecuta la acción correspondiente de forma segura.
        
        Args:
            dialog: Diálogo de confirmación a cerrar
            ver_parciales: Si es True, muestra resultados parciales; si es False, descarta todo
        """
        dialog.destroy()
        
        # Forzar actualización del layout antes de transición (estabilidad X11)
        self.update_idletasks()
        
        if ver_parciales and self.on_ver_parciales:
            self.on_ver_parciales()
        else:
            self.controlador.escanner.cancelar()
            # Pequeño delay para estabilizar en Linux X11
            self.after(100, self.on_cancelar)
