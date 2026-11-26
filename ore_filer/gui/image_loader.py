from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QImage, QPixmap

from ore_filer.services.image_operations import HEIF_EXTENSIONS, open_heif_image


def load_image_pixmap(path: Path) -> QPixmap:
    if path.suffix.casefold() not in HEIF_EXTENSIONS:
        return QPixmap(str(path))

    try:
        image = open_heif_image(path)
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
