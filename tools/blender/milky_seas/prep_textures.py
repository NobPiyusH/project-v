"""Make working-size textures from the NASA downloads (run: blender -b -P prep_textures.py)."""
import bpy, os
T = r"E:\project-data\blender\milky-seas\textures"
def resize(src, dst, w, h, fmt="JPEG"):
    img = bpy.data.images.load(os.path.join(T, src))
    print(src, img.size[:])
    img.scale(w, h)
    img.filepath_raw = os.path.join(T, dst)
    img.file_format = fmt
    img.save()
    bpy.data.images.remove(img)
resize("dnb_land_ocean_ice_54000.tif", "earth_base_21600.jpg", 21600, 10800)
resize("earth_base_21600.jpg", "earth_base_4096.jpg", 4096, 2048)
resize("blackmarble_2016_3km_gray.jpg", "lights_4096.jpg", 4096, 2048)
