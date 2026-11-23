import bpy, math, random, sys
from mathutils import Vector
a = sys.argv[sys.argv.index('--') + 1:]
which = a[0]; secs = float(a[1]); out = a[2]; still = len(a) > 3
fps = 24; N = int(fps * secs); random.seed(11)
bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene; sc.frame_start = 1; sc.frame_end = N; sc.render.fps = fps
sc.render.engine = 'BLENDER_EEVEE'; sc.view_settings.view_transform = 'AgX'
sc.render.resolution_x = 3840; sc.render.resolution_y = 2160
if still:
    sc.render.image_settings.file_format = 'PNG'
else:
    sc.render.image_settings.media_type = 'VIDEO'; sc.render.image_settings.file_format = 'FFMPEG'
    sc.render.ffmpeg.format = 'MPEG4'; sc.render.ffmpeg.codec = 'H264'
sc.render.filepath = out


def mk(name):
    m = bpy.data.materials.new(name); m.use_nodes = True; return m


def emit(name, col, s):
    m = mk(name); b = m.node_tree.nodes['Principled BSDF']; b.inputs['Base Color'].default_value = (0, 0, 0, 1)
    b.inputs['Emission Color'].default_value = col; b.inputs['Emission Strength'].default_value = s; return m


def world(star_lo=0.87, color=(0, 0, 0, 1), stars=True):
    w = bpy.data.worlds.new('w'); sc.world = w; w.use_nodes = True; nt = w.node_tree; nt.nodes.clear()
    bg = nt.nodes.new('ShaderNodeBackground'); wo = nt.nodes.new('ShaderNodeOutputWorld')
    if stars:
        tc = nt.nodes.new('ShaderNodeTexCoord'); no = nt.nodes.new('ShaderNodeTexNoise')
        no.inputs['Scale'].default_value = 300; no.inputs['Detail'].default_value = 0
        cr = nt.nodes.new('ShaderNodeValToRGB'); cr.color_ramp.elements[0].position = star_lo; cr.color_ramp.elements[1].position = star_lo + 0.01
        bg.inputs['Strength'].default_value = 3.0
        nt.links.new(tc.outputs['Generated'], no.inputs['Vector']); nt.links.new(no.outputs['Fac'], cr.inputs['Fac'])
        nt.links.new(cr.outputs['Color'], bg.inputs['Color'])
    else:
        bg.inputs['Color'].default_value = color; bg.inputs['Strength'].default_value = 1.0
    nt.links.new(bg.outputs['Background'], wo.inputs['Surface'])


def glow(thr=0.8, size=7):
    try:
        ng = bpy.data.node_groups.new('comp', 'CompositorNodeTree'); sc.compositing_node_group = ng
        ng.interface.new_socket('Image', in_out='OUTPUT', socket_type='NodeSocketColor')
        rl = ng.nodes.new('CompositorNodeRLayers'); gl = ng.nodes.new('CompositorNodeGlare'); go = ng.nodes.new('NodeGroupOutput')
        for k, v in (('Type', 'Fog Glow'), ('Threshold', thr), ('Strength', 1.0), ('Size', size)):
            try:
                gl.inputs[k].default_value = v
            except Exception:
                pass
        ng.links.new(rl.outputs['Image'], gl.inputs['Image']); ng.links.new(gl.outputs['Image'], go.inputs[0])
    except Exception as ex:
        print('GLOW FAIL', repr(ex))


def camera(p0, p1, target, lens=35):
    bpy.ops.object.camera_add(location=p0); cam = bpy.context.object; sc.camera = cam; cam.data.lens = lens
    tgt = bpy.data.objects.new('t', None); bpy.context.collection.objects.link(tgt); tgt.location = target
    cn = cam.constraints.new('TRACK_TO'); cn.target = tgt; cn.track_axis = 'TRACK_NEGATIVE_Z'; cn.up_axis = 'UP_Y'
    cam.keyframe_insert('location', frame=1); cam.location = p1; cam.keyframe_insert('location', frame=N)
    return cam, tgt


