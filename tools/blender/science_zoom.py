"""3D science zoom: Moon -> Earth -> Sun -> Milky Way in ONE continuous camera pull-back (Blender 5.x, EEVEE).

Usage (headless, rerunnable; everything is driven by the JSON plan):
  blender -b --factory-startup -P tools/blender/science_zoom.py -- --plan tools/blender/science_zoom_plan.json \
      --out E:/project-data/blender/library/science-zoom/science_zoom.blend \
      [--res 480x854] [--samples 16] [--stills 1,150,300,450,700,900 --stills-dir DIR] [--video OUT.mp4] [--pack]

How the scale swap works (see docs/science-zoom-3d.md): the scene is solved in doubles in "Earth radius" units. Every frame we take
the camera focus F(t) and frame half-width H(t) (log-space monotone cubic), and place each body at p = (P - F) * g, g = HW / H.
So the camera never moves in scale: the WORLD is rescaled around the focus each frame, there is no float-precision problem and
every layer is always rendered at a sane size. A layer that gets too small is held at a minimum on-screen dot and faded out exactly
when the next layer's orbit ring fades in; the galaxy disc fades in over the same zoom range. All channels are baked as keyframes
(constant interpolation, one key per frame), so the .blend renders with no Python at all.
"""
import sys, os, json, math, argparse
import bpy
from mathutils import Vector, Quaternion, Matrix

argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
ap = argparse.ArgumentParser()
ap.add_argument('--plan', required=True)
ap.add_argument('--out', required=True)
ap.add_argument('--res')
ap.add_argument('--samples', type=int)
ap.add_argument('--stills')
ap.add_argument('--stills-dir')
ap.add_argument('--video')
ap.add_argument('--frames')
ap.add_argument('--pack', action='store_true')
args = ap.parse_args(argv)
P = json.load(open(args.plan))
FPS, SECS = P['fps'], P['seconds']
NF = int(FPS * SECS)
LIB = P['library']
TEX = os.path.join(LIB, 'textures')
HW = P['view_halfwidth']
UA, UB, UC = P['levels']['A'], P['levels']['B'], P['levels']['C']
BD = P['bodies']

# ------------------------------------------------------------------ math helpers (plain doubles)
def smooth(e0, e1, x):
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3 - 2 * t)

def pchip(xs, ys):
    n = len(xs)
    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    d = [(ys[i + 1] - ys[i]) / h[i] for i in range(n - 1)]
    m = [0.0] * n
    m[0], m[-1] = d[0], d[-1]
    for i in range(1, n - 1):
        m[i] = 0.0 if d[i - 1] * d[i] <= 0 else 2 * d[i - 1] * d[i] / (d[i - 1] + d[i])
    def f(x):
        x = min(max(x, xs[0]), xs[-1])
        i = max(j for j in range(n - 1) if xs[j] <= x) if x < xs[-1] else n - 2
        t = (x - xs[i]) / h[i]
        t2, t3 = t * t, t * t * t
        return ((2 * t3 - 3 * t2 + 1) * ys[i] + (t3 - 2 * t2 + t) * h[i] * m[i]
                + (-2 * t3 + 3 * t2) * ys[i + 1] + (t3 - t2) * h[i] * m[i + 1])
    return f

def keyed(keys):
    """[[t, v], ...] -> function with smoothstep easing between keys."""
    def f(t):
        if t <= keys[0][0]: return keys[0][1]
        for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
            if t <= t1:
                return v0 + (v1 - v0) * smooth(t0, t1, t)
        return keys[-1][1]
    return f

def add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def mul(a, k): return (a[0] * k, a[1] * k, a[2] * k)
def rotx(v, ang):
    c, s = math.cos(ang), math.sin(ang)
    return (v[0], v[1] * c - v[2] * s, v[1] * s + v[2] * c)

zoom_f = pchip([k[0] for k in P['zoom']], [math.log(k[1]) for k in P['zoom']])
elev_f = keyed(P['elevation_deg'])
azim_f = keyed(P['azimuth_deg'])
focus_keys = P['focus']

# simulated clock: integrate the (log-interpolated) days-per-second curve
rk = P['time_scale_days_per_s']
def rate(t):
    if t <= rk[0][0]: return rk[0][1]
    for (t0, v0), (t1, v1) in zip(rk, rk[1:]):
        if t <= t1:
            return math.exp(math.log(v0) + (math.log(v1) - math.log(v0)) * smooth(t0, t1, t))
    return rk[-1][1]
SIM = [0.0]
SUB = 20
for i in range(NF):
    acc = 0.0
    for j in range(SUB):
        acc += rate((i + (j + 0.5) / SUB) / FPS) / FPS / SUB
    SIM.append(SIM[-1] + acc)  # SIM[f-1] = days at frame f

# ------------------------------------------------------------------ world state at a time
PL = {p['name']: p for p in BD['planets']}
TILT = math.radians(BD['galaxy_tilt_deg'])
SUN_PERIOD_D = BD['sun_galactic_period_years'] * 365.25

def state(frame):
    t = (frame - 1) / FPS
    T = SIM[frame - 1]
    a0 = math.radians(BD['sun_phase_deg'])
    alpha = a0 + 2 * math.pi * T / SUN_PERIOD_D
    sun = rotx((BD['sun_galactic_radius_C'] * UC * math.cos(alpha), BD['sun_galactic_radius_C'] * UC * math.sin(alpha), 0.0), TILT)
    pos = {}
    for p in BD['planets']:
        ang = math.radians(p['phase_deg']) + 2 * math.pi * T / p['period_days']
        R = p['orbit_B'] * UB
        pos[p['name']] = add(sun, (R * math.cos(ang), R * math.sin(ang), 0.0))
    ph = math.radians(BD['moon_phase_deg']) + 2 * math.pi * T / BD['moon_period_days']
    mo = rotx((BD['moon_orbit'] * math.cos(ph), BD['moon_orbit'] * math.sin(ph), 0.0), math.radians(BD['moon_incl_deg']))
    pos['moon'] = add(pos['earth'], mo)
    pos['sun'] = sun
    pos['galaxy'] = (0.0, 0.0, 0.0)
    # focus: smooth blend between named targets
    fk = focus_keys
    F = pos[fk[0][1]]
    for (t0, n0), (t1, n1) in zip(fk, fk[1:]):
        if t0 < t <= t1:
            w = smooth(t0, t1, t)
            F = add(mul(pos[n0], 1 - w), mul(pos[n1], w))
            break
        if t > t1:
            F = pos[n1]
    H = math.exp(zoom_f(t))
    return dict(t=t, T=T, pos=pos, F=F, H=H, g=HW / H, alpha=alpha)

