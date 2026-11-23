"""Shared helpers for the Milky Seas scenes. Runs inside Blender 5.3 (bpy). Textures live in ../textures next to scenes/."""
import math, os, sys
import bpy
from mathutils import Vector

ROOT = os.environ.get("MS_ROOT", r"E:\project-data\blender\milky-seas")
TEX = os.path.join(ROOT, "textures")
W4K, H4K = 2160, 3840


def argv():
    a = sys.argv
    return a[a.index("--") + 1:] if "--" in a else []


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    s = bpy.context.scene
    s.render.resolution_x, s.render.resolution_y, s.render.resolution_percentage = W4K, H4K, 100
    s.render.fps = 24
    s.frame_start, s.frame_end = 1, 144
    s.render.film_transparent = False
    s.render.image_settings.file_format = "PNG"
    s.render.image_settings.color_depth = "16"
    s.view_settings.view_transform = "AgX"
    s.view_settings.look = "None"
    s.cycles.device = "GPU"
    s.cycles.use_denoising = True
    s.cycles.denoiser = "OPENIMAGEDENOISE"      # render_job.py switches to OPTIX on the pod
    s.cycles.samples = 128
    s.cycles.adaptive_threshold = 0.02
    s.cycles.use_adaptive_sampling = True
    s.cycles.max_bounces = 6
    s.cycles.volume_bounces = 2
    return s


def tex(name):
    """Return a path relative to the .blend (which sits in scenes/), so the project moves to the pod unchanged."""
    return "//../textures/" + name


def load_image(name, colorspace="sRGB"):
    p = os.path.join(TEX, name)
    img = bpy.data.images.load(p, check_existing=True)
    img.colorspace_settings.name = colorspace
    img.filepath = tex(name)
    return img


def mat(name):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    m.node_tree.nodes.clear()
    return m


def node(nt, kind, loc=(0, 0), **kw):
    n = nt.nodes.new(kind)
    n.location = loc
    for k, v in kw.items():
        setattr(n, k, v)
    return n


def link(nt, a, ao, b, bi):
    nt.links.new(a.outputs[ao], b.inputs[bi])


def set_in(n, name, val):
    n.inputs[name].default_value = val


def lonlat_dir(lon_deg, lat_deg):
    """Direction for Blender's equirect Environment Texture (lon 0 at -X, east positive)."""
    lo, la = math.radians(lon_deg), math.radians(lat_deg)
    return Vector((-math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)))


def look_at(obj, target):
    d = Vector(target) - obj.location
    obj.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()


def look_at_up(obj, target, up):
    """Aim -Z at target keeping 'up' as the camera's up (needed on a sphere: world Z is not 'up' there)."""
    f = (Vector(target) - obj.location).normalized()
    u = (Vector(up) - Vector(up).dot(f) * f).normalized()
    r = f.cross(u)
    from mathutils import Matrix
    m = Matrix(((r.x, u.x, -f.x), (r.y, u.y, -f.y), (r.z, u.z, -f.z)))
    obj.rotation_euler = m.to_euler()


def camera(name, loc, target, lens=35, sensor=24, fstop=None, focus=None):
    cd = bpy.data.cameras.new(name)
    cd.lens, cd.sensor_width = lens, sensor
    cd.sensor_fit = "HORIZONTAL"
    cd.clip_start, cd.clip_end = 0.01, 1e5
    if fstop:
        cd.dof.use_dof = True
        cd.dof.aperture_fstop = fstop
        cd.dof.focus_distance = focus
    o = bpy.data.objects.new(name, cd)
    bpy.context.scene.collection.objects.link(o)
    o.location = loc
    look_at(o, target)
    bpy.context.scene.camera = o
    return o


def world_exr(name="milkyway_2020_8k.exr", strength=1.0, rot=(0, 0, 0), tint=(1, 1, 1)):
    w = bpy.data.worlds.new("World")
    bpy.context.scene.world = w
    w.use_nodes = True
    nt = w.node_tree
    nt.nodes.clear()
    tc = node(nt, "ShaderNodeTexCoord", (-900, 0))
    mp = node(nt, "ShaderNodeMapping", (-700, 0))
    mp.inputs["Rotation"].default_value = rot
    env = node(nt, "ShaderNodeTexEnvironment", (-450, 0))
    env.image = bpy.data.images.load(os.path.join(TEX, name), check_existing=True)
    env.image.filepath = tex(name)
    env.image.colorspace_settings.name = "Linear Rec.709"
    mx = node(nt, "ShaderNodeMix", (-250, 0), data_type="RGBA", blend_type="MULTIPLY")
    mx.inputs[0].default_value = 1.0
    mx.inputs[7].default_value = (*tint, 1)
    bg = node(nt, "ShaderNodeBackground", (-50, 0))
    bg.inputs["Strength"].default_value = strength
    out = node(nt, "ShaderNodeOutputWorld", (150, 0))
    link(nt, tc, "Generated", mp, "Vector")
    link(nt, mp, 0, env, "Vector")
    link(nt, env, "Color", mx, 6)
    link(nt, mx, 2, bg, "Color")
    link(nt, bg, 0, out, "Surface")
    return w


def save(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=path, relative_remap=False, compress=True)
    print("SAVED", path)
