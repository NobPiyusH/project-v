import bpy, math, random, sys, os
from mathutils import Vector
a = sys.argv[sys.argv.index('--') + 1:]
secs = float(a[0]); out = a[1]; still = len(a) > 2
fps = 24; N = int(fps * secs); random.seed(7)
bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene; sc.frame_start = 1; sc.frame_end = N; sc.render.fps = fps
sc.render.engine = os.environ.get('BL_ENGINE', 'BLENDER_EEVEE'); sc.view_settings.view_transform = 'AgX'
sc.render.resolution_x = 3840; sc.render.resolution_y = 2160
if still:
    sc.render.image_settings.file_format = 'PNG'
else:
    sc.render.image_settings.media_type = 'VIDEO'; sc.render.image_settings.file_format = 'FFMPEG'
    sc.render.ffmpeg.format = 'MPEG4'; sc.render.ffmpeg.codec = 'H264'; sc.render.ffmpeg.constant_rate_factor = 'HIGH'
sc.render.filepath = out

if sc.render.engine == 'CYCLES':
    sc.cycles.device = 'GPU'; sc.cycles.samples = 96; sc.cycles.use_denoising = True
    try:
        pr = bpy.context.preferences.addons['cycles'].preferences
        pr.compute_device_type = 'OPTIX'; pr.get_devices()
        for d in pr.devices: d.use = d.type != 'CPU'
    except Exception as ex:
        print('cycles prefs', ex)
# ---- quality settings (each guarded: names differ between Blender versions)
ev = sc.eevee
for k, v in (('taa_render_samples', 96), ('use_raytracing', True), ('use_shadows', True), ('shadow_ray_count', 2),
             ('shadow_step_count', 8), ('use_volumetric_shadows', True), ('fast_gi_method', 'GLOBAL_ILLUMINATION')):
    try:
        setattr(ev, k, v)
    except Exception:
        print('skip eevee', k)
for k, v in (('use_motion_blur', True), ('motion_blur_shutter', 0.4), ('filter_size', 1.0)):
    try:
        setattr(sc.render, k, v)
    except Exception:
        print('skip render', k)
try:
    sc.view_settings.look = 'AgX - Medium High Contrast'
except Exception:
    pass

# ---- world: stars with some size/brightness variety
w = bpy.data.worlds.new('w'); sc.world = w; w.use_nodes = True; nt = w.node_tree; nt.nodes.clear()
tc = nt.nodes.new('ShaderNodeTexCoord'); no = nt.nodes.new('ShaderNodeTexNoise')
no.inputs['Scale'].default_value = 380; no.inputs['Detail'].default_value = 0
cr = nt.nodes.new('ShaderNodeValToRGB'); cr.color_ramp.elements[0].position = 0.86; cr.color_ramp.elements[1].position = 0.9
bg = nt.nodes.new('ShaderNodeBackground'); bg.inputs['Strength'].default_value = 4.0
n2 = nt.nodes.new('ShaderNodeTexNoise'); n2.inputs['Scale'].default_value = 1.6; n2.inputs['Detail'].default_value = 6   # faint nebula
c2 = nt.nodes.new('ShaderNodeValToRGB'); c2.color_ramp.elements[0].position = 0.45; c2.color_ramp.elements[0].color = (0, 0, 0, 1)
c2.color_ramp.elements[1].position = 0.8; c2.color_ramp.elements[1].color = (0.05, 0.015, 0.03, 1)
add = nt.nodes.new('ShaderNodeMix'); add.data_type = 'RGBA'; add.blend_type = 'ADD'; add.inputs['Factor'].default_value = 1.0
wo = nt.nodes.new('ShaderNodeOutputWorld')
nt.links.new(tc.outputs['Generated'], no.inputs['Vector']); nt.links.new(no.outputs['Fac'], cr.inputs['Fac'])
nt.links.new(tc.outputs['Generated'], n2.inputs['Vector']); nt.links.new(n2.outputs['Fac'], c2.inputs['Fac'])
nt.links.new(cr.outputs['Color'], add.inputs[6]); nt.links.new(c2.outputs['Color'], add.inputs[7])
nt.links.new(add.outputs[2], bg.inputs['Color']); nt.links.new(bg.outputs['Background'], wo.inputs['Surface'])


