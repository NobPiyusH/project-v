"""Shot B: sea level, ship's bow at night, vast flat softly glowing pale sea under a dense Milky Way.
blender -b -P build_b_ship.py -- [--preview]   -> scenes/b_ship.blend
Units: metres. Bow tip at y=0, ship points +Y and moves 3 m/s over the Ocean-modifier sea."""
import math, os, sys, random
sys.path.insert(0, os.path.dirname(__file__))
import bpy, bmesh
from mathutils import Vector
from common import *

PREVIEW = "--preview" in argv()
s = reset()
s.frame_end = 144
SPEED = 3.0   # m/s, ship + camera move; the sea is a fixed Ocean-modifier field
world_exr(strength=30.0, rot=(math.radians(60), math.radians(0), math.radians(20)), tint=(0.85, 0.95, 1.1))

# --- the sea -----------------------------------------------------------------------
bpy.ops.mesh.primitive_plane_add(size=2, location=(0, 120, 0))
sea = bpy.context.active_object; sea.name = "Sea"
om = sea.modifiers.new("Ocean", "OCEAN")
om.geometry_mode = "GENERATE"
om.resolution = 10 if PREVIEW else 32      # grid is 16*res+1 points per tile axis
om.viewport_resolution = 6
om.spatial_size = 220
om.repeat_x = om.repeat_y = 3
om.wave_scale = 0.45            # light wind, flat sea (milky seas form in calm weather)
om.wave_scale_min = 0.02
om.choppiness = 0.9
om.wind_velocity = 4.5
om.wave_alignment = 0.4
om.damping = 0.5
om.use_normals = True
om.use_foam = True
om.foam_coverage = 0.05
om.foam_layer_name = "foam"
om.random_seed = 7
om.time = 0.0
om.keyframe_insert("time", frame=1)
om.time = (s.frame_end - 1) / 24.0 * 0.8
om.keyframe_insert("time", frame=s.frame_end)
bpy.ops.object.shade_smooth()

m = mat("MilkySea"); nt = m.node_tree
tc = node(nt, "ShaderNodeTexCoord", (-1200, 0))
at = node(nt, "ShaderNodeAttribute", (-1200, -250), attribute_name="foam")
nz = node(nt, "ShaderNodeTexNoise", (-1000, 200), noise_dimensions="3D")
nz.inputs["Scale"].default_value = 0.03; nz.inputs["Detail"].default_value = 6; nz.inputs["Roughness"].default_value = 0.55
link(nt, tc, "Object", nz, "Vector")
rp = node(nt, "ShaderNodeValToRGB", (-780, 200)); rp.color_ramp.elements[0].position = 0.3; rp.color_ramp.elements[1].position = 0.75
rp.color_ramp.elements[0].color = (0.75, 0.75, 0.75, 1); rp.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1)
link(nt, nz, "Fac", rp, "Fac")
glow = node(nt, "ShaderNodeVectorMath", (-500, 100), operation="MULTIPLY"); link(nt, rp, "Color", glow, 0)
glow.inputs[1].default_value = (0.22, 0.52, 0.62)           # pale blue-white, a uniform soft glow
fo = node(nt, "ShaderNodeMath", (-780, -250), operation="MULTIPLY", use_clamp=True); link(nt, at, "Fac", fo, 0); fo.inputs[1].default_value = 2.0
fcol = node(nt, "ShaderNodeVectorMath", (-500, -250), operation="MULTIPLY"); fcol.inputs[0].default_value = (1, 1, 1); fcol.inputs[1].default_value = (0.9, 1.0, 1.0)
mixe = node(nt, "ShaderNodeMix", (-250, 0), data_type="RGBA"); link(nt, fo, 0, mixe, 0); link(nt, glow, 0, mixe, 6); link(nt, fcol, 0, mixe, 7)
bs = node(nt, "ShaderNodeBsdfPrincipled", (50, 0))
bs.inputs["Base Color"].default_value = (0.012, 0.035, 0.045, 1); bs.inputs["Roughness"].default_value = 0.06; bs.inputs["IOR"].default_value = 1.33
bs.inputs["Subsurface Weight"].default_value = 0.35; bs.inputs["Subsurface Radius"].default_value = (0.6, 1.2, 1.5)
link(nt, mixe, 2, bs, "Emission Color"); bs.inputs["Emission Strength"].default_value = 0.22
out = node(nt, "ShaderNodeOutputMaterial", (350, 0)); link(nt, bs, 0, out, "Surface")
sea.data.materials.append(m)

