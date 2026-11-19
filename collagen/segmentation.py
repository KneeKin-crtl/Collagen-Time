from dataclasses import asdict, dataclass
from typing import Protocol
import numpy as np
from skimage import filters, morphology
from scipy.ndimage import gaussian_filter


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
    background_sigma_px: float = 0.0
    low_threshold: float = 0.2
    high_threshold: float = 0.5

    def __call__(self, image):
        if self.method not in {"otsu", "manual", "local", "hysteresis"}:
            raise ValueError("Unknown threshold method.")
        if not 0 <= self.threshold <= 1:
            raise ValueError("Manual threshold must be between 0 and 1.")
        if self.block_size < 3 or self.block_size % 2 != 1:
            raise ValueError("Local block size must be odd and at least 3.")
        if min(self.opening_radius, self.closing_radius) < 0:
            raise ValueError("Morphology radii must be nonnegative.")
        if not np.isfinite(self.background_sigma_px) or self.background_sigma_px<0:
            raise ValueError('Background correction sigma must be finite and nonnegative.')
        if self.method=='hysteresis' and not (0<=self.low_threshold<self.high_threshold<=1):
            raise ValueError('Hysteresis needs 0 ≤ low threshold < high threshold ≤ 1.')
        if image.max() == image.min():
            return np.zeros(image.shape, dtype=bool)
        working=image
        if self.background_sigma_px:
            # Opt-in slowly varying illumination subtraction, at unchanged size.
            # This can amplify texture/halos: compare independent development data.
            working=image-gaussian_filter(image,self.background_sigma_px,mode='reflect')
            span=float(np.ptp(working))
            if span<=np.finfo(float).eps*max(1.,float(np.ptp(image))):
                return np.zeros(image.shape,dtype=bool)
            working=(working-working.min())/span
        if self.method=='hysteresis':
            signal=working if self.bright else 1-working
            mask=filters.apply_hysteresis_threshold(signal,self.low_threshold,self.high_threshold)
        else:
            threshold = (filters.threshold_otsu(working) if self.method == "otsu" else
                         filters.threshold_local(working, self.block_size) if self.method == "local" else self.threshold)
            mask = working > threshold if self.bright else working < threshold
        if self.opening_radius:
            mask = morphology.binary_opening(mask, morphology.disk(self.opening_radius))
        if self.closing_radius:
            mask = morphology.binary_closing(mask, morphology.disk(self.closing_radius))
        return mask


def segmentation_parameters(segmenter):
    """Keep the original default parameter identity and saved review mappings."""
    if not isinstance(segmenter,ThresholdSegmenter):return {'type':type(segmenter).__name__}
    parameters=asdict(segmenter)
    if not segmenter.background_sigma_px:parameters.pop('background_sigma_px')
    if segmenter.method!='hysteresis':
        parameters.pop('low_threshold');parameters.pop('high_threshold')
    return parameters