def mk(name):
    m = bpy.data.materials.new(name); m.use_nodes = True; return m


def emit(name, col, s):
    m = mk(name); b = m.node_tree.nodes['Principled BSDF']; b.inputs['Base Color'].default_value = (0, 0, 0, 1)
    b.inputs['Emission Color'].default_value = col; b.inputs['Emission Strength'].default_value = s; return m


def ramp(t, positions_colors):
    r = t.nodes.new('ShaderNodeValToRGB')
    while len(r.color_ramp.elements) < len(positions_colors):
        r.color_ramp.elements.new(0.5)
    for el, (p, c) in zip(r.color_ramp.elements, positions_colors):
        el.position = p; el.color = c
    return r


# ---- Earth: reflective ocean, bumpy land, night-side city lights
bpy.ops.mesh.primitive_uv_sphere_add(radius=2, segments=128, ring_count=64, location=(0, 0, 0)); E = bpy.context.object
bpy.ops.object.shade_smooth()
m = mk('earth'); t = m.node_tree; b = t.nodes['Principled BSDF']
n1 = t.nodes.new('ShaderNodeTexNoise'); n1.inputs['Scale'].default_value = 2.2; n1.inputs['Detail'].default_value = 12
col = ramp(t, [(0.47, (0.01, 0.06, 0.28, 1)), (0.50, (0.35, 0.3, 0.15, 1)), (0.56, (0.08, 0.24, 0.06, 1)), (0.8, (0.35, 0.32, 0.28, 1))])
land = ramp(t, [(0.495, (0.0, 0.0, 0.0, 1)), (0.505, (1, 1, 1, 1))])
rough = ramp(t, [(0.495, (0.08, 0.08, 0.08, 1)), (0.505, (0.75, 0.75, 0.75, 1))])
for r_ in (col, land, rough):
    t.links.new(n1.outputs['Fac'], r_.inputs['Fac'])
t.links.new(col.outputs['Color'], b.inputs['Base Color']); t.links.new(rough.outputs['Color'], b.inputs['Roughness'])
bn = t.nodes.new('ShaderNodeTexNoise'); bn.inputs['Scale'].default_value = 45; bn.inputs['Detail'].default_value = 14
bump = t.nodes.new('ShaderNodeBump'); bump.inputs['Strength'].default_value = 0.5; bump.inputs['Distance'].default_value = 0.02
t.links.new(bn.outputs['Fac'], bump.inputs['Height']); t.links.new(land.outputs['Color'], bump.inputs['Height']) if False else None
t.links.new(bump.outputs['Normal'], b.inputs['Normal'])
ln = t.nodes.new('ShaderNodeTexNoise'); ln.inputs['Scale'].default_value = 70; ln.inputs['Detail'].default_value = 4
lr = ramp(t, [(0.62, (0, 0, 0, 1)), (0.66, (1.0, 0.7, 0.35, 1))])
t.links.new(ln.outputs['Fac'], lr.inputs['Fac'])
lm = t.nodes.new('ShaderNodeMix'); lm.data_type = 'RGBA'; lm.blend_type = 'MULTIPLY'; lm.inputs['Factor'].default_value = 1.0
t.links.new(lr.outputs['Color'], lm.inputs[6]); t.links.new(land.outputs['Color'], lm.inputs[7])
t.links.new(lm.outputs[2], b.inputs['Emission Color']); b.inputs['Emission Strength'].default_value = 0.15
E.data.materials.append(m)

