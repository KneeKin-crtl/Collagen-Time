import numpy as np
from skimage.color import rgb2gray
from skimage.filters import gaussian


def normalize(image):
    image = np.asarray(image)
    if image.ndim == 3:
        image = rgb2gray(image[..., :3])
    image = image.astype(float)
    if not np.isfinite(image).all():
        raise ValueError("Image contains nonfinite pixels.")
    lo, hi = image.min(), image.max()
    return (image - lo) / (hi - lo) if hi > lo else np.zeros_like(image)


def preprocess(image, sigma=0.0):
    if sigma < 0:
        raise ValueError("Smoothing sigma must be nonnegative.")
    return gaussian(normalize(image), sigma=sigma, preserve_range=True)
