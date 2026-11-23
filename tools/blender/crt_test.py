import bpy, math, sys
fps=24; secs=float(sys.argv[sys.argv.index('--')+1]) if '--' in sys.argv else 3
N=int(fps*secs); out=sys.argv[sys.argv.index('--')+2]
bpy.ops.wm.read_factory_settings(use_empty=True)
sc=bpy.context.scene; sc.frame_start=1; sc.frame_end=N; sc.render.fps=fps
sc.render.engine='BLENDER_EEVEE'
sc.render.resolution_x=960; sc.render.resolution_y=540
sc.render.image_settings.media_type='VIDEO'; sc.render.image_settings.file_format='FFMPEG'
sc.render.ffmpeg.format='MPEG4'; sc.render.ffmpeg.codec='H264'; sc.render.filepath=out
w=bpy.data.worlds.new('w'); sc.world=w; w.use_nodes=True
w.node_tree.nodes['Background'].inputs[0].default_value=(0.004,0.005,0.008,1)
def mat(name,col,strength=0,rough=0.4,metal=0):
    m=bpy.data.materials.new(name); m.use_nodes=True; b=m.node_tree.nodes['Principled BSDF']
    b.inputs['Base Color'].default_value=col; b.inputs['Roughness'].default_value=rough; b.inputs['Metallic'].default_value=metal
    if strength:
        b.inputs['Emission Color'].default_value=col; b.inputs['Emission Strength'].default_value=strength
    return m
def obj(o,m): o.data.materials.append(m); return o
# floor + back wall
bpy.ops.mesh.primitive_plane_add(size=30,location=(0,0,-1.2)); obj(bpy.context.object,mat('floor',(0.05,0.05,0.06,1),0,0.25))
# glass screen (slightly curved look by a flattened sphere) and the dark tube body
bpy.ops.mesh.primitive_cube_add(size=1,location=(0,1.4,0)); bd=bpy.context.object; bd.scale=(2.2,2.2,1.5); obj(bd,mat('body',(0.02,0.02,0.025,1),0,0.5))
bpy.ops.mesh.primitive_cube_add(size=1,location=(0,-0.05,0)); fr=bpy.context.object; fr.scale=(2.5,0.12,1.9); obj(fr,mat('frame',(0.015,0.015,0.018,1),0,0.35,0.3))
bpy.ops.mesh.primitive_plane_add(size=1,location=(0,-0.115,0),rotation=(math.pi/2,0,0)); sg=bpy.context.object; sg.scale=(2.2,1.65,1); obj(sg,mat('glass',(0.01,0.012,0.015,1),0,0.05))
# scanlines: thin green bars that grow left->right as the beam passes
rows=14; H=1.55; t_total=N
for i in range(rows):
    y=H/2 - i*(H/(rows-1))
    bpy.ops.mesh.primitive_cube_add(size=1,location=(0,-0.12,y)); b=bpy.context.object
    b.scale=(0.0001,0.005,0.045); obj(b,mat(f'l{i}',(0.25,1.0,0.5,1),6))
    t0=1+ (i/rows)*(N*0.82); t1=t0+N*0.82/rows*0.95
    for f,s in ((t0,0.0001),(t1,2.05)):
        b.scale[0]=s; b.location.x=-1.025+ s/2 if s>0.01 else -1.025
        b.keyframe_insert('scale',index=0,frame=f); b.keyframe_insert('location',index=0,frame=f)
# the beam dot, sweeping along the same raster path
bpy.ops.mesh.primitive_uv_sphere_add(radius=0.05,location=(0,-0.13,0)); d=bpy.context.object; obj(d,mat('dot',(1,1,1,1),40))
for i in range(rows):
    y=H/2 - i*(H/(rows-1)); t0=1+(i/rows)*(N*0.82); t1=t0+N*0.82/rows*0.95
    for f,x in ((t0,-1.025),(t1,1.025)):
        d.location=(x,-0.13,y); d.keyframe_insert('location',frame=f)
# camera: slow push-in
bpy.ops.object.camera_add(location=(0.6,-6.0,0.5)); cam=bpy.context.object; sc.camera=cam; cam.data.lens=50
cam.rotation_euler=(math.radians(88),0,math.radians(6))
cam.keyframe_insert('location',frame=1); cam.location=(0.1,-3.6,0.1); cam.rotation_euler=(math.radians(90),0,0)
cam.keyframe_insert('location',frame=N); cam.keyframe_insert('rotation_euler',frame=N)
# soft room light
bpy.ops.object.light_add(type='AREA',location=(-3,-4,3)); l=bpy.context.object; l.data.energy=40; l.data.size=3; l.data.color=(0.5,0.6,1)
bpy.ops.render.render(animation=True)
