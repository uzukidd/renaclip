"""Generate a multi-resolution ICO from the original PNG without stretching it."""
import argparse
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def generate(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination:
        raise ValueError('Source and destination must be different files')
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original).convert('RGBA')
    side = max(image.size)
    if side < 256:
        raise ValueError('Use an original PNG at least 256 pixels across')
    square = Image.new('RGBA', (side, side), (0, 0, 0, 0))
    square.alpha_composite(image, ((side-image.width)//2, (side-image.height)//2))
    # Create each size directly from the original rather than repeatedly resizing.
    frames = [square.resize((size, size), Image.Resampling.LANCZOS) for size in SIZES]
    buffer = BytesIO()
    frames[-1].save(buffer, format='ICO', sizes=[(s,s) for s in SIZES], append_images=frames[:-1])
    payload = buffer.getvalue()
    with Image.open(BytesIO(payload)) as check:
        assert check.ico.sizes() == {(s,s) for s in SIZES}
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    print(f'Created {destination.name}: ' + ', '.join(f'{s}x{s}' for s in SIZES))
    print(f'Source: {image.width}x{image.height}; transparent padding preserves aspect ratio.')


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=root/'assets'/'renaclip_icon.png')
    parser.add_argument('--output', type=Path, default=root/'assets'/'renaclip_icon.ico')
    args = parser.parse_args()
    generate(args.source, args.output)
