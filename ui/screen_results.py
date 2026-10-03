"""
Pantalla 3: Resultados del escaneo
Explorador de doble panel con Treeview paginado de alto rendimiento
Arquitectura: ttk.Treeview + Paginación + Persistencia de selección
"""

from pathlib import Path
import tkinter as tk
from tkinter import ttk
import customtkinter as ctk
from tkinter import filedialog
from typing import Callable, Optional

from core.scanner import ArchivoEncontrado
from core.recover import RecuperadorArchivos
from core.signatures import CATEGORIAS


class PantallaResultados(ctk.CTkFrame):
    """
    Pantalla de resultados con Treeview paginado de alto rendimiento.
    
    Características:
    - ttk.Treeview nativo con multi-columna
    - Paginación de 100 elementos por página
    - Filtrado instantáneo sin recargar toda la lista
    - Persistencia de selección entre páginas
    - Estilo oscuro minimalista personalizado
    """

    # Configuración de paginación
    ITEMS_POR_PAGINA = 100

    def __init__(self, parent, controlador, on_volver: Callable):
        super().__init__(parent, fg_color="transparent")

        self.controlador = controlador
        self.on_volver = on_volver
        self.archivos: list[ArchivoEncontrado] = []
        self.archivos_filtrados: list[ArchivoEncontrado] = []
        self.recuperador = RecuperadorArchivos()
        
        # Estado de paginación
        self.pagina_actual: int = 1
        self.total_paginas: int = 1
        
        # Persistencia de selección: {id(archivo): bool}
        self.selecciones: dict[int, bool] = {}
        
        # Estado de filtro por categoría
        self.categoria_filtrada: Optional[str] = None
        self.nodos_categorias: dict[str, ctk.CTkFrame] = {}
        
        # Configurar estilo del Treeview
        self._configurar_estilo_treeview()
        
        # Configurar grid
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Crear elementos
        self._crear_encabezado()
        self._crear_panel_principal()
        self._crear_barra_inferior()

    def _configurar_estilo_treeview(self):
        """Configura el estilo oscuro minimalista del Treeview."""
        self.style = ttk.Style()
        self.style.theme_use('default')
        
        # Colores
        bg_color = "#1A1A1A"
        fg_color = "#FFFFFF"
        select_color = "#4FC3F7"
        header_bg = "#2D2D2D"
        
        # Estilo del Treeview
        self.style.configure(
            "Custom.Treeview",
            background=bg_color,
            foreground=fg_color,
            fieldbackground=bg_color,
            borderwidth=0,
            rowheight=30,
            font=("Segoe UI", 10)
        )
        self.style.configure(
            "Custom.Treeview.Heading",
            background=header_bg,
            foreground=fg_color,
            font=("Segoe UI", 10, "bold"),
            padding=5
        )
        self.style.map(
            "Custom.Treeview",
            background=[("selected", select_color)],
            foreground=[("selected", "#000000")]
        )

    def _crear_encabezado(self):
        """Crea el encabezado con resumen y barra de filtros."""
        frame_header = ctk.CTkFrame(self, fg_color="transparent")
        frame_header.grid(row=0, column=0, sticky="ew", pady=(10, 10))
        frame_header.grid_columnconfigure(0, weight=1)

        # Fila superior: Título y botón volver
        frame_titulo = ctk.CTkFrame(frame_header, fg_color="transparent")
        frame_titulo.grid(row=0, column=0, sticky="ew", padx=20, pady=(0, 10))

        # Icono y título
        frame_icono_titulo = ctk.CTkFrame(frame_titulo, fg_color="transparent")
        frame_icono_titulo.pack(side="left")

        label_icono = ctk.CTkLabel(
            frame_icono_titulo,
            text="✅",
            font=ctk.CTkFont(size=36)
        )
        label_icono.pack(side="left")

        frame_texto = ctk.CTkFrame(frame_icono_titulo, fg_color="transparent")
        frame_texto.pack(side="left", padx=(10, 0))

        label_titulo = ctk.CTkLabel(
            frame_texto,
            text="Escaneo completado",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color="#4FC3F7",
            anchor="w"
        )
        label_titulo.pack(anchor="w")

        self.label_resumen = ctk.CTkLabel(
            frame_texto,
            text="",
            font=ctk.CTkFont(size=12),
            text_color="#888888",
            anchor="w"
        )
        self.label_resumen.pack(anchor="w")

        # Botón volver
        self.boton_volver = ctk.CTkButton(
            frame_titulo,
            text="← Nuevo escaneo",
            font=ctk.CTkFont(size=13),
            width=140,
            height=36,
            corner_radius=10,
            fg_color="#2D2D2D",
            hover_color="#3D3D3D",
            command=self.on_volver
        )
        self.boton_volver.pack(side="right")

        # Fila inferior: Barra de filtros
        self._crear_barra_filtros(frame_header)

    def _crear_barra_filtros(self, parent):
        """Crea la barra de filtros horizontal."""
        frame_filtros = ctk.CTkFrame(parent, fg_color="#1E1E1E", corner_radius=12)
        frame_filtros.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 10))
        frame_filtros.grid_columnconfigure(0, weight=2)
        frame_filtros.grid_columnconfigure(1, weight=1)
        frame_filtros.grid_columnconfigure(2, weight=1)

        # Campo de búsqueda
        self.var_busqueda = ctk.StringVar()
        self.var_busqueda.trace_add("write", self._aplicar_filtros)
        
        self.entry_busqueda = ctk.CTkEntry(
            frame_filtros,
            textvariable=self.var_busqueda,
            placeholder_text="Buscar por nombre o extensión...",
            font=ctk.CTkFont(size=12),
            height=35,
            corner_radius=8,
            fg_color="#2D2D2D",
            border_color="#4FC3F7",
            text_color="#CCCCCC"
        )
        self.entry_busqueda.grid(row=0, column=0, sticky="ew", padx=10, pady=10)

        # Dropdown por tamaño
        self.var_filtro_tamano = ctk.StringVar(value="Todos los tamaños")
        self.opciones_tamano = [
            "Todos los tamaños",
            "< 1 MB",
            "1 MB - 100 MB",
            "> 100 MB"
        ]
        
        self.dropdown_tamano = ctk.CTkOptionMenu(
            frame_filtros,
            values=self.opciones_tamano,
            variable=self.var_filtro_tamano,
            font=ctk.CTkFont(size=12),
            height=35,
            corner_radius=8,
            fg_color="#2D2D2D",
            button_color="#4FC3F7",
            button_hover_color="#29B6F6",
            dropdown_fg_color="#2D2D2D",
            dropdown_hover_color="#3D3D3D",
            text_color="#CCCCCC",
            command=lambda _: self._aplicar_filtros()
        )
        self.dropdown_tamano.grid(row=0, column=1, sticky="ew", padx=10, pady=10)

        # Dropdown por salud
        self.var_filtro_salud = ctk.StringVar(value="Cualquier estado")
        self.opciones_salud = [
            "Cualquier estado",
            "Solo salud Buena"
        ]
        
        self.dropdown_salud = ctk.CTkOptionMenu(
            frame_filtros,
            values=self.opciones_salud,
            variable=self.var_filtro_salud,
            font=ctk.CTkFont(size=12),
            height=35,
            corner_radius=8,
            fg_color="#2D2D2D",
            button_color="#4FC3F7",
            button_hover_color="#29B6F6",
            dropdown_fg_color="#2D2D2D",
            dropdown_hover_color="#3D3D3D",
            text_color="#CCCCCC",
            command=lambda _: self._aplicar_filtros()
        )
        self.dropdown_salud.grid(row=0, column=2, sticky="ew", padx=10, pady=10)

    def _crear_panel_principal(self):
        """Crea el panel principal de doble vista."""
        frame_principal = ctk.CTkFrame(self, fg_color="transparent")
        frame_principal.grid(row=1, column=0, sticky="nsew", padx=20, pady=5)
        frame_principal.grid_rowconfigure(0, weight=1)
        frame_principal.grid_columnconfigure(0, weight=1)
        frame_principal.grid_columnconfigure(1, weight=2)

        # Panel izquierdo: Árbol de categorías
        self._crear_panel_arbol(frame_principal)

        # Panel derecho: Treeview paginado
        self._crear_panel_treeview(frame_principal)

    def _crear_panel_arbol(self, parent):
        """Crea el panel izquierdo con el árbol de categorías."""
        frame_arbol = ctk.CTkFrame(parent, fg_color="#1E1E1E", corner_radius=16)
        frame_arbol.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        frame_arbol.grid_rowconfigure(1, weight=1)
        frame_arbol.grid_columnconfigure(0, weight=1)

        # Título
        label_titulo = ctk.CTkLabel(
            frame_arbol,
            text="📂 Explorar por",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#CCCCCC"
        )
        label_titulo.grid(row=0, column=0, sticky="w", padx=15, pady=(15, 5))

        # Pestañas de vista
        self.tabs_vista = ctk.CTkTabview(
            frame_arbol,
            fg_color="transparent",
            segmented_button_fg_color="#2D2D2D",
            segmented_button_selected_color="#4FC3F7",
            segmented_button_selected_hover_color="#29B6F6",
            text_color="#888888",
            segmented_button_unselected_color="#2D2D2D"
        )
        self.tabs_vista.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))

        # Crear pestañas
        self.tabs_vista.add("Tipos de archivo")
        self.tabs_vista.add("Carpeta original")

        # Treeview para tipos de archivo
        self.tree_tipos = ctk.CTkScrollableFrame(
            self.tabs_vista.tab("Tipos de archivo"),
            fg_color="transparent"
        )
        self.tree_tipos.pack(fill="both", expand=True, padx=5, pady=5)

        # Treeview para carpetas originales
        self.tree_carpetas = ctk.CTkScrollableFrame(
            self.tabs_vista.tab("Carpeta original"),
            fg_color="transparent"
        )
        self.tree_carpetas.pack(fill="both", expand=True, padx=5, pady=5)

    def _crear_panel_treeview(self, parent):
        """Crea el panel derecho con Treeview paginado de alto rendimiento."""
        frame_treeview = ctk.CTkFrame(parent, fg_color="#1E1E1E", corner_radius=16)
        frame_treeview.grid(row=0, column=1, sticky="nsew")
        frame_treeview.grid_rowconfigure(1, weight=1)
        frame_treeview.grid_columnconfigure(0, weight=1)

        # Título y contador
        frame_titulo = ctk.CTkFrame(frame_treeview, fg_color="transparent")
        frame_titulo.grid(row=0, column=0, sticky="ew", padx=15, pady=(15, 5))

        label_titulo = ctk.CTkLabel(
            frame_titulo,
            text="📋 Archivos encontrados",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#CCCCCC"
        )
        label_titulo.pack(side="left")

        self.label_contador = ctk.CTkLabel(
            frame_titulo,
            text="0 archivos",
            font=ctk.CTkFont(size=12),
            text_color="#888888"
        )
        self.label_contador.pack(side="right")

        # Frame para Treeview con scroll
        frame_tree_container = tk.Frame(frame_treeview, bg="#1A1A1A")
        frame_tree_container.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 5))
        frame_tree_container.grid_rowconfigure(0, weight=1)
        frame_tree_container.grid_columnconfigure(0, weight=1)

        # Crear Treeview con columnas
        columnas = ("seleccion", "nombre", "tipo", "tamano", "salud", "resultado")
        self.tree_archivos = ttk.Treeview(
            frame_tree_container,
            columns=columnas,
            show="headings",
            style="Custom.Treeview",
            selectmode="browse"
        )

        # Configurar columnas
        self.tree_archivos.heading("seleccion", text="✓", anchor="center")
        self.tree_archivos.heading("nombre", text="Nombre del archivo", anchor="w")
        self.tree_archivos.heading("tipo", text="Tipo", anchor="w")
        self.tree_archivos.heading("tamano", text="Tamaño", anchor="e")
        self.tree_archivos.heading("salud", text="Salud (tamaño)", anchor="center")
        self.tree_archivos.heading("resultado", text="Resultado", anchor="center")

        self.tree_archivos.column("seleccion", width=40, minwidth=40, anchor="center", stretch=False)
        self.tree_archivos.column("nombre", width=250, minwidth=150, anchor="w")
        self.tree_archivos.column("tipo", width=100, minwidth=80, anchor="w")
        self.tree_archivos.column("tamano", width=80, minwidth=60, anchor="e", stretch=False)
        self.tree_archivos.column("salud", width=100, minwidth=80, anchor="center", stretch=False)
        self.tree_archivos.column("resultado", width=140, minwidth=100, anchor="center", stretch=False)

        # Scrollbar vertical
        scrollbar_y = ttk.Scrollbar(frame_tree_container, orient="vertical", command=self.tree_archivos.yview)
        self.tree_archivos.configure(yscrollcommand=scrollbar_y.set)

        # Scrollbar horizontal
        scrollbar_x = ttk.Scrollbar(frame_tree_container, orient="horizontal", command=self.tree_archivos.xview)
        self.tree_archivos.configure(xscrollcommand=scrollbar_x.set)

        # Posicionar Treeview y scrollbars
        self.tree_archivos.grid(row=0, column=0, sticky="nsew")
        scrollbar_y.grid(row=0, column=1, sticky="ns")
        scrollbar_x.grid(row=1, column=0, sticky="ew")

        # Bindings
        self.tree_archivos.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree_archivos.bind("<Double-1>", self._on_tree_double_click)
        self.tree_archivos.bind("<space>", self._on_tree_space)
        
        # Binding para selección global (click en header de columna ✓)
        self.tree_archivos.bind("<Button-1>", self._on_header_click)

        # Barra de paginación
        self._crear_barra_paginacion(frame_treeview)

    def _crear_barra_paginacion(self, parent):
        """Crea la barra de paginación compacta y elegante."""
        frame_paginacion = ctk.CTkFrame(parent, fg_color="transparent")
        frame_paginacion.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 10))

        # Botón anterior
        self.boton_anterior = ctk.CTkButton(
            frame_paginacion,
            text="◀ Anterior",
            font=ctk.CTkFont(size=11),
            width=90,
            height=30,
            corner_radius=6,
            fg_color="#2D2D2D",
            hover_color="#3D3D3D",
            command=self._pagina_anterior
        )
        self.boton_anterior.pack(side="left")

        # Etiqueta de página
        self.label_pagina = ctk.CTkLabel(
            frame_paginacion,
            text="Página 1 de 1",
            font=ctk.CTkFont(size=11),
            text_color="#888888"
        )
        self.label_pagina.pack(side="left", expand=True)

        # Botón siguiente
        self.boton_siguiente = ctk.CTkButton(
            frame_paginacion,
            text="Siguiente ▶",
            font=ctk.CTkFont(size=11),
            width=90,
            height=30,
            corner_radius=6,
            fg_color="#2D2D2D",
            hover_color="#3D3D3D",
            command=self._pagina_siguiente
        )
        self.boton_siguiente.pack(side="right")

    def _crear_barra_inferior(self):
        """Crea la barra inferior con ruta de destino y botón de recuperación."""
        frame_inferior = ctk.CTkFrame(self, fg_color="#1E1E1E", corner_radius=16)
        frame_inferior.grid(row=2, column=0, sticky="ew", padx=20, pady=(5, 15))
        frame_inferior.grid_rowconfigure(1, weight=0)
        frame_inferior.grid_columnconfigure(0, weight=1)

        # Fila superior: Información de selección
        frame_info = ctk.CTkFrame(frame_inferior, fg_color="transparent")
        frame_info.grid(row=0, column=0, sticky="ew", padx=20, pady=(15, 5))

        self.label_seleccion = ctk.CTkLabel(
            frame_info,
            text="0 archivos seleccionados",
            font=ctk.CTkFont(size=13),
            text_color="#888888"
        )
        self.label_seleccion.pack(side="left")

        self.label_tamano_total = ctk.CTkLabel(
            frame_info,
            text="0 MB",
            font=ctk.CTkFont(size=11),
            text_color="#666666"
        )
        self.label_tamano_total.pack(side="left", padx=(20, 0))

        # Fila inferior: Ruta de destino + botones
        frame_destino = ctk.CTkFrame(frame_inferior, fg_color="transparent")
        frame_destino.grid(row=1, column=0, sticky="ew", padx=20, pady=(5, 15))
        frame_destino.grid_columnconfigure(1, weight=1)

        # Etiqueta "Ruta de destino"
        label_destino = ctk.CTkLabel(
            frame_destino,
            text="Ruta de destino:",
            font=ctk.CTkFont(size=12),
            text_color="#888888"
        )
        label_destino.grid(row=0, column=0, sticky="w", padx=(0, 10))

        # Campo de ruta (no editable)
        self.ruta_destino = ctk.StringVar(value=str(Path.home() / "Recuperados"))
        self.entry_ruta = ctk.CTkEntry(
            frame_destino,
            textvariable=self.ruta_destino,
            font=ctk.CTkFont(size=12),
            height=38,
            corner_radius=8,
            fg_color="#2D2D2D",
            border_color="#4FC3F7",
            text_color="#CCCCCC",
            state="readonly"
        )
        self.entry_ruta.grid(row=0, column=1, sticky="ew", padx=(0, 10))

        # Botón "Cambiar ruta"
        self.boton_cambiar_ruta = ctk.CTkButton(
            frame_destino,
            text="Cambiar ruta",
            font=ctk.CTkFont(size=12),
            width=120,
            height=38,
            corner_radius=8,
            fg_color="#2D2D2D",
            hover_color="#3D3D3D",
            command=self._cambiar_ruta_destino
        )
        self.boton_cambiar_ruta.grid(row=0, column=2, padx=(0, 10))

        # Botón de recuperación (sticky)
        self.boton_recuperar = ctk.CTkButton(
            frame_destino,
            text="💾 Recuperar seleccionados",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=38,
            corner_radius=10,
            fg_color="#4FC3F7",
            hover_color="#29B6F6",
            text_color="#000000",
            command=self._iniciar_recuperacion
        )
        self.boton_recuperar.grid(row=0, column=3, sticky="e")

    # ==================== MÉTODOS DE PAGINACIÓN ====================

    def _pagina_anterior(self):
        """Navega a la página anterior."""
        if self.pagina_actual > 1:
            self.pagina_actual -= 1
            self._cargar_pagina_actual()

    def _pagina_siguiente(self):
        """Navega a la página siguiente."""
        if self.pagina_actual < self.total_paginas:
            self.pagina_actual += 1
            self._cargar_pagina_actual()

    def _cargar_pagina_actual(self):
        """
        Carga solo los archivos de la página actual en el Treeview.
        
        Este método es la clave del rendimiento:
        - Elimina solo los items visibles (máximo 100)
        - Inserta solo los items de la página actual
        - No toca los archivos fuera de la página
        """
        # Calcular rango de la página actual
        inicio = (self.pagina_actual - 1) * self.ITEMS_POR_PAGINA
        fin = min(inicio + self.ITEMS_POR_PAGINA, len(self.archivos_filtrados))
        archivos_pagina = self.archivos_filtrados[inicio:fin]

        # Limpiar Treeview (solo los items visibles, máximo 100)
        self.tree_archivos.delete(*self.tree_archivos.get_children())

        # Insertar solo los archivos de la página actual
        for archivo in archivos_pagina:
            # Verificar si está seleccionado (persistencia)
            seleccionado = self.selecciones.get(id(archivo), False)
            archivo.seleccionado = seleccionado
            
            # Formatear valores
            tamano_str = self._formatear_tamano(archivo.tamano)
            icono_salud = self._obtener_icono_salud(archivo.salud)
            marca_seleccion = "✓" if seleccionado else ""
            
            # Insertar en Treeview
            self.tree_archivos.insert(
                "",
                "end",
                iid=str(id(archivo)),
                values=(marca_seleccion, archivo.nombre, archivo.extension.upper(), tamano_str, f"{icono_salud} {archivo.salud}", self._texto_resultado(archivo))
            )

        # Actualizar etiqueta de página
        self.label_pagina.configure(text=f"Página {self.pagina_actual} de {self.total_paginas}")
        
        # Actualizar estado de botones
        self.boton_anterior.configure(state="normal" if self.pagina_actual > 1 else "disabled")
        self.boton_siguiente.configure(state="normal" if self.pagina_actual < self.total_paginas else "disabled")

    def _formatear_tamano(self, bytes_valor: int) -> str:
        """Formatea bytes a cadena legible."""
        if bytes_valor < 1024:
            return f"{bytes_valor} B"
        elif bytes_valor < 1024 ** 2:
            return f"{bytes_valor / 1024:.1f} KB"
        elif bytes_valor < 1024 ** 3:
            return f"{bytes_valor / (1024 ** 2):.1f} MB"
        else:
            return f"{bytes_valor / (1024 ** 3):.1f} GB"

    def _texto_resultado(self, archivo: ArchivoEncontrado) -> str:
        """Devuelve la etiqueta de resultado de recuperación para la tabla."""
        if archivo.evidencia is None:
            return "—"
        return archivo.evidencia.resultado.value

    def _obtener_icono_salud(self, salud: str) -> str:
        """Obtiene el icono de salud correspondiente."""
        iconos = {
            "Buena": "🟢",
            "Regular": "🟡",
            "Mala": "🔴",
            "Desconocida": "⚪"
        }
        return iconos.get(salud, "⚪")

    # ==================== EVENTOS DEL TREEVIEW ====================

    def _on_header_click(self, event):
        """
        Maneja el click en el header del Treeview.
        
        Si el usuario hace clic en la columna de selección (✓),
        activa la selección global de todos los archivos filtrados.
        """
        # Obtener la región del header clicada
        region = self.tree_archivos.identify_region(event.x, event.y)
        
        if region == "heading":
            # Obtener la columna clicada
            columna = self.tree_archivos.identify_column(event.x)
            
            # Columna "#1" es la de selección (✓)
            if columna == "#1":
                self._toggle_seleccion_global()

    def _toggle_seleccion_global(self):
        """
        Alterna la selección de TODOS los archivos filtrados (global).
        
        Este método selecciona o deselecciona todos los archivos del filtro
        activo, independientemente de la página actual. La selección se
        almacena en el diccionario `self.selecciones` y persiste al
        cambiar de página.
        """
        # Determinar si todos están seleccionados
        total_filtrados = len(self.archivos_filtrados)
        seleccionados_actuales = sum(
            1 for a in self.archivos_filtrados 
            if self.selecciones.get(id(a), False)
        )
        
        # Toggle: si todos están seleccionados, deseleccionar; si no, seleccionar todos
        nuevo_estado = seleccionados_actuales < total_filtrados / 2
        
        # Aplicar selección global a todos los archivos filtrados
        for archivo in self.archivos_filtrados:
            self.selecciones[id(archivo)] = nuevo_estado
            archivo.seleccionado = nuevo_estado
        
        # Recargar página actual para reflejar cambios
        self._cargar_pagina_actual()
        
        # Actualizar información de selección
        self._actualizar_info_seleccion()

    def _on_tree_select(self, event):
        """Maneja la selección de un item en el Treeview."""
        selection = self.tree_archivos.selection()
        if not selection:
            return
        
        item_id = selection[0]
        # Buscar el archivo correspondiente
        for archivo in self.archivos_filtrados:
            if str(id(archivo)) == item_id:
                # Toggle selección
                self.selecciones[id(archivo)] = not self.selecciones.get(id(archivo), False)
                archivo.seleccionado = self.selecciones[id(archivo)]
                
                # Actualizar visualización
                marca = "✓" if archivo.seleccionado else ""
                tamano_str = self._formatear_tamano(archivo.tamano)
                icono_salud = self._obtener_icono_salud(archivo.salud)
                
                self.tree_archivos.item(
                    item_id,
                    values=(marca, archivo.nombre, archivo.extension.upper(), tamano_str, f"{icono_salud} {archivo.salud}", self._texto_resultado(archivo))
                )
                
                self._actualizar_info_seleccion()
                break

    def _on_tree_double_click(self, event):
        """Maneja el doble click en un item."""
        self._on_tree_select(event)

    def _on_tree_space(self, event):
        """Maneja la barra espaciadora para toggle selección."""
        self._on_tree_select(event)

    # ==================== FILTRADO ====================

    def _on_categoria_click(self, categoria: str):
        """
        Maneja el clic en una categoría del árbol.
        
        Toggle: si la categoría ya está seleccionada, quita el filtro.
        Si no está seleccionada, filtra por esa categoría.
        """
        if self.categoria_filtrada == categoria:
            # Toggle: quitar filtro
            self.categoria_filtrada = None
        else:
            # Filtrar por esta categoría
            self.categoria_filtrada = categoria

        # Actualizar visualización de nodos
        self._actualizar_visual_categorias()
        
        # Aplicar filtros
        self._aplicar_filtros()

    def _actualizar_visual_categorias(self):
        """Actualiza el color de fondo de los nodos según la selección."""
        for cat, nodo in self.nodos_categorias.items():
            es_seleccionada = (self.categoria_filtrada == cat)
            color_fondo = "#4FC3F7" if es_seleccionada else "#2D2D2D"
            color_texto = "#000000" if es_seleccionada else "#CCCCCC"
            
            nodo.configure(fg_color=color_fondo)
            # Actualizar el label dentro del nodo
            for child in nodo.winfo_children():
                if isinstance(child, ctk.CTkLabel):
                    child.configure(text_color=color_texto)

    def _aplicar_filtros(self, *args):
        """
        Aplica los filtros y recarga la primera página.
        
        El filtrado es instantáneo porque:
        1. Solo filtra la lista en memoria (sin operaciones gráficas)
        2. Recalcula el total de páginas
        3. Carga solo la primera página (máximo 100 items)
        """
        # Obtener valores de filtros
        texto_busqueda = self.var_busqueda.get().lower()
        filtro_tamano = self.var_filtro_tamano.get()
        filtro_salud = self.var_filtro_salud.get()

        # Filtrar archivos en memoria (operación rápida)
        self.archivos_filtrados = []
        for archivo in self.archivos:
            # Filtro por categoría
            if self.categoria_filtrada and archivo.categoria != self.categoria_filtrada:
                continue

            # Filtro de búsqueda
            # Filtro de búsqueda
            if texto_busqueda:
                nombre_lower = archivo.nombre.lower()
                extension_lower = archivo.extension.lower()
                if texto_busqueda not in nombre_lower and texto_busqueda not in extension_lower:
                    continue

            # Filtro de tamaño
            if filtro_tamano != "Todos los tamaños":
                tamano_mb = archivo.tamano / (1024 * 1024)
                if filtro_tamano == "< 1 MB" and tamano_mb >= 1:
                    continue
                elif filtro_tamano == "1 MB - 100 MB" and (tamano_mb < 1 or tamano_mb > 100):
                    continue
                elif filtro_tamano == "> 100 MB" and tamano_mb <= 100:
                    continue

            # Filtro de salud
            if filtro_salud == "Solo salud Buena":
                if archivo.salud not in ["Buena", "Excelente"]:
                    continue

            self.archivos_filtrados.append(archivo)

        # Actualizar contador
        self.label_contador.configure(text=f"{len(self.archivos_filtrados)} archivos")

        # Recalcular paginación
        self.total_paginas = max(1, (len(self.archivos_filtrados) + self.ITEMS_POR_PAGINA - 1) // self.ITEMS_POR_PAGINA)
        self.pagina_actual = 1

        # Cargar primera página (operación rápida, máximo 100 items)
        self._cargar_pagina_actual()

    # ==================== CARGA DE RESULTADOS ====================

    def cargar_resultados(self, archivos: list[ArchivoEncontrado]):
        """
        Carga los resultados del escaneo.
        
        Args:
            archivos: Lista de archivos encontrados
        """
        self.archivos = archivos
        self.archivos_filtrados = archivos.copy()
        self.selecciones.clear()

        # Actualizar resumen
        total_mb = sum(a.tamano for a in archivos) / (1024 * 1024)
        self.label_resumen.configure(
            text=f"Se encontraron {len(archivos)} archivos recuperables • {total_mb:.1f} MB totales"
        )
        self.label_contador.configure(text=f"{len(archivos)} archivos")

        # Poblar árbol de tipos
        self._poblar_arbol_tipos()

        # Poblar árbol de carpetas
        self._poblar_arbol_carpetas()

        # Calcular paginación
        self.total_paginas = max(1, (len(archivos) + self.ITEMS_POR_PAGINA - 1) // self.ITEMS_POR_PAGINA)
        self.pagina_actual = 1

        # Cargar primera página de forma asíncrona
        self.after(150, self._cargar_pagina_actual)

    def _poblar_arbol_tipos(self):
        """Pobla el árbol de tipos de archivo con nodos interactivos."""
        for widget in self.tree_tipos.winfo_children():
            widget.destroy()

        self.nodos_categorias.clear()
        categorias = {}
        for archivo in self.archivos:
            cat = archivo.categoria
            if cat not in categorias:
                categorias[cat] = []
            categorias[cat].append(archivo)

        for categoria, archivos in sorted(categorias.items()):
            icono = CATEGORIAS.get(categoria, "📎")
            tamano_total = sum(a.tamano for a in archivos) / (1024 * 1024)

            # Determinar si esta categoría está seleccionada
            es_seleccionada = (self.categoria_filtrada == categoria)
            color_fondo = "#4FC3F7" if es_seleccionada else "#2D2D2D"
            color_texto = "#000000" if es_seleccionada else "#CCCCCC"

            nodo = ctk.CTkFrame(self.tree_tipos, fg_color=color_fondo, corner_radius=8)
            nodo.pack(fill="x", pady=2, padx=2)
            self.nodos_categorias[categoria] = nodo

            label = ctk.CTkLabel(
                nodo,
                text=f"{icono} {categoria} ({len(archivos)}) • {tamano_total:.1f} MB",
                font=ctk.CTkFont(size=12),
                text_color=color_texto,
                anchor="w"
            )
            label.pack(fill="x", padx=10, pady=8)

            # Hacer el nodo clickeable
            nodo.bind("<Button-1>", lambda e, cat=categoria: self._on_categoria_click(cat))
            label.bind("<Button-1>", lambda e, cat=categoria: self._on_categoria_click(cat))

    def _poblar_arbol_carpetas(self):
        """Pobla el árbol de carpetas originales."""
        for widget in self.tree_carpetas.winfo_children():
            widget.destroy()

        carpetas = {}
        for archivo in self.archivos:
            carpeta = archivo.carpeta_original or "Desconocida"
            if carpeta not in carpetas:
                carpetas[carpeta] = []
            carpetas[carpeta].append(archivo)

        for carpeta, archivos in sorted(carpetas.items()):
            tamano_total = sum(a.tamano for a in archivos) / (1024 * 1024)

            nodo = ctk.CTkFrame(self.tree_carpetas, fg_color="#2D2D2D", corner_radius=8)
            nodo.pack(fill="x", pady=2, padx=2)

            label = ctk.CTkLabel(
                nodo,
                text=f"📁 {carpeta} ({len(archivos)}) • {tamano_total:.1f} MB",
                font=ctk.CTkFont(size=11),
                text_color="#CCCCCC",
                anchor="w"
            )
            label.pack(fill="x", padx=10, pady=8)

    # ==================== SELECCIÓN Y RECUPERACIÓN ====================

    def _actualizar_info_seleccion(self):
        """Actualiza la información de selección en la barra inferior."""
        seleccionados = [a for a in self.archivos if self.selecciones.get(id(a), False)]
        total_mb = sum(a.tamano for a in seleccionados) / (1024 * 1024)

        self.label_seleccion.configure(
            text=f"{len(seleccionados)} archivos seleccionados"
        )
        self.label_tamano_total.configure(
            text=f"{total_mb:.1f} MB a recuperar"
        )

    def _cambiar_ruta_destino(self):
        """Abre un diálogo para cambiar la carpeta de destino."""
        nueva_ruta = filedialog.askdirectory(
            title="Selecciona la carpeta de destino",
            initialdir=self.ruta_destino.get(),
            mustexist=True
        )

        if nueva_ruta:
            self.ruta_destino.set(nueva_ruta)

    def _iniciar_recuperacion(self):
        """Inicia el proceso de recuperación de archivos seleccionados."""
        seleccionados = [a for a in self.archivos if self.selecciones.get(id(a), False)]

        if not seleccionados:
            self._mostrar_error("Selecciona al menos un archivo para recuperar")
            return

        destino = self.ruta_destino.get()

        if not Path(destino).exists():
            self._mostrar_error(f"La carpeta de destino no existe: {destino}")
            return

        self._confirmar_recuperacion(seleccionados, destino)

    def _confirmar_recuperacion(self, archivos: list[ArchivoEncontrado], destino: str):
        """Muestra diálogo de confirmación antes de recuperar."""
        dialog = ctk.CTkToplevel(self)
        dialog.title("Confirmar recuperación")
        dialog.geometry("450x200")
        dialog.transient(self)
        dialog.grab_set()

        label_titulo = ctk.CTkLabel(
            dialog,
            text="¿Confirmar recuperación?",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#4FC3F7"
        )
        label_titulo.pack(pady=(20, 10))

        total_mb = sum(a.tamano for a in archivos) / (1024 * 1024)
        label_info = ctk.CTkLabel(
            dialog,
            text=f"Se recuperarán {len(archivos)} archivos ({total_mb:.1f} MB)\n"
                 f"en la carpeta: {destino}",
            font=ctk.CTkFont(size=12),
            text_color="#CCCCCC",
            justify="center"
        )
        label_info.pack()

        frame_botones = ctk.CTkFrame(dialog, fg_color="transparent")
        frame_botones.pack(pady=20)

        boton_cancelar = ctk.CTkButton(
            frame_botones,
            text="Cancelar",
            width=120,
            fg_color="#2D2D2D",
            hover_color="#3D3D3D",
            command=dialog.destroy
        )
        boton_cancelar.pack(side="left", padx=10)

        boton_confirmar = ctk.CTkButton(
            frame_botones,
            text="Confirmar",
            width=120,
            fg_color="#4FC3F7",
            hover_color="#29B6F6",
            text_color="#000000",
            command=lambda: self._ejecutar_recuperacion(archivos, destino, dialog)
        )
        boton_confirmar.pack(side="left", padx=10)

    def _ejecutar_recuperacion(
        self,
        archivos: list[ArchivoEncontrado],
        destino: str,
        dialog: ctk.CTkToplevel
    ):
        """Ejecuta la recuperación de archivos."""
        dialog.destroy()
        self._mostrar_progreso_recuperacion(archivos, destino)

    def _mostrar_progreso_recuperacion(self, archivos: list[ArchivoEncontrado], destino: str):
        """Muestra el progreso de la recuperación."""
        dialog = ctk.CTkToplevel(self)
        dialog.title("Recuperando archivos...")
        dialog.geometry("500x200")
        dialog.transient(self)
        dialog.grab_set()

        barra = ctk.CTkProgressBar(dialog, height=20, corner_radius=10)
        barra.pack(fill="x", padx=30, pady=(30, 10))
        barra.set(0)

        label_estado = ctk.CTkLabel(
            dialog,
            text="Preparando...",
            font=ctk.CTkFont(size=12),
            text_color="#888888"
        )
        label_estado.pack()

        label_archivo = ctk.CTkLabel(
            dialog,
            text="",
            font=ctk.CTkFont(size=11),
            text_color="#666666"
        )
        label_archivo.pack(pady=(5, 0))

        def callback_progreso(actual: int, total: int, nombre: str):
            progreso = actual / total
            barra.set(progreso)
            label_estado.configure(text=f"Recuperando {actual} de {total}...")
            label_archivo.configure(text=nombre)
            dialog.update()

        self.recuperador.registrar_callback_progreso(callback_progreso)
        recuperados, fallidos = self.recuperador.recuperar_archivos(archivos, destino)

        dialog.destroy()
        # Refrescar la tabla para mostrar la columna Resultado actualizada
        try:
            self._cargar_pagina_actual()
        except Exception:
            pass
        self._mostrar_resultado_recuperacion(recuperados, fallidos, destino, archivos)

    def _mostrar_resultado_recuperacion(self, recuperados: int, fallidos: int, destino: str, archivos=None):
        """Muestra el resultado final de la recuperación."""
        dialog = ctk.CTkToplevel(self)
        dialog.title("Recuperación completada")
        dialog.geometry("500x280")
        dialog.transient(self)
        dialog.grab_set()

        if fallidos == 0:
            icono = "✅"
            titulo = "¡Recuperación exitosa!"
            color = "#66BB6A"
        else:
            icono = "⚠️"
            titulo = "Recuperación completada con advertencias"
            color = "#FFA726"

        label_icono = ctk.CTkLabel(dialog, text=icono, font=ctk.CTkFont(size=48))
        label_icono.pack(pady=(20, 10))

        label_titulo = ctk.CTkLabel(
            dialog,
            text=titulo,
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=color
        )
        label_titulo.pack()

        # Desglose por resultado y bytes totales conservados
        lineas = [f"Se recuperaron {recuperados} archivos",
                  f"{f'({fallidos} fallidos) ' if fallidos > 0 else ''}en: {destino}"]
        if archivos:
            from core.scanner import ResultadoRecuperacion
            conteo = {}
            total_bytes = 0
            for a in archivos:
                if a.evidencia is not None:
                    conteo[a.evidencia.resultado.value] = conteo.get(a.evidencia.resultado.value, 0) + 1
                    total_bytes += a.evidencia.bytes_escritos
            if conteo:
                detalle = ", ".join(f"{k}: {v}" for k, v in sorted(conteo.items()))
                lineas.append(f"Clasificación: {detalle}")
                lineas.append(f"Bytes conservados: {total_bytes / (1024**2):.2f} MiB")

        label_info = ctk.CTkLabel(
            dialog,
            text="\n".join(lineas),
            font=ctk.CTkFont(size=12),
            text_color="#CCCCCC",
            justify="center"
        )
        label_info.pack(pady=10)

        boton = ctk.CTkButton(
            dialog,
            text="Aceptar",
            command=dialog.destroy,
            fg_color="#4FC3F7",
            text_color="#000000"
        )
        boton.pack(pady=10)

    def _mostrar_error(self, mensaje: str):
        """Muestra un mensaje de error."""
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