# two cloud layers drifting at slightly different speeds
for k, (rad, scl, spin) in enumerate(((2.03, 4.0, 0.35), (2.06, 7.0, 0.55))):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=rad, segments=96, ring_count=48); C = bpy.context.object
    bpy.ops.object.shade_smooth()
    m = mk('clouds%d' % k); t = m.node_tree; b = t.nodes['Principled BSDF']
    n = t.nodes.new('ShaderNodeTexNoise'); n.inputs['Scale'].default_value = scl; n.inputs['Detail'].default_value = 14
    r = ramp(t, [(0.52, (0, 0, 0, 1)), (0.7, (1, 1, 1, 1))])
    t.links.new(n.outputs['Fac'], r.inputs['Fac']); t.links.new(r.outputs['Color'], b.inputs['Alpha'])
    b.inputs['Base Color'].default_value = (1, 1, 1, 1); b.inputs['Roughness'].default_value = 1
    C.data.materials.append(m); m.surface_render_method = 'BLENDED'
    C.rotation_euler[2] = 0; C.keyframe_insert('rotation_euler', frame=1)
    C.rotation_euler[2] = spin; C.keyframe_insert('rotation_euler', frame=N)

# atmosphere glow
bpy.ops.mesh.primitive_uv_sphere_add(radius=2.24, segments=96, ring_count=48); A = bpy.context.object
bpy.ops.object.shade_smooth()
m = mk('atmo'); m.surface_render_method = 'BLENDED'; t = m.node_tree; t.nodes.clear()
lw = t.nodes.new('ShaderNodeLayerWeight'); lw.inputs['Blend'].default_value = 0.22
em = t.nodes.new('ShaderNodeEmission'); em.inputs['Color'].default_value = (0.22, 0.5, 1, 1); em.inputs['Strength'].default_value = 3.5
tr = t.nodes.new('ShaderNodeBsdfTransparent'); mx = t.nodes.new('ShaderNodeMixShader'); o = t.nodes.new('ShaderNodeOutputMaterial')
t.links.new(lw.outputs['Facing'], mx.inputs['Fac']); t.links.new(em.outputs[0], mx.inputs[2])
t.links.new(tr.outputs[0], mx.inputs[1]); t.links.new(mx.outputs[0], o.inputs['Surface'])
A.data.materials.append(m)

# ---- the star (granulated surface + corona) and the Dyson swarm
SP = Vector((-16, 3, 2.5))
bpy.ops.mesh.primitive_uv_sphere_add(radius=1.6, segments=96, ring_count=48, location=SP); S = bpy.context.object
m = mk('star'); t = m.node_tree; t.nodes.clear()
tcn = t.nodes.new('ShaderNodeTexCoord'); sn = t.nodes.new('ShaderNodeTexNoise'); sn.inputs['Scale'].default_value = 7; sn.inputs['Detail'].default_value = 12
sr = ramp(t, [(0.3, (1, 0.35, 0.05, 1)), (0.7, (1, 0.95, 0.65, 1))])
se = t.nodes.new('ShaderNodeEmission'); se.inputs['Strength'].default_value = 28; so = t.nodes.new('ShaderNodeOutputMaterial')
t.links.new(tcn.outputs['Object'], sn.inputs['Vector']); t.links.new(sn.outputs['Fac'], sr.inputs['Fac'])
t.links.new(sr.outputs['Color'], se.inputs['Color']); t.links.new(se.outputs[0], so.inputs['Surface'])
S.data.materials.append(m)
bpy.ops.mesh.primitive_uv_sphere_add(radius=2.4, segments=64, ring_count=32, location=SP); K = bpy.context.object
m = mk('corona'); m.surface_render_method = 'BLENDED'; t = m.node_tree; t.nodes.clear()
lw = t.nodes.new('ShaderNodeLayerWeight'); lw.inputs['Blend'].default_value = 0.35
em = t.nodes.new('ShaderNodeEmission'); em.inputs['Color'].default_value = (1, 0.5, 0.12, 1); em.inputs['Strength'].default_value = 5
tr = t.nodes.new('ShaderNodeBsdfTransparent'); mx = t.nodes.new('ShaderNodeMixShader'); o = t.nodes.new('ShaderNodeOutputMaterial')
t.links.new(lw.outputs['Facing'], mx.inputs['Fac']); t.links.new(em.outputs[0], mx.inputs[2])
t.links.new(tr.outputs[0], mx.inputs[1]); t.links.new(mx.outputs[0], o.inputs['Surface'])
K.data.materials.append(m)
bpy.ops.object.light_add(type='SUN'); L = bpy.context.object; L.data.energy = 6; L.data.color = (1, 0.9, 0.75)
L.data.angle = math.radians(1.5)
L.rotation_euler = (math.radians(85), 0, math.radians(-80))