# ------------------------------------------------------------------ scene
bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene
sc.frame_start, sc.frame_end = 1, NF
sc.render.fps = FPS
sc.render.engine = 'BLENDER_EEVEE'
res = [int(v) for v in args.res.lower().split('x')] if args.res else P['resolution']
sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = res[0], res[1], 100
sc.eevee.taa_render_samples = args.samples or P['samples']
sc.eevee.light_threshold = 0.0005
sc.render.film_transparent = False
for vt, lk in (('AgX', 'AgX - Medium High Contrast'), ('AgX', 'AgX - High Contrast'), ('AgX', 'None')):
    try:
        sc.view_settings.view_transform = vt
        sc.view_settings.look = lk
        break
    except Exception:
        continue
sc.view_settings.exposure = 0.0
bpy.context.preferences.edit.keyframe_new_interpolation_type = 'CONSTANT'

def N(nt, kind, loc=(0, 0), **kw):
    n = nt.nodes.new(kind)
    n.location = loc
    for k, v in kw.items():
        if k in n.inputs:
            n.inputs[k].default_value = v
        else:
            setattr(n, k, v)
    return n

def L(nt, a, b, c=None):
    """link output a to input b (sockets), or node outputs by name"""
    nt.links.new(a, b)

def new_mat(name, method='DITHERED'):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    m.node_tree.nodes.clear()
    m.surface_render_method = method
    return m

def out_node(m, shader):
    o = m.node_tree.nodes.new('ShaderNodeOutputMaterial')
    m.node_tree.links.new(shader, o.inputs['Surface'])
    return o

def emission_vis_mat(name, color, strength, method='BLENDED'):
    """emission * 'vis' value (keyed per frame) over transparent: used for rings, halos, text"""
    m = new_mat(name, method)
    nt = m.node_tree
    vis = N(nt, 'ShaderNodeValue', label='vis')
    vis.name = 'vis'
    em = N(nt, 'ShaderNodeEmission', Color=color, Strength=strength)
    tr = N(nt, 'ShaderNodeBsdfTransparent')
    mix = N(nt, 'ShaderNodeMixShader')
    L(nt, vis.outputs[0], mix.inputs[0]); L(nt, tr.outputs[0], mix.inputs[1]); L(nt, em.outputs[0], mix.inputs[2])
    out_node(m, mix.outputs[0])
    return m

def lit_mask(nt, lo=0.0, hi=0.12):
    """0 on the day side, 1 on the night side, computed from the real scene lights"""
    d = N(nt, 'ShaderNodeBsdfDiffuse', Color=(1, 1, 1, 1))
    s = N(nt, 'ShaderNodeShaderToRGB')
    bw = N(nt, 'ShaderNodeRGBToBW')
    mr = N(nt, 'ShaderNodeMapRange', **{'From Min': lo, 'From Max': hi, 'To Min': 1.0, 'To Max': 0.0})
    mr.clamp = True
    L(nt, d.outputs[0], s.inputs[0]); L(nt, s.outputs[0], bw.inputs[0]); L(nt, bw.outputs[0], mr.inputs['Value'])
    return mr.outputs[0]

def tex(nt, path, cs='sRGB', loc=(0, 0)):
    im = bpy.data.images.load(path)
    im.colorspace_settings.name = cs
    t = N(nt, 'ShaderNodeTexImage', loc)
    t.image = im
    t.interpolation = 'Cubic'
    return t

# ---------------- Earth
m_earth = new_mat('earth')
nt = m_earth.node_tree
uv = N(nt, 'ShaderNodeTexCoord')
base = tex(nt, os.path.join(TEX, 'earth_day_8192.jpg'))
lights = tex(nt, os.path.join(TEX, 'lights_4096.jpg'), 'Non-Color')
L(nt, uv.outputs['UV'], base.inputs[0]); L(nt, uv.outputs['UV'], lights.inputs[0])
sep = N(nt, 'ShaderNodeSeparateColor')
L(nt, base.outputs['Color'], sep.inputs[0])
sub_ = N(nt, 'ShaderNodeMath', operation='SUBTRACT')
L(nt, sep.outputs['Blue'], sub_.inputs[0]); L(nt, sep.outputs['Red'], sub_.inputs[1])
ocean = N(nt, 'ShaderNodeMapRange', **{'From Min': 0.03, 'From Max': 0.20, 'To Min': 0.0, 'To Max': 1.0})
ocean.clamp = True
L(nt, sub_.outputs[0], ocean.inputs['Value'])
rough = N(nt, 'ShaderNodeMapRange', **{'From Min': 0.0, 'From Max': 1.0, 'To Min': 0.82, 'To Max': 0.16})
L(nt, ocean.outputs[0], rough.inputs['Value'])
bsdf = N(nt, 'ShaderNodeBsdfPrincipled')
L(nt, base.outputs['Color'], bsdf.inputs['Base Color'])
L(nt, rough.outputs[0], bsdf.inputs['Roughness'])
night = lit_mask(nt, 0.0, 0.05)
nm = N(nt, 'ShaderNodeMath', operation='MULTIPLY')
lt = N(nt, 'ShaderNodeMath', operation='POWER')
L(nt, lights.outputs['Color'], lt.inputs[0]); lt.inputs[1].default_value = 1.2
L(nt, night, nm.inputs[0]); L(nt, lt.outputs[0], nm.inputs[1])
mulc = N(nt, 'ShaderNodeMath', operation='MULTIPLY'); mulc.inputs[1].default_value = 9.0
L(nt, nm.outputs[0], mulc.inputs[0])
bsdf.inputs['Emission Color'].default_value = (1.0, 0.62, 0.25, 1)
L(nt, mulc.outputs[0], bsdf.inputs['Emission Strength'])
out_node(m_earth, bsdf.outputs[0])

