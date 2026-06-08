"""
debug_uv.py — Inspeciona UV original após import e decimate
"""
import bpy, sys, os

def parse_path():
    argv = sys.argv
    if "--" in argv: return argv[argv.index("--") + 1]
    return None

def main():
    path = parse_path()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    bpy.ops.wm.obj_import(filepath=os.path.abspath(path))
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]

    for obj in meshes:
        print(f"\n=== {obj.name} (antes do decimate) ===")
        mesh = obj.data
        uv   = mesh.uv_layers.get("UVMap")
        if not uv:
            print("  sem UVMap"); continue

        # Amostra primeiros 20 loops
        u_values = set()
        for i, loop in enumerate(mesh.loops):
            u = uv.data[i].uv[0]
            u_values.add(round(u, 6))
            if i < 20:
                print(f"  loop {i:3d}: u={u:.6f}  key512={round(u*512)}")

        print(f"  Total loops: {len(mesh.loops)}")
        print(f"  U values únicos: {len(u_values)}")
        print(f"  Amostra U únicos: {sorted(u_values)[:10]}")

        # Aplica decimate
        mod = obj.modifiers.new("D","DECIMATE")
        mod.decimate_type="DISSOLVE"
        mod.angle_limit=0.5*(3.14159/180)
        bpy.context.view_layer.objects.active=obj
        bpy.ops.object.modifier_apply(modifier=mod.name)

        print(f"\n  Após decimate: {len(mesh.loops)} loops")
        u_after = set()
        for i in range(len(mesh.loops)):
            u = uv.data[i].uv[0]
            u_after.add(round(u,6))
            if i < 20:
                print(f"  loop {i:3d}: u={u:.6f}  key512={round(u*512)}")
        print(f"  U únicos após: {sorted(u_after)[:10]}")

main()
