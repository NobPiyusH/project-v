"""Reusable gothic cathedral 'set' for Blender 5.x (headless, rerunnable).

Build the set (rebuilds everything from scratch; same args -> same result):
  E:\\Blender\\blender.exe -b -P tools/blender/cathedral_set.py -- build \
      --out E:\\project-data\\blender\\library\\cathedral\\cathedral.blend \
      --route tools/blender/routes/cathedral_plaza_to_nave.json \
      [--hour 22] [--fog 0.009] [--candles 700] [--crowd 260] [--glow 1.0] [--seed 7]

Render anchors / preview from a built .blend:
  ... -P tools/blender/cathedral_set.py -- render --blend X.blend --outdir D \
      --anchors 0,5,10 [--res 1080x1920 --samples 48]
  ... -P tools/blender/cathedral_set.py -- render --blend X.blend --outdir D \
      --preview preview.mp4 [--res 480x854 --samples 12]

World layout: +Y runs plaza -> doors (y=0) -> nave -> rose-window wall (y=48). Metres.
The parameters used are stored as custom properties on the scene (scene['set_params']).
"""
import bpy, bmesh, sys, os, json, math, random, time
from mathutils import Vector, Euler, Matrix

LIB = r"E:\project-data\blender\library\cathedral"
NAVE_LEN = 48.0
DEFAULTS = dict(hour=22.0, fog=0.009, candles=700, crowd=260, glow=1.0, seed=7, lens=28.0)


# ----------------------------------------------------------------- args
def parse(argv):
    a = argv[argv.index('--') + 1:] if '--' in argv else []
    cmd = a[0] if a else 'build'
    o = {}
    i = 1
    while i < len(a):
        o[a[i].lstrip('-')] = a[i + 1]
        i += 2
    return cmd, o


# ----------------------------------------------------------------- materials
def _img(path, nonc=False):
    im = bpy.data.images.load(path, check_existing=True)
    if nonc:
        im.colorspace_settings.name = 'Non-Color'
    return im


def stone_mat(name, tex, scale=0.28, tint=(1, 1, 1), bright=1.0, bump=0.6, box=True):
    """PBR stone from a Poly Haven texture folder, triplanar via object coords."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    bsdf = nt.nodes.new('ShaderNodeBsdfPrincipled')
    tc = nt.nodes.new('ShaderNodeTexCoord')
    mp = nt.nodes.new('ShaderNodeMapping')
    mp.inputs['Scale'].default_value = (scale,) * 3
    nt.links.new(tc.outputs['Object'], mp.inputs['Vector'])
    d = os.path.join(LIB, 'textures', tex)

    def tex_node(f, nonc):
        n = nt.nodes.new('ShaderNodeTexImage')
        n.image = _img(os.path.join(d, f), nonc)
        n.projection = 'BOX'
        n.projection_blend = 0.25
        nt.links.new(mp.outputs['Vector'], n.inputs['Vector'])
        return n
    dif, nor, rgh = tex_node('diff.jpg', False), tex_node('nor_gl.jpg', True), tex_node('rough.jpg', True)
    mix = nt.nodes.new('ShaderNodeMix')
    mix.data_type = 'RGBA'
    mix.blend_type = 'MULTIPLY'
    mix.inputs[0].default_value = 1.0
    nt.links.new(dif.outputs['Color'], mix.inputs[6])
    mix.inputs[7].default_value = (tint[0] * bright, tint[1] * bright, tint[2] * bright, 1)
    nt.links.new(mix.outputs[2], bsdf.inputs['Base Color'])
    nt.links.new(rgh.outputs['Color'], bsdf.inputs['Roughness'])
    nm = nt.nodes.new('ShaderNodeNormalMap')
    nm.inputs['Strength'].default_value = bump
    nt.links.new(nor.outputs['Color'], nm.inputs['Color'])
    nt.links.new(nm.outputs['Normal'], bsdf.inputs['Normal'])
    nt.links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])
    return m


def flat_mat(name, col, rough=0.6, metal=0.0, emit=0.0, emit_col=None):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes['Principled BSDF']
    b.inputs['Base Color'].default_value = (*col, 1)
    b.inputs['Roughness'].default_value = rough
    b.inputs['Metallic'].default_value = metal
    if emit:
        b.inputs['Emission Color'].default_value = (*(emit_col or col), 1)
        b.inputs['Emission Strength'].default_value = emit
    return m


def glass_mat(name, glow):
    """Stained glass: voronoi cells coloured by a ramp, emission scaled by `glow`."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    em = nt.nodes.new('ShaderNodeEmission')
    tc = nt.nodes.new('ShaderNodeTexCoord')
    vor = nt.nodes.new('ShaderNodeTexVoronoi')
    vor.feature = 'F1'
    vor.inputs['Scale'].default_value = 5.0
    vor.inputs['Randomness'].default_value = 1.0
    nt.links.new(tc.outputs['Generated'], vor.inputs['Vector'])
    ramp = nt.nodes.new('ShaderNodeValToRGB')
    cols = [(0.0, (0.0, 0.05, 0.6, 1)), (0.30, (0.0, 0.2, 0.7, 1)), (0.55, (0.7, 0.0, 0.06, 1)),
            (0.78, (1.0, 0.45, 0.0, 1)), (1.0, (0.0, 0.5, 0.3, 1))]
    ramp.color_ramp.elements[0].position, ramp.color_ramp.elements[0].color = cols[0]
    ramp.color_ramp.elements[1].position, ramp.color_ramp.elements[1].color = cols[-1]
    for p, c in cols[1:-1]:
        e = ramp.color_ramp.elements.new(p)
        e.color = c
    sep = nt.nodes.new('ShaderNodeSeparateColor')           # random per-cell value -> jewel-tone ramp
    nt.links.new(vor.outputs['Color'], sep.inputs['Color'])
    nt.links.new(sep.outputs['Red'], ramp.inputs['Fac'])
    nt.links.new(ramp.outputs['Color'], em.inputs['Color'])
    em.inputs['Strength'].default_value = 1.7 * glow
    nt.links.new(em.outputs['Emission'], out.inputs['Surface'])
    return m


