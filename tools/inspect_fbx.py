import bpy, sys, os

def parse_path():
    argv = sys.argv
    if "--" in argv: return argv[argv.index("--") + 1]
    return None

def main():
    path = parse_path()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

    bpy.ops.import_scene.fbx(filepath=os.path.abspath(path))
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]

    for obj in meshes:
        print(f"\n=== {obj.name} ===")
        print(f"  UV layers: {[uv.name for uv in obj.data.uv_layers]}")
        print(f"  Active UV: {obj.data.uv_layers.active.name if obj.data.uv_layers.active else 'None'}")
        print(f"  Materials: {len(obj.data.materials)}")

        for i, mat in enumerate(obj.data.materials):
            if not mat: continue
            print(f"  Mat[{i}]: {mat.name} | use_nodes={mat.use_nodes}")
            if mat.use_nodes:
                for node in mat.node_tree.nodes:
                    print(f"    Node: {node.type:25} name={node.name}")
                    if node.type == "TEX_IMAGE":
                        img = node.image
                        print(f"      image={img.name if img else 'None'}")
                        if img:
                            print(f"      source={img.source}")
                            print(f"      packed={img.packed_file is not None}")
                            print(f"      size={img.size[0]}x{img.size[1]}")
                    for inp in node.inputs:
                        if inp.links:
                            print(f"      input '{inp.name}' ← {inp.links[0].from_node.type}.{inp.links[0].from_socket.name}")

        # Verifica UV layer ativa
        n_loops = len(obj.data.loops)
        if obj.data.uv_layers.active and n_loops > 0:
            uv_data = [0.0] * (n_loops * 2)
            obj.data.uv_layers.active.data.foreach_get("uv", uv_data)
            u_vals = set(round(uv_data[i*2], 4) for i in range(min(20, n_loops)))
            print(f"  UV sample (primeiros 20 loops): {sorted(u_vals)}")

main()
