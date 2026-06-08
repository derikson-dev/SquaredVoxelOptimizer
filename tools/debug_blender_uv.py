"""
Verifica exatamente o que o Blender armazena como UV após importar o OBJ.
"""
import bpy, sys, os

argv = sys.argv
path = argv[argv.index("--") + 1] if "--" in argv else None

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete()
bpy.ops.wm.obj_import(filepath=os.path.abspath(path))

meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]

for obj in meshes:
    mesh    = obj.data
    uv_orig = mesh.uv_layers.get("UVMap")
    if not uv_orig:
        print(f"{obj.name}: sem UVMap"); continue

    n_loops  = len(mesh.loops)
    uv_data  = [0.0] * (n_loops * 2)
    uv_orig.data.foreach_get("uv", uv_data)

    # Coleta u únicos
    u_vals = set()
    for li in range(n_loops):
        u_vals.add(round(uv_data[li*2], 6))

    print(f"\n{obj.name}: {n_loops} loops, {len(u_vals)} u-values únicos")
    print(f"  u-values: {sorted(u_vals)}")

    # Mostra os primeiros 8 loops com face info
    print(f"  Primeiros loops:")
    for poly in list(mesh.polygons)[:3]:
        ls = poly.loop_start
        lt = poly.loop_total
        u  = uv_data[ls * 2]
        key = round(u * 512)
        print(f"    face {poly.index}: loop_start={ls} u={u:.6f} key512={key}")