# ----------------------------------------------------------------- mesh builders
def new_obj(name, mesh, mat=None, coll=None, loc=(0, 0, 0), rot=(0, 0, 0), scl=(1, 1, 1)):
    o = bpy.data.objects.new(name, mesh)
    (coll or bpy.context.scene.collection).objects.link(o)
    o.location, o.rotation_euler, o.scale = loc, rot, scl
    if mat is not None:
        if isinstance(mat, (list, tuple)):
            for mm in mat:
                o.data.materials.append(mm)
        else:
            o.data.materials.append(mat)
    return o


def box_mesh(name, sx, sy, sz):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(sx, sy, sz), verts=bm.verts)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return me


def box(name, loc, size, mat, coll=None):
    """Axis-aligned box centred at loc."""
    return new_obj(name, box_mesh(name, *size), mat, coll, loc=loc)


def arch_points(h, R, n=14):
    """Pointed arch centreline from left springing (-h,0) over the apex to (h,0): [(x, z, outward_angle)]."""
    th = math.acos((R - h) / R)
    right = [(-(R - h) + R * math.cos(i * th / n), R * math.sin(i * th / n), i * th / n) for i in range(n + 1)]
    left = [(-x, z, math.pi - a) for x, z, a in right]          # springing -> apex
    return left + right[::-1][1:]                                # ... apex -> right springing


def arch_rib_mesh(name, h, R, w, d, n=14):
    """Swept rectangular rib along a pointed arch in the XZ plane, extruded along Y."""
    pts = arch_points(h, R, n)
    bm = bmesh.new()
    rings = []
    for x, z, a in pts:
        nx, nz = math.cos(a), math.sin(a)     # outward radial direction
        ring = []
        for rw, rd in ((-w / 2, -d / 2), (w / 2, -d / 2), (w / 2, d / 2), (-w / 2, d / 2)):
            ring.append(bm.verts.new((x + nx * rw, rd, z + nz * rw)))
        rings.append(ring)
    for r0, r1 in zip(rings[:-1], rings[1:]):
        for k in range(4):
            bm.faces.new((r0[k], r0[(k + 1) % 4], r1[(k + 1) % 4], r1[k]))
    bm.faces.new(rings[0][::-1])
    bm.faces.new(rings[-1])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return me


def arch_solid_mesh(name, h, R, depth, n=14, z0=0.0, flat=False):
    """Filled pointed-arch shape in XZ (extruded along Y by `depth` unless flat=True)."""
    pts = [(x, z + z0) for x, z, a in arch_points(h, R, n)]
    bm = bmesh.new()
    verts = [bm.verts.new((x, -depth / 2 if not flat else 0, z)) for x, z in pts]
    f = bm.faces.new(verts)
    if not flat:
        r = bmesh.ops.extrude_face_region(bm, geom=[f])
        vs = [v for v in r['geom'] if isinstance(v, bmesh.types.BMVert)]
        bmesh.ops.translate(bm, vec=(0, depth, 0), verts=vs)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return me


def vault_mesh(name, h, R, length, thick=0.6, n=18):
    """Pointed barrel vault surface (inner skin + outer skin) along +Y."""
    pts = arch_points(h, R, n)
    bm = bmesh.new()
    rows = []
    for off in (0.0, thick):
        row = []
        for x, z, a in pts:
            nx, nz = math.cos(a), math.sin(a)
            p = (x + nx * off, z + nz * off)
            row.append((bm.verts.new((p[0], 0, p[1])), bm.verts.new((p[0], length, p[1]))))
        rows.append(row)
    for row in rows:
        for (a0, b0), (a1, b1) in zip(row[:-1], row[1:]):
            bm.faces.new((a0, a1, b1, b0))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return me


