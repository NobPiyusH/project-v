"""Zip the cathedral set into one self-contained, relocatable bundle for a render pod.
Usage: python tools/blender/cathedral_bundle.py [library_dir] [out_zip]
Contents (top folder cathedral/): cathedral.blend (relative texture paths), textures/, hdri/, LICENSES.md, cathedral_set.py, route.json.
On the pod:  unzip cathedral_bundle.zip && blender -b cathedral/cathedral.blend -P cathedral/cathedral_set.py -- render \
               --blend cathedral/cathedral.blend --outdir out --frames 1-30 --res 2160x3840 --samples 48
Run `build` first (rebuilds the .blend with relative paths)."""
import os, sys, zipfile
lib = sys.argv[1] if len(sys.argv) > 1 else r"E:\project-data\blender\library\cathedral"
out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(lib, "cathedral_bundle.zip")
here = os.path.dirname(os.path.abspath(__file__))
with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:           # jpg/hdr are already compressed
    z.write(os.path.join(lib, "cathedral.blend"), "cathedral/cathedral.blend")
    for sub in ("textures", "hdri"):
        for root, _, files in os.walk(os.path.join(lib, sub)):
            for f in files:
                p = os.path.join(root, f)
                z.write(p, "cathedral/" + os.path.relpath(p, lib).replace(os.sep, "/"))
    z.write(os.path.join(lib, "LICENSES.md"), "cathedral/LICENSES.md")
    z.write(os.path.join(here, "cathedral_set.py"), "cathedral/cathedral_set.py")
    z.write(os.path.join(here, "routes", "cathedral_plaza_to_nave.json"), "cathedral/route.json")
print(out, round(os.path.getsize(out) / 1e6, 1), "MB")
