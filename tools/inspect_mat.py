"""
inspect_mat.py — Diagnóstico do material importado do OBJ
"""
import bpy, sys, os

def parse_path():
    argv = sys.argv
    if "--" in argv:
        return argv[argv.index("--") + 1]
    return None

def main():
    path = parse_path()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

    bpy.ops.wm.obj_import(filepath=os.path.abspath(path))
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]

    for obj in meshes:
        print(f"\n=== {obj.name} ===")
        print(f"  UV layers   : {[uv.name for uv in obj.data.uv_layers]}")
        print(f"  Materials   : {len(obj.data.materials)}")

        for i, mat in enumerate(obj.data.materials):
            if not mat:
                print(f"  Mat[{i}]: None")
                continue
            print(f"  Mat[{i}]: {mat.name} | use_nodes={mat.use_nodes}")
            if mat.use_nodes:
                for node in mat.node_tree.nodes:
                    print(f"    Node: {node.type:25} name={node.name}")
                    if node.type == "TEX_IMAGE":
                        print(f"      image={node.image.name if node.image else 'None'}")
                        if node.image:
                            print(f"      filepath={node.image.filepath}")
                    for inp in node.inputs:
                        if inp.links:
                            print(f"      input '{inp.name}' linked from {inp.links[0].from_node.type}")
            else:
                print(f"    (sem nodes)")

main()