# ---------------------------------------------------------------- 1. black hole
def blackhole():
    world(0.88)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=1.3, segments=64, ring_count=32); bh = bpy.context.object
    bh.data.materials.append(emit('bh', (0, 0, 0, 1), 0))
    m = mk('disk'); t = m.node_tree; t.nodes.clear()
    tc = t.nodes.new('ShaderNodeTexCoord'); vl = t.nodes.new('ShaderNodeVectorMath'); vl.operation = 'LENGTH'
    mr = t.nodes.new('ShaderNodeMapRange'); mr.inputs['From Min'].default_value = 1.35; mr.inputs['From Max'].default_value = 6.0
    cr = t.nodes.new('ShaderNodeValToRGB'); e = cr.color_ramp.elements
    e[0].position = 0.0; e[0].color = (1, 0.95, 0.85, 1); e[1].position = 1.0; e[1].color = (0, 0, 0, 1)
    e2 = cr.color_ramp.elements.new(0.25); e2.color = (1, 0.5, 0.1, 1)
    e3 = cr.color_ramp.elements.new(0.6); e3.color = (0.5, 0.08, 0.02, 1)
    no = t.nodes.new('ShaderNodeTexNoise'); no.inputs['Scale'].default_value = 5; no.inputs['Detail'].default_value = 10
    nr = t.nodes.new('ShaderNodeValToRGB'); nr.color_ramp.elements[0].position = 0.3; nr.color_ramp.elements[1].position = 0.75
    mx = t.nodes.new('ShaderNodeMix'); mx.data_type = 'RGBA'; mx.blend_type = 'MULTIPLY'; mx.inputs['Factor'].default_value = 1.0
    em = t.nodes.new('ShaderNodeEmission'); em.inputs['Strength'].default_value = 7
    ou = t.nodes.new('ShaderNodeOutputMaterial')
    t.links.new(tc.outputs['Object'], vl.inputs[0]); t.links.new(vl.outputs['Value'], mr.inputs['Value'])
    t.links.new(mr.outputs['Result'], cr.inputs['Fac']); t.links.new(tc.outputs['Object'], no.inputs['Vector'])
    t.links.new(no.outputs['Fac'], nr.inputs['Fac'])
    t.links.new(cr.outputs['Color'], mx.inputs[6]); t.links.new(nr.outputs['Color'], mx.inputs[7])
    t.links.new(mx.outputs[2], em.inputs['Color']); t.links.new(em.outputs[0], ou.inputs['Surface'])
    def disc(tilt, r, z=0):
        bpy.ops.mesh.primitive_circle_add(vertices=128, radius=r, fill_type='TRIFAN', location=(0, 0, z))
        d = bpy.context.object; d.rotation_euler = (tilt, 0, 0); d.data.materials.append(m)
        d.rotation_euler[2] = 0
        return d
    d1 = disc(math.radians(78), 6.0)                      # the main disk, seen almost edge-on
    d2 = disc(math.radians(78) + math.pi / 2, 4.2)        # fake gravitational-lensing halo (tilted ring)
    for d in (d1, d2):
        d.keyframe_insert('rotation_euler', frame=1)
    # spin the texture: rotate the disk around its own normal
    for d in (d1, d2):
        d.rotation_mode = 'XYZ'
    d1.animation_data_clear(); d2.animation_data_clear()
    d1.rotation_euler = (math.radians(78), 0, 0); d1.keyframe_insert('rotation_euler', frame=1)
    d1.rotation_euler = (math.radians(78), 0, math.radians(120)); d1.keyframe_insert('rotation_euler', frame=N)
    camera((0, -27, 4.5), (0, -20, 2.5), (0, 0, 0), 32)
    glow(0.7, 9)


# ---------------------------------------------------------------- 2. meteor storm on a red planet
def meteors():
    world(0.87)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=3, segments=96, ring_count=48); P = bpy.context.object; bpy.ops.object.shade_smooth()
    m = mk('mars'); t = m.node_tree; b = t.nodes['Principled BSDF']
    n1 = t.nodes.new('ShaderNodeTexNoise'); n1.inputs['Scale'].default_value = 3; n1.inputs['Detail'].default_value = 12
    r = t.nodes.new('ShaderNodeValToRGB'); e = r.color_ramp.elements
    e[0].position = 0.35; e[0].color = (0.25, 0.06, 0.03, 1); e[1].position = 0.65; e[1].color = (0.7, 0.3, 0.14, 1)
    t.links.new(n1.outputs['Fac'], r.inputs['Fac']); t.links.new(r.outputs['Color'], b.inputs['Base Color']); b.inputs['Roughness'].default_value = 0.9
    P.data.materials.append(m)
    bpy.ops.object.light_add(type='SUN'); L = bpy.context.object; L.data.energy = 5; L.rotation_euler = (math.radians(70), 0, math.radians(35))
    mm = emit('meteor', (1, 0.6, 0.25, 1), 14); fm = emit('boom', (1, 0.8, 0.5, 1), 30)
    for i in range(36):
        # surface target on the camera-facing hemisphere
        v = Vector((random.uniform(-1.4, 1.4), random.uniform(-2.4, -0.8), random.uniform(-1.2, 1.4))).normalized() * 3.03
        start = v + Vector((random.uniform(-9, 9), random.uniform(-18, -9), random.uniform(5, 12)))
        d = v - start; ln = random.uniform(1.8, 3.2)
        t0 = random.randint(1, int(N * 0.8)); dur = random.randint(int(fps * 1.2), int(fps * 2.0)); t1 = min(N, t0 + dur)
        bpy.ops.mesh.primitive_cone_add(radius1=0.06, radius2=0.0, depth=ln, vertices=10); c = bpy.context.object
        c.data.materials.append(mm); q = (-d).to_track_quat('Z', 'Y'); c.rotation_euler = q.to_euler()
        # cone tip faces the planet: flip so the point leads
        c.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()
        c.location = start; c.keyframe_insert('location', frame=t0); c.scale = (1, 1, 1); c.keyframe_insert('scale', frame=t0)
        c.location = v; c.keyframe_insert('location', frame=t1)
        c.scale = (0.001, 0.001, 0.001); c.keyframe_insert('scale', frame=min(N, t1 + 1))
        c.scale = (1, 1, 1); c.keyframe_insert('scale', frame=t1)
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.07, location=v); f = bpy.context.object; f.data.materials.append(fm)
        f.scale = (0.001, 0.001, 0.001); f.keyframe_insert('scale', frame=t1)
        f.scale = (1.8, 1.8, 1.8); f.keyframe_insert('scale', frame=min(N, t1 + 5))
        f.scale = (0.001, 0.001, 0.001); f.keyframe_insert('scale', frame=min(N, t1 + 14))
    camera((4, -17, 3.5), (-2, -14, 2.0), (0, 0, 0), 32)
    glow(0.8, 7)


