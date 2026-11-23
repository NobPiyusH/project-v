"""Download and prepare the NASA day-side Earth and LRO Moon maps for the science zoom (plain python + Pillow).

  python tools/blender/science_zoom_prep_textures.py E:/project-data/blender/library/science-zoom/textures

Sources (NASA SVS, public domain imagery; credit NASA, no NASA logo):
  Earth day map with clouds: https://svs.gsfc.nasa.gov/3615  (flat_earth03.jpg, 2048x1024)
  Moon colour map (LRO LROC): https://svs.gsfc.nasa.gov/4720 (lroc_color_poles_4k.tif)
The Earth map has a grey placeholder band at the top (no data over the Arctic Ocean): it is blended to pack ice here.
"""
import sys, os, urllib.request
from PIL import Image
Image.MAX_IMAGE_PIXELS = None
out = sys.argv[1]
os.makedirs(out, exist_ok=True)
def get(url, name):
    p = os.path.join(out, name)
    if not os.path.exists(p):
        urllib.request.urlretrieve(url, p)
    return p
e = Image.open(get('https://svs.gsfc.nasa.gov/vis/a000000/a003600/a003615/flat_earth03.jpg', 'flat_earth03.jpg')).convert('RGB')
e = e.resize((8192, 4096), Image.BICUBIC)
h = e.size[1]
band = int(h * 0.105)
ramp = Image.linear_gradient('L').rotate(180).resize((e.size[0], int(band * 1.45)))   # 255 at the top -> 0
mask = Image.new('L', e.size, 0)
mask.paste(ramp, (0, 0))
e = Image.composite(Image.new('RGB', e.size, (237, 242, 252)), e, mask)
e.save(os.path.join(out, 'earth_day_8192.jpg'), quality=94)
m = Image.open(get('https://svs.gsfc.nasa.gov/vis/a000000/a004700/a004720/lroc_color_poles_4k.tif', 'lroc_color_poles_4k.tif')).convert('RGB')
m.save(os.path.join(out, 'moon_color_4096.jpg'), quality=94)
print('ok')