m_cloud = new_mat('clouds', 'BLENDED')
nt = m_cloud.node_tree
tc = N(nt, 'ShaderNodeTexCoord')
mp = N(nt, 'ShaderNodeMapping'); mp.inputs['Scale'].default_value = (1.0, 1.0, 2.2)
nz = N(nt, 'ShaderNodeTexNoise', Scale=2.6, Detail=9, Roughness=0.62, Distortion=0.9)
nz.noise_dimensions = '3D'
nz2 = N(nt, 'ShaderNodeTexNoise', Scale=1.3, Detail=2, Roughness=0.5)
mixn = N(nt, 'ShaderNodeMix'); mixn.data_type = 'FLOAT'; mixn.inputs[0].default_value = 0.35
L(nt, tc.outputs['Object'], mp.inputs[0]); L(nt, mp.outputs[0], nz.inputs['Vector']); L(nt, mp.outputs[0], nz2.inputs['Vector'])
L(nt, nz.outputs['Fac'], mixn.inputs[2]); L(nt, nz2.outputs['Fac'], mixn.inputs[3])
cr = N(nt, 'ShaderNodeValToRGB')
cr.color_ramp.elements[0].position = 0.62; cr.color_ramp.elements[1].position = 0.82
L(nt, mixn.outputs[0], cr.inputs[0])
cb = N(nt, 'ShaderNodeBsdfPrincipled', **{'Base Color': (0.95, 0.96, 1.0, 1), 'Roughness': 0.9})
L(nt, cr.outputs['Color'], cb.inputs['Alpha'])
out_node(m_cloud, cb.outputs[0])

m_atm = new_mat('atmosphere', 'BLENDED')
nt = m_atm.node_tree
lw = N(nt, 'ShaderNodeLayerWeight', Blend=0.5)
pw = N(nt, 'ShaderNodeMath', operation='POWER'); pw.inputs[1].default_value = 5.0
L(nt, lw.outputs['Facing'], pw.inputs[0])
day = lit_mask(nt, 0.0, 0.6)   # 1 on night side
dd = N(nt, 'ShaderNodeMath', operation='SUBTRACT'); dd.inputs[0].default_value = 1.0
L(nt, day, dd.inputs[1])
dmin = N(nt, 'ShaderNodeMath', operation='MAXIMUM'); dmin.inputs[1].default_value = 0.25
L(nt, dd.outputs[0], dmin.inputs[0])
fac = N(nt, 'ShaderNodeMath', operation='MULTIPLY')
L(nt, pw.outputs[0], fac.inputs[0]); L(nt, dmin.outputs[0], fac.inputs[1])
fac2 = N(nt, 'ShaderNodeMath', operation='MULTIPLY', use_clamp=True); fac2.inputs[1].default_value = 1.6
L(nt, fac.outputs[0], fac2.inputs[0])
em = N(nt, 'ShaderNodeEmission', Color=(0.28, 0.52, 1.0, 1), Strength=1.7)
tr = N(nt, 'ShaderNodeBsdfTransparent')
mx = N(nt, 'ShaderNodeMixShader')
L(nt, fac2.outputs[0], mx.inputs[0]); L(nt, tr.outputs[0], mx.inputs[1]); L(nt, em.outputs[0], mx.inputs[2])
out_node(m_atm, mx.outputs[0])

# ---------------- Moon (real displacement from layered Voronoi craters)
m_moon = new_mat('moon')
m_moon.displacement_method = 'BOTH'
nt = m_moon.node_tree
tc = N(nt, 'ShaderNodeTexCoord')
def crater_layer(scale, seed):
    v = N(nt, 'ShaderNodeTexVoronoi', Scale=scale, Randomness=1.0)
    v.voronoi_dimensions = '4D'; v.feature = 'F1'
    v.inputs['W'].default_value = seed
    L(nt, tc.outputs['Object'], v.inputs['Vector'])
    r = N(nt, 'ShaderNodeValToRGB')
    el = r.color_ramp.elements
    el[0].position, el[0].color = 0.0, (0.0, 0.0, 0.0, 1)
    el[1].position, el[1].color = 1.0, (0.5, 0.5, 0.5, 1)
    e2 = el.new(0.30); e2.color = (0.2, 0.2, 0.2, 1)
    e3 = el.new(0.40); e3.color = (0.78, 0.78, 0.78, 1)   # raised rim
    e4 = el.new(0.52); e4.color = (0.5, 0.5, 0.5, 1)
    L(nt, v.outputs['Distance'], r.inputs[0])
    return r.outputs['Color']
c1, c2, c3 = crater_layer(3.2, 1.0), crater_layer(9.0, 2.0), crater_layer(26.0, 3.0)
def wsum(a, wa, b, wb):
    m1 = N(nt, 'ShaderNodeMath', operation='MULTIPLY'); m1.inputs[1].default_value = wa
    m2 = N(nt, 'ShaderNodeMath', operation='MULTIPLY'); m2.inputs[1].default_value = wb
    L(nt, a, m1.inputs[0]); L(nt, b, m2.inputs[0])
    s = N(nt, 'ShaderNodeMath', operation='ADD')
    L(nt, m1.outputs[0], s.inputs[0]); L(nt, m2.outputs[0], s.inputs[1])
    return s.outputs[0]