def column_mesh(name, r=0.55, h=11.0):
    """Clustered gothic pier: central shaft, 8 thin colonnettes, base block and capital."""
    bm = bmesh.new()

    def cyl(rad, z0, z1, x=0, y=0, seg=14):
        res = bmesh.ops.create_cone(bm, cap_ends=True, segments=seg, radius1=rad, radius2=rad, depth=z1 - z0)
        vs = [v for v in res['verts']]
        bmesh.ops.translate(bm, vec=(x, y, (z0 + z1) / 2), verts=vs)

    cyl(r, 0.4, h - 0.5)
    for k in range(8):
        a = k * math.pi / 4
        cyl(0.13, 0.4, h - 0.5, math.cos(a) * r * 0.95, math.sin(a) * r * 0.95, seg=8)
    res = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(r * 2.7, r * 2.7, 0.4), verts=res['verts'])
    bmesh.ops.translate(bm, vec=(0, 0, 0.2), verts=res['verts'])
    res = bmesh.ops.create_cone(bm, cap_ends=True, segments=14, radius1=r * 1.0, radius2=r * 1.7, depth=0.7)
    bmesh.ops.translate(bm, vec=(0, 0, h - 0.35), verts=res['verts'])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return me


def figure_mesh(name):
    """Hooded robed figure ~1.7 m: flared robe, shoulders, hooded head. Low poly."""
    bm = bmesh.new()
    res = bmesh.ops.create_cone(bm, cap_ends=True, segments=10, radius1=0.42, radius2=0.2, depth=1.3)
    bmesh.ops.translate(bm, vec=(0, 0, 0.65), verts=res['verts'])
    res = bmesh.ops.create_uvsphere(bm, u_segments=10, v_segments=6, radius=0.23)
    bmesh.ops.scale(bm, vec=(1.0, 0.75, 0.55), verts=res['verts'])
    bmesh.ops.translate(bm, vec=(0, 0, 1.33), verts=res['verts'])
    res = bmesh.ops.create_uvsphere(bm, u_segments=10, v_segments=6, radius=0.135)
    bmesh.ops.scale(bm, vec=(1.0, 1.0, 1.25), verts=res['verts'])
    bmesh.ops.translate(bm, vec=(0, 0.01, 1.58), verts=res['verts'])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    for f in bm.faces:
        f.smooth = True
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return me


def candle_mesh(name):
    """Wax stub (material 0) + flame teardrop (material 1)."""
    bm = bmesh.new()
    res = bmesh.ops.create_cone(bm, cap_ends=True, segments=8, radius1=0.025, radius2=0.022, depth=0.16)
    bmesh.ops.translate(bm, vec=(0, 0, 0.08), verts=res['verts'])
    res = bmesh.ops.create_uvsphere(bm, u_segments=8, v_segments=6, radius=0.02)
    bmesh.ops.scale(bm, vec=(0.8, 0.8, 1.9), verts=res['verts'])
    bmesh.ops.translate(bm, vec=(0, 0, 0.2), verts=res['verts'])
    for f in bm.faces:
        f.material_index = 1 if min(v.co.z for v in f.verts) > 0.165 else 0
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return me


# ----------------------------------------------------------------- instancing via Geometry Nodes
def points_mesh(name, pts, yaw, scl):
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(p) for p in pts], [], [])
    a = me.attributes.new('yaw', 'FLOAT', 'POINT')
    a.data.foreach_set('value', yaw)
    b = me.attributes.new('scl', 'FLOAT', 'POINT')
    b.data.foreach_set('value', scl)
    return me


