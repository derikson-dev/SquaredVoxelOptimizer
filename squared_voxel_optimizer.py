bl_info = {
    "name": "Squared Voxel Optimizer",
    "author": "Derikson",
    "version": (1, 0, 0),
    "blender": (5, 1, 0),
    "location": "View3D > Sidebar > Squared VOR",
    "description": "Monolithic VOX -> Greedy Mesh -> Bake -> FBX pipeline. Optimized for Bevy 0.18.",
    "category": "Import-Export",
}

import bpy
import struct
import zlib
import math
import os
import re
import tempfile
import mathutils
from pathlib import Path
from collections import defaultdict

# ==============================================================================
# DOMAIN 0: IDEMPOTENT IMPORT / NAMING HELPERS
# ==============================================================================

_NUMERIC_SUFFIX = re.compile(r"\.\d{3,}$")

def _is_name_variant(name, base):
    if name == base:
        return True
    return name.startswith(base) and bool(_NUMERIC_SUFFIX.match(name[len(base):]))

def _free_name(id_collection, name):
    existing = id_collection.get(name)
    if existing is not None and existing.users == 0:
        id_collection.remove(existing)

def _base_part_name(name):
    return _NUMERIC_SUFFIX.sub("", name)

def _free_image(png_path):
    target = os.path.normcase(os.path.abspath(png_path))
    for img in list(bpy.data.images):
        fp = img.filepath
        if not fp:
            continue
        try:
            resolved = os.path.normcase(os.path.abspath(bpy.path.abspath(fp)))
        except Exception:
            continue
        if resolved == target:
            img.user_clear() # Garante que soltou todas as referências
            bpy.data.images.remove(img)

def purge_previous_import(coll_name):
    target_names = [c.name for c in bpy.data.collections
                    if _is_name_variant(c.name, coll_name)]
    meshes, materials = set(), set()
    for cname in target_names:
        coll = bpy.data.collections.get(cname)
        if coll is None:
            continue
        for obj in list(coll.objects):
            if obj.type == 'MESH' and obj.data is not None:
                meshes.add(obj.data)
                for slot in obj.material_slots:
                    if slot.material is not None:
                        materials.add(slot.material)
            bpy.data.objects.remove(obj, do_unlink=True)
    for me in list(meshes):
        if me.users == 0:
            bpy.data.meshes.remove(me)
    for mat in list(materials):
        if mat.users == 0:
            bpy.data.materials.remove(mat)
    for cname in target_names:
        coll = bpy.data.collections.get(cname)
        if coll is not None:
            bpy.data.collections.remove(coll)

# ==============================================================================
# DOMAIN 1: C EXTENSIONS & FALLBACKS
# ==============================================================================
try:
    import greedy_mesher_ext as _EXT
    BACKEND = 'c_ext'
except ImportError:
    BACKEND = 'python'

try:
    import tjunction_resolver as _TJ_C
    _HAS_TJ_C = True
except ImportError:
    _HAS_TJ_C = False