# --- the ship's bow -----------------------------------------------------------------
ship = bpy.data.objects.new("Ship", None); s.collection.objects.link(ship)
paint = mat("HullPaint"); pn = paint.node_tree
b = node(pn, "ShaderNodeBsdfPrincipled", (0, 0)); b.inputs["Base Color"].default_value = (0.03, 0.035, 0.045, 1); b.inputs["Roughness"].default_value = 0.45; b.inputs["Metallic"].default_value = 0.3
o = node(pn, "ShaderNodeOutputMaterial", (250, 0)); link(pn, b, 0, o, "Surface")
deckm = mat("Deck"); dn = deckm.node_tree
b = node(dn, "ShaderNodeBsdfPrincipled", (0, 0)); b.inputs["Base Color"].default_value = (0.05, 0.055, 0.06, 1); b.inputs["Roughness"].default_value = 0.8
o = node(dn, "ShaderNodeOutputMaterial", (250, 0)); link(dn, b, 0, o, "Surface")
metal = mat("Metal"); mn = metal.node_tree
b = node(mn, "ShaderNodeBsdfPrincipled", (0, 0)); b.inputs["Base Color"].default_value = (0.25, 0.25, 0.27, 1); b.inputs["Metallic"].default_value = 1.0; b.inputs["Roughness"].default_value = 0.35
o = node(mn, "ShaderNodeOutputMaterial", (250, 0)); link(mn, b, 0, o, "Surface")


def plan(y_aft, half_beam, n=40):
    """Deck outline from the stem (0,0) back to y_aft: a clean concave-sweep bow."""
    pts = []
    for i in range(n + 1):
        t = i / n
        y = -t * (-y_aft)
        x = half_beam * (t ** 0.62)
        pts.append((x, y))
    return pts


def make_bow():
    side = plan(-22.0, 4.2)
    bm = bmesh.new()
    # hull: stem raked forward at the bottom (waterline at z=0), flare outwards going up to deck at z=4.2
    z_deck = 4.2
    rings = []
    for (x, y) in side:
        rings.append((x, y))
    # build hull skin by lofting: lower ring at waterline narrower (x*0.78, y shifted aft), upper ring at deck
    def ring_v(x, y, z, k):
        return Vector((x * k, y, z))
    lower = [ring_v(x, y - 1.8 * (1 - abs(y) / 22.0) ** 2 + 0.0, -1.0, 0.72) for (x, y) in side]
    mid = [ring_v(x, y - 0.7, 0.0, 0.84) for (x, y) in side]
    upper = [ring_v(x, y, z_deck, 1.0) for (x, y) in side]
    layers = [lower, mid, upper]
    vv = []
    for L in layers:
        row_r = [bm.verts.new(v) for v in L]
        row_l = [bm.verts.new(Vector((-v.x, v.y, v.z))) for v in L]
        vv.append((row_r, row_l))
    for li in range(2):
        for side_i in range(2):
            a, c = vv[li][side_i], vv[li + 1][side_i]
            for i in range(len(a) - 1):
                f = [a[i], a[i + 1], c[i + 1], c[i]]
                try:
                    bm.faces.new(f if side_i == 0 else f[::-1])
                except ValueError:
                    pass
    me = bpy.data.meshes.new("Hull"); bm.to_mesh(me); bm.free()
    ob = bpy.data.objects.new("Hull", me); s.collection.objects.link(ob); ob.parent = ship
    ob.data.materials.append(paint)
    sub = ob.modifiers.new("Sub", "SUBSURF"); sub.levels = 1 if PREVIEW else 2; sub.render_levels = 2 if PREVIEW else 3
    ob.modifiers.new("Solid", "SOLIDIFY").thickness = 0.06
    bpy.context.view_layer.objects.active = ob
    for p in ob.data.polygons: p.use_smooth = True
    return ob


