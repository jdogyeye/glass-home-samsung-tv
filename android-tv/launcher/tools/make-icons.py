#!/usr/bin/env python3
"""Makes the launcher's icon and 16:9 TV banner from assets/icon-1024.png and a
bundled wallpaper. Needs Pillow. Run once after changing the logo; the PNGs it
writes are committed, so building the app doesn't need Pillow."""
import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, '..', '..', '..'))
RES = os.path.join(HERE, '..', 'res')

def font(size):
    for path in ('/usr/share/fonts/opentype/inter/Inter-SemiBold.otf',
                 '/System/Library/Fonts/SFNS.ttf',
                 '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()

def main():
    icon = Image.open(os.path.join(REPO, 'assets', 'icon-1024.png')).convert('RGBA')
    os.makedirs(os.path.join(RES, 'mipmap-xhdpi'), exist_ok=True)
    os.makedirs(os.path.join(RES, 'drawable-xhdpi'), exist_ok=True)
    icon.resize((96, 96), Image.LANCZOS).save(os.path.join(RES, 'mipmap-xhdpi', 'ic_launcher.png'), optimize=True)

    # Banner: 320 x 180 dp at xhdpi = 640 x 360 px.
    w, h = 640, 360
    photo = Image.open(os.path.join(REPO, 'app', 'wallpapers', 'nature-06.jpg')).convert('RGB')
    scale = max(w / photo.width, h / photo.height)
    photo = photo.resize((round(photo.width * scale), round(photo.height * scale)), Image.LANCZOS)
    left, top = (photo.width - w) // 2, (photo.height - h) // 2
    banner = photo.crop((left, top, left + w, top + h)).filter(ImageFilter.GaussianBlur(10))
    banner = Image.blend(banner, Image.new('RGB', (w, h), (10, 18, 24)), 0.45).convert('RGBA')
    glyph = icon.resize((150, 150), Image.LANCZOS)
    mask = Image.new('L', glyph.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, 149, 149), radius=34, fill=255)
    glyph.putalpha(mask)
    banner.alpha_composite(glyph, (44, (h - 150) // 2))
    d = ImageDraw.Draw(banner)
    f = font(62)
    d.text((216, h // 2), 'Glass\nHome', font=f, fill=(255, 255, 255), anchor='lm', spacing=4)
    banner.convert('RGB').save(os.path.join(RES, 'drawable-xhdpi', 'banner.png'), optimize=True)

if __name__ == '__main__':
    sys.exit(main())