hsum = wsum(wsum(c1, 0.55, c2, 0.30), 1.0, c3, 0.15)
fine = N(nt, 'ShaderNodeTexNoise', Scale=60, Detail=8, Roughness=0.7)
L(nt, tc.outputs['Object'], fine.inputs['Vector'])
hfin = wsum(hsum, 1.0, fine.outputs['Fac'], 0.10)
disp = N(nt, 'ShaderNodeDisplacement', Midlevel=0.5, Scale=0.035)
L(nt, hfin, disp.inputs['Height'])
maria = N(nt, 'ShaderNodeTexNoise', Scale=1.6, Detail=4, Roughness=0.5)
L(nt, tc.outputs['Object'], maria.inputs['Vector'])
mr = N(nt, 'ShaderNodeValToRGB')
mr.color_ramp.elements[0].position = 0.42; mr.color_ramp.elements[0].color = (0.06, 0.06, 0.065, 1)
mr.color_ramp.elements[1].position = 0.58; mr.color_ramp.elements[1].color = (0.30, 0.29, 0.28, 1)
L(nt, maria.outputs['Fac'], mr.inputs[0])
moon_tex = tex(nt, os.path.join(TEX, 'moon_color_4096.jpg'))
uvm = N(nt, 'ShaderNodeTexCoord'); L(nt, uvm.outputs['UV'], moon_tex.inputs[0])
moon_gain = N(nt, 'ShaderNodeVectorMath', operation='SCALE'); moon_gain.inputs['Scale'].default_value = 1.7
L(nt, moon_tex.outputs['Color'], moon_gain.inputs[0])
shade = N(nt, 'ShaderNodeMix'); shade.data_type = 'RGBA'; shade.blend_type = 'MULTIPLY'; shade.inputs[0].default_value = 0.55
ramp_h = N(nt, 'ShaderNodeValToRGB')
ramp_h.color_ramp.elements[0].position = 0.25; ramp_h.color_ramp.elements[0].color = (0.55, 0.55, 0.55, 1)
ramp_h.color_ramp.elements[1].position = 0.7; ramp_h.color_ramp.elements[1].color = (1.25, 1.25, 1.25, 1)
L(nt, hfin, ramp_h.inputs[0])
L(nt, moon_gain.outputs[0], shade.inputs[6]); L(nt, ramp_h.outputs['Color'], shade.inputs[7])
mb = N(nt, 'ShaderNodeBsdfPrincipled', Roughness=0.95)
L(nt, shade.outputs[2], mb.inputs['Base Color'])
out = out_node(m_moon, mb.outputs[0])
L(nt, disp.outputs[0], out.inputs['Displacement'])

# ---------------- Sun
m_sun = new_mat('sun')
nt = m_sun.node_tree
tc = N(nt, 'ShaderNodeTexCoord')
gr = N(nt, 'ShaderNodeTexNoise', Scale=14, Detail=6, Roughness=0.6, Distortion=0.4)
L(nt, tc.outputs['Object'], gr.inputs['Vector'])
cr = N(nt, 'ShaderNodeValToRGB')
cr.color_ramp.elements[0].position = 0.40; cr.color_ramp.elements[0].color = (1.0, 0.62, 0.20, 1)
cr.color_ramp.elements[1].position = 0.75; cr.color_ramp.elements[1].color = (1.0, 0.97, 0.80, 1)
L(nt, gr.outputs['Fac'], cr.inputs[0])
lw = N(nt, 'ShaderNodeLayerWeight', Blend=0.5)          # Facing: 0 at disc centre, 1 at the limb
limbk = N(nt, 'ShaderNodeMath', operation='SUBTRACT', use_clamp=True); limbk.inputs[0].default_value = 1.0
L(nt, lw.outputs['Facing'], limbk.inputs[1])            # 1 centre -> 0 limb
limbmix = N(nt, 'ShaderNodeMix'); limbmix.data_type = 'RGBA'
limbmix.inputs[7].default_value = (1.0, 0.99, 0.92, 1)  # centre: white-yellow hot
L(nt, limbk.outputs[0], limbmix.inputs[0]); L(nt, cr.outputs['Color'], limbmix.inputs[6])
se = N(nt, 'ShaderNodeMath', operation='MULTIPLY'); se.inputs[1].default_value = 14.0
lk2 = N(nt, 'ShaderNodeMath', operation='ADD'); lk2.inputs[1].default_value = 0.35
L(nt, limbk.outputs[0], lk2.inputs[0]); L(nt, lk2.outputs[0], se.inputs[0])
emn = N(nt, 'ShaderNodeEmission')
L(nt, limbmix.outputs[2], emn.inputs['Color']); L(nt, se.outputs[0], emn.inputs['Strength'])
out_node(m_sun, emn.outputs[0])

# ---------------- generic planet dot
def planet_mat(name, col):
    m = new_mat(name)
    b = N(m.node_tree, 'ShaderNodeBsdfPrincipled', **{'Base Color': (*col, 1), 'Roughness': 0.7})
    out_node(m, b.outputs[0])
    return m

# ---------------- halo billboard (sun glow)
def halo_mat(name='sun_halo', inner=(1.0, 0.97, 0.85, 1), outer=(1.0, 0.55, 0.15, 1), strength=7.0, power=3.0):
    m = emission_vis_mat(name, inner, strength)
    nt = m.node_tree
    tcn = N(nt, 'ShaderNodeTexCoord')
    vsub = N(nt, 'ShaderNodeVectorMath', operation='SUBTRACT'); vsub.inputs[1].default_value = (0.5, 0.5, 0)
    ln = N(nt, 'ShaderNodeVectorMath', operation='LENGTH')
    L(nt, tcn.outputs['UV'], vsub.inputs[0]); L(nt, vsub.outputs[0], ln.inputs[0])
    r = N(nt, 'ShaderNodeMath', operation='MULTIPLY', use_clamp=True); r.inputs[1].default_value = 2.0
    L(nt, ln.outputs['Value'], r.inputs[0])
    om = N(nt, 'ShaderNodeMath', operation='SUBTRACT', use_clamp=True); om.inputs[0].default_value = 1.0
    L(nt, r.outputs[0], om.inputs[1])
    p = N(nt, 'ShaderNodeMath', operation='POWER'); p.inputs[1].default_value = power
    L(nt, om.outputs[0], p.inputs[0])
    vis = nt.nodes['vis']
    mm = N(nt, 'ShaderNodeMath', operation='MULTIPLY')
    L(nt, p.outputs[0], mm.inputs[0]); L(nt, vis.outputs[0], mm.inputs[1])
    mix = [n for n in nt.nodes if n.bl_idname == 'ShaderNodeMixShader'][0]
    for l in list(nt.links):
        if l.to_node == mix and l.to_socket == mix.inputs[0]:
            nt.links.remove(l)
    L(nt, mm.outputs[0], mix.inputs[0])
    em = [n for n in nt.nodes if n.bl_idname == 'ShaderNodeEmission'][0]
    cm = N(nt, 'ShaderNodeMix'); cm.data_type = 'RGBA'
    cm.inputs[6].default_value = inner; cm.inputs[7].default_value = outer
    L(nt, r.outputs[0], cm.inputs[0]); L(nt, cm.outputs[2], em.inputs['Color'])
    return m


