"""Shot C: macro of glowing particles (luminous bacteria / dinoflagellates) in dark water, shallow depth of field.
blender -b -P build_c_macro.py -- [--preview]   -> scenes/c_macro.blend
Units: 1 BU = 1 cm. Particles are Geometry-Nodes instances whose motion is a pure function of time
(no particle sim), so any frame renders identically on any machine -> chunked cloud renders can resume anywhere."""
import math, os, sys
sys.path.insert(0, os.path.dirname(__file__))
import bpy
from common import *

PREVIEW = "--preview" in argv()
N_BIG = 3500 if PREVIEW else 9000       # in-focus-range bacteria clusters
N_FINE = 6000 if PREVIEW else 40000      # fine plankton dust
s = reset()
s.frame_end = 144
s.cycles.volume_bounces = 3
s.cycles.max_bounces = 8
s.cycles.volume_step_rate = 2.0 if not PREVIEW else 0.5

w = bpy.data.worlds.new("W"); s.world = w; w.use_nodes = True
w.node_tree.nodes["Background"].inputs["Color"].default_value = (0.0006, 0.0012, 0.002, 1)
w.node_tree.nodes["Background"].inputs["Strength"].default_value = 1.0

# --- particle cloud: geometry nodes, stateless in time -------------------------------------
def rvset(n, lo, hi, seed):
    for sk in n.inputs:
        if not sk.enabled:
            continue
        if sk.name == "Min": sk.default_value = lo
        elif sk.name == "Max": sk.default_value = hi
        elif sk.name == "Seed": sk.default_value = seed


