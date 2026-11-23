import bpy, math, random, sys
from mathutils import Vector
a = sys.argv[sys.argv.index('--') + 1:]
secs = float(a[0]); out = a[1]; still = len(a) > 2
fps = 24; N = int(fps * secs); random.seed(7)
bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene; sc.frame_start = 1; sc.frame_end = N; sc.render.fps = fps
sc.render.engine = 'BLENDER_EEVEE'; sc.view_settings.view_transform = 'AgX'
sc.render.resolution_x = 960; sc.render.resolution_y = 540
if still:
    sc.render.image_settings.file_format = 'PNG'
else:
    sc.render.image_settings.media_type = 'VIDEO'; sc.render.image_settings.file_format = 'FFMPEG'
    sc.render.ffmpeg.format = 'MPEG4'; sc.render.ffmpeg.codec = 'H264'
sc.render.filepath = out

# world: black sky with stars (noise threshold)
w = bpy.data.worlds.new('w'); sc.world = w; w.use_nodes = True; nt = w.node_tree; nt.nodes.clear()
tc = nt.nodes.new('ShaderNodeTexCoord'); no = nt.nodes.new('ShaderNodeTexNoise')
no.inputs['Scale'].default_value = 300; no.inputs['Detail'].default_value = 0
cr = nt.nodes.new('ShaderNodeValToRGB'); cr.color_ramp.elements[0].position = 0.87; cr.color_ramp.elements[1].position = 0.88
bg = nt.nodes.new('ShaderNodeBackground'); bg.inputs['Strength'].default_value = 3.0
wo = nt.nodes.new('ShaderNodeOutputWorld')
nt.links.new(tc.outputs['Generated'], no.inputs['Vector']); nt.links.new(no.outputs['Fac'], cr.inputs['Fac'])
nt.links.new(cr.outputs['Color'], bg.inputs['Color']); nt.links.new(bg.outputs['Background'], wo.inputs['Surface'])


def mk(name):
    m = bpy.data.materials.new(name); m.use_nodes = True; return m


def emit(name, col, s):
    m = mk(name); b = m.node_tree.nodes['Principled BSDF']; b.inputs['Base Color'].default_value = (0, 0, 0, 1)
    b.inputs['Emission Color'].default_value = col; b.inputs['Emission Strength'].default_value = s; return m


# Earth
bpy.ops.mesh.primitive_uv_sphere_add(radius=2, segments=96, ring_count=48, location=(0, 0, 0)); E = bpy.context.object
bpy.ops.object.shade_smooth()
m = mk('earth'); t = m.node_tree; b = t.nodes['Principled BSDF']
n1 = t.nodes.new('ShaderNodeTexNoise'); n1.inputs['Scale'].default_value = 2.2; n1.inputs['Detail'].default_value = 10
r = t.nodes.new('ShaderNodeValToRGB'); e = r.color_ramp.elements
e[0].position = 0.47; e[0].color = (0.01, 0.05, 0.25, 1); e[1].position = 0.52; e[1].color = (0.08, 0.22, 0.06, 1)
t.links.new(n1.outputs['Fac'], r.inputs['Fac']); t.links.new(r.outputs['Color'], b.inputs['Base Color'])
b.inputs['Roughness'].default_value = 0.55
E.data.materials.append(m)

# clouds
bpy.ops.mesh.primitive_uv_sphere_add(radius=2.03, segments=64, ring_count=32); C = bpy.context.object
bpy.ops.object.shade_smooth()
m = mk('clouds'); t = m.node_tree; b = t.nodes['Principled BSDF']
n = t.nodes.new('ShaderNodeTexNoise'); n.inputs['Scale'].default_value = 4; n.inputs['Detail'].default_value = 12
r = t.nodes.new('ShaderNodeValToRGB'); r.color_ramp.elements[0].position = 0.5; r.color_ramp.elements[1].position = 0.68
t.links.new(n.outputs['Fac'], r.inputs['Fac']); t.links.new(r.outputs['Color'], b.inputs['Alpha'])
b.inputs['Base Color'].default_value = (1, 1, 1, 1); b.inputs['Roughness'].default_value = 1
C.data.materials.append(m); m.surface_render_method = 'BLENDED'
C.rotation_euler[2] = 0; C.keyframe_insert('rotation_euler', frame=1)
C.rotation_euler[2] = 0.35; C.keyframe_insert('rotation_euler', frame=N)

# atmosphere glow
bpy.ops.mesh.primitive_uv_sphere_add(radius=2.22, segments=64, ring_count=32); A = bpy.context.object
bpy.ops.object.shade_smooth()
m = mk('atmo'); m.surface_render_method = 'BLENDED'; t = m.node_tree; t.nodes.clear()
lw = t.nodes.new('ShaderNodeLayerWeight'); lw.inputs['Blend'].default_value = 0.25
em = t.nodes.new('ShaderNodeEmission'); em.inputs['Color'].default_value = (0.25, 0.55, 1, 1); em.inputs['Strength'].default_value = 3
tr = t.nodes.new('ShaderNodeBsdfTransparent'); mx = t.nodes.new('ShaderNodeMixShader'); o = t.nodes.new('ShaderNodeOutputMaterial')
t.links.new(lw.outputs['Facing'], mx.inputs['Fac']); t.links.new(em.outputs[0], mx.inputs[2])
t.links.new(tr.outputs[0], mx.inputs[1]); t.links.new(mx.outputs[0], o.inputs['Surface'])
A.data.materials.append(m)

