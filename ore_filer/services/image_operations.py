from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageOps
from pillow_heif import open_heif

HEIF_EXTENSIONS = frozenset({".heic", ".heif"})
CONVERSION_FORMATS = {
    "JPG": {
        "extension": ".jpg",
        "pil_format": "JPEG",
        "save_kwargs": {"quality": 95, "subsampling": 0},
    },
    "PNG": {
        "extension": ".png",
        "pil_format": "PNG",
        "save_kwargs": {},
    },
    "WebP": {
        "extension": ".webp",
        "pil_format": "WEBP",
        "save_kwargs": {"quality": 95, "method": 6},
    },
}


def open_heif_image(path: Path) -> Image.Image:
    heif_file = open_heif(path, convert_hdr_to_8bit=True)
    return ImageOps.exif_transpose(heif_file.to_pillow())


def convert_images(
    pairs: list[tuple[Path, Path]],
    *,
    output_format: str,
    progress_cb: Callable[[int, int], None] | None = None,
) -> None:
    try:
        config = CONVERSION_FORMATS[output_format]
    except KeyError as error:
        raise ValueError(f"対応していない画像形式です: {output_format}") from error

    total = len(pairs)
    for index, (source, destination) in enumerate(pairs, start=1):
        if source.resolve() == destination.resolve():
            raise ValueError(f"保存先は元画像と別のパスにしてください: {source.name}")
        if destination.suffix.casefold() != config["extension"]:
            raise ValueError(f"{output_format}保存先ではありません: {destination.name}")

        if source.suffix.casefold() in HEIF_EXTENSIONS:
            image = open_heif_image(source)
        else:
            with Image.open(source) as opened:
                image = ImageOps.exif_transpose(opened).copy()

        if output_format == "JPG":
            if image.mode in {"RGBA", "LA"} or (
                image.mode == "P" and "transparency" in image.info
            ):
                image = image.convert("RGBA")
                background = Image.new("RGBA", image.size, "white")
                image = Image.alpha_composite(background, image).convert("RGB")
            elif image.mode != "RGB":
                image = image.convert("RGB")
        elif output_format == "PNG" and image.mode == "CMYK":
            image = image.convert("RGB")

        exif = image.getexif().tobytes()
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            save_kwargs: dict[str, object] = {
                "format": config["pil_format"],
                **config["save_kwargs"],
            }
            if exif:
                save_kwargs["exif"] = exif
            image.save(temporary, **save_kwargs)
            temporary.replace(destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

        if progress_cb is not None:
            progress_cb(index, total)
