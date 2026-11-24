"""Untextured blockout of the ruined-avenue keyframe + a smooth 5 s push-in camera, for use as a Wan 2.2 Fun VACE control video.

Run:  E:/Blender/blender.exe -b -P tools/blender/vace_blockout.py -- <out_dir> [width height frames [still_frame]]
Writes <out_dir>/f_0001.png ... (Workbench, flat studio light, one colour per object so ffmpeg edgedetect finds every silhouette)
(or only frame `still_frame` as still.png). Then: python -m tools.film4k.control_from_render <out_dir> <control.mp4>.
Camera: ~walking-pace push-in down the road (y -10 -> 0) at eye height 1.6 m, slight sway and rise for parallax. Deterministic.
"""

import math
import random
import sys

import bpy
from mathutils import Vector

a = sys.argv[sys.argv.index("--") + 1 :]
out = a[0]
W, H, N = (int(a[1]), int(a[2]), int(a[3])) if len(a) >= 4 else (720, 1280, 81)
still = int(a[4]) if len(a) >= 5 else 0
FPS = 16
random.seed(11)

bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene
sc.frame_start, sc.frame_end = 1, N
sc.render.fps = FPS
sc.render.engine = "BLENDER_WORKBENCH"
sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = W, H, 100
sc.render.image_settings.file_format = "PNG"
sd = sc.display.shading
sd.light, sd.color_type, sd.show_object_outline, sd.show_cavity = "STUDIO", "OBJECT", True, False
sc.display.render_aa = "8"
sc.view_settings.view_transform = "Standard"
bpy.context.scene.world = bpy.data.worlds.new("w")
bpy.context.scene.world.color = (0.9, 0.9, 0.9)


def hue(i):
    import colorsys

    return (*colorsys.hsv_to_rgb((i * 0.381966) % 1.0, 0.45, 0.85), 1.0)


_n = [0]


def add(kind, loc, scale, rot=(0, 0, 0), **kw):
    if kind == "cube":
        bpy.ops.mesh.primitive_cube_add(location=loc)
    elif kind == "sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(location=loc, segments=24, ring_count=12)
    elif kind == "cone":
        bpy.ops.mesh.primitive_cone_add(location=loc, vertices=4, radius1=kw.get("r", 1.0), depth=2.0)
    elif kind == "cyl":
        bpy.ops.mesh.primitive_cylinder_add(location=loc, vertices=12)
    elif kind == "plane":
        bpy.ops.mesh.primitive_plane_add(location=loc)
    o = bpy.context.active_object
    o.scale = scale
    o.rotation_euler = [math.radians(r) for r in rot]
    _n[0] += 1
    o.color = hue(_n[0])
    return o


# ground (road) with a darker verge, centre line as thin box
add("plane", (0, 40, 0), (60, 80, 1))
add("cube", (0, 40, 0.01), (0.12, 80, 0.01))
# near tower, left (leans a little), far tower right; window bands as thin boxes give inner edges
add("cube", (-14, 14, 15), (5.5, 4.5, 15), rot=(0, 3.5, 0))
add("cube", (13, 24, 14), (4.5, 6, 14), rot=(0, -1.5, 0))
for z in range(3, 28, 4):
    add("cube", (-8.4, 14, z), (0.15, 3.6, 0.9), rot=(0, 3.5, 0))
    add("cube", (8.4, 24, z - 1), (0.15, 4.8, 0.9))
# mid-distance ruins and far skyline
for x, y, h in [(-8, 34, 8), (8, 30, 10), (-5, 52, 14), (6, 56, 12), (-14, 62, 18), (13, 66, 16), (0, 90, 20)]:
    add("cube", (x, y, h / 2), (3 + random.random() * 2, 3 + random.random() * 2, h / 2), rot=(0, 0, random.uniform(-6, 6)))
# lamp poles
for y in (6, 20, 36, 50):
    add("cyl", (-4.5, y, 3.5), (0.12, 0.12, 3.5))
    add("cyl", (4.6, y + 3, 3.5), (0.12, 0.12, 3.5))
# rubble piles at the verges
for side in (-1, 1):
    for _ in range(14):
        add("cube", (side * random.uniform(3.2, 8), random.uniform(10, 40), 0.35), tuple(random.uniform(0.3, 1.0) for _ in range(3)),
            rot=tuple(random.uniform(-30, 30) for _ in range(3)))  # fmt: skip
# dragon-sized blob: body, neck, head, two wings (tilted slabs), tail; ~30 m span, 36 m away, hovering above the road
DY = 36
add("sphere", (0, DY, 8), (2.8, 2.4, 5.5))
add("cyl", (0, DY - 0.5, 15), (0.9, 0.9, 3.5), rot=(-10, 0, 0))
add("sphere", (0, DY - 1.4, 19.5), (1.1, 1.9, 1.0))
add("cube", (-8, DY, 13.5), (7.5, 0.3, 4.0), rot=(0, -22, 0))
add("cube", (8, DY, 13.5), (7.5, 0.3, 4.0), rot=(0, 22, 0))
add("cube", (-12.5, DY, 17), (3.0, 0.3, 2.5), rot=(0, -45, 0))
add("cube", (12.5, DY, 17), (3.0, 0.3, 2.5), rot=(0, 45, 0))
add("cyl", (0.5, DY + 3, 3.5), (0.7, 0.7, 5), rot=(55, 0, 0))  # tail down toward the road
# camera
cam_d = bpy.data.cameras.new("cam")
cam_d.lens, cam_d.sensor_width, cam_d.sensor_fit = 22.0, 24.0, "HORIZONTAL"
cam = bpy.data.objects.new("cam", cam_d)
sc.collection.objects.link(cam)
sc.camera = cam
for f in range(1, N + 1):
    t = (f - 1) / (N - 1)
    s = t * t * (3 - 2 * t) * 0.35 + t * 0.65  # gentle ease: mostly steady speed, soft start and stop
    cam.location = Vector((0.5 * math.sin(t * math.pi * 1.2) - 0.4, -10 + 10.0 * s, 1.6 + 0.25 * t))
    pitch = 90 + 4.0 + 1.5 * math.sin(t * math.pi)
    cam.rotation_euler = (math.radians(pitch), 0, math.radians(1.2 * math.sin(t * math.pi * 1.2)))
    cam.keyframe_insert("location", frame=f)
    cam.keyframe_insert("rotation_euler", frame=f)
if still:
    sc.frame_set(still)
    sc.render.filepath = out.rstrip("/\\") + "/still.png"
    bpy.ops.render.render(write_still=True)
else:
    sc.render.filepath = out.rstrip("/\\") + "/f_"
    bpy.ops.render.render(animation=True)