def instancer(name, pts, yaw, scl, source_obj, coll):
    """Object whose points are replaced by instances of source_obj (yaw/scl point attributes)."""
    host = new_obj(name, points_mesh(name + '_pts', pts, yaw, scl), None, coll)
    ng = bpy.data.node_groups.new(name + '_gn', 'GeometryNodeTree')
    ng.interface.new_socket('Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket('Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    n = ng.nodes
    gi, go = n.new('NodeGroupInput'), n.new('NodeGroupOutput')
    iop = n.new('GeometryNodeInstanceOnPoints')
    oi = n.new('GeometryNodeObjectInfo')
    oi.inputs['Object'].default_value = source_obj
    oi.inputs['As Instance'].default_value = True
    ay, asc = n.new('GeometryNodeInputNamedAttribute'), n.new('GeometryNodeInputNamedAttribute')
    for nd, nm in ((ay, 'yaw'), (asc, 'scl')):
        nd.data_type = 'FLOAT'
        nd.inputs['Name'].default_value = nm
    comb = n.new('ShaderNodeCombineXYZ')
    e2r = n.new('FunctionNodeEulerToRotation')
    cs = n.new('ShaderNodeCombineXYZ')
    ng.links.new(ay.outputs['Attribute'], comb.inputs['Z'])
    ng.links.new(comb.outputs['Vector'], e2r.inputs['Euler'])
    for k in ('X', 'Y', 'Z'):
        ng.links.new(asc.outputs['Attribute'], cs.inputs[k])
    ng.links.new(gi.outputs[0], iop.inputs['Points'])
    ng.links.new(oi.outputs['Geometry'], iop.inputs['Instance'])
    ng.links.new(e2r.outputs['Rotation'], iop.inputs['Rotation'])
    ng.links.new(cs.outputs['Vector'], iop.inputs['Scale'])
    ng.links.new(iop.outputs['Instances'], go.inputs[0])
    md = host.modifiers.new('instances', 'NODES')
    md.node_group = ng
    source_obj.hide_render = True        # the source object itself is not rendered
    source_obj.hide_viewport = True
    return host


# ----------------------------------------------------------------- the build
def build(opts):
    P = dict(DEFAULTS)
    for k, v in opts.items():
        if k in P:
            P[k] = type(DEFAULTS[k])(v)
    rng = random.Random(P['seed'])
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.name = 'cathedral'
    sc['set_params'] = json.dumps(P)

    c_arch = bpy.data.collections.new('architecture'); sc.collection.children.link(c_arch)
    c_glass = bpy.data.collections.new('glass_and_lights'); sc.collection.children.link(c_glass)
    c_crowd = bpy.data.collections.new('crowd_and_candles'); sc.collection.children.link(c_crowd)
    c_src = bpy.data.collections.new('instance_sources'); sc.collection.children.link(c_src)

    glow = P['glow']
    wall = stone_mat('wall_church_bricks', 'church_bricks_02', 0.22, (0.75, 0.74, 0.8), 0.9)
    pier = stone_mat('pier_blocks', 'medieval_blocks_05', 0.3, (0.8, 0.78, 0.8), 0.95)
    floor_in = stone_mat('floor_monastery', 'monastery_stone_floor', 0.2, (0.9, 0.9, 1.0), 1.0, 0.5)
    floor_out = stone_mat('plaza_cobble', 'cobblestone_floor_01', 0.25, (0.7, 0.72, 0.8), 0.8)
    wood = flat_mat('door_wood', (0.05, 0.03, 0.02), 0.7)
    iron = flat_mat('iron', (0.02, 0.02, 0.025), 0.45, 0.8)
    glass = glass_mat('stained_glass', glow)
    robe = flat_mat('robe', (0.012, 0.011, 0.014), 0.9)
    wax = flat_mat('wax', (0.85, 0.8, 0.65), 0.5)
    flame = flat_mat('flame', (1, 0.55, 0.15), 0.5, emit=60.0 * glow, emit_col=(1.0, 0.55, 0.16))

    # --- ground
    me = bpy.data.meshes.new('plaza'); me.from_pydata([(-90, -140, 0), (90, -140, 0), (90, 0, 0), (-90, 0, 0)], [], [(0, 1, 2, 3)])
    new_obj('plaza', me, floor_out, c_arch)
    me = bpy.data.meshes.new('nave_floor')
    me.from_pydata([(-12, 0, 0), (12, 0, 0), (12, NAVE_LEN, 0), (-12, NAVE_LEN, 0)], [], [(0, 1, 2, 3)])
    new_obj('nave_floor', me, floor_in, c_arch)
    box('ground_behind', (0, NAVE_LEN + 40, -0.5), (180, 80, 1), floor_out, c_arch)

    # --- facade with pointed doorway (boolean, applied)
    FW, FH, T = 30.0, 26.0, 2.0
    fac = box('facade', (0, 0, FH / 2), (FW, T, FH), wall, c_arch)
    cut = new_obj('door_cut', arch_solid_mesh('door_cut', 2.6, 5.2, 6.0), None, c_arch, loc=(0, 0, 0))
    bm_ = fac.modifiers.new('door', 'BOOLEAN'); bm_.object = cut; bm_.operation = 'DIFFERENCE'
    cut2 = new_obj('fwin_cut', arch_solid_mesh('fwin_cut', 1.5, 3.3, 6.0), None, c_arch, loc=(0, 0, 13.5))
    bm2 = fac.modifiers.new('fwin', 'BOOLEAN'); bm2.object = cut2; bm2.operation = 'DIFFERENCE'
    bpy.context.view_layer.objects.active = fac
    for md in list(fac.modifiers):
        bpy.ops.object.modifier_apply(modifier=md.name)
    for o in (cut, cut2):
        bpy.data.objects.remove(o)
    # archivolt ribs round the doorway
    for i, (hh, ww) in enumerate(((2.9, 0.45), (3.3, 0.55), (3.7, 0.7))):
        new_obj(f'archivolt{i}', arch_rib_mesh(f'archivolt{i}', hh, hh * 2.0, ww, 0.5 + i * 0.12),
                pier, c_arch, loc=(0, -T / 2 - 0.1, 0))
    # towers + buttresses
    for sx in (-1, 1):
        box(f'tower{sx}', (sx * 13.0, -0.5, 18.0), (6.0, 4.0, 36.0), wall, c_arch)
        for k in range(3):
            box(f'butt{sx}{k}', (sx * (4.5 + k * 3.6), -1.4, 4.0 + k), (1.1, 1.6, 8.0 + 2 * k), pier, c_arch)
    # lancet window over the door (glass behind the cut)
    lanc = arch_solid_mesh('lancet_flat', 1.5, 3.3, 0, flat=True)
    new_obj('facade_window', lanc, glass, c_glass, loc=(0, 0.2, 13.5))
    # open doors (hinged at the jambs, swung 100 degrees inward)
    for sx in (-1, 1):
        me = box_mesh(f'door{sx}', 2.6, 0.2, 4.6)
        me.transform(Matrix.Translation((-sx * 1.3, 0, 0)))
        new_obj(f'door{sx}', me, wood, c_arch, loc=(sx * 2.6, 0.0, 2.3), rot=(0, 0, -sx * math.radians(100)))

    # --- nave: side walls, end wall, columns, ribs, vault
    for sx in (-1, 1):
        box(f'side_wall{sx}', (sx * 12.5, NAVE_LEN / 2, 7.0), (1.0, NAVE_LEN, 14.0), wall, c_arch)
    box('end_wall', (0, NAVE_LEN + 0.5, 12.0), (25.0, 1.0, 24.0), wall, c_arch)
    box('aisle_roof_L', (-9.0, NAVE_LEN / 2, 11.3), (6.0, NAVE_LEN, 0.6), wall, c_arch)
    box('aisle_roof_R', (9.0, NAVE_LEN / 2, 11.3), (6.0, NAVE_LEN, 0.6), wall, c_arch)
    vlt = new_obj('vault', vault_mesh('vault', 6.0, 12.0, NAVE_LEN, 0.6), wall, c_arch, loc=(0, 0, 11.0))
    rib_m = arch_rib_mesh('rib', 6.0, 12.0, 0.5, 0.5)
    arc_m = arch_rib_mesh('arcade', 3.0, 6.0, 0.4, 0.5)
    col_m = column_mesh('column')
    bays = 8
    step = NAVE_LEN / bays
    ys = [step * (i + 0.5) for i in range(bays)]
    for y in ys:
        new_obj('rib', rib_m, pier, c_arch, loc=(0, y, 11.0))
        for sx in (-1, 1):
            new_obj('col', col_m, pier, c_arch, loc=(sx * 6.0, y, 0))
    ys_arc = ys[:-1]
    for sx in (-1, 1):
        for y0, y1 in zip(ys[:-1], ys[1:]):
            new_obj('arcade', arc_m, pier, c_arch, loc=(sx * 6.0, (y0 + y1) / 2, 5.5), rot=(0, 0, math.pi / 2))
    # --- rose window in the end wall
    RZ, RR, RY = 15.6, 3.6, NAVE_LEN - 0.02
    bm = bmesh.new()
    bmesh.ops.create_circle(bm, cap_ends=True, segments=64, radius=RR)
    me = bpy.data.meshes.new('rose_glass'); bm.to_mesh(me); bm.free()
    new_obj('rose_glass', me, glass, c_glass, loc=(0, RY, RZ), rot=(-math.pi / 2, 0, 0))
    spoke = box_mesh('spoke', 0.12, 0.18, RR * 2)
    for k in range(6):
        new_obj('rose_spoke', spoke, pier, c_glass, loc=(0, RY - 0.05, RZ), rot=(0, k * math.pi / 6, 0))
    for rr, tt in ((RR, 0.22), (RR * 0.62, 0.12), (RR * 0.25, 0.14)):
        # thin ring via a bevelled circle curve
        cu = bpy.data.curves.new('ring', 'CURVE'); cu.dimensions = '3D'
        sp = cu.splines.new('POLY'); sp.points.add(47)
        for i, pt in enumerate(sp.points):
            a = i / 48 * 2 * math.pi
            pt.co = (rr * math.cos(a), 0, rr * math.sin(a), 1)
        sp.use_cyclic_u = True
        cu.bevel_depth = tt / 2
        cu.bevel_resolution = 1
        ob = bpy.data.objects.new('rose_ring', cu); c_glass.objects.link(ob)
        ob.location = (0, RY - 0.05, RZ); ob.data.materials.append(pier)
    # altar + tall lancets in end wall flanks
    box('altar', (0, NAVE_LEN - 5, 0.55), (3.2, 1.4, 1.1), pier, c_arch)
    box('altar_step', (0, NAVE_LEN - 5, 0.15), (5.0, 3.0, 0.3), pier, c_arch)
    # --- side-aisle stained lancets (emissive) in the outer walls
    lan = arch_solid_mesh('lancet_side', 1.0, 2.2, 0, flat=True)
    for sx in (-1, 1):
        for y in ys:
            for dy in (-1.6, 1.6):
                new_obj('side_window', lan, glass, c_glass, loc=(sx * 11.98, y + dy, 4.5), rot=(0, 0, math.pi / 2))

    # --- lights: moon, window shafts, rose shaft
    day = max(0.0, math.sin(math.pi * (P['hour'] - 6) / 12))        # 0 at night .. 1 at noon
    sun_el = max(-10, 85 * math.sin(math.pi * (P['hour'] - 6) / 12))
    sun = bpy.data.lights.new('sun_moon', 'SUN')
    sun.energy = 0.25 + 3.5 * day
    sun.color = (0.62, 0.72, 1.0) if day < 0.15 else (1.0, 0.9, 0.75)
    sun.angle = math.radians(2)
    so = bpy.data.objects.new('sun_moon', sun); c_glass.objects.link(so)
    so.rotation_euler = (math.radians(90 - max(sun_el, 20)), 0, math.radians(35))
    # cool moonlight wash on the facade for the plaza shots
    fl = bpy.data.lights.new('facade_wash', 'SPOT')
    fl.energy = 140000 * (0.6 + day); fl.spot_size = math.radians(70); fl.spot_blend = 0.7; fl.color = (0.55, 0.65, 1.0)
    fl.shadow_soft_size = 3.0
    fo = bpy.data.objects.new('facade_wash', fl); c_glass.objects.link(fo)
    fo.location = (-14, -40, 22); fo.rotation_euler = (math.radians(90 - 10), 0, math.radians(-20) * -1)
    # rose shaft: spot just inside the wall, aimed down the nave
    rs = bpy.data.lights.new('rose_shaft', 'SPOT')
    rs.energy = 25000 * glow; rs.spot_size = math.radians(48); rs.spot_blend = 0.5
    rs.shadow_soft_size = 1.2; rs.color = (1.0, 0.72, 0.5)
    ro = bpy.data.objects.new('rose_shaft', rs); c_glass.objects.link(ro)
    ro.location = (0, NAVE_LEN - 1.0, RZ)
    ro.rotation_euler = (math.radians(76), 0, math.pi)
    # window shafts: spots at the outer wall pointing in and down
    wy = [ys[i] for i in range(1, bays - 1, 2)]
    for sx in (-1, 1):
        for y in wy:
            ls = bpy.data.lights.new('win_shaft', 'SPOT')
            ls.energy = 9000 * glow * (0.5 + day); ls.spot_size = math.radians(30); ls.spot_blend = 0.6
            ls.shadow_soft_size = 0.6; ls.color = (0.45, 0.6, 1.0) if day < 0.2 else (1.0, 0.85, 0.6)
            lo = bpy.data.objects.new('win_shaft', ls); c_glass.objects.link(lo)
            lo.location = (sx * 11.6, y, 7.5)
            lo.rotation_euler = (math.radians(60), 0, math.radians(90) * (1 if sx > 0 else -1))
    # warm fill pools around the aisle (few real lights; flames are emissive)
    for y in ys:
        for sx in (-1, 1):
            pl = bpy.data.lights.new('candle_pool', 'POINT')
            pl.use_shadow = False; pl.energy = 350 * glow; pl.color = (1.0, 0.6, 0.28); pl.shadow_soft_size = 0.3
            po = bpy.data.objects.new('candle_pool', pl); c_glass.objects.link(po)
            po.location = (sx * 3.2, y, 1.1)
    for k, y in enumerate((-8.0, -16.0)):
        for sx in (-1, 1):
            pl = bpy.data.lights.new('plaza_lamp', 'POINT')
            pl.use_shadow = False; pl.energy = 900 * glow; pl.color = (1.0, 0.62, 0.3); pl.shadow_soft_size = 0.4
            po = bpy.data.objects.new('plaza_lamp', pl); c_glass.objects.link(po)
            po.location = (sx * 6.0, y, 1.5)

    # --- crowd + candles
    fig_src = new_obj('figure_src', figure_mesh('figure'), robe, c_src)
    can_src = new_obj('candle_src', candle_mesh('candle'), [wax, flame], c_src)
    pts, yaw, scl = [], [], []
    n_nave = int(P['crowd'] * 0.8)
    for _ in range(n_nave):
        side = rng.choice((-1, 1))
        x = side * rng.uniform(1.9, 5.3)
        y = rng.uniform(4.0, NAVE_LEN - 8.0)
        pts.append((x, y, 0)); yaw.append(rng.uniform(-0.35, 0.35)); scl.append(rng.uniform(0.9, 1.08))
    for _ in range(P['crowd'] - n_nave):                     # plaza crowd facing the doors
        x = rng.choice((-1, 1)) * rng.uniform(3.0, 11.0)
        y = rng.uniform(-18.0, -3.5)
        pts.append((x, y, 0)); yaw.append(rng.uniform(-0.3, 0.3)); scl.append(rng.uniform(0.9, 1.08))
    # model faces -Y by default (nose at +y in mesh -> yaw 0 faces +Y); keep as is
    instancer('crowd', pts, yaw, scl, fig_src, c_crowd)
    cpts, cyaw, cscl = [], [], []
    n_total = P['candles']
    n_hand = min(int(n_total * 0.35), len(pts))
    for p in rng.sample(pts, n_hand):                       # hand-held
        cpts.append((p[0] + rng.uniform(-0.18, 0.18), p[1] + 0.22, 1.05)); cyaw.append(0); cscl.append(rng.uniform(0.9, 1.3))
    for _ in range(n_total - n_hand):                        # floor clusters / aisle lines
        r = rng.random()
        if r < 0.5:
            y = rng.choice(ys); sx = rng.choice((-1, 1)); a = rng.uniform(0, 6.283); rr = rng.uniform(0.7, 1.5)
            cpts.append((sx * 6.0 + math.cos(a) * rr, y + math.sin(a) * rr, 0.0))
        elif r < 0.85:
            cpts.append((rng.choice((-1, 1)) * rng.uniform(1.4, 2.0), rng.uniform(1.0, NAVE_LEN - 7), 0.0))
        else:
            cpts.append((rng.uniform(-2.2, 2.2), NAVE_LEN - 5 + rng.uniform(-1.3, 1.3), 1.1))
        cyaw.append(rng.uniform(0, 6.283)); cscl.append(rng.uniform(0.8, 1.5))
    instancer('candles', cpts, cyaw, cscl, can_src, c_crowd)

    # --- fog volume box (density = parameter)
    fm = bpy.data.materials.new('fog'); fm.use_nodes = True
    nt = fm.node_tree; nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial'); vol = nt.nodes.new('ShaderNodeVolumePrincipled')
    vol.inputs['Color'].default_value = (0.75, 0.8, 0.95, 1)
    vol.inputs['Density'].default_value = P['fog']
    vol.inputs['Anisotropy'].default_value = 0.35
    nt.links.new(vol.outputs['Volume'], out.inputs['Volume'])
    fb = box('fog_volume', (0, (NAVE_LEN - 60) / 2 + 6, 15), (46, NAVE_LEN + 60, 30), fm, c_glass)
    fb.display_type = 'WIRE'
    sc['fog_object'] = fb.name

    # --- world (night HDRI blended with a parametric sky by time of day)
    w = bpy.data.worlds.new('sky'); sc.world = w; w.use_nodes = True
    nt = w.node_tree; nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputWorld')
    bg = nt.nodes.new('ShaderNodeBackground')
    env = nt.nodes.new('ShaderNodeTexEnvironment')
    env.image = _img(os.path.join(LIB, 'hdri', 'moonless_golf.hdr'))
    sky = nt.nodes.new('ShaderNodeTexSky'); pass
    sky.sun_elevation = math.radians(max(sun_el, 1)); sky.sun_rotation = math.radians(35)
    mixn = nt.nodes.new('ShaderNodeMix'); mixn.data_type = 'RGBA'
    mixn.inputs[0].default_value = min(1.0, day * 1.5)
    tcw = nt.nodes.new('ShaderNodeTexCoord'); mp = nt.nodes.new('ShaderNodeMapping')
    mp.inputs['Rotation'].default_value = (0, 0, math.radians(90))
    nt.links.new(tcw.outputs['Generated'], mp.inputs['Vector']); nt.links.new(mp.outputs['Vector'], env.inputs['Vector'])
    nt.links.new(env.outputs['Color'], mixn.inputs[6]); nt.links.new(sky.outputs['Color'], mixn.inputs[7])
    nt.links.new(mixn.outputs[2], bg.inputs['Color'])
    bg.inputs['Strength'].default_value = 0.35 + 1.2 * day
    nt.links.new(bg.outputs['Background'], out.inputs['Surface'])

    # --- camera route
    route = opts.get('route') or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'routes', 'cathedral_plaza_to_nave.json')
    apply_route(sc, json.load(open(route)), P)
    sc['route_file'] = os.path.basename(route)

    render_defaults(sc, 1080, 1920, 48)
    out_path = opts.get('out', os.path.join(LIB, 'cathedral.blend'))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    for im in bpy.data.images:            # relative paths ("//textures/..") so the folder can move (pod, zip)
        if im.filepath and not im.packed_file:
            im.filepath = bpy.path.relpath(bpy.path.abspath(im.filepath), start=os.path.dirname(os.path.abspath(out_path))).replace(chr(92), '/')
    bpy.ops.wm.save_as_mainfile(filepath=out_path, relative_remap=True)
    print('BUILT', out_path)


