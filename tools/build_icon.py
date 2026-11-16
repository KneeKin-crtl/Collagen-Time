"""Encode supplied PNG artwork as Windows icon assets, without changing its design.

Usage: python tools/build_icon.py artwork.png --output-dir assets
"""
import argparse
from io import BytesIO
from pathlib import Path
from PIL import Image

SIZES = [16, 24, 32, 48, 64, 128, 256]


def build_icon(source, output_dir):
    raw = Path(source).read_bytes()
    with Image.open(BytesIO(raw)) as artwork:
        if artwork.format != 'PNG':
            raise ValueError('Provide the original PNG artwork.')
        if artwork.width != artwork.height or artwork.width < 256:
            raise ValueError('Artwork must be square and at least 256 pixels wide.')
        image = artwork.convert('RGBA')
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    # Retain the supplied bytes as the master; only runtime formats are resized.
    (output / 'collagen-original.png').write_bytes(raw)
    image.resize((256, 256), Image.Resampling.LANCZOS).save(output / 'collagen.png')
    image.save(output / 'collagen.ico', format='ICO', sizes=[(n, n) for n in SIZES])
    with Image.open(output / 'collagen.ico') as icon:
        if icon.ico.sizes() != {(n, n) for n in SIZES}:
            raise RuntimeError('Windows icon is missing requested sizes.')
    return output / 'collagen.ico'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artwork', type=Path)
    parser.add_argument('--output-dir', type=Path, default=Path('assets'))
    args = parser.parse_args()
    print(build_icon(args.artwork, args.output_dir))