def build_cloud(name, count, box, rmin, rmax, glow_min, glow_max, seed, amp, flow):
    ob = bpy.data.objects.new(name, bpy.data.meshes.new(name)); s.collection.objects.link(ob)
    ng = bpy.data.node_groups.new(name + "GN", "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    N = ng.nodes
    gin = N.new("NodeGroupInput"); gout = N.new("NodeGroupOutput")
    line = N.new("GeometryNodeMeshLine"); line.inputs["Count"].default_value = count
    line.inputs["Offset"].default_value = (0.0, 0.0, 0.0)
    m2p = N.new("GeometryNodeMeshToPoints")
    rv = N.new("FunctionNodeRandomValue"); rv.data_type = "FLOAT_VECTOR"
    rvset(rv, (-box[0], -box[1], -box[2]), box, seed)
    pos = N.new("GeometryNodeInputPosition")
    tm = N.new("GeometryNodeInputSceneTime")
    nz = N.new("ShaderNodeTexNoise"); nz.noise_dimensions = "4D"; nz.inputs["Scale"].default_value = 0.35; nz.inputs["Detail"].default_value = 1.0
    sub = N.new("ShaderNodeVectorMath"); sub.operation = "SUBTRACT"; sub.inputs[1].default_value = (0.5, 0.5, 0.5)
    sc = N.new("ShaderNodeVectorMath"); sc.operation = "SCALE"; sc.inputs["Scale"].default_value = amp
    tmul = N.new("ShaderNodeMath"); tmul.operation = "MULTIPLY"; tmul.inputs[1].default_value = 0.35
    fl = N.new("ShaderNodeVectorMath"); fl.operation = "SCALE"; fl.inputs[0].default_value = flow
    add1 = N.new("ShaderNodeVectorMath"); add1.operation = "ADD"
    add2 = N.new("ShaderNodeVectorMath"); add2.operation = "ADD"
    setp = N.new("GeometryNodeSetPosition")
    ico = N.new("GeometryNodeMeshIcoSphere"); ico.inputs["Radius"].default_value = 1.0; ico.inputs["Subdivisions"].default_value = 2
    rs = N.new("FunctionNodeRandomValue"); rs.data_type = "FLOAT"; rvset(rs, rmin, rmax, seed + 11)
    cxyz = N.new("ShaderNodeCombineXYZ")
    iop = N.new("GeometryNodeInstanceOnPoints")
    rg = N.new("FunctionNodeRandomValue"); rg.data_type = "FLOAT"; rvset(rg, glow_min, glow_max, seed + 23)
    store = N.new("GeometryNodeStoreNamedAttribute"); store.data_type = "FLOAT"; store.domain = "INSTANCE"; store.inputs["Name"].default_value = "glow"
    L = ng.links.new
    L(line.outputs["Mesh"], m2p.inputs["Mesh"])
    L(m2p.outputs["Points"], setp.inputs["Geometry"])
    # position = random box + noise wobble (animated by 4th dim = seconds) + steady flow * seconds
    L(pos.outputs["Position"], nz.inputs["Vector"]); L(tm.outputs["Seconds"], tmul.inputs[0]); L(tmul.outputs["Value"], nz.inputs["W"])
    L(nz.outputs["Color"], sub.inputs[0]); L(sub.outputs["Vector"], sc.inputs[0])
    L(tm.outputs["Seconds"], fl.inputs["Scale"])
    L(rv.outputs["Value"], add1.inputs[0]); L(sc.outputs["Vector"], add1.inputs[1])
    L(add1.outputs["Vector"], add2.inputs[0]); L(fl.outputs["Vector"], add2.inputs[1])
    L(add2.outputs["Vector"], setp.inputs["Position"])
    L(setp.outputs["Geometry"], iop.inputs["Points"]); L(ico.outputs["Mesh"], iop.inputs["Instance"])
    L(rs.outputs["Value"], cxyz.inputs["X"]); L(rs.outputs["Value"], cxyz.inputs["Y"]); L(rs.outputs["Value"], cxyz.inputs["Z"])
    L(cxyz.outputs["Vector"], iop.inputs["Scale"])
    L(iop.outputs["Instances"], store.inputs["Geometry"]); L(rg.outputs["Value"], store.inputs["Value"])
    L(store.outputs["Geometry"], gout.inputs["Geometry"])
    md = ob.modifiers.new("Cloud", "NODES"); md.node_group = ng
    return ob


# pre-position random positions through a noise of the *point's own rest position*: the Noise reads Position of the
# point set at that moment (before Set Position), i.e. the random box position, so the wobble is stable per bacterium.
big = build_cloud("Bacteria", N_BIG, (4.5, 7.0, 6.0), 0.008, 0.07, 0.4, 6.0, 3, 0.35, (0.00, 0.02, 0.0))
fine = build_cloud("Dust", N_FINE, (6.0, 4.5, 8.0), 0.003, 0.012, 0.08, 1.2, 57, 0.5, (0.0, 0.03, 0.0))

hero = build_cloud("Hero", 450 if PREVIEW else 900, (2.6, 0.12, 4.6), 0.018, 0.05, 2.0, 9.0, 91, 0.12, (0.0, 0.0, 0.0))
for ob, base in ((big, (0.03, 0.30, 1.0)), (fine, (0.10, 0.45, 1.0)), (hero, (0.05, 0.45, 1.0))):
    m = mat(ob.name); nt = m.node_tree
    at = node(nt, "ShaderNodeAttribute", (-500, 0), attribute_type="INSTANCER", attribute_name="glow")
    em = node(nt, "ShaderNodeEmission", (-100, 0)); em.inputs["Color"].default_value = (*base, 1)
    link(nt, at, "Fac", em, "Strength")
    o = node(nt, "ShaderNodeOutputMaterial", (200, 0)); link(nt, em, 0, o, "Surface")
    ob.data.materials.append(m)       # instance material comes from the instanced mesh; see below
# the instanced ico sphere carries no material of its own, so put the material on the realised instance via a material slot
for ob in (big, fine, hero):
    ng = ob.modifiers["Cloud"].node_group
    sm = ng.nodes.new("GeometryNodeSetMaterial"); sm.inputs["Material"].default_value = ob.data.materials[0]
    ico = [n for n in ng.nodes if n.bl_idname == "GeometryNodeMeshIcoSphere"][0]
    iop = [n for n in ng.nodes if n.bl_idname == "GeometryNodeInstanceOnPoints"][0]
    for l in list(ng.links):
        if l.to_node == iop and l.to_socket.name == "Instance":
            ng.links.remove(l)
    ng.links.new(ico.outputs["Mesh"], sm.inputs["Geometry"]); ng.links.new(sm.outputs["Geometry"], iop.inputs["Instance"])

# --- water volume ------------------------------------------------------------------------
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0)); water = bpy.context.active_object; water.name = "Water"; water.scale = (40, 40, 40)
mv = mat("WaterVolume"); vn = mv.node_tree
pv = node(vn, "ShaderNodeVolumePrincipled", (0, 0))
pv.inputs["Color"].default_value = (0.25, 0.55, 0.65, 1)
pv.inputs["Density"].default_value = 0.012
pv.inputs["Anisotropy"].default_value = 0.35
o = node(vn, "ShaderNodeOutputMaterial", (300, 0)); link(vn, pv, 0, o, "Volume")
water.data.materials.append(mv)
water.display_type = "WIRE"

# --- camera: 100 mm macro, f/2.8, slow push-in and drift ----------------------------------------
cam = camera("Cam", (0.0, -12.0, 0.0), (0.0, 0.0, 0.0), lens=100, sensor=36, fstop=2.8, focus=12.0)
cam.location = (0.0, -12.0, 0.0)
cam.keyframe_insert("location", frame=1)
cam.location = (0.6, -11.0, 0.35)
cam.keyframe_insert("location", frame=s.frame_end)
cam.data.dof.focus_distance = 12.0
cam.data.dof.keyframe_insert("focus_distance", frame=1)
cam.data.dof.focus_distance = 11.0
cam.data.dof.keyframe_insert("focus_distance", frame=s.frame_end)
cam.rotation_euler = (math.radians(90), 0, 0)
cam.keyframe_insert("rotation_euler", frame=1)
cam.rotation_euler = (math.radians(90.8), 0, math.radians(2.0))
cam.keyframe_insert("rotation_euler", frame=s.frame_end)
save(os.path.join(ROOT, "scenes", "c_macro_preview.blend" if PREVIEW else "c_macro.blend"))