# ----------------------------------------------------------------- camera route
def catmull(p0, p1, p2, p3, t):
    return 0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3)


def apply_route(sc, route, P=None):
    """Keyframe a camera along the route: [{t, pos, look}] with Catmull-Rom sampling every frame."""
    for o in [o for o in bpy.data.objects if o.type == 'CAMERA']:
        bpy.data.objects.remove(o)
    fps = int(route.get('fps', 30)); dur = float(route['duration'])
    sc.render.fps = fps; sc.frame_start = 1; sc.frame_end = int(round(fps * dur)) + 1
    cd = bpy.data.cameras.new('route_cam'); cd.lens = route.get('lens', (P or DEFAULTS)['lens'])
    cd.clip_start = 0.1; cd.clip_end = 400
    cam = bpy.data.objects.new('route_cam', cd); sc.collection.objects.link(cam); sc.camera = cam
    keys = sorted(route['keys'], key=lambda k: k['t'])
    pos = [Vector(k['pos']) for k in keys]; look = [Vector(k['look']) for k in keys]
    ts = [k['t'] for k in keys]
    last = None
    for f in range(sc.frame_start, sc.frame_end + 1):
        t = (f - 1) / fps
        i = max(0, min(len(ts) - 2, next((j for j in range(len(ts) - 1) if ts[j] <= t <= ts[j + 1]), len(ts) - 2)))
        u = (t - ts[i]) / (ts[i + 1] - ts[i]); u = max(0.0, min(1.0, u))
        u = u * u * (3 - 2 * u) if route.get('ease', True) and len(ts) == 2 else u
        g = lambda arr, j: arr[max(0, min(len(arr) - 1, j))]
        p = catmull(g(pos, i - 1), g(pos, i), g(pos, i + 1), g(pos, i + 2), u)
        l = catmull(g(look, i - 1), g(look, i), g(look, i + 1), g(look, i + 2), u)
        cam.location = p
        cam.rotation_euler = (l - p).to_track_quat('-Z', 'Y').to_euler('XYZ', last or Euler((0, 0, 0)))
        last = cam.rotation_euler.copy()
        cam.keyframe_insert('location', frame=f); cam.keyframe_insert('rotation_euler', frame=f)
    for fc in cam.animation_data.action.layers[0].strips[0].channelbags[0].fcurves if hasattr(cam.animation_data.action, 'layers') else cam.animation_data.action.fcurves:
        for kp in fc.keyframe_points:
            kp.interpolation = 'LINEAR'
    return cam


