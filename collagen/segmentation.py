from dataclasses import dataclass
from typing import Protocol
import numpy as np
from skimage import filters, morphology


class Segmenter(Protocol):
    def __call__(self, image: np.ndarray) -> np.ndarray: ...


@dataclass(frozen=True)
class ThresholdSegmenter:
    method: str = "otsu"
    bright: bool = True
    threshold: float = 0.5
    block_size: int = 35
    opening_radius: int = 0
    closing_radius: int = 0

    def __call__(self, image):
        if self.method not in {"otsu", "manual", "local"}:
            raise ValueError("Unknown threshold method.")
        if not 0 <= self.threshold <= 1:
            raise ValueError("Manual threshold must be between 0 and 1.")
        if self.block_size < 3 or self.block_size % 2 != 1:
            raise ValueError("Local block size must be odd and at least 3.")
        if min(self.opening_radius, self.closing_radius) < 0:
            raise ValueError("Morphology radii must be nonnegative.")
        if image.max() == image.min():
            return np.zeros(image.shape, dtype=bool)
        threshold = (filters.threshold_otsu(image) if self.method == "otsu" else
                     filters.threshold_local(image, self.block_size) if self.method == "local" else self.threshold)
        mask = image > threshold if self.bright else image < threshold
        if self.opening_radius:
            mask = morphology.binary_opening(mask, morphology.disk(self.opening_radius))
        if self.closing_radius:
            mask = morphology.binary_closing(mask, morphology.disk(self.closing_radius))
        return mask