# ---------------------------------------------------------------- 3. neon city fly-through
def city():
    world(color=(0.005, 0.004, 0.012, 1), stars=False)
    cols = [(1, 0.1, 0.6, 1), (0.1, 0.8, 1, 1), (0.6, 0.2, 1, 1), (1, 0.45, 0.1, 1), (0.1, 1, 0.5, 1)]
    ms = [emit('neon%d' % i, c, 9) for i, c in enumerate(cols)]
    body = mk('body'); bb = body.node_tree.nodes['Principled BSDF']; bb.inputs['Base Color'].default_value = (0.06, 0.06, 0.09, 1); bb.inputs['Roughness'].default_value = 0.35
    fl = mk('floor'); fb = fl.node_tree.nodes['Principled BSDF']; fb.inputs['Base Color'].default_value = (0.02, 0.02, 0.04, 1); fb.inputs['Roughness'].default_value = 0.08; fb.inputs['Metallic'].default_value = 0.6
    bpy.ops.mesh.primitive_plane_add(size=400, location=(0, 60, 0)); bpy.context.object.data.materials.append(fl)
    bpy.ops.mesh.primitive_cube_add(size=1); proto = bpy.context.object
    for side in (-1, 1):
        for i in range(70):
            y = i * 2.6 + random.uniform(-0.5, 0.5); x = side * random.uniform(3.2, 4.6); h = random.uniform(3, 16); w = random.uniform(1.4, 2.6)
            o = proto.copy(); o.data = proto.data.copy(); bpy.context.collection.objects.link(o)
            o.location = (x, y, h / 2); o.scale = (w, random.uniform(1.4, 2.4), h); o.data.materials.append(body)
            # neon strips on the street-facing side and a roof bar
            for z in (random.uniform(0.5, h - 0.5), h + 0.05):
                s = proto.copy(); s.data = proto.data.copy(); bpy.context.collection.objects.link(s)
                s.location = (x - side * w / 2, y, z); s.scale = (0.05, o.scale[1] * 0.9, 0.12); s.data.materials.append(random.choice(ms))
            if random.random() < 0.3:
                bar = proto.copy(); bar.data = proto.data.copy(); bpy.context.collection.objects.link(bar)
                bar.location = (x - side * w / 2, y, h * 0.5); bar.scale = (0.06, 0.12, h * 0.9); bar.data.materials.append(random.choice(ms))
    bpy.data.objects.remove(proto)
    for col, x in (((1, 0.1, 0.6), -2.5), ((0.1, 0.8, 1), 2.5)):
        bpy.ops.object.light_add(type='POINT', location=(x, 0, 3)); lt = bpy.context.object
        lt.data.energy = 3000; lt.data.color = col; lt.data.shadow_soft_size = 1
        lt.location = (x, 2, 3); lt.keyframe_insert('location', frame=1); lt.location = (x, 154, 3); lt.keyframe_insert('location', frame=N)
    camera((0, -4, 2.2), (0, 150, 2.6), (0, 60, 2.4), 24)
    cam = sc.camera; tg = [o for o in bpy.data.objects if o.name.startswith('t')][0]
    tg.location = (0, 40, 2.4); tg.keyframe_insert('location', frame=1); tg.location = (0, 200, 2.6); tg.keyframe_insert('location', frame=N)
    glow(0.9, 8)


{'blackhole': blackhole, 'meteors': meteors, 'city': city}[which]()
if still:
    sc.frame_set(int(a[3])); bpy.ops.render.render(write_still=True)
else:
    bpy.ops.render.render(animation=True)