def make_deck():
    bm = bmesh.new()
    pts = plan(-22.0, 4.2)
    r = [bm.verts.new((x, y, 4.2)) for (x, y) in pts]
    l = [bm.verts.new((-x, y, 4.2)) for (x, y) in pts]
    face = r + l[::-1][:-1] if False else r + l[::-1]
    try:
        bm.faces.new(face)
    except ValueError:
        pass
    me = bpy.data.meshes.new("Deck"); bm.to_mesh(me); bm.free()
    ob = bpy.data.objects.new("Deck", me); s.collection.objects.link(ob); ob.parent = ship
    ob.data.materials.append(deckm)
    return ob


def make_bulwark():
    # a 1.1 m wall along the deck edge, built as a swept ribbon with thickness
    bm = bmesh.new()
    pts = plan(-22.0, 4.15)
    for sgn in (1, -1):
        a = [bm.verts.new((sgn * x, y, 4.2)) for (x, y) in pts]
        c = [bm.verts.new((sgn * x * 1.0, y, 5.3)) for (x, y) in pts]
        for i in range(len(a) - 1):
            try:
                bm.faces.new([a[i], a[i + 1], c[i + 1], c[i]] if sgn == 1 else [c[i], c[i + 1], a[i + 1], a[i]])
            except ValueError:
                pass
    me = bpy.data.meshes.new("Bulwark"); bm.to_mesh(me); bm.free()
    ob = bpy.data.objects.new("Bulwark", me); s.collection.objects.link(ob); ob.parent = ship
    ob.data.materials.append(paint)
    ob.modifiers.new("Solid", "SOLIDIFY").thickness = 0.1
    bev = ob.modifiers.new("Bevel", "BEVEL"); bev.width = 0.02; bev.segments = 2
    ob.modifiers.new("WeightedNormal", "WEIGHTED_NORMAL")
    for p in ob.data.polygons: p.use_smooth = True
    return ob


def cyl(name, loc, r, h, mt, rot=(0, 0, 0), verts=24):
    bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=r, depth=h, location=loc, rotation=rot)
    o = bpy.context.active_object; o.name = name; o.parent = ship; o.data.materials.append(mt)
    bpy.ops.object.shade_smooth()
    return o


make_hull = make_bow(); make_deck(); make_bulwark()
# bollards, windlass, mast, rail stanchions, anchor chain
for (x, y) in ((1.3, -2.2), (-1.3, -2.2), (2.0, -7.0), (-2.0, -7.0)):
    cyl("Bollard", (x, y, 4.45), 0.17, 0.5, metal)
    cyl("BollardCap", (x, y, 4.75), 0.24, 0.1, metal)
cyl("Windlass", (0, -5.0, 4.75), 0.55, 1.1, paint, rot=(0, math.radians(90), 0))
cyl("WindlassL", (0.8, -5.0, 4.75), 0.38, 0.28, metal, rot=(0, math.radians(90), 0))
cyl("WindlassR", (-0.8, -5.0, 4.75), 0.38, 0.28, metal, rot=(0, math.radians(90), 0))
cyl("Mast", (0, -17.0, 10.0), 0.22, 12.0, paint)
cyl("Yard", (0, -17.0, 13.2), 0.07, 5.0, paint, rot=(0, math.radians(90), 0))
for k in range(0, 14):
    y = -3.2 - k * 1.45
    w = 4.1 * ((-y) / 22.0) ** 0.62
    for sgn in (1, -1):
        cyl("Stan", (sgn * (w - 0.1), y, 5.75), 0.025, 0.9, metal, verts=8)
# anchor chain: a short run of links down the hawse
for k in range(10):
    bpy.ops.mesh.primitive_torus_add(major_radius=0.07, minor_radius=0.02, location=(0.0 + 0.0, -0.8 - 0.03 * k, 4.0 - 0.12 * k), rotation=(math.radians(90 * (k % 2)), 0, 0), major_segments=16, minor_segments=8)
    t = bpy.context.active_object; t.parent = ship; t.data.materials.append(metal)

# deck lamp: a small warm lamp on the mast and a dim cool lamp over the windlass (the only 'man-made' light)
bpy.ops.object.light_add(type="POINT", location=(0, -16.2, 9.4)); lamp = bpy.context.active_object; lamp.parent = ship
lamp.data.energy = 120; lamp.data.color = (1.0, 0.68, 0.38); lamp.data.shadow_soft_size = 0.12
bpy.ops.object.light_add(type="AREA", location=(0, -4.0, 8.0)); fill = bpy.context.active_object; fill.parent = ship
fill.data.energy = 60; fill.data.color = (0.6, 0.85, 1.0); fill.data.size = 6
fill.rotation_euler = (0, 0, 0)

