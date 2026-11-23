"""Shot A: low-orbit pass over the night side of Earth, milky-sea patch south of Java.
blender -b -P build_a_earth.py -- [--preview]   -> E:/project-data/blender/milky-seas/scenes/a_earth.blend
Units: 1 BU = 100 km. Earth radius 63.71. Camera altitude ~400 km."""
import math, os, sys
sys.path.insert(0, os.path.dirname(__file__))
import bpy
from mathutils import Vector, Matrix
from common import *

PREVIEW = "--preview" in argv()
R = 63.71
ALT = 4.0
PATCH_LON, PATCH_LAT, PATCH_R_KM = 108.0, -10.5, 178.0     # ~100,000 km2 (2019 patch south of Java; verify before quoting)
SWEEP_DEG = 6.0
BACK_DEG = 8.5          # start this many degrees of orbit before the patch
PITCH_DOWN = 19.0       # camera pitch below local horizontal (deg)

s = reset()
world_exr(strength=14.0, rot=(math.radians(60), 0, math.radians(20)), tint=(0.9, 0.95, 1.05))

# --- Earth ---------------------------------------------------------------------------
bpy.ops.mesh.primitive_uv_sphere_add(segments=192 if PREVIEW else 720, ring_count=96 if PREVIEW else 360, radius=R)
earth = bpy.context.active_object
earth.name = "Earth"
bpy.ops.object.shade_smooth()
base_img = "earth_base_4096.jpg" if PREVIEW else "earth_base_21600.jpg"
m = mat("EarthNight"); nt = m.node_tree
tc = node(nt, "ShaderNodeTexCoord", (-1400, 0))
base = node(nt, "ShaderNodeTexEnvironment", (-1100, 200)); base.image = load_image(base_img)
lights = node(nt, "ShaderNodeTexEnvironment", (-1100, -150)); lights.image = load_image("lights_4096.jpg", "Non-Color")
link(nt, tc, "Object", base, "Vector"); link(nt, tc, "Object", lights, "Vector")
# city lights: warm sodium; fine noise breaks them up so cities twinkle at 4K instead of looking like a blurred jpeg
nz = node(nt, "ShaderNodeTexNoise", (-1100, -450), noise_dimensions="3D")
nz.inputs["Scale"].default_value = 900; nz.inputs["Detail"].default_value = 4
link(nt, tc, "Object", nz, "Vector")
cr = node(nt, "ShaderNodeValToRGB", (-850, -450)); cr.color_ramp.elements[0].position = 0.25; cr.color_ramp.elements[1].position = 0.75
link(nt, nz, "Fac", cr, "Fac")
lmul = node(nt, "ShaderNodeVectorMath", (-650, -250), operation="MULTIPLY"); link(nt, lights, "Color", lmul, 0); link(nt, cr, "Color", lmul, 1)
lpow = node(nt, "ShaderNodeVectorMath", (-450, -250), operation="POWER"); link(nt, lmul, 0, lpow, 0); lpow.inputs[1].default_value = (1.15, 1.15, 1.15)
lstr = node(nt, "ShaderNodeVectorMath", (-250, -250), operation="MULTIPLY"); link(nt, lpow, 0, lstr, 0); lstr.inputs[1].default_value = (22.0, 13.6, 6.2)  # warm tint x strength
# milky-sea patch: angular distance to the patch centre, noisy edge, pale blue-white glow
P = lonlat_dir(PATCH_LON, PATCH_LAT)
vn = node(nt, "ShaderNodeVectorMath", (-1100, -700), operation="NORMALIZE"); link(nt, tc, "Object", vn, 0)
dt = node(nt, "ShaderNodeVectorMath", (-900, -700), operation="DOT_PRODUCT"); link(nt, vn, 0, dt, 0); dt.inputs[1].default_value = P
ac = node(nt, "ShaderNodeMath", (-700, -700), operation="ARCCOSINE"); link(nt, dt, "Value", ac, 0)
sc = node(nt, "ShaderNodeMath", (-500, -700), operation="DIVIDE"); link(nt, ac, 0, sc, 0); sc.inputs[1].default_value = PATCH_R_KM / 6371.0
pn = node(nt, "ShaderNodeTexNoise", (-900, -950), noise_dimensions="3D")
pn.inputs["Scale"].default_value = 70; pn.inputs["Detail"].default_value = 8; pn.inputs["Roughness"].default_value = 0.6
link(nt, tc, "Object", pn, "Vector")
pm = node(nt, "ShaderNodeMath", (-500, -950), operation="MULTIPLY"); link(nt, pn, "Fac", pm, 0); pm.inputs[1].default_value = 0.55
pw = node(nt, "ShaderNodeMath", (-300, -800), operation="ADD"); link(nt, sc, 0, pw, 0); link(nt, pm, 0, pw, 1)
pr = node(nt, "ShaderNodeValToRGB", (-100, -800)); pr.color_ramp.elements[0].position = 0.35; pr.color_ramp.elements[1].position = 1.1
pr.color_ramp.elements[0].color = (1, 1, 1, 1); pr.color_ramp.elements[1].color = (0, 0, 0, 1)
link(nt, pw, 0, pr, "Fac")
ps = node(nt, "ShaderNodeVectorMath", (150, -800), operation="MULTIPLY"); link(nt, pr, "Color", ps, 0)
ps.inputs[1].default_value = (0.09, 0.19, 0.31)      # pale blue-white, kept faint as in life
em_sum = node(nt, "ShaderNodeVectorMath", (400, -500), operation="ADD"); link(nt, lstr, 0, em_sum, 0); link(nt, ps, 0, em_sum, 1)
# base surface: dark ocean/land under faint earthshine (the map is a day colour map; kept ~2 % bright)
dim = node(nt, "ShaderNodeVectorMath", (-700, 200), operation="MULTIPLY"); link(nt, base, "Color", dim, 0); dim.inputs[1].default_value = (0.008, 0.010, 0.014)
bs = node(nt, "ShaderNodeBsdfPrincipled", (700, 100))
link(nt, dim, 0, bs, "Base Color"); bs.inputs["Roughness"].default_value = 0.55
link(nt, em_sum, 0, bs, "Emission Color"); bs.inputs["Emission Strength"].default_value = 1.0
out = node(nt, "ShaderNodeOutputMaterial", (1000, 100)); link(nt, bs, 0, out, "Surface")
earth.data.materials.append(m)