# ---------------- galaxy disc
def galaxy_mat():
    m = new_mat('galaxy', 'BLENDED')
    nt = m.node_tree
    tcn = N(nt, 'ShaderNodeTexCoord')
    t = tex(nt, os.path.join(TEX, 'galaxy_disc_4096.exr'), 'Linear Rec.709')
    t.extension = 'CLIP'
    L(nt, tcn.outputs['UV'], t.inputs[0])
    tb = tex(nt, os.path.join(TEX, 'galaxy_blur_1024.exr'), 'Linear Rec.709')
    tb.extension = 'CLIP'
    L(nt, tcn.outputs['UV'], tb.inputs[0])
    sharp = N(nt, 'ShaderNodeValue', label='sharp'); sharp.name = 'sharp'
    cmix = N(nt, 'ShaderNodeMix'); cmix.data_type = 'RGBA'
    L(nt, sharp.outputs[0], cmix.inputs[0]); L(nt, tb.outputs['Color'], cmix.inputs[6]); L(nt, t.outputs['Color'], cmix.inputs[7])
    amix = N(nt, 'ShaderNodeMix'); amix.data_type = 'FLOAT'
    L(nt, sharp.outputs[0], amix.inputs[0]); L(nt, tb.outputs['Alpha'], amix.inputs[2]); L(nt, t.outputs['Alpha'], amix.inputs[3])
    vis = N(nt, 'ShaderNodeValue', label='vis'); vis.name = 'vis'
    mc = N(nt, 'ShaderNodeVectorMath', operation='SCALE'); mc.inputs['Scale'].default_value = 2.4
    L(nt, cmix.outputs[2], mc.inputs[0])
    em = N(nt, 'ShaderNodeEmission')
    L(nt, mc.outputs[0], em.inputs['Color'])
    tr = N(nt, 'ShaderNodeBsdfTransparent')
    am = N(nt, 'ShaderNodeMath', operation='MULTIPLY', use_clamp=True)
    L(nt, amix.outputs[0], am.inputs[0]); L(nt, vis.outputs[0], am.inputs[1])
    mix = N(nt, 'ShaderNodeMixShader')
    L(nt, am.outputs[0], mix.inputs[0]); L(nt, tr.outputs[0], mix.inputs[1]); L(nt, em.outputs[0], mix.inputs[2])
    out_node(m, mix.outputs[0])
    return m

# ---------------- world: black for light, stars + Milky Way band only for camera rays
w = bpy.data.worlds.new('space'); sc.world = w; w.use_nodes = True
nt = w.node_tree; nt.nodes.clear()
wo = N(nt, 'ShaderNodeOutputWorld')
tcw = N(nt, 'ShaderNodeTexCoord')
vor = N(nt, 'ShaderNodeTexVoronoi', Scale=520, Randomness=1.0)
vor.voronoi_dimensions = '3D'; vor.feature = 'F1'
L(nt, tcw.outputs['Generated'], vor.inputs['Vector'])
sepc = N(nt, 'ShaderNodeSeparateColor')
L(nt, vor.outputs['Color'], sepc.inputs[0])
mag = N(nt, 'ShaderNodeMapRange', **{'From Min': 0.90, 'From Max': 1.0, 'To Min': 0.0, 'To Max': 1.0}); mag.clamp = True
L(nt, sepc.outputs['Red'], mag.inputs['Value'])
sz = N(nt, 'ShaderNodeMapRange', **{'From Min': 0.0, 'From Max': 0.07, 'To Min': 1.0, 'To Max': 0.0}); sz.clamp = True
L(nt, vor.outputs['Distance'], sz.inputs['Value'])
spow = N(nt, 'ShaderNodeMath', operation='POWER'); spow.inputs[1].default_value = 2.0
L(nt, sz.outputs[0], spow.inputs[0])
stars = N(nt, 'ShaderNodeMath', operation='MULTIPLY')
L(nt, spow.outputs[0], stars.inputs[0]); L(nt, mag.outputs[0], stars.inputs[1])
sbright = N(nt, 'ShaderNodeMath', operation='MULTIPLY'); sbright.inputs[1].default_value = 5.0
L(nt, stars.outputs[0], sbright.inputs[0])
mwpath = os.path.join(TEX, 'milkyway_4k.exr')
src = 'E:/project-data/blender/milky-seas/textures/milkyway_2020_8k.exr'
if not os.path.exists(mwpath) and os.path.exists(src):
    im = bpy.data.images.load(src); im.scale(4096, 2048); im.filepath_raw = mwpath; im.file_format = 'OPEN_EXR'
    sc.render.image_settings.color_depth = '16'; im.save(); bpy.data.images.remove(im)
env = None
if os.path.exists(mwpath):
    env = N(nt, 'ShaderNodeTexEnvironment'); env.image = bpy.data.images.load(mwpath)
    env.image.colorspace_settings.name = 'Linear Rec.709'
    mapn = N(nt, 'ShaderNodeMapping'); mapn.inputs['Rotation'].default_value = (math.radians(60), 0, math.radians(30))
    L(nt, tcw.outputs['Generated'], mapn.inputs[0]); L(nt, mapn.outputs[0], env.inputs[0])
    mwv = N(nt, 'ShaderNodeValue', label='mw'); mwv.name = 'mw'
    mws = N(nt, 'ShaderNodeVectorMath', operation='SCALE')
    L(nt, env.outputs['Color'], mws.inputs[0]); L(nt, mwv.outputs[0], mws.inputs['Scale'])
    sstar = N(nt, 'ShaderNodeCombineColor')
    for i in range(3): L(nt, sbright.outputs[0], sstar.inputs[i])
    tot = N(nt, 'ShaderNodeVectorMath', operation='ADD')
    L(nt, mws.outputs[0], tot.inputs[0]); L(nt, sstar.outputs[0], tot.inputs[1])
    col = tot.outputs[0]