# ==============================================================================
# DOMAIN 2: VOX PARSING & STRUCTURES
# ==============================================================================
_DEFAULT_PALETTE_RGBA = [
    0x00000000,0xffffffff,0xffccffff,0xff99ffff,0xff66ffff,0xff33ffff,
    0xff00ffff,0xffffccff,0xffccccff,0xff99ccff,0xff66ccff,0xff33ccff,
    0xff00ccff,0xffff99ff,0xffcc99ff,0xff9999ff,0xff6699ff,0xff3399ff,
    0xff0099ff,0xffff66ff,0xffcc66ff,0xff9966ff,0xff6666ff,0xff3366ff,
    0xff0066ff,0xffff33ff,0xffcc33ff,0xff9933ff,0xff6633ff,0xff3333ff,
    0xff0033ff,0xffff00ff,0xffcc00ff,0xff9900ff,0xff6600ff,0xff3300ff,
    0xff0000ff,0xffffffcc,0xffccffcc,0xff99ffcc,0xff66ffcc,0xff33ffcc,
    0xff00ffcc,0xffffcccc,0xffcccccc,0xff99cccc,0xff66cccc,0xff33cccc,
    0xff00cccc,0xffff99cc,0xffcc99cc,0xff9999cc,0xff6699cc,0xff3399cc,
    0xff0099cc,0xffff66cc,0xffcc66cc,0xff9966cc,0xff6666cc,0xff3366cc,
    0xff0066cc,0xffff33cc,0xffcc33cc,0xff9933cc,0xff6633cc,0xff3333cc,
    0xff0033cc,0xffff00cc,0xffcc00cc,0xff9900cc,0xff6600cc,0xff3300cc,
    0xff0000cc,0xffffff99,0xffccff99,0xff99ff99,0xff66ff99,0xff33ff99,
    0xff00ff99,0xffffcc99,0xffcccc99,0xff99cc99,0xff66cc99,0xff33cc99,
    0xff00cc99,0xffff9999,0xffcc9999,0xff999999,0xff669999,0xff339999,
    0xff009999,0xffff6699,0xffcc6699,0xff996699,0xff666699,0xff336699,
    0xff006699,0xffff3399,0xffcc3399,0xff993399,0xff663399,0xff333399,
    0xff003399,0xffff0099,0xffcc0099,0xff990099,0xff660099,0xff330099,
    0xff000099,0xffffff66,0xffccff66,0xff99ff66,0xff66ff66,0xff33ff66,
    0xff00ff66,0xffffcc66,0xffcccc66,0xff99cc66,0xff66cc66,0xff33cc66,
    0xff00cc66,0xffff9966,0xffcc9966,0xff999966,0xff669966,0xff339966,
    0xff009966,0xffff6666,0xffcc6666,0xff996666,0xff666666,0xff336666,
    0xff006666,0xffff3366,0xffcc3366,0xff993366,0xff663366,0xff333366,
    0xff003366,0xffff0066,0xffcc0066,0xff990066,0xff660066,0xff330066,
    0xff000066,0xffffff33,0xffccff33,0xff99ff33,0xff66ff33,0xff33ff33,
    0xff00ff33,0xffffcc33,0xffcccc33,0xff99cc33,0xff66cc33,0xff33cc33,
    0xff00cc33,0xffff9933,0xffcc9933,0xff999933,0xff669933,0xff339933,
    0xff009933,0xffff6633,0xffcc6633,0xff996633,0xff666633,0xff336633,
    0xff006633,0xffff3333,0xffcc3333,0xff993333,0xff663333,0xff333333,
    0xff003333,0xffff0033,0xffcc0033,0xff990033,0xff660033,0xff330033,
    0xff000033,0xffffff00,0xffccff00,0xff99ff00,0xff66ff00,0xff33ff00,
    0xff00ff00,0xffffcc00,0xffcccc00,0xff99cc00,0xff66cc00,0xff33cc00,
    0xff00cc00,0xffff9900,0xffcc9900,0xff999900,0xff669900,0xff339900,
    0xff009900,0xffff6600,0xffcc6600,0xff996600,0xff666600,0xff336600,
    0xff006600,0xffff3300,0xffcc3300,0xff993300,0xff663300,0xff333300,
    0xff003300,0xffff0000,0xffcc0000,0xff990000,0xff660000,0xff330000,
    0xff0000ee,0xff0000dd,0xff0000bb,0xff0000aa,0xff000088,0xff000077,
    0xff000055,0xff000044,0xff000022,0xff000011,0xff00ee00,0xff00dd00,
    0xff00bb00,0xff00aa00,0xff008800,0xff007700,0xff005500,0xff004400,
    0xff002200,0xff001100,0xffee0000,0xffdd0000,0xffbb0000,0xffaa0000,
    0xff880000,0xff770000,0xff550000,0xff440000,0xff220000,0xff110000,
    0xffeeeeee,0xffdddddd,0xffbbbbbb,0xffaaaaaa,0xff888888,0xff777777,
    0xff555555,0xff444444,0xff222222,0xff111111,0xff000000,
]

def build_default_palette():
    result = []
    for rgba in _DEFAULT_PALETTE_RGBA:
        r=(rgba>>0)&0xFF; g=(rgba>>8)&0xFF; b=(rgba>>16)&0xFF; a=(rgba>>24)&0xFF
        result.append((r,g,b,a))
    while len(result) < 256:
        result.append((0,0,0,255))
    return result

class VoxObject:
    def __init__(self, name, size, voxels, transform=None, pivot=None):
        self.name   = name
        self.size   = size
        self.voxels = voxels
        self.transform = transform
        self.pivot = pivot

class VoxScene:
    def __init__(self):
        self.objects = []
        self.palette = []

def _read_dict(data, pos):
    n = struct.unpack_from('<i', data, pos)[0]; pos += 4
    d = {}
    for _ in range(n):
        klen = struct.unpack_from('<i', data, pos)[0]; pos += 4
        key  = data[pos:pos+klen].decode('utf-8', errors='replace'); pos += klen
        vlen = struct.unpack_from('<i', data, pos)[0]; pos += 4
        val  = data[pos:pos+vlen].decode('utf-8', errors='replace'); pos += vlen
        d[key] = val
    return d, pos

def _decode_rotation(r_byte):
    idx0=(r_byte)&0x3; idx1=(r_byte>>2)&0x3
    s0=-1 if (r_byte>>4)&1 else 1
    s1=-1 if (r_byte>>5)&1 else 1
    s2=-1 if (r_byte>>6)&1 else 1
    idx2=3-idx0-idx1
    row0=[0,0,0]; row0[idx0]=s0
    row1=[0,0,0]; row1[idx1]=s1
    row2=[0,0,0]; row2[idx2]=s2
    return [row0,row1,row2]