# --- thin moonlit cloud deck -------------------------------------------------------------
bpy.ops.mesh.primitive_uv_sphere_add(segments=128 if PREVIEW else 480, ring_count=64 if PREVIEW else 240, radius=R * 1.0016)
cl = bpy.context.active_object; cl.name = "Clouds"; bpy.ops.object.shade_smooth()
m = mat("Clouds"); nt = m.node_tree
tc = node(nt, "ShaderNodeTexCoord", (-900, 0))
n1 = node(nt, "ShaderNodeTexNoise", (-650, 100), noise_dimensions="3D")
n1.inputs["Scale"].default_value = 5.5; n1.inputs["Detail"].default_value = 14; n1.inputs["Roughness"].default_value = 0.62; n1.inputs["Distortion"].default_value = 0.6
link(nt, tc, "Object", n1, "Vector")
cr = node(nt, "ShaderNodeValToRGB", (-400, 100)); cr.color_ramp.elements[0].position = 0.56; cr.color_ramp.elements[1].position = 0.82
link(nt, n1, "Fac", cr, "Fac")
tr = node(nt, "ShaderNodeBsdfTransparent", (-100, 250))
df = node(nt, "ShaderNodeBsdfPrincipled", (-100, -50)); df.inputs["Base Color"].default_value = (0.07, 0.08, 0.1, 1); df.inputs["Roughness"].default_value = 0.9
mx = node(nt, "ShaderNodeMixShader", (200, 100)); link(nt, cr, "Color", mx, 0); link(nt, tr, 0, mx, 1); link(nt, df, 0, mx, 2)
out = node(nt, "ShaderNodeOutputMaterial", (450, 100)); link(nt, mx, 0, out, "Surface")
cl.data.materials.append(m)