else:
    sstar = N(nt, 'ShaderNodeCombineColor')
    for i in range(3): L(nt, sbright.outputs[0], sstar.inputs[i])
    col = sstar.outputs[0]
bgn = N(nt, 'ShaderNodeBackground', Strength=1.0)
L(nt, col, bgn.inputs['Color'])
bgk = N(nt, 'ShaderNodeBackground', Color=(0, 0, 0, 1))
lp = N(nt, 'ShaderNodeLightPath')
mxw = N(nt, 'ShaderNodeMixShader')
L(nt, lp.outputs['Is Camera Ray'], mxw.inputs[0]); L(nt, bgk.outputs[0], mxw.inputs[1]); L(nt, bgn.outputs[0], mxw.inputs[2])
L(nt, mxw.outputs[0], wo.inputs[0])

# ------------------------------------------------------------------ objects
coll = sc.collection
def finish(o, mat=None, smooth_=True):
    if mat: o.data.materials.append(mat)
    if smooth_ and o.type == 'MESH':
        for p in o.data.polygons: p.use_smooth = True
    o.rotation_mode = 'QUATERNION'
    return o

def sphere(name, mat, seg=128, rings=64):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, segments=seg, ring_count=rings)
    o = bpy.context.object; o.name = name
    return finish(o, mat)

OBJ = {}
PGLOW = []
OBJ['earth'] = sphere('earth', m_earth, 160, 80)
OBJ['clouds'] = sphere('clouds', m_cloud, 128, 64)
OBJ['atm'] = sphere('atm', m_atm, 96, 48)
OBJ['atm'].visible_shadow = False; OBJ['clouds'].visible_shadow = False   # shells must not shadow the surface
OBJ['moon'] = sphere('moon', m_moon, 256, 128)
OBJ['sun'] = sphere('sun', m_sun, 96, 48); OBJ['sun'].visible_shadow = False
for p in BD['planets']:
    if p['name'] != 'earth':
        OBJ[p['name']] = sphere(p['name'], planet_mat(p['name'], p['color']), 48, 24)

def plane(name, mat, size=2.0):
    bpy.ops.mesh.primitive_plane_add(size=size)
    o = bpy.context.object; o.name = name
    return finish(o, mat, False)
OBJ['halo'] = plane('halo', halo_mat()); OBJ['halo'].visible_shadow = False
OBJ['galaxy'] = plane('galaxy', galaxy_mat()); OBJ['galaxy'].visible_shadow = False
OBJ['haze_a'] = plane('haze_a', galaxy_mat()); OBJ['haze_a'].visible_shadow = False   # blurred depth layers above / below the disc
OBJ['haze_b'] = plane('haze_b', galaxy_mat()); OBJ['haze_b'].visible_shadow = False

for p in BD['planets']:
    col = p.get('color', (0.4, 0.62, 1.0))
    OBJ['pglow_' + p['name']] = plane('pglow_' + p['name'], halo_mat('pglow_' + p['name'], (*col, 1), (*col, 1), 2.4, 2.2))
    OBJ['pglow_' + p['name']].visible_shadow = False
    PGLOW.append(OBJ['pglow_' + p['name']])


def ring(name, color, strength):
    bpy.ops.curve.primitive_bezier_circle_add(radius=1.0)
    o = bpy.context.object; o.name = name
    o.data.resolution_u = 128
    o.data.bevel_depth = 0.004; o.data.bevel_resolution = 1
    o.data.materials.append(emission_vis_mat(name + '_m', color, strength))
    o.rotation_mode = 'QUATERNION'
    return o
for p in BD['planets']:
    OBJ['ring_' + p['name']] = ring('ring_' + p['name'], (0.55, 0.68, 1.0, 1), 0.9 if p['name'] == 'earth' else 0.45)
OBJ['ring_moon'] = ring('ring_moon', (0.7, 0.75, 0.9, 1), 0.5)
OBJ['ring_sun_gal'] = ring('ring_sun_gal', (1.0, 0.75, 0.45, 1), 0.7)
for n_, c_ in (('trail_moon', (0.85, 0.9, 1.0, 1)), ('trail_earth', (0.45, 0.7, 1.0, 1)), ('trail_sun_gal', (1.0, 0.85, 0.5, 1))):
    OBJ[n_] = ring(n_, c_, 1.8)
    OBJ[n_].data.use_fill_caps = True

# billboard halo tracks the camera
bpy.ops.object.camera_add()
cam = bpy.context.object; cam.name = 'cam'; sc.camera = cam
cam.data.lens = 50; cam.data.sensor_fit = 'HORIZONTAL'; cam.data.sensor_width = 36
cam.data.clip_start = 0.1; cam.data.clip_end = 20000
cam.rotation_mode = 'QUATERNION'
CAMD = HW * 50.0 / 18.0
for o_ in [OBJ['halo']] + PGLOW:
    tt = o_.constraints.new('TRACK_TO'); tt.target = cam; tt.track_axis = 'TRACK_Z'; tt.up_axis = 'UP_Y'

bpy.ops.object.light_add(type='SUN')
lamp = bpy.context.object; lamp.name = 'sunlight'
lamp.rotation_mode = 'QUATERNION'
lamp.data.angle = math.radians(0.6); lamp.data.use_shadow = True
lamp.data.energy = 9.0
lamp.data.color = (1.0, 0.95, 0.88)

# ---- overlay text (parented to the camera, so it is screen-locked)
fontpath = 'C:/Windows/Fonts/bahnschrift.ttf'
font = bpy.data.fonts.load(fontpath) if os.path.exists(fontpath) else None
TXT = []
def text(name, body, y, size, strength, wfont=1.0):
    c = bpy.data.curves.new(name, 'FONT')
    c.body = body; c.align_x = 'CENTER'; c.size = size
    if font: c.font = font
    c.space_character = 1.08
    o = bpy.data.objects.new(name, c); sc.collection.objects.link(o)
    o.parent = cam; o.location = (0, y, -5.0)
    m = emission_vis_mat(name + '_m', (0.92, 0.95, 1.0, 1), strength)
    o.data.materials.append(m)
    return o, m
LB = P['labels']
for i, tl in enumerate(LB['titles']):
    o, m = text(f'title{i}', tl['text'], 2.88, 0.115, 2.2)
    TXT.append((m, tl['from'], tl['to']))