def parse_vox_file(filepath):
    data = Path(filepath).read_bytes()
    if data[:4] != b'VOX ':
        raise ValueError(f'Corrupted file or invalid format: {filepath}')

    offset = 8
    main_content_sz  = struct.unpack_from('<I', data, offset+4)[0]
    main_children_sz = struct.unpack_from('<I', data, offset+8)[0]
    pos = offset + 12 + main_content_sz
    end = pos + main_children_sz

    raw_models = []
    nodes      = {}
    palette    = None

    while pos < end:
        cid  = data[pos:pos+4].decode('ascii')
        csz  = struct.unpack_from('<I', data, pos+4)[0]
        cdat = data[pos+12:pos+12+csz]

        if cid == 'SIZE':
            sx,sy,sz = struct.unpack_from('<III', cdat, 0)
            raw_models.append({'size':(sx,sy,sz), 'voxels':[]})
        elif cid == 'XYZI':
            n_vox = struct.unpack_from('<i', cdat, 0)[0]
            voxels = [(cdat[4+i*4],cdat[4+i*4+1],cdat[4+i*4+2],cdat[4+i*4+3]) for i in range(n_vox)]
            if raw_models:
                raw_models[-1]['voxels'] = voxels
        elif cid == 'nTRN':
            p = 0
            node_id = struct.unpack_from('<i', cdat, p)[0]; p += 4
            attrs, p = _read_dict(cdat, p)
            child_id = struct.unpack_from('<i', cdat, p)[0]; p += 4
            p += 4
            layer_id = struct.unpack_from('<i', cdat, p)[0]; p += 4
            num_frames = struct.unpack_from('<i', cdat, p)[0]; p += 4
            frame, p = _read_dict(cdat, p)
            
            tx=ty=tz=0
            px=py=pz=None
            
            if '_t' in frame:
                parts=frame['_t'].split(); tx,ty,tz=int(parts[0]),int(parts[1]),int(parts[2])
            
            if '_p' in frame:
                parts=frame['_p'].split(); px,py,pz=float(parts[0]),float(parts[1]),float(parts[2])
                
            rot=[[1,0,0],[0,1,0],[0,0,1]]
            if '_r' in frame:
                rot=_decode_rotation(int(frame['_r']))
                
            nodes[node_id]={'type':'TRN','child':child_id, 'tx':tx,'ty':ty,'tz':tz,'rot':rot, 'px':px, 'py':py, 'pz':pz, 'name':attrs.get('_name','')}
        elif cid == 'nGRP':
            p=0
            node_id=struct.unpack_from('<i', cdat, p)[0]; p+=4
            _,p=_read_dict(cdat,p)
            n_ch=struct.unpack_from('<i', cdat, p)[0]; p+=4
            children=[struct.unpack_from('<i',cdat,p+i*4)[0] for i in range(n_ch)]
            nodes[node_id]={'type':'GRP','children':children}
        elif cid == 'nSHP':
            p=0
            node_id=struct.unpack_from('<i', cdat, p)[0]; p+=4
            _,p=_read_dict(cdat,p)
            n_models=struct.unpack_from('<i', cdat, p)[0]; p+=4
            model_id=struct.unpack_from('<i', cdat, p)[0]
            nodes[node_id]={'type':'SHP','model_id':model_id}
        elif cid == 'RGBA':
            palette=[(cdat[i*4],cdat[i*4+1],cdat[i*4+2],cdat[i*4+3]) for i in range(256)]
        
        pos += 12 + csz

    if palette is None:
        palette = build_default_palette()

    scene = VoxScene()
    scene.palette = palette

    if not nodes or not raw_models:
        for i, m in enumerate(raw_models):
            scene.objects.append(VoxObject(f'VoxObject_{i}', m['size'], m['voxels']))
        return scene

    obj_idx = [0]
    
    def traverse(node_id, acc_rot, acc_tx, acc_ty, acc_tz, current_pivot=None, trn_name=''):
        if node_id not in nodes: return
        node = nodes[node_id]
        if node['type'] == 'TRN':
            lr = node['rot']
            new_rot = [[sum(acc_rot[i][k]*lr[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
            lt = (node['tx'], node['ty'], node['tz'])
            rt = tuple(sum(acc_rot[i][k]*lt[k] for k in range(3)) for i in range(3))
            
            p_val = (node.get('px'), node.get('py'), node.get('pz'))
            next_pivot = p_val if p_val[0] is not None else current_pivot
            
            traverse(node['child'], new_rot, acc_tx+rt[0], acc_ty+rt[1], acc_tz+rt[2], next_pivot, node.get('name', ''))
            
        elif node['type'] == 'GRP':
            for child_id in node['children']: traverse(child_id, acc_rot, acc_tx, acc_ty, acc_tz, current_pivot, trn_name)
            
        elif node['type'] == 'SHP':
            mid = node['model_id']
            if mid >= len(raw_models): return
            m = raw_models[mid]
            sx, sy, sz = m['size']

            scene.objects.append(VoxObject(
                name=trn_name if trn_name else f'Object_{obj_idx[0]}',
                size=(sx, sy, sz),
                voxels=m['voxels'],
                transform=(acc_rot, acc_tx, acc_ty, acc_tz),
                pivot=(sx // 2, sy // 2, sz // 2)
            ))
            obj_idx[0] += 1

    root_id  = min(n for n,v in nodes.items() if v['type']=='TRN')
    traverse(root_id, [[1,0,0],[0,1,0],[0,0,1]], 0, 0, 0, None)
    return scene

# ==============================================================================
# DOMAIN 3: GREEDY MESHER & T-JUNCTION
# ==============================================================================
def _greedy_mesh_plane_python(model, axis, direction):
    voxels = {(x, y, z): color for x, y, z, color in model.voxels}
    sx, sy, sz = model.size
    if axis == "x":
        size_w, size_u, size_v = sx, sy, sz
        coord_global  = lambda u, v, w: (w, u, v)
        coord_neighbor = lambda u, v, w: (w + direction, u, v)
    elif axis == "y":
        size_w, size_u, size_v = sy, sx, sz
        coord_global  = lambda u, v, w: (u, w, v)
        coord_neighbor = lambda u, v, w: (u, w + direction, v)
    elif axis == "z":
        size_w, size_u, size_v = sz, sx, sy
        coord_global  = lambda u, v, w: (u, v, w)
        coord_neighbor = lambda u, v, w: (u, v, w + direction)
    
    quads = []
    for w in range(size_w):
        mask = [[None] * size_v for _ in range(size_u)]
        for u in range(size_u):
            for v in range(size_v):
                pa = coord_global(u, v, w)
                pv = coord_neighbor(u, v, w)
                if pa not in voxels or pv in voxels: continue
                mask[u][v] = voxels[pa]
        used = [[False] * size_v for _ in range(size_u)]
        for u in range(size_u):
            for v in range(size_v):
                color = mask[u][v]
                if color is None or used[u][v]: continue
                width = 1
                while u + width < size_u and mask[u + width][v] == color and not used[u + width][v]:
                    width += 1
                height = 1
                done = False
                while v + height < size_v and not done:
                    for uu in range(u, u + width):
                        if mask[uu][v + height] != color or used[uu][v + height]: done = True; break
                    if not done: height += 1
                for uu in range(u, u + width):
                    for vv in range(v, v + height): used[uu][vv] = True
                quads.append((u, v, w, width, height, color))
    return quads

def greedy_mesh_python_dispatch(model, side):
    axis, direction = side[0], 1 if side[1] == 'p' else -1
    return _greedy_mesh_plane_python(model, axis, direction)

def _quad_verts(side, u, v, w, width, height):
    if side=='xp': return[(w+1,u,v),(w+1,u+width,v),(w+1,u+width,v+height),(w+1,u,v+height)]
    elif side=='xn': return[(w,u,v+height),(w,u+width,v+height),(w,u+width,v),(w,u,v)]
    elif side=='yp': return[(u,w+1,v+height),(u+width,w+1,v+height),(u+width,w+1,v),(u,w+1,v)]
    elif side=='yn': return[(u,w,v),(u+width,w,v),(u+width,w,v+height),(u,w,v+height)]
    elif side=='zp': return[(u,v,w+1),(u+width,v,w+1),(u+width,v+height,w+1),(u,v+height,w+1)]
    else:            return[(u,v+height,w),(u+width,v+height,w),(u+width,v,w),(u,v,w)]

def _pt_strictly_between(p, a, b):
    for i in range(3):
        if a[i] != b[i]:
            lo, hi = min(a[i],b[i]), max(a[i],b[i])
            return all(p[j]==a[j] for j in range(3) if j!=i) and lo<p[i]<hi
    return False

def _resolver_python(raw_quads):
    vertex_map = {}; vertices = []
    def gv(pt):
        if pt not in vertex_map: vertex_map[pt]=len(vertices)+1; vertices.append(pt)
        return vertex_map[pt]
    polygons = [([gv(v) for v in verts], side, color) for verts, side, color in raw_quads]
    
    for _ in range(60):
        em = defaultdict(list)
        for fi, (vis, _side, _color) in enumerate(polygons):
            n = len(vis)
            for i in range(n): em[tuple(sorted([vis[i],vis[(i+1)%n]]))].append((fi, i))
        open_edges = {e: fl[0] for e, fl in em.items() if len(fl)==1}
        if not open_edges: break
        
        face_tjoints = defaultdict(list)
        for (ea,eb),(fi,ei) in open_edges.items():
            va, vb = vertices[ea-1], vertices[eb-1]
            for vi in range(1, len(vertices)+1):
                if vi!=ea and vi!=eb and _pt_strictly_between(vertices[vi-1], va, vb):
                    face_tjoints[fi].append((ea, eb, vi))
        if not face_tjoints: break

        new_polygons = []
        for fi, (vis, side, color) in enumerate(polygons):
            if fi not in face_tjoints:
                new_polygons.append((vis, side, color)); continue
            coords = [vertices[vi-1] for vi in vis]
            fixed_ax = next((ax for ax in range(3) if len(set(c[ax] for c in coords))==1), None)
            if fixed_ax is None: new_polygons.append((vis, side, color)); continue
            fixed_val = coords[0][fixed_ax]
            ax0, ax1 = [i for i in range(3) if i!=fixed_ax]
            u_vals = sorted(set(c[ax0] for c in coords))
            v_vals = sorted(set(c[ax1] for c in coords))
            for ea,eb,t_vi in face_tjoints[fi]:
                tp = vertices[t_vi-1]
                u_vals.append(tp[ax0]); v_vals.append(tp[ax1])
            u_vals = sorted(set(u_vals)); v_vals = sorted(set(v_vals))
            n = len(coords)
            area2 = sum(coords[i][ax0]*coords[(i+1)%n][ax1]-coords[(i+1)%n][ax0]*coords[i][ax1] for i in range(n))
            ccw = area2 > 0
            u0,u1 = min(c[ax0] for c in coords), max(c[ax0] for c in coords)
            v0,v1 = min(c[ax1] for c in coords), max(c[ax1] for c in coords)
            def m3(u,v,_ax0=ax0,_ax1=ax1,_fax=fixed_ax,_fv=fixed_val):
                p=[0,0,0]; p[_ax0]=u; p[_ax1]=v; p[_fax]=_fv; return tuple(p)
            for i in range(len(u_vals)-1):
                for j in range(len(v_vals)-1):
                    uu0,uu1=u_vals[i],u_vals[i+1]; vv0,vv1=v_vals[j],v_vals[j+1]
                    if uu0<u0 or uu1>u1 or vv0<v0 or vv1>v1: continue
                    if ccw: q=[gv(m3(uu0,vv0)),gv(m3(uu1,vv0)),gv(m3(uu1,vv1)),gv(m3(uu0,vv1))]
                    else:   q=[gv(m3(uu0,vv1)),gv(m3(uu1,vv1)),gv(m3(uu1,vv0)),gv(m3(uu0,vv0))]
                    new_polygons.append((q, side, color))
        polygons = new_polygons
    return vertices, polygons

def build_blender_geometry(vox_obj, voxel_size, palette, target_collection):
    sides = ['xp', 'xn', 'yp', 'yn', 'zp', 'zn']
    raw_quads = []
    
    for side in sides:
        if BACKEND == 'c_ext':
            quads = getattr(_EXT, f"greedy_mesh_{side}")(vox_obj)
        else:
            quads = greedy_mesh_python_dispatch(vox_obj, side)
        for u,v,w,width,height,color in quads:
            raw_quads.append((_quad_verts(side,u,v,w,width,height), side, color))
            
    if _HAS_TJ_C:
        vertices, polygons = _TJ_C.resolve_tjunctions(raw_quads)
    else:
        vertices, polygons = _resolver_python(raw_quads)
        
    rot, tx, ty, tz = vox_obj.transform
    cx, cy, cz = vox_obj.pivot
    
    scaled_verts = [((lx - cx) * voxel_size, (ly - cy) * voxel_size, (lz - cz) * voxel_size) for lx, ly, lz in vertices]
    faces = [[v-1 for v in p[0]] for p in polygons]
    
    _free_name(bpy.data.meshes, vox_obj.name)
    mesh = bpy.data.meshes.new(vox_obj.name)
    mesh.from_pydata(scaled_verts, [], faces)
    
    color_attr = mesh.attributes.new(name="vox_color", type='INT', domain='FACE')
    rgba_attr = mesh.attributes.new(name="Color", type='FLOAT_COLOR', domain='FACE')
    
    for i, poly in enumerate(polygons):
        c_idx = poly[2]
        color_attr.data[i].value = c_idx
        
        pal_idx = max(0, min(255, c_idx - 1))
        r, g, b, a = palette[pal_idx]
        rgba_attr.data[i].color = ((r/255.0)**2.2, (g/255.0)**2.2, (b/255.0)**2.2, 1.0)
        
        mesh.polygons[i].use_smooth = False
        
    mesh.update()
    _free_name(bpy.data.objects, vox_obj.name)
    obj = bpy.data.objects.new(vox_obj.name, mesh)
    target_collection.objects.link(obj)
    
    obj.matrix_world = mathutils.Matrix((
        (rot[0][0], rot[0][1], rot[0][2], tx * voxel_size),
        (rot[1][0], rot[1][1], rot[1][2], ty * voxel_size),
        (rot[2][0], rot[2][1], rot[2][2], tz * voxel_size),
        (0.0, 0.0, 0.0, 1.0),
    ))
    
    mat_name = f"{vox_obj.name}_Preview"
    _free_name(bpy.data.materials, mat_name)
    mat = bpy.data.materials.new(name=mat_name)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    
    attr_node = nodes.new("ShaderNodeAttribute")
    attr_node.attribute_name = "Color"
    attr_node.location = (-300, 0)
    
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = 1.0
    bsdf.location = (0, 0)
    
    out = nodes.new("ShaderNodeOutputMaterial")
    out.location = (300, 0)
    
    links.new(attr_node.outputs["Color"], bsdf.inputs["Base Color"])
    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    obj.data.materials.append(mat)
    
    raw_faces_count = len(vox_obj.voxels) * 6
    opt_faces_count = len(polygons)
    
    return obj, raw_faces_count, opt_faces_count

# ==============================================================================
# DOMAIN 4: BAKE & MATERIAL
# ==============================================================================
def save_png(path, pixels, width, height):
    def chunk(tag, data):
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', crc)
    raw_rows = bytearray()
    for y in range(height):
        raw_rows.append(0)
        raw_rows.extend(pixels[y*width*3:(y+1)*width*3])
    png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(bytes(raw_rows), 9)) + chunk(b'IEND', b''))
    with open(path, 'wb') as f:
        f.write(png)

def execute_bake(obj, palette, resolution, output_dir):
    mesh = obj.data
    if "vox_color" not in mesh.attributes:
        return False, "Object has no 'vox_color' attribute. Import a .vox first."

    used_colors = sorted(set(attr.value for attr in mesh.attributes["vox_color"].data))
    n = len(used_colors)
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    tw = max(4, resolution // cols)
    th = max(4, resolution // rows)

    pixels = bytearray(resolution * resolution * 3)
    color_to_uv = {}

    for fi, c_idx in enumerate(used_colors):
        pal_idx = max(0, min(255, c_idx - 1))
        r, g, b = palette[pal_idx][:3]
        col, row = fi % cols, fi // cols
        x0, y0 = col * tw, row * th
        x1, y1 = min(resolution, x0 + tw), min(resolution, y0 + th)

        for y in range(y0 + 1, y1 - 1):
            for x in range(x0 + 1, x1 - 1):
                idx = (y * resolution + x) * 3
                pixels[idx], pixels[idx+1], pixels[idx+2] = r, g, b

        u_c = (x0 + x1) / 2.0 / resolution
        v_c = 1.0 - (y0 + y1) / 2.0 / resolution
        color_to_uv[c_idx] = (u_c, v_c)

    base_name = _base_part_name(obj.name)
    png_path = os.path.join(output_dir, f"{base_name}_baked.png")
    
    _free_image(png_path)
    
    # Forçar a remoção do arquivo físico atualiza o timestamp no SO (Windows)
    if os.path.exists(png_path):
        try:
            os.remove(png_path)
        except OSError:
            pass
            
    try:
        save_png(png_path, pixels, resolution, resolution)
    except (PermissionError, OSError) as e:
        return False, (f"Could not write the texture to '{png_path}'. "
                       f"Save the .blend file in a writable folder (or enable "
                       f"'Save in imported .vox folder') and bake again. [{e}]")

    uv_layer = mesh.uv_layers.get("UVMap_baked") or mesh.uv_layers.new(name="UVMap_baked")
    mesh.uv_layers.active = uv_layer
    
    for poly in mesh.polygons:
        c_idx = mesh.attributes["vox_color"].data[poly.index].value
        u, v = color_to_uv[c_idx]
        for loop_idx in poly.loop_indices:
            uv_layer.data[loop_idx].uv = (u, v)

    img = bpy.data.images.load(png_path)
    img.colorspace_settings.name = "sRGB"
    mat_name = f"{base_name}_Baked"
    obj.data.materials.clear()
    _free_name(bpy.data.materials, mat_name)
    mat = bpy.data.materials.new(name=mat_name)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    
    uv_node = nodes.new("ShaderNodeUVMap")
    uv_node.uv_map = "UVMap_baked"
    uv_node.location = (-600, 0)
    
    tex_node = nodes.new("ShaderNodeTexImage")
    tex_node.image = img
    tex_node.interpolation = "Closest"
    tex_node.location = (-300, 0)
    
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Metallic"].default_value = 0.0
    bsdf.inputs["Roughness"].default_value = 1.0
    bsdf.location = (0, 0)
    
    out = nodes.new("ShaderNodeOutputMaterial")
    out.location = (300, 0)
    
    links.new(uv_node.outputs["UV"], tex_node.inputs["Vector"])
    links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    
    obj.data.materials.append(mat)
    
    return True, f"Bake finished and saved to {png_path}"

# ==============================================================================
# DOMAIN 5: UI PANEL & OPERATORS
# ==============================================================================
class VOX_OT_ShowReport(bpy.types.Operator):
    bl_idname = "vox.show_report"
    bl_label = "Optimization Report"

    def execute(self, context):
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=400)

    def draw(self, context):
        layout = self.layout
        report = context.scene.vox_settings.last_report
        for line in report.split('\n'):
            if line.startswith("Total:") or line.startswith("Geometry reduction"):
                layout.label(text=line, icon='INFO')
            elif line.strip() == "":
                layout.separator()
            else:
                layout.label(text=line)

class VOX_OT_ImportOperator(bpy.types.Operator):
    bl_idname = "vox.import"
    bl_label = "Import and Optimize .Vox"
    bl_options = {'REGISTER', 'UNDO'}
    filepath: bpy.props.StringProperty(subtype="FILE_PATH")

    @staticmethod
    def _make_active_collection(context, collection):
        def find(layer_coll):
            if layer_coll.collection == collection:
                return layer_coll
            for child in layer_coll.children:
                found = find(child)
                if found:
                    return found
            return None
        lc = find(context.view_layer.layer_collection)
        if lc:
            context.view_layer.active_layer_collection = lc

    @staticmethod
    def _recenter_to_floor(context, objects):
        if not objects:
            return
        context.view_layer.update()
        mn = [float('inf')] * 3
        mx = [float('-inf')] * 3
        for obj in objects:
            for corner in obj.bound_box:
                wc = obj.matrix_world @ mathutils.Vector(corner)
                for i in range(3):
                    if wc[i] < mn[i]: mn[i] = wc[i]
                    if wc[i] > mx[i]: mx[i] = wc[i]
        if mn[0] == float('inf'):
            return
        offset = mathutils.Vector((
            -(mn[0] + mx[0]) / 2.0,
            -(mn[1] + mx[1]) / 2.0,
            -mn[2],
        ))
        shift = mathutils.Matrix.Translation(offset)
        for obj in objects:
            obj.matrix_world = shift @ obj.matrix_world

    @staticmethod
    def _select_and_frame(context, objects):
        for o in context.view_layer.objects:
            o.select_set(False)
        for obj in objects:
            obj.select_set(True)
        if objects:
            context.view_layer.objects.active = objects[0]
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type != 'VIEW_3D':
                    continue
                region = next((r for r in area.regions if r.type == 'WINDOW'), None)
                if region:
                    with context.temp_override(window=window, area=area, region=region):
                        bpy.ops.view3d.view_selected()
                    return

    def execute(self, context):
        size = context.scene.vox_settings.voxel_size
        try:
            scene = parse_vox_file(self.filepath)
            context.scene.vox_settings.last_palette = str(scene.palette)
            context.scene.vox_settings.last_import_dir = os.path.dirname(self.filepath)

            coll_name = os.path.splitext(os.path.basename(self.filepath))[0] or "VoxImport"
            context.scene.vox_settings.last_import_name = coll_name
            purge_previous_import(coll_name)
            vox_collection = bpy.data.collections.new(coll_name)
            context.scene.collection.children.link(vox_collection)
            self._make_active_collection(context, vox_collection)

            report_lines = []
            total_raw = 0
            total_opt = 0
            created = []

            for vox_obj in scene.objects:
                obj, raw_f, opt_f = build_blender_geometry(vox_obj, size, scene.palette, vox_collection)
                created.append(obj)
                report_lines.append(f"{vox_obj.name}: {raw_f} -> {opt_f} faces")
                total_raw += raw_f
                total_opt += opt_f

            self._recenter_to_floor(context, created)
            self._select_and_frame(context, created)

            report_str = f"Loaded objects: {len(scene.objects)}\n\n"
            report_str += "\n".join(report_lines)
            report_str += f"\n\nTotal: {total_raw} raw faces -> {total_opt} optimized faces."
            if total_raw > 0:
                report_str += f"\nGeometry reduction: {100 - (total_opt/total_raw*100):.1f}%"
                
            context.scene.vox_settings.last_report = report_str
            
            if context.scene.vox_settings.show_optimization_report:
                bpy.ops.vox.show_report('INVOKE_DEFAULT')
            
            self.report({'INFO'}, f"Successfully imported {len(scene.objects)} meshes.")
        except Exception as e:
            self.report({'ERROR'}, f"Error: {str(e)}")
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

class VOX_OT_BakeOperator(bpy.types.Operator):
    bl_idname = "vox.bake"
    bl_label = "Bake"
    bl_options = {'REGISTER', 'UNDO'}

    directory: bpy.props.StringProperty(subtype="DIR_PATH", options={'HIDDEN'})

    def _gather_targets(self, context):
        sel = [o for o in context.selected_objects
               if o.type == 'MESH' and "vox_color" in o.data.attributes]
        if sel:
            return sel
        coll = context.view_layer.active_layer_collection.collection
        if coll and coll != context.scene.collection:
            return [o for o in coll.all_objects
                    if o.type == 'MESH' and "vox_color" in o.data.attributes]
        return []

    def invoke(self, context, event):
        settings = context.scene.vox_settings
        if not self._gather_targets(context):
            self.report({'WARNING'}, "Select imported objects, or activate the collection imported by the add-on.")
            return {'CANCELLED'}
        if settings.save_in_vox_folder and settings.last_import_dir:
            return self.execute(context)
        if bpy.data.filepath:
            self.directory = os.path.dirname(bpy.data.filepath)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        settings = context.scene.vox_settings
        targets = self._gather_targets(context)
        if not targets:
            self.report({'WARNING'}, "Select imported objects, or activate the collection imported by the add-on.")
            return {'CANCELLED'}

        if settings.save_in_vox_folder and settings.last_import_dir:
            out_dir = settings.last_import_dir
        elif self.directory:
            out_dir = self.directory
        elif bpy.data.filepath:
            out_dir = os.path.dirname(bpy.data.filepath)
        else:
            out_dir = bpy.app.tempdir or tempfile.gettempdir()
        if not os.path.isdir(out_dir):
            out_dir = bpy.app.tempdir or tempfile.gettempdir()

        palette = eval(settings.last_palette) if settings.last_palette else build_default_palette()
        res = int(settings.texture_resolution)

        baked = 0
        for obj in targets:
            success, msg = execute_bake(obj, palette, res, out_dir)
            if not success:
                self.report({'ERROR'}, msg)
                return {'CANCELLED'}
            baked += 1

        self.report({'INFO'}, f"Baked {baked} object(s) into {out_dir}")
        return {'FINISHED'}

class VOX_OT_ExportFBXOperator(bpy.types.Operator):
    bl_idname = "vox.export_fbx"
    bl_label = "Export FBX"
    filepath: bpy.props.StringProperty(subtype="FILE_PATH", default="//export.fbx")

    def _default_filepath(self, context):
        settings = context.scene.vox_settings

        # Nome base: prioriza a Collection ativa (clicada no Outliner). No fluxo normal
        # de importação ela tem o mesmo nome do modelo .vox importado.
        name = ""
        try:
            coll = context.view_layer.active_layer_collection.collection
            if coll and coll != context.scene.collection:
                name = _base_part_name(coll.name)
        except Exception:
            pass
        if not name:
            name = (getattr(settings, "last_import_name", "") or "").strip()
        if not name and context.active_object:
            name = _base_part_name(context.active_object.name)
        if not name:
            name = "export"

        # Diretório padrão: pasta do .vox importado; senão, a pasta do .blend.
        out_dir = ""
        if settings.last_import_dir and os.path.isdir(settings.last_import_dir):
            out_dir = settings.last_import_dir
        elif bpy.data.filepath:
            out_dir = os.path.dirname(bpy.data.filepath)

        filename = name + ".fbx"
        return os.path.join(out_dir, filename) if out_dir else filename

    def execute(self, context):
        # GARANTIA DO SUFIXO FBX
        if not self.filepath.lower().endswith('.fbx'):
            self.filepath += '.fbx'

        fbx_kwargs = dict(
            filepath=self.filepath,
            use_selection=True,
            embed_textures=True,
            path_mode="COPY",
            mesh_smooth_type="FACE",
        )

        if context.selected_objects:
            # Comportamento padrão: exporta os objetos selecionados na Viewport.
            bpy.ops.export_scene.fbx(**fbx_kwargs)
        else:
            # Sem seleção: exporta os objetos da Collection ativa (clicada no Outliner),
            # como se eles estivessem selecionados.
            coll = context.view_layer.active_layer_collection.collection
            targets = list(coll.all_objects) if (coll and coll != context.scene.collection) else []
            if not targets:
                self.report({'WARNING'}, "Nenhum objeto para exportar. Selecione objetos na Viewport ou clique numa Collection no Outliner.")
                return {'CANCELLED'}

            view_layer = context.view_layer
            prev_active = view_layer.objects.active
            selected_any = False
            for o in view_layer.objects:
                o.select_set(False)
            for o in targets:
                try:
                    o.select_set(True)
                    view_layer.objects.active = o
                    selected_any = True
                except RuntimeError:
                    pass  # objeto oculto / fora da view layer
            if not selected_any:
                self.report({'WARNING'}, "Os objetos da Collection não puderam ser selecionados (ocultos?).")
                return {'CANCELLED'}
            try:
                bpy.ops.export_scene.fbx(**fbx_kwargs)
            finally:
                # Restaura o estado anterior (nada selecionado, como após clicar na Collection).
                for o in view_layer.objects:
                    o.select_set(False)
                view_layer.objects.active = prev_active

        self.report({'INFO'}, "FBX exported successfully for the engine.")
        return {'FINISHED'}
        
    def invoke(self, context, event):
        # VALIDAÇÃO: aceita objetos selecionados OU uma Collection ativa (clicada no Outliner)
        if not context.selected_objects:
            coll = context.view_layer.active_layer_collection.collection
            has_coll_objs = bool(coll and coll != context.scene.collection and len(coll.all_objects) > 0)
            if not has_coll_objs:
                self.report({'WARNING'}, "Nenhum objeto selecionado nem Collection ativa com objetos. Selecione objetos na Viewport ou clique numa Collection no Outliner.")
                return {'CANCELLED'}

        self.filepath = self._default_filepath(context)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

class VoxelSettings(bpy.types.PropertyGroup):
    texture_resolution: bpy.props.EnumProperty(
        name="Texture Resolution",
        description="Industry-standard POT (power of two) sizes",
        items=[
            ('128', "128x128", "Minimal detail"),
            ('256', "256x256", "Small props"),
            ('512', "512x512", "Lightweight default"),
            ('1024', "1024x1024 (1K)", "Recommended default"),
            ('2048', "2048x2048 (2K)", "Recommended maximum for voxels"),
        ],
        default='1024'
    )
    voxel_size: bpy.props.FloatProperty(name="Voxel Size", default=1.0, precision=2) 
    save_in_vox_folder: bpy.props.BoolProperty(name="Save in imported .vox folder", default=True)
    show_optimization_report: bpy.props.BoolProperty(name="Show Optimization Report", default=True)
    last_palette: bpy.props.StringProperty()
    last_import_dir: bpy.props.StringProperty()
    last_import_name: bpy.props.StringProperty()
    last_report: bpy.props.StringProperty()

class VIEW3D_PT_VoxelPipelinePanel(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Squared VOR'
    bl_label = "Squared Voxel Optimizer"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.vox_settings

        layout.label(text="Import", icon='IMPORT')
        layout.prop(settings, "show_optimization_report")
        layout.operator("vox.import", text="Import and Optimize .Vox")
        layout.separator()

        layout.label(text="Bake", icon='TEXTURE')
        box = layout.box()
        box.prop(settings, "texture_resolution")
        box.prop(settings, "voxel_size")
        box.prop(settings, "save_in_vox_folder")
        layout.operator("vox.bake", text="Bake")
        layout.separator()

        layout.label(text="Export", icon='EXPORT')
        layout.operator("vox.export_fbx", text="Export FBX")

classes = (
    VoxelSettings, 
    VOX_OT_ShowReport, 
    VOX_OT_ImportOperator, 
    VOX_OT_BakeOperator, 
    VOX_OT_ExportFBXOperator, 
    VIEW3D_PT_VoxelPipelinePanel
)

def register():
    for cls in classes: bpy.utils.register_class(cls)
    bpy.types.Scene.vox_settings = bpy.props.PointerProperty(type=VoxelSettings)

def unregister():
    for cls in reversed(classes): bpy.utils.unregister_class(cls)
    del bpy.types.Scene.vox_settings

if __name__ == "__main__": register()