# ----------------------------------------------------------------- render
def render_defaults(sc, rx, ry, samples):
    sc.render.engine = 'BLENDER_EEVEE'
    sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = rx, ry, 100
    sc.view_settings.view_transform = 'AgX'
    sc.view_settings.exposure = 0.3
    ee = sc.eevee
    ee.taa_render_samples = samples
    for k, v in (('use_raytracing', False), ('use_shadows', True), ('volumetric_tile_size', '4'),
                 ('volumetric_samples', 48), ('volumetric_end', 90.0), ('use_volumetric_shadows', True),
                 ('volumetric_shadow_samples', 24), ('light_threshold', 0.02),
                 ('shadow_pool_size', '1024'), ('shadow_ray_count', 1), ('shadow_step_count', 6)):
        if hasattr(ee, k):
            try:
                setattr(ee, k, v)
            except Exception as e:
                print('eevee set fail', k, e)
    for l in bpy.data.lights:
        if hasattr(l, 'volume_factor'):
            l.volume_factor = 1.0
    add_glare(sc)


def add_glare(sc):
    try:
        ng = sc.compositing_node_group or bpy.data.node_groups.new('comp', 'CompositorNodeTree')
        sc.compositing_node_group = ng
        ng.nodes.clear()
        ng.interface.new_socket('Image', in_out='OUTPUT', socket_type='NodeSocketColor')
        rl = ng.nodes.new('CompositorNodeRLayers'); gl = ng.nodes.new('CompositorNodeGlare'); go = ng.nodes.new('NodeGroupOutput')
        for k, v in (('Type', 'Fog Glow'), ('Quality', 'Medium'), ('Threshold', 1.0), ('Strength', 0.55), ('Size', 0.6)):
            if k in gl.inputs:
                try:
                    gl.inputs[k].default_value = v
                except Exception as e:
                    print('glare fail', k, e)
        ng.links.new(rl.outputs['Image'], gl.inputs['Image']); ng.links.new(gl.outputs['Image'], go.inputs[0])
    except Exception as e:
        print('glare unavailable:', e)