for i, tl in enumerate(LB['rates']):
    o, m = text(f'rate{i}', tl['text'], -2.72, 0.11, 2.2)
    TXT.append((m, tl['from'], tl['to']))
o, m_note = text('note', LB['note'], -2.98, 0.062, 1.0)
TXT.append((m_note, 0.0, SECS + 1))

# compositor: bloom for the real glow
try:
    ng = bpy.data.node_groups.new('comp', 'CompositorNodeTree'); sc.compositing_node_group = ng
    ng.interface.new_socket('Image', in_out='OUTPUT', socket_type='NodeSocketColor')
    rl = ng.nodes.new('CompositorNodeRLayers'); gl = ng.nodes.new('CompositorNodeGlare'); go = ng.nodes.new('NodeGroupOutput')
    gl.inputs['Type'].default_value = 'Bloom'
    for k, v in (('Threshold', 0.9), ('Strength', 0.85), ('Size', 0.8), ('Quality', 'High')):
        try: gl.inputs[k].default_value = v
        except Exception as e: print('glare param', k, e)
    ng.links.new(rl.outputs['Image'], gl.inputs['Image']); ng.links.new(gl.outputs['Image'], go.inputs[0])
except Exception as e:
    print('GLOW FAIL', repr(e))

# ------------------------------------------------------------------ bake keyframes
def key_xform(o, f, loc, s, q):
    o.location = loc; o.scale = (s, s, s); o.rotation_quaternion = q
    o.keyframe_insert('location', frame=f); o.keyframe_insert('scale', frame=f); o.keyframe_insert('rotation_quaternion', frame=f)

def key_val(mat, node, v, f):
    s = mat.node_tree.nodes[node].outputs[0]
    s.default_value = v
    s.keyframe_insert('default_value', frame=f)

def quat_z(a): return Quaternion((0, 0, 1), a)
def quat_x(a): return Quaternion((1, 0, 0), a)
IDQ = Quaternion((1, 0, 0, 0))
MINP, MINS = 0.013, 0.016
LINE = 0.004
ED = {}
earth_axis = quat_x(math.radians(23.4))

def rel(state_, P_):
    g = state_['g']
    d = sub(P_, state_['F'])
    return (d[0] * g, d[1] * g, d[2] * g)

def ring_update(name, center, R, st, quat, f, vis_extra=1.0, lo=0.03, hi=0.10):
    g = st['g']
    s_ = R * g
    a = smooth(lo, hi, s_) * (1 - smooth(2.0, 4.5, s_)) * vis_extra
    o = OBJ[name]
    if a < 0.003:
        key_xform(o, f, (0, 0, 0), 0.0, IDQ)
        o.data.keyframe_insert('bevel_depth', frame=f)
        key_val(o.data.materials[0], 'vis', 0.0, f)
        return 0.0
    c = rel(st, center)
    o.data.bevel_depth = LINE / s_
    o.data.keyframe_insert('bevel_depth', frame=f)
    key_xform(o, f, c, s_, quat)
    key_val(o.data.materials[0], 'vis', a, f)
    return a

def trail_update(name, center, R, st, tilt_q, angle, f, start, vis_extra=1.0, lo=0.03, hi=0.10):
    """bright arc that trails the body along its orbit (bevel factor 'start'..1 of a circle rotated so the body sits at its end)"""
    g = st['g']
    s_ = R * g
    a = smooth(lo, hi, s_) * (1 - smooth(2.0, 4.5, s_)) * vis_extra
    o = OBJ[name]
    if a < 0.003:
        key_xform(o, f, (0, 0, 0), 0.0, IDQ)
        o.data.keyframe_insert('bevel_depth', frame=f)
        key_val(o.data.materials[0], 'vis', 0.0, f)
        return
    o.data.bevel_depth = LINE * 1.15 / s_
    o.data.bevel_factor_start = start
    o.data.bevel_factor_end = 1.0
    o.data.keyframe_insert('bevel_depth', frame=f); o.data.keyframe_insert('bevel_factor_start', frame=f)
    key_xform(o, f, rel(st, center), s_, tilt_q @ quat_z(angle))
    key_val(o.data.materials[0], 'vis', a, f)