# panel material: solar-cell grid with golden seams that glow faintly
pm = mk('panel'); t = pm.node_tree; b = t.nodes['Principled BSDF']
tcn = t.nodes.new('ShaderNodeTexCoord'); br = t.nodes.new('ShaderNodeTexBrick'); br.inputs['Scale'].default_value = 7
br.inputs['Mortar Size'].default_value = 0.04; br.inputs['Color1'].default_value = (0.01, 0.02, 0.07, 1); br.inputs['Color2'].default_value = (0.02, 0.035, 0.11, 1)
br.inputs['Mortar'].default_value = (0.35, 0.2, 0.05, 1)
t.links.new(tcn.outputs['Object'], br.inputs['Vector']); t.links.new(br.outputs['Color'], b.inputs['Base Color'])
b.inputs['Metallic'].default_value = 0.9; b.inputs['Roughness'].default_value = 0.22
inv = t.nodes.new('ShaderNodeMath'); inv.operation = 'SUBTRACT'; inv.inputs[0].default_value = 1.0
mul = t.nodes.new('ShaderNodeMath'); mul.operation = 'MULTIPLY'; mul.inputs[1].default_value = 0.7
t.links.new(br.outputs['Fac'], inv.inputs[1]); t.links.new(inv.outputs[0], mul.inputs[0])
b.inputs['Emission Color'].default_value = (1, 0.5, 0.12, 1); t.links.new(mul.outputs[0], b.inputs['Emission Strength'])
swarm = bpy.data.objects.new('swarm', None); bpy.context.collection.objects.link(swarm); swarm.location = SP
bpy.ops.mesh.primitive_cube_add(size=1); proto = bpy.context.object
panels = []
for i in range(650):
    v = Vector((random.gauss(0, 1), random.gauss(0, 1), random.gauss(0, 1))).normalized() * random.uniform(3.2, 5.0)
    o = proto.copy(); o.data = proto.data.copy(); bpy.context.collection.objects.link(o)
    o.parent = swarm; o.location = v
    o.scale = (random.uniform(.25, .7), random.uniform(.25, .7), 0.035)
    o.rotation_euler = (random.random() * 6, random.random() * 6, random.random() * 6)
    o.data.materials.append(pm); panels.append(o)
bpy.data.objects.remove(proto)
swarm.keyframe_insert('rotation_euler', frame=1)
swarm.rotation_euler = (0.2, 0.9, 0.1); swarm.keyframe_insert('rotation_euler', frame=N)
bpy.context.view_layer.update()

# ---- beams (white core inside a red glow) from swarm panels to Earth, with flash + shockwave ring
core_m = emit('core', (1, 0.9, 0.8, 1), 60); glow_m = emit('beam', (1, 0.12, 0.04, 1), 9); fm = emit('flash', (1, 0.6, 0.25, 1), 45)
ring_m = emit('ring', (1, 0.55, 0.2, 1), 12)


def tube(src, dst, rad, mat, t0, t1):
    d = dst - src; ln = d.length
    bpy.ops.mesh.primitive_cylinder_add(radius=rad, depth=1, vertices=16); c = bpy.context.object
    c.data.materials.append(mat); c.rotation_euler = d.to_track_quat('Z', 'Y').to_euler()
    c.scale = (1, 1, 0.0001); c.location = src; c.keyframe_insert('scale', frame=t0); c.keyframe_insert('location', frame=t0)
    c.scale = (1, 1, ln); c.location = src + d * 0.5
    c.keyframe_insert('scale', frame=t1); c.keyframe_insert('location', frame=t1)
    c.keyframe_insert('scale', frame=N); c.keyframe_insert('location', frame=N)


