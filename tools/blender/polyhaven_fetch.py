"""Fetch CC0 Poly Haven assets (1k) into a set library folder and print licence rows.
Usage: python tools/blender/polyhaven_fetch.py <dest_dir> tex:church_bricks_02 hdri:moonless_golf ...
Only polyhaven.com (CC0) is allowed here. Rerunnable: existing files are skipped."""
import sys, os, json, urllib.request
API = "https://api.polyhaven.com"
def get(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "studio-asset-fetch"})) as r:
        return r.read()
def save(url, path):
    if os.path.exists(path): return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(get(url))
def main():
    dest = sys.argv[1]
    for spec in sys.argv[2:]:
        kind, aid = spec.split(":")
        files = json.loads(get(f"{API}/files/{aid}")); info = json.loads(get(f"{API}/info/{aid}"))
        if kind == "tex":
            for m, keys in (("diff", ("Diffuse", "diff_png")), ("nor_gl", ("nor_gl",)), ("rough", ("Rough",))):
                key = next(k for k in keys if k in files)
                save(files[key]["1k"]["jpg"]["url"], os.path.join(dest, "textures", aid, f"{m}.jpg"))
        else:
            save(files["hdri"]["1k"]["hdr"]["url"], os.path.join(dest, "hdri", f"{aid}.hdr"))
        print(f"| {info['name']} ({aid}) | https://polyhaven.com/a/{aid} | CC0 | {', '.join(info['authors'])} |")
main()