# --- atmosphere: two thin glowing shells (limb brightening seen from orbit) -----------------
def shell(name, rad, color, power, gain):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=128 if PREVIEW else 360, ring_count=64 if PREVIEW else 180, radius=rad)
    o = bpy.context.active_object; o.name = name; bpy.ops.object.shade_smooth()
    m = mat(name); m.use_backface_culling = True; nt = m.node_tree
    lw = node(nt, "ShaderNodeLayerWeight", (-700, 0)); lw.inputs["Blend"].default_value = 0.5
    pw = node(nt, "ShaderNodeMath", (-300, 0), operation="POWER"); link(nt, lw, "Facing", pw, 0); pw.inputs[1].default_value = power   # Facing is 0 face-on, 1 at the limb
    em = node(nt, "ShaderNodeEmission", (-100, 100)); em.inputs["Color"].default_value = color; link(nt, pw, 0, em, "Strength")
    g = node(nt, "ShaderNodeMath", (-100, -100), operation="MULTIPLY", use_clamp=True); link(nt, pw, 0, g, 0); g.inputs[1].default_value = gain
    tr = node(nt, "ShaderNodeBsdfTransparent", (-100, 250))
    mx = node(nt, "ShaderNodeMixShader", (300, 100)); link(nt, g, 0, mx, 0); link(nt, tr, 0, mx, 1); link(nt, em, 0, mx, 2)
    out = node(nt, "ShaderNodeOutputMaterial", (500, 100)); link(nt, mx, 0, out, "Surface")
    o.data.materials.append(m)
    o.visible_shadow = False


shell("AtmoInner", R * 1.0075, (0.10, 0.34, 1.0, 1), 5.0, 1.0)
shell("AtmoOuter", R * 1.022, (0.05, 0.16, 0.7, 1), 9.0, 0.45)

# faint moonlight so the clouds and the sea have some form
bpy.ops.object.light_add(type="SUN", location=(0, 0, 0)); sun = bpy.context.active_object; sun.name = "Moonlight"
sun.data.energy = 0.35; sun.data.color = (0.75, 0.85, 1.0); sun.data.angle = math.radians(1.0)
sun.rotation_euler = (math.radians(70), 0, math.radians(30))

# --- camera on an orbit pivot ---------------------------------------------------------------
Pn = lonlat_dir(PATCH_LON, PATCH_LAT)
east = Vector((0, 0, 1)).cross(Pn).normalized()          # along-track direction (eastward pass)
axis = Pn.cross(east).normalized()                        # rotating about this moves a point along 'east'
rot0 = Matrix.Rotation(-math.radians(BACK_DEG), 3, axis)
u0 = (rot0 @ Pn).normalized(); fwd = (rot0 @ east).normalized()
pivot = bpy.data.objects.new("OrbitPivot", None); s.collection.objects.link(pivot)
cam_pos = u0 * (R + ALT)
pt = math.radians(PITCH_DOWN)
aim = cam_pos + (fwd * math.cos(pt) - u0 * math.sin(pt)) * 100.0
cam = camera("Cam", cam_pos, aim, lens=24)
look_at_up(cam, aim, u0)
cam.parent = pivot
pivot.rotation_mode = "AXIS_ANGLE"
pivot.rotation_axis_angle = (0.0, axis.x, axis.y, axis.z)
pivot.keyframe_insert("rotation_axis_angle", frame=1)
pivot.rotation_axis_angle = (math.radians(SWEEP_DEG), axis.x, axis.y, axis.z)
pivot.keyframe_insert("rotation_axis_angle", frame=s.frame_end)

save(os.path.join(ROOT, "scenes", "a_earth_preview.blend" if PREVIEW else "a_earth.blend"))
