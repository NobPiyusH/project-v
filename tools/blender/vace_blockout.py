"""Untextured blockout of the ruined-avenue keyframe + a smooth 5 s push-in camera, for use as a Wan 2.2 Fun VACE control video.

Run:  E:/Blender/blender.exe -b -P tools/blender/vace_blockout.py -- <out_dir> [width height frames [still_frame]]
Writes <out_dir>/f_0001.png ... (Workbench, flat studio light, one colour per object so ffmpeg edgedetect finds every silhouette)
(or only frame `still_frame` as still.png). Then: python -m tools.film4k.control_from_render <out_dir> <control.mp4>.
Camera: ~walking-pace push-in down the road (y 0 -> 12) at 1.3 m (BLOCKOUT_MODE=depth: bright=near depth pass), slight sway and rise for parallax. Deterministic.
"""

import math
import os
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


# Layout v2 (25 Nov 2026): measured from the keyframe (camera at 1.3 m, 28 mm, horizon at 55 % of frame height).
add("plane", (0, 40, 0), (60, 80, 1))
add("cube", (0, 40, 0.01), (0.12, 80, 0.01))
# near tower left: right edge at x=-3.5, leaning outward; far tower right: left edge at x=7.4
add("cube", (-9.5, 31, 13), (6.0, 5, 13), rot=(0, 3.0, 0))
add("cube", (13.5, 35, 12), (6.2, 5, 12), rot=(0, -1.5, 0))
for z in range(3, 24, 4):
    add("cube", (-3.6, 26, z), (0.15, 3.6, 0.9), rot=(0, 3.0, 0))
    add("cube", (7.3, 30, z - 1), (0.15, 4.0, 0.9))
# mid-distance ruins and far skyline (low, behind the dragon's legs)
for x, y, h in [(-9, 48, 7), (9, 46, 9), (-6, 62, 11), (6, 66, 10), (-14, 74, 14), (13, 78, 13), (0, 100, 16)]:
    add("cube", (x, y, h / 2), (3 + random.random() * 2, 3 + random.random() * 2, h / 2), rot=(0, 0, random.uniform(-6, 6)))
for y in (14, 28, 42, 56):
    add("cyl", (-5.0, y, 3.5), (0.12, 0.12, 3.5))
    add("cyl", (5.2, y + 3, 3.5), (0.12, 0.12, 3.5))
# rubble piles at the verges (fire stays for the generator to paint)
for side in (-1, 1):
    for _ in range(14):
        add("cube", (side * random.uniform(3.4, 9), random.uniform(14, 44), 0.4), tuple(random.uniform(0.3, 1.1) for _ in range(3)),
            rot=tuple(random.uniform(-30, 30) for _ in range(3)))  # fmt: skip
# dragon: ~20 m span, ~20 m tall, 45 m away, wings up, legs on the road
DY = 45
add("sphere", (0.5, DY, 11), (2.2, 2.0, 5.0))
add("cyl", (0.2, DY - 0.3, 17), (0.8, 0.8, 3.0), rot=(-8, 0, 0))
add("sphere", (-0.4, DY - 1.2, 20.5), (0.9, 1.5, 0.8))
add("cube", (-9.5, DY, 15), (6.5, 0.25, 4.2), rot=(0, -26, 0))
add("cube", (10.5, DY, 15), (6.5, 0.25, 4.2), rot=(0, 26, 0))
add("cube", (-14, DY, 19), (3.0, 0.25, 2.0), rot=(0, -50, 0))
add("cube", (15, DY, 19), (3.0, 0.25, 2.0), rot=(0, 50, 0))
add("cyl", (-1.0, DY, 4), (0.8, 0.8, 4.0))
add("cyl", (2.2, DY, 4), (0.8, 0.8, 4.0))
add("cyl", (1.0, DY + 3, 4), (0.6, 0.6, 5), rot=(55, 0, 0))
if os.environ.get("BLOCKOUT_MODE") == "depth":  # bright = near, black = far/sky; one emission material on everything
    sc.render.engine = "BLENDER_EEVEE"
    sc.view_settings.view_transform, sc.view_settings.look = "Standard", "None"
    sc.world.color = (0, 0, 0)
    m = bpy.data.materials.new("depth")
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    cd = nt.nodes.new("ShaderNodeCameraData")
    mr = nt.nodes.new("ShaderNodeMapRange")
    mr.inputs["From Min"].default_value, mr.inputs["From Max"].default_value = 8.0, 70.0
    mr.inputs["To Min"].default_value, mr.inputs["To Max"].default_value = 1.0, 0.0
    em = nt.nodes.new("ShaderNodeEmission")
    outn = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(cd.outputs["View Z Depth"], mr.inputs["Value"])
    nt.links.new(mr.outputs["Result"], em.inputs["Color"])
    nt.links.new(em.outputs["Emission"], outn.inputs["Surface"])
    bpy.context.view_layer.material_override = m
# camera
cam_d = bpy.data.cameras.new("cam")
cam_d.lens, cam_d.sensor_width, cam_d.sensor_fit = 28.0, 24.0, "HORIZONTAL"
cam = bpy.data.objects.new("cam", cam_d)
sc.collection.objects.link(cam)
sc.camera = cam
for f in range(1, N + 1):
    t = (f - 1) / (N - 1)
    s = t * t * (3 - 2 * t) * 0.35 + t * 0.65  # gentle ease: mostly steady speed, soft start and stop
    cam.location = Vector((0.5 * math.sin(t * math.pi * 1.2), 0 + 12.0 * s, 1.3 + 0.25 * t))
    pitch = 90 + 3.5 + 1.0 * math.sin(t * math.pi)
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