# the star and the Dyson swarm (far left)
SP = Vector((-16, 3, 2.5))
bpy.ops.mesh.primitive_uv_sphere_add(radius=1.6, location=SP); S = bpy.context.object
S.data.materials.append(emit('star', (1, 0.8, 0.5, 1), 25))
bpy.ops.object.light_add(type='SUN'); L = bpy.context.object; L.data.energy = 6; L.data.color = (1, 0.9, 0.75)
L.rotation_euler = (math.radians(85), 0, math.radians(-80))
pm = mk('panel'); b = pm.node_tree.nodes['Principled BSDF']
b.inputs['Base Color'].default_value = (0.03, 0.025, 0.02, 1); b.inputs['Metallic'].default_value = 1; b.inputs['Roughness'].default_value = 0.3
b.inputs['Emission Color'].default_value = (1, 0.4, 0.1, 1); b.inputs['Emission Strength'].default_value = 0.6
swarm = bpy.data.objects.new('swarm', None); bpy.context.collection.objects.link(swarm); swarm.location = SP
bpy.ops.mesh.primitive_cube_add(size=1); proto = bpy.context.object
panels = []
for i in range(500):
    v = Vector((random.gauss(0, 1), random.gauss(0, 1), random.gauss(0, 1))).normalized() * random.uniform(3.2, 4.6)
    o = proto.copy(); o.data = proto.data.copy(); bpy.context.collection.objects.link(o)
    o.parent = swarm; o.location = v
    o.scale = (random.uniform(.25, .6), random.uniform(.25, .6), 0.03)
    o.rotation_euler = (random.random() * 6, random.random() * 6, random.random() * 6)
    o.data.materials.append(pm); panels.append(o)
bpy.data.objects.remove(proto)
swarm.keyframe_insert('rotation_euler', frame=1)
swarm.rotation_euler = (0.2, 0.9, 0.1); swarm.keyframe_insert('rotation_euler', frame=N)
bpy.context.view_layer.update()

# beams from swarm panels to the Earth surface, with impact flashes
bm = emit('beam', (1, 0.15, 0.05, 1), 10); fm = emit('flash', (1, 0.5, 0.2, 1), 40)


def beam(src, dst, t0, t1):
    d = dst - src; ln = d.length
    bpy.ops.mesh.primitive_cylinder_add(radius=0.018, depth=1, vertices=12); c = bpy.context.object
    c.data.materials.append(bm)
    c.rotation_euler = d.to_track_quat('Z', 'Y').to_euler()
    c.scale = (1, 1, 0.0001); c.location = src; c.keyframe_insert('scale', frame=t0); c.keyframe_insert('location', frame=t0)
    c.scale = (1, 1, ln); c.location = src + d * 0.5
    c.keyframe_insert('scale', frame=t1); c.keyframe_insert('location', frame=t1)
    c.keyframe_insert('scale', frame=N); c.keyframe_insert('location', frame=N)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.06, location=dst); f = bpy.context.object
    f.data.materials.append(fm)
    f.scale = (0.001, 0.001, 0.001); f.keyframe_insert('scale', frame=t1)
    f.scale = (1, 1, 1); f.keyframe_insert('scale', frame=t1 + 8)
    f.scale = (2.5, 2.5, 2.5); f.keyframe_insert('scale', frame=N)


targets = [(-1.9, 0.3, 0.6), (-1.6, 0.2, -1.0), (-1.3, 0.9, 1.1), (-1.8, -0.5, -0.2)]
for k, tg in enumerate(targets):
    dst = Vector(tg).normalized() * 2.02
    src = panels[k * 37].matrix_world.translation.copy()
    t0 = int(N * (0.15 + 0.17 * k)); beam(src, dst, t0, t0 + int(fps * 1.2))

# camera: slow arc, always looking at the space between Earth and swarm
bpy.ops.object.camera_add(location=(5, -13, 2.5)); cam = bpy.context.object; sc.camera = cam; cam.data.lens = 32
tgt = bpy.data.objects.new('t', None); bpy.context.collection.objects.link(tgt); tgt.location = (-6, 0, 1.0)
cn = cam.constraints.new('TRACK_TO'); cn.target = tgt; cn.track_axis = 'TRACK_NEGATIVE_Z'; cn.up_axis = 'UP_Y'
cam.keyframe_insert('location', frame=1); cam.location = (-1, -11, 1.5); cam.keyframe_insert('location', frame=N)

# glow
try:
    ng = bpy.data.node_groups.new('comp', 'CompositorNodeTree'); sc.compositing_node_group = ng
    ng.interface.new_socket('Image', in_out='OUTPUT', socket_type='NodeSocketColor')
    rl = ng.nodes.new('CompositorNodeRLayers'); gl = ng.nodes.new('CompositorNodeGlare'); go = ng.nodes.new('NodeGroupOutput')
    for k, v in (('Type', 'Fog Glow'), ('Threshold', 0.8), ('Strength', 1.0), ('Size', 7)):
        try:
            gl.inputs[k].default_value = v
        except Exception:
            pass
    ng.links.new(rl.outputs['Image'], gl.inputs['Image']); ng.links.new(gl.outputs['Image'], go.inputs[0])
except Exception as ex:
    print('GLOW FAIL', repr(ex))

if still:
    sc.frame_set(int(a[2])); bpy.ops.render.render(write_still=True)
else:
    bpy.ops.render.render(animation=True)