def render(opts):
    bpy.ops.wm.open_mainfile(filepath=opts['blend'])
    sc = bpy.context.scene
    for im in bpy.data.images:                 # Linux pod: tolerate backslashes saved on Windows
        if not im.packed_file:
            im.filepath = im.filepath.replace(chr(92), '/')
    rx, ry = map(int, opts.get('res', '1080x1920').split('x'))
    render_defaults(sc, rx, ry, int(opts.get('samples', 48)))
    if 'route' in opts:
        apply_route(sc, json.load(open(opts['route'])))
    opts['outdir'] = os.path.abspath(opts['outdir'])
    os.makedirs(opts['outdir'], exist_ok=True)
    fps = sc.render.fps
    if 'anchors' in opts:
        for t in [float(x) for x in opts['anchors'].split(',')]:
            f = int(round(t * fps)) + 1
            sc.frame_set(f)
            sc.render.image_settings.file_format = 'PNG'
            sc.render.filepath = os.path.join(opts['outdir'], f'anchor_t{t:05.2f}s'.replace('.', '_') + '.png')
            t0 = time.time()
            bpy.ops.render.render(write_still=True)
            print(f'ANCHOR t={t}s frame={f} {time.time() - t0:.1f}s -> {sc.render.filepath}')
    if 'frames' in opts:                       # chunked, resumable: --frames 1-30 (skips frames that already exist)
        f0, f1 = (int(x) for x in opts['frames'].split('-'))
        sc.render.image_settings.media_type = 'IMAGE'
        sc.render.image_settings.file_format = 'PNG'
        sc.render.image_settings.color_depth = opts.get('depth', '16')
        for f in range(f0, f1 + 1):
            path = os.path.join(opts['outdir'], 'frame_%04d.png' % f)
            if os.path.exists(path):
                continue
            sc.frame_set(f)
            sc.render.filepath = path[:-4] + '_part'
            t0 = time.time()
            bpy.ops.render.render(write_still=True)
            os.replace(path[:-4] + '_part.png', path)      # final name only once fully written
            json.dump({'frame': f, 'seconds': round(time.time() - t0, 2)}, open(path[:-4] + '.json', 'w'))
            print('FRAME_DONE', f, round(time.time() - t0, 2), flush=True)
    if 'preview' in opts:
        sc.render.image_settings.media_type = 'VIDEO'
        sc.render.image_settings.file_format = 'FFMPEG'
        sc.render.ffmpeg.format = 'MPEG4'; sc.render.ffmpeg.codec = 'H264'
        sc.render.ffmpeg.constant_rate_factor = 'MEDIUM'
        sc.render.filepath = os.path.join(opts['outdir'], opts['preview'])
        t0 = time.time()
        bpy.ops.render.render(animation=True)
        n = sc.frame_end - sc.frame_start + 1
        print(f'PREVIEW {n} frames {time.time() - t0:.1f}s ({(time.time() - t0) / n:.2f}s/frame)')


if __name__ == '__main__':
    cmd, o = parse(sys.argv)
    {'build': build, 'render': render}[cmd](o)
