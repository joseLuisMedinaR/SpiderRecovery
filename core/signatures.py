"""
Firmas mágicas (magic bytes) para identificar tipos de archivo
Permite detectar archivos eliminados por su contenido, no por su nombre
"""

# Diccionario de firmas: extensión -> (magic bytes, descripción, categoría)
FIRMAS_ARCHIVOS = {
    # Imágenes
    "png": (b"\x89PNG\r\n\x1a\n", "Imagen PNG", "Imágenes"),
    "jpg": (b"\xff\xd8\xff", "Imagen JPEG", "Imágenes"),
    "gif": (b"GIF87a", "Imagen GIF", "Imágenes"),
    "gif89": (b"GIF89a", "Imagen GIF", "Imágenes"),
    "bmp": (b"BM", "Imagen BMP", "Imágenes"),
    "tiff": (b"II*\x00", "Imagen TIFF", "Imágenes"),
    "webp": (b"RIFF", "Imagen WebP", "Imágenes"),

    # Documentos
    "pdf": (b"%PDF", "Documento PDF", "Documentos"),
    "docx": (b"PK\x03\x04", "Documento Word (DOCX)", "Documentos"),
    "xlsx": (b"PK\x03\x04", "Hoja de cálculo Excel", "Documentos"),
    "pptx": (b"PK\x03\x04", "Presentación PowerPoint", "Documentos"),
    "odt": (b"PK\x03\x04", "Documento OpenDocument", "Documentos"),
    "rtf": (b"{\\rtf", "Documento RTF", "Documentos"),

    # Archivos de texto y código
    "txt": (None, "Archivo de texto", "Texto"),
    "html": (b"<!DOCTYPE", "Documento HTML", "Texto"),
    "xml": (b"<?xml", "Documento XML", "Texto"),
    "json": (None, "Archivo JSON", "Texto"),
    "csv": (None, "Archivo CSV", "Texto"),

    # Audio y video
    "mp3": (b"ID3", "Audio MP3", "Multimedia"),
    "mp3_2": (b"\xff\xfb", "Audio MP3", "Multimedia"),
    "wav": (b"RIFF", "Audio WAV", "Multimedia"),
    "mp4": (b"\x00\x00\x00\x18ftyp", "Video MP4", "Multimedia"),
    "avi": (b"RIFF", "Video AVI", "Multimedia"),
    "mkv": (b"\x1a\x45\xdf\xa3", "Video MKV", "Multimedia"),

    # Archivos comprimidos
    "zip": (b"PK\x03\x04", "Archivo ZIP", "Comprimidos"),
    "rar": (b"Rar!", "Archivo RAR", "Comprimidos"),
    "7z": (b"7z\xbc\xaf\x27\x1c", "Archivo 7Z", "Comprimidos"),
    "tar": (b"ustar", "Archivo TAR", "Comprimidos"),
    "gz": (b"\x1f\x8b", "Archivo GZIP", "Comprimidos"),

    # Ejecutables y binarios
    "elf": (b"\x7fELF", "Binario ELF", "Sistema"),
    "exe": (b"MZ", "Ejecutable Windows", "Sistema"),

    # Bases de datos
    "sqlite": (b"SQLite format 3\x00", "Base de datos SQLite", "Bases de datos"),
    "db": (b"SQLite format 3\x00", "Base de datos", "Bases de datos"),
}

# Mapeo de categorías para organizar los resultados
CATEGORIAS = {
    "Imágenes": "🖼️",
    "Documentos": "📄",
    "Texto": "📝",
    "Multimedia": "🎵",
    "Comprimidos": "📦",
    "Sistema": "⚙️",
    "Bases de datos": "🗃️",
    "Otros": "📎",
}


def obtener_categoria(extension: str) -> str:
    """Obtiene la categoría de un archivo según su extensión."""
    for ext, (_, _, categoria) in FIRMAS_ARCHIVOS.items():
        if ext == extension.lower():
            return categoria
    return "Otros"


def obtener_descripcion(extension: str) -> str:
    """Obtiene la descripción legible de un tipo de archivo."""
    for ext, (_, descripcion, _) in FIRMAS_ARCHIVOS.items():
        if ext == extension.lower():
            return descripcion
    return f"Archivo {extension.upper()}"