def beam(src, dst, t0, t1):
    tube(src, dst, 0.012, core_m, t0, t1); tube(src, dst, 0.04, glow_m, t0, t1)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.07, location=dst); f = bpy.context.object; f.data.materials.append(fm)
    f.scale = (0.001,) * 3; f.keyframe_insert('scale', frame=t1)
    f.scale = (1, 1, 1); f.keyframe_insert('scale', frame=t1 + 6)
    f.scale = (2.2, 2.2, 2.2); f.keyframe_insert('scale', frame=N)
    bpy.ops.mesh.primitive_torus_add(major_radius=0.1, minor_radius=0.008, location=dst); rg = bpy.context.object
    rg.data.materials.append(ring_m); rg.rotation_euler = dst.normalized().to_track_quat('Z', 'Y').to_euler()
    rg.scale = (0.001,) * 3; rg.keyframe_insert('scale', frame=t1)
    rg.scale = (9, 9, 9); rg.keyframe_insert('scale', frame=min(N, t1 + 26))
    rg.scale = (0.001,) * 3; rg.keyframe_insert('scale', frame=min(N, t1 + 28))


targets = [(-1.9, 0.3, 0.6), (-1.6, 0.2, -1.0), (-1.3, 0.9, 1.1), (-1.8, -0.5, -0.2)]
for k, tg in enumerate(targets):
    dst = Vector(tg).normalized() * 2.02
    src = panels[k * 37].matrix_world.translation.copy()
    t0 = int(N * (0.15 + 0.17 * k)); beam(src, dst, t0, t0 + int(fps * 1.2))

# ---- camera: slow arc, shallow depth of field on Earth
bpy.ops.object.camera_add(location=(5, -13, 2.5)); cam = bpy.context.object; sc.camera = cam; cam.data.lens = 32
tgt = bpy.data.objects.new('t', None); bpy.context.collection.objects.link(tgt); tgt.location = (-6, 0, 1.0)
cn = cam.constraints.new('TRACK_TO'); cn.target = tgt; cn.track_axis = 'TRACK_NEGATIVE_Z'; cn.up_axis = 'UP_Y'
cam.keyframe_insert('location', frame=1); cam.location = (-1, -11, 1.5); cam.keyframe_insert('location', frame=N)
try:
    cam.data.dof.use_dof = True; cam.data.dof.focus_object = E; cam.data.dof.aperture_fstop = 11
except Exception:
    print('skip dof')

# ---- glow
try:
    ng = bpy.data.node_groups.new('comp', 'CompositorNodeTree'); sc.compositing_node_group = ng
    ng.interface.new_socket('Image', in_out='OUTPUT', socket_type='NodeSocketColor')
    rl = ng.nodes.new('CompositorNodeRLayers'); gl = ng.nodes.new('CompositorNodeGlare'); go = ng.nodes.new('NodeGroupOutput')
    for k, v in (('Type', 'Fog Glow'), ('Threshold', 0.9), ('Strength', 0.9), ('Size', 7)):
        try:
            gl.inputs[k].default_value = v
        except Exception:
            pass
    ng.links.new(rl.outputs['Image'], gl.inputs['Image']); ng.links.new(gl.outputs['Image'], go.inputs[0])
except Exception as ex:
    print('GLOW FAIL', repr(ex))

if os.environ.get('BL_RANGE'):
    a0, a1 = os.environ['BL_RANGE'].split(','); sc.frame_start = int(a0); sc.frame_end = int(a1)
if still:
    sc.frame_set(int(a[2])); bpy.ops.render.render(write_still=True)
else:
    bpy.ops.render.render(animation=True)
