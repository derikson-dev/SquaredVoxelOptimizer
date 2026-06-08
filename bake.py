"""
bake.py — Pipeline correto: OBJ + paleta → textura baked + UV reescrito → FBX

Abordagem definitiva:
  - Gera textura com 1 tile por COR (não por face) — muito mais simples e robusto
  - Cada cor ocupa uma região da textura
  - UV de cada face aponta para o centro do tile da sua cor
  - Sem decimate (preserva geometria exata do OBJ)
  - Sem rewrite UV complexo — o UV é construído diretamente no bake.py
    e aplicado via Blender antes de exportar
"""

import bpy
import sys
import os
import struct
import zlib
import math
import argparse


# ═══════════════════════════════════════════════════════════════════════════════
# ETAPA 1 — Baker Python puro: 1 tile por COR
# ═══════════════════════════════════════════════════════════════════════════════

def load_palette_png(path):
    with open(path, 'rb') as f:
        data = f.read()
    pos = 8
    chunks = {}
    while pos < len(data):
        length = struct.unpack('>I', data[pos:pos+4])[0]
        ctype  = data[pos+4:pos+8].decode('ascii')
        chunks.setdefault(ctype, b'')
        chunks[ctype] += data[pos+8:pos+8+length]
        pos += 12 + length
    raw = zlib.decompress(chunks['IDAT'])
    return [(raw[1+i*3], raw[1+i*3+1], raw[1+i*3+2]) for i in range(256)]


