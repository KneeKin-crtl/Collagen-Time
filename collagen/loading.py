from io import BytesIO
import cv2
import numpy as np
import tifffile


def load_image(data: bytes, filename: str) -> np.ndarray:
    suffix = filename.rsplit(".", 1)[-1].lower()
    if suffix in {"tif", "tiff"}:
        with tifffile.TiffFile(BytesIO(data)) as tif:
            if len(tif.pages) != 1:
                raise ValueError("Upload a single-plane TIFF; stacks require explicit plane selection.")
            image = tif.pages[0].asarray()
    elif suffix in {"png", "jpg", "jpeg"}:
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
        if image is None:
            raise ValueError("Image could not be decoded.")
        if image.ndim == 3:
            image = cv2.cvtColor(image, cv2.COLOR_BGRA2RGBA if image.shape[2] == 4 else cv2.COLOR_BGR2RGB)
    else:
        raise ValueError("Supported formats: TIFF, PNG, JPEG.")
    if image.ndim not in (2, 3) or (image.ndim == 3 and image.shape[2] not in (3, 4)):
        raise ValueError("Expected a grayscale or RGB(A) image, not a stack.")
    if not np.isfinite(image).all() or image.size == 0:
        raise ValueError("Image must contain finite pixels.")
    return image