for f in range(1, NF + 1):
    st = state(f)
    t, T, g, H, pos = st['t'], st['T'], st['g'], st['H'], st['pos']
    # camera
    e = math.radians(elev_f(t)); az = math.radians(azim_f(t))
    cx, cy, cz = 0.0, -CAMD * math.cos(e), CAMD * math.sin(e)
    cp = Vector((cx * math.cos(az) - cy * math.sin(az), cx * math.sin(az) + cy * math.cos(az), cz))
    cam.location = cp
    cam.rotation_quaternion = (-cp).to_track_quat('-Z', 'Y')
    cam.keyframe_insert('location', frame=f); cam.keyframe_insert('rotation_quaternion', frame=f)

    # Sun
    sun_p = rel(st, pos['sun'])
    rs = BD['sun_radius_B'] * UB * g
    rsv = max(rs, MINS)
    key_xform(OBJ['sun'], f, sun_p, rsv, quat_z(T * 0.2))
    hv = 1.0
    key_xform(OBJ['halo'], f, sun_p, min(rsv * 4.5, 1.2) if rsv < 0.3 else rsv * 2.0, IDQ)
    key_val(OBJ['halo'].data.materials[0], 'vis', hv, f)
    # light: real inverse-square falloff, scaled with the world so Earth irradiance stays constant
    ldir = Vector(sub(pos['earth'], pos['sun']))   # sunlight travels Sun -> Earth (all planets share it; they are dots)
    lamp.rotation_quaternion = ldir.to_track_quat('-Z', 'Y')
    lamp.keyframe_insert('rotation_quaternion', frame=f)

    # planets
    for p in BD['planets']:
        n = p['name']
        R = p['orbit_B'] * UB
        a = ring_update('ring_' + n, pos['sun'], R, st, IDQ, f)
        vin = smooth(0.03, 0.10, R * g)
        if n == 'earth':
            continue
        r = max(0.0, BD.get(n + '_radius', 0.9)) * g
        rv = max(r, MINP) * vin
        key_xform(OBJ[n], f, rel(st, pos[n]), rv, IDQ)
        key_xform(OBJ['pglow_' + n], f, rel(st, pos[n]), rv * 4.5 if vin > 0.01 else 0.0, IDQ)
        key_val(OBJ['pglow_' + n].data.materials[0], 'vis', 0.85 * vin, f)

    # earth + shells
    ep = rel(st, pos['earth'])
    R_e = PL['earth']['orbit_B'] * UB
    vin_e = smooth(0.03, 0.10, R_e * g)
    re_ = max(BD['earth_radius'] * g, MINP * 1.2) * vin_e
    spin = 2 * math.pi * T / 0.99727 + math.radians(BD['earth_spin0_deg'])
    q = earth_axis @ quat_z(spin)
    key_xform(OBJ['earth'], f, ep, re_, q)
    gl_e = (1 - smooth(MINP * 2, MINP * 6, BD['earth_radius'] * g)) * vin_e
    key_xform(OBJ['pglow_earth'], f, ep, re_ * 4.5 if gl_e > 0.01 else 0.0, IDQ)
    key_val(OBJ['pglow_earth'].data.materials[0], 'vis', 0.9 * gl_e, f)
    trail_update('trail_earth', pos['sun'], R_e, st, IDQ,
                 math.radians(PL['earth']['phase_deg']) + 2 * math.pi * T / PL['earth']['period_days'], f, 0.45)
    big = smooth(0.02, 0.08, BD['earth_radius'] * g)
    key_xform(OBJ['clouds'], f, ep, re_ * 1.010 * (1 if big > 0.01 else 0), earth_axis @ quat_z(spin * 0.985 + 0.4))
    key_xform(OBJ['atm'], f, ep, re_ * 1.045 * (1 if big > 0.01 else 0), IDQ)

    # moon + ring
    ma = ring_update('ring_moon', pos['earth'], BD['moon_orbit'], st,
                     quat_x(math.radians(BD['moon_incl_deg'])), f, lo=0.012, hi=0.05)
    mph = math.radians(BD['moon_phase_deg']) + 2 * math.pi * T / BD['moon_period_days']
    trail_update('trail_moon', pos['earth'], BD['moon_orbit'], st, quat_x(math.radians(BD['moon_incl_deg'])), mph, f, 0.62, lo=0.012, hi=0.05)
    mr_ = max(BD['moon_radius'] * g, MINP * 0.8) * smooth(0.012, 0.05, BD['moon_orbit'] * g)
    key_xform(OBJ['moon'], f, rel(st, pos['moon']), mr_, quat_z(mph + math.pi))

    # sun's galactic orbit ring + disc
    Rg = BD['sun_galactic_radius_C'] * UC
    ring_update('ring_sun_gal', pos['galaxy'], Rg, st, quat_x(TILT), f, vis_extra=smooth(7000, 16000, H) , lo=0.2, hi=0.5)
    gv = smooth(7000.0, 24000.0, H)
    trail_update('trail_sun_gal', pos['galaxy'], Rg, st, quat_x(TILT), st['alpha'], f, 0.62,
                 vis_extra=smooth(7000, 16000, H), lo=0.2, hi=0.5)
    spin_g = BD['disc_spin_fraction'] * (st['alpha'] - math.radians(BD['sun_phase_deg'])) + math.radians(20)
    gq = quat_x(TILT) @ quat_z(spin_g)
    gsz = BD['galaxy_disc_radius_C'] * UC * g
    if gv < 0.003: gsz = 0.0
    key_xform(OBJ['galaxy'], f, rel(st, pos['galaxy']), gsz, gq)
    key_val(OBJ['galaxy'].data.materials[0], 'vis', gv, f)
    key_val(OBJ['galaxy'].data.materials[0], 'sharp', smooth(18000.0, 34000.0, H), f)
    nrm = gq @ Vector((0, 0, 1))
    for hn, off, rot, k in (('haze_a', 0.05, 0.10, 1.08), ('haze_b', -0.05, -0.14, 0.95)):
        o = OBJ[hn]
        c = Vector(rel(st, pos['galaxy'])) + nrm * (off * gsz)
        key_xform(o, f, tuple(c), gsz * k if gsz else 0.0, quat_x(TILT) @ quat_z(spin_g + rot))
        key_val(o.data.materials[0], 'vis', gv * 0.30, f)
    # text
    for (m, t0, t1) in TXT:
        a = smooth(t0, t0 + 0.4, t) * (1 - smooth(t1 - 0.4, t1, t)) if m is not m_note else 0.45 * smooth(0.0, 1.0, t)
        key_val(m, 'vis', a, f)

# world MW band fade keyed on the world node tree
if env is not None:
    for f in range(1, NF + 1):
        st = state(f)
        v = 0.55 * (1 - smooth(6000.0, 22000.0, st['H']))
        s_ = w.node_tree.nodes['mw'].outputs[0]; s_.default_value = v
        s_.keyframe_insert('default_value', frame=f)

# ------------------------------------------------------------------ save / render
sc.frame_set(1)
if args.pack:
    bpy.ops.file.pack_all()
os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=args.out, compress=True)
print('saved', args.out, 'frames', NF, 'res', res)

if args.stills:
    d = args.stills_dir or os.path.dirname(args.out)
    os.makedirs(d, exist_ok=True)
    sc.render.image_settings.file_format = 'PNG'
    for fr in [int(x) for x in args.stills.split(',')]:
        sc.frame_set(fr)
        sc.render.filepath = os.path.join(d, f'still_{fr:04d}.png')
        bpy.ops.render.render(write_still=True)
        print('still', fr)
if args.video:
    sc.render.image_settings.media_type = 'VIDEO'
    sc.render.image_settings.file_format = 'FFMPEG'
    sc.render.ffmpeg.format = 'MPEG4'; sc.render.ffmpeg.codec = 'H264'
    sc.render.ffmpeg.constant_rate_factor = 'HIGH'
    sc.render.filepath = args.video
    if args.frames:
        a, b = args.frames.split('-'); sc.frame_start, sc.frame_end = int(a), int(b)
    bpy.ops.render.render(animation=True)