def parse_obj(path):
    """
    Retorna:
      objects: {name: [vt_idx, ...]}  — lista de vt_idx por face
      vt_u:   {vt_idx: u_float}
    """
    objects = {}
    vt_u    = {}
    current = None
    vt_i    = 0

    with open(path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('vt '):
                vt_i += 1
                vt_u[vt_i] = float(line.split()[1])
            elif line.startswith('o '):
                current = line[2:].strip()
                objects[current] = []
            elif line.startswith('f ') and current is not None:
                parts  = line[2:].split()
                for p in parts:
                    tokens = p.split('/')
                    if len(tokens) > 1 and tokens[1]:
                        objects[current].append(int(tokens[1]))
                        break

    return objects, vt_u


def bake_texture_by_color(used_vt_indices, palette, resolution):
    """
    Gera textura com 1 tile por cor única usada.
    Retorna (pixels, vt_to_uv) onde vt_to_uv[vt_idx] = (u_center, v_center).
    """
    colors = sorted(set(used_vt_indices))
    n      = len(colors)

    # Grid quadrado
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    tw   = max(4, resolution // cols)
    th   = max(4, resolution // rows)

    pixels    = bytearray(resolution * resolution * 3)
    vt_to_uv  = {}

    for fi, vt_idx in enumerate(colors):
        cidx    = max(0, min(255, vt_idx - 1))
        r, g, b = palette[cidx]
        col = fi % cols
        row = fi // cols
        x0  = col * tw
        y0  = row * th
        x1  = min(resolution, x0 + tw)
        y1  = min(resolution, y0 + th)

        # Pinta tile com margem de 1px (evita bleeding)
        for y in range(y0 + 1, y1 - 1):
            for x in range(x0 + 1, x1 - 1):
                idx = (y * resolution + x) * 3
                pixels[idx]   = r
                pixels[idx+1] = g
                pixels[idx+2] = b

        # Centro normalizado, Y flippado (Blender Y=0 embaixo)
        u_c = (x0 + x1) / 2.0 / resolution
        v_c = 1.0 - (y0 + y1) / 2.0 / resolution
        vt_to_uv[vt_idx] = (u_c, v_c)

    return pixels, vt_to_uv


def save_png(path, pixels, width, height):
    def chunk(tag, data):
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', crc)
    raw_rows = bytearray()
    for y in range(height):
        raw_rows.append(0)
        raw_rows.extend(pixels[y*width*3:(y+1)*width*3])
    png = (b'\x89PNG\r\n\x1a\n'
           + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(bytes(raw_rows), 9))
           + chunk(b'IEND', b''))
    with open(path, 'wb') as f:
        f.write(png)


def run_bake(obj_path, palette_path, output_dir, resolution):
    print("=== Etapa 1: Bake Python puro (1 tile por cor) ===")
    palette       = load_palette_png(palette_path)
    objects, vt_u = parse_obj(obj_path)
    print(f"  Objetos: {len(objects)}")

    # Lookup exato u → vt_idx
    u_to_vt = {round(u * 512): idx for idx, u in vt_u.items()}

    # Uma textura por objeto (cores únicas daquele objeto)
    result = {}
    for name, face_vts in objects.items():
        used = sorted(set(face_vts))
        print(f"  [{name}] {len(face_vts)} faces, {len(used)} cores únicas: {used}")

        pixels, vt_to_uv = bake_texture_by_color(face_vts, palette, resolution)
        out_path = os.path.join(output_dir, f"{name}_baked.png")
        save_png(out_path, pixels, resolution, resolution)
        print(f"    Salvo: {out_path}")

        for vt, (uc, vc) in vt_to_uv.items():
            cidx = max(0, min(255, vt-1))
            r,g,b = palette[cidx]
            print(f"    vt={vt} #{r:02X}{g:02X}{b:02X} → UV({uc:.4f},{vc:.4f})")

        result[name] = {
            'png':      out_path,
            'vt_to_uv': vt_to_uv,
            'u_to_vt':  u_to_vt,
        }

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# ETAPA 2 — Blender: import, rewrite UV (sem decimate), export FBX
# ═══════════════════════════════════════════════════════════════════════════════

def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for m in bpy.data.materials: bpy.data.materials.remove(m)
    for i in bpy.data.images:    bpy.data.images.remove(i)


def import_obj(path):
    bpy.ops.wm.obj_import(filepath=path)
    return [o for o in bpy.context.scene.objects if o.type == "MESH"]


def rewrite_uvs(obj, u_to_vt, vt_to_uv):
    """
    Reescreve UV usando foreach_get/foreach_set (API segura).
    Lê UV original → vt_idx → UV do tile correto.
    SEM decimate — preserva geometria exata.
    """
    mesh     = obj.data
    uv_orig  = mesh.uv_layers.get("UVMap")
    if not uv_orig:
        print(f"    AVISO: UVMap não encontrado")
        return False

    n_loops  = len(mesh.loops)
    uv_in    = [0.0] * (n_loops * 2)
    uv_orig.data.foreach_get("uv", uv_in)

    # Cria nova UV layer
    if "UVMap_baked" in mesh.uv_layers:
        mesh.uv_layers.remove(mesh.uv_layers["UVMap_baked"])
    uv_baked = mesh.uv_layers.new(name="UVMap_baked")
    mesh.uv_layers.active = uv_baked

    uv_out = [0.0] * (n_loops * 2)
    hits = misses = 0

    for li in range(n_loops):
        u_val  = uv_in[li * 2]
        key    = round(u_val * 512)
        vt_idx = u_to_vt.get(key)

        if vt_idx and vt_idx in vt_to_uv:
            uc, vc = vt_to_uv[vt_idx]
            hits += 1
        else:
            uc, vc = 0.5, 0.5
            misses += 1
            if misses <= 3:
                print(f"    MISS loop {li}: u={u_val:.6f} key={key}")

        uv_out[li * 2]     = uc
        uv_out[li * 2 + 1] = vc

    uv_baked.data.foreach_set("uv", uv_out)
    mesh.update()
    print(f"    UV: {hits} hits, {misses} misses de {n_loops} loops")
    return True


def apply_baked_material(obj, png_path):
    image = bpy.data.images.load(png_path)
    image.colorspace_settings.name = "sRGB"

    mat = bpy.data.materials.new(name=f"{obj.name}_Baked")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    uv           = nodes.new("ShaderNodeUVMap")
    uv.uv_map    = "UVMap_baked"
    uv.location  = (-500, 0)

    tex              = nodes.new("ShaderNodeTexImage")
    tex.image        = image
    tex.interpolation = "Closest"
    tex.location     = (-300, 0)

    bsdf         = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (0, 0)
    # Metallic=0, Roughness=1 para flat shading
    bsdf.inputs["Metallic"].default_value   = 0.0
    bsdf.inputs["Roughness"].default_value  = 1.0

    out          = nodes.new("ShaderNodeOutputMaterial")
    out.location = (300, 0)

    links.new(uv.outputs["UV"],     tex.inputs["Vector"])
    links.new(tex.outputs["Color"],  bsdf.inputs["Base Color"])
    links.new(bsdf.outputs["BSDF"],  out.inputs["Surface"])

    obj.data.materials.clear()
    obj.data.materials.append(mat)


def export_fbx(output_dir, base_name):
    path = os.path.join(output_dir, f"{base_name}.fbx")
    bpy.ops.export_scene.fbx(
        filepath=path,
        use_selection=False,
        embed_textures=True,
        path_mode="COPY",
        mesh_smooth_type="FACE",
        use_mesh_modifiers=True,
    )
    size_kb = os.path.getsize(path) // 1024
    print(f"  FBX: {path} ({size_kb} KB)")


def run_export(obj_path, baked, output_dir, base_name, no_fbx):
    print("\n=== Etapa 2: Blender export ===")
    clear_scene()
    meshes = import_obj(obj_path)
    print(f"  {len(meshes)} objeto(s)")

    for obj in meshes:
        print(f"  [{obj.name}]")
        data = baked.get(obj.name)
        if not data:
            print(f"    AVISO: sem dados de bake")
            continue

        print(f"    Reescrevendo UV (sem decimate)...")
        ok = rewrite_uvs(obj, data['u_to_vt'], data['vt_to_uv'])
        if not ok:
            continue

        print(f"    Aplicando material baked...")
        apply_baked_material(obj, data['png'])

    # Garante UVMap_baked como UV ativo para render e exportação
    for obj in bpy.context.scene.objects:
        if obj.type == "MESH" and "UVMap_baked" in obj.data.uv_layers:
            obj.data.uv_layers.active = obj.data.uv_layers["UVMap_baked"]
            # Remove UVMap original — FBX exporta o primeiro UV como ativo
            if "UVMap" in obj.data.uv_layers:
                obj.data.uv_layers.remove(obj.data.uv_layers["UVMap"])

    if not no_fbx:
        print("  Exportando FBX...")
        export_fbx(output_dir, base_name)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def parse_args():
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = []
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--palette",    default=None)
    parser.add_argument("--resolution", type=int, default=1024)
    parser.add_argument("--output",     default=None)
    parser.add_argument("--no-fbx",    action="store_true")
    parser.add_argument("--no-glb",    action="store_true")
    return parser.parse_args(argv)


def main():
    args      = parse_args()
    obj_path  = os.path.abspath(args.input)
    base      = os.path.splitext(obj_path)[0]
    base_name = os.path.basename(base) + "_Baked"
    out_dir   = os.path.abspath(args.output) if args.output else os.path.dirname(obj_path)

    palette_path = args.palette or (base + "_palette.png")
    if not os.path.exists(palette_path):
        print(f"ERRO: paleta não encontrada: {palette_path}")
        return

    os.makedirs(out_dir, exist_ok=True)

    print(f"\n=== Bake Pipeline ===")
    print(f"OBJ        : {obj_path}")
    print(f"Paleta     : {palette_path}")
    print(f"Resolução  : {args.resolution}x{args.resolution}")
    print(f"Output     : {out_dir}\n")

    baked = run_bake(obj_path, palette_path, out_dir, args.resolution)
    run_export(obj_path, baked, out_dir, base_name, args.no_fbx)

    print("\nConcluído!")


main()