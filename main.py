"""
SpiderRecovery - Punto de entrada principal
Aplicación de recuperación de archivos eliminados para Linux (Fedora)
"""

import customtkinter as ctk
from ui.app import AppPrincipal


def main():
    """Función principal que inicia la aplicación."""
    # Configurar el modo oscuro por defecto
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    # Crear y ejecutar la aplicación
    app = AppPrincipal()
    app.mainloop()


if __name__ == "__main__":
    main()
