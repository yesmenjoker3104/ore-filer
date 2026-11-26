from __future__ import annotations

from pathlib import Path

from PIL import ImageOps
from pillow_heif import open_heif
from PySide6.QtGui import QImage, QPixmap

HEIF_EXTENSIONS = frozenset({".heic", ".heif"})


def load_image_pixmap(path: Path) -> QPixmap:
    if path.suffix.casefold() not in HEIF_EXTENSIONS:
        return QPixmap(str(path))

    try:
        heif_file = open_heif(path, convert_hdr_to_8bit=True)
        image = ImageOps.exif_transpose(heif_file.to_pillow())
    except (ValueError, EOFError, SyntaxError, RuntimeError, OSError):
        return QPixmap()

    if "A" in image.getbands():
        image = image.convert("RGBA")
        image_format = QImage.Format.Format_RGBA8888
    else:
        image = image.convert("RGB")
        image_format = QImage.Format.Format_RGB888

    qimage = QImage(
        image.tobytes(),
        image.width,
        image.height,
        image_format,
    ).copy()
    return QPixmap.fromImage(qimage)
