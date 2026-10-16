"""Validation for uploaded medicine-package images."""

from io import BytesIO

from PIL import Image, UnidentifiedImageError

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000

IMAGE_FORMATS = {
    "image/jpeg": ("JPEG",),
    "image/png": ("PNG",),
    "image/webp": ("WEBP",),
}


class InvalidPackageImage(ValueError):
    """The uploaded file is not a supported, valid image."""


def validate_package_image(data: bytes, declared_mime: str) -> str:
    """Validate size, MIME type, file signature, and decodability."""
    if not data:
        raise InvalidPackageImage("The uploaded image is empty.")

    if len(data) > MAX_IMAGE_BYTES:
        raise InvalidPackageImage("Image exceeds the 5 MB size limit.")

    mime_type = declared_mime.lower().split(";", 1)[0].strip()
    allowed_formats = IMAGE_FORMATS.get(mime_type)

    if allowed_formats is None:
        raise InvalidPackageImage(
            "Unsupported image type. Upload a JPEG, PNG, or WebP image."
        )

    try:
        with Image.open(BytesIO(data)) as image:
            if image.format not in allowed_formats:
                raise InvalidPackageImage(
                    "The file contents do not match the declared image type."
                )

            width, height = image.size
            if width <= 0 or height <= 0:
                raise InvalidPackageImage("The image dimensions are invalid.")

            if width * height > MAX_IMAGE_PIXELS:
                raise InvalidPackageImage(
                    "Image dimensions exceed the supported limit."
                )

            image.verify()

    except InvalidPackageImage:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
        Image.DecompressionBombError,
    ) as exc:
        raise InvalidPackageImage(
            "The uploaded file is not a valid, decodable image."
        ) from exc

    return mime_type