# --- bow wave / wash: foamy sheet hugging the hull ------------------------------------
bpy.ops.mesh.primitive_grid_add(x_subdivisions=64 if PREVIEW else 200, y_subdivisions=64 if PREVIEW else 200, size=1.0, location=(0, -3.0, 0.1))
wake = bpy.context.active_object; wake.name = "BowWash"; wake.parent = ship
wake.scale = (15, 15, 1)
mw = mat("Foam"); wn = mw.node_tree
tc2 = node(wn, "ShaderNodeTexCoord", (-900, 0))
mp2 = node(wn, "ShaderNodeMapping", (-700, 0))
mp2.inputs["Scale"].default_value = (1, 1, 1)
link(wn, tc2, "Object", mp2, "Vector")
f1 = node(wn, "ShaderNodeTexNoise", (-500, 100), noise_dimensions="4D")
f1.inputs["Scale"].default_value = 18; f1.inputs["Detail"].default_value = 10; f1.inputs["Roughness"].default_value = 0.65
link(wn, mp2, 0, f1, "Vector")
f1.inputs["W"].default_value = 0.0
fk = f1.inputs["W"].driver_add("default_value")
fk.driver.expression = "frame/24.0*0.9"
# V-shape mask from the stem: foam sits along the hull and in a Kelvin-like wedge behind it
sep = node(wn, "ShaderNodeSeparateXYZ", (-700, -250)); link(wn, tc2, "Object", sep, "Vector")
ab = node(wn, "ShaderNodeMath", (-500, -250), operation="ABSOLUTE"); link(wn, sep, "X", ab, 0)
ed = node(wn, "ShaderNodeMath", (-300, -250), operation="SUBTRACT"); link(wn, ab, 0, ed, 0); ed.inputs[1].default_value = 0.0
mk = node(wn, "ShaderNodeValToRGB", (-100, -250)); mk.color_ramp.elements[0].position = 0.0; mk.color_ramp.elements[1].position = 0.42
mk.color_ramp.elements[0].color = (1, 1, 1, 1); mk.color_ramp.elements[1].color = (0, 0, 0, 1)
link(wn, ed, 0, mk, "Fac")
mul = node(wn, "ShaderNodeMath", (150, 0), operation="MULTIPLY", use_clamp=True); link(wn, f1, "Fac", mul, 0); link(wn, mk, "Color", mul, 1)
sm = node(wn, "ShaderNodeMapRange", (350, 0)); link(wn, mul, 0, sm, 0); sm.inputs["From Min"].default_value = 0.22; sm.inputs["From Max"].default_value = 0.5
tr = node(wn, "ShaderNodeBsdfTransparent", (350, 250))
fb = node(wn, "ShaderNodeBsdfPrincipled", (350, -150)); fb.inputs["Base Color"].default_value = (0.7, 0.85, 0.9, 1); fb.inputs["Roughness"].default_value = 0.8
fb.inputs["Emission Color"].default_value = (0.35, 0.65, 0.8, 1); fb.inputs["Emission Strength"].default_value = 0.55
mx = node(wn, "ShaderNodeMixShader", (650, 0)); link(wn, sm, 0, mx, 0); link(wn, tr, 0, mx, 1); link(wn, fb, 0, mx, 2)
oo = node(wn, "ShaderNodeOutputMaterial", (900, 0)); link(wn, mx, 0, oo, "Surface")
wake.data.materials.append(mw)

# --- camera: on deck, over the bow, motion = ship moving forward -----------------------------
cam = camera("Cam", (0.35, -7.5, 6.9), (0.0, 60.0, 2.6), lens=22)
cam.parent = ship
ship.location = (0, 0, 0)
ship.keyframe_insert("location", frame=1)
ship.location = (0, SPEED * (s.frame_end - 1) / 24.0, 0)
ship.keyframe_insert("location", frame=s.frame_end)
save(os.path.join(ROOT, "scenes", "b_ship_preview.blend" if PREVIEW else "b_ship.blend"))
