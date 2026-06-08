# SquaredVoxGameReady

> **MagicaVoxel → Greedy Mesh → Bake → Engine-ready FBX**
>
> CLI pipeline that converts `.vox` files into optimized, engine-ready assets using the Greedy Meshing algorithm — with baked UV textures for maximum GPU performance.

---

## Overview

MagicaVoxel stores voxel art as a 3-D grid of cubes. Exported naively, a single 32³ model can generate hundreds of thousands of faces. **SquaredVoxGameReady** collapses coplanar, same-color faces into the largest possible quads before any polygon ever leaves Python, cutting geometry by an order of magnitude. A second pipeline stage bakes per-color UV textures and exports engine-ready FBX via Blender headless.

```
MagicaVoxel (.vox)
  → main_greedy.py   → .obj + palette.png
  → bake.py          → baked texture + FBX (Blender headless)
  → Engine (UE5 / O3DE)
```

---

## Features

- **Greedy Meshing** — all 6 face directions, producing minimal quads
- **T-junction resolution** — seam-free, manifold meshes (no cracks in Blender)
- **Flat normals** — correct hard-edge shading per face out of the box
- **Palette texture** — 256-color PNG atlas auto-generated alongside `.obj`
- **Baked UV texture** — 1 tile per color, UV rewritten per face, packed into FBX
- **Vertex colors** — embedded in `.glb` (no texture bleeding, works with `NEAREST`)
- **`KHR_materials_unlit`** — flat shading in `.glb` without a PBR setup
- **Multi-object support** — World View hierarchy preserved with per-object offsets
- **Configurable scale** — `1.0` for UE5 (cm) or `0.01` for O3DE (m)
- **Quad or triangle output** — quads for Blender loop-cuts; tris for max compatibility
- **numpy-accelerated mesher** — 2–8x speedup on real voxel art models (Fase 1)

---

## Requirements

- Python **3.8+**
- numpy (greedy mesher acceleration — falls back to pure Python if absent)
- Blender **4.2+** (headless, for bake pipeline only)
- No other third-party dependencies

---

## Installation

```bash
git clone https://github.com/derikson-dev/SquaredVoxGameReady.git
cd SquaredVoxGameReady
```

---

## Quick Start

### Step 1 — Generate OBJ

```powershell
python main_greedy.py MyModel.vox .obj
```

Output:

```
MyModel_Greedy.obj
MyModel_Greedy.mtl
MyModel_Greedy_palette.png
```

### Step 2 — Bake + Export FBX

```powershell
& "C:\Program Files\Blender Foundation\Blender 4.5\blender.exe" --background --python bake.py -- MyModel_Greedy.obj --resolution 1024 --output ./out --no-glb
```

Output:

```
out/Object_0_baked.png      ← baked texture per object
out/Object_1_baked.png
out/MyModel_Greedy_Baked.fbx  ← engine-ready, textures packed
```

### One-liner (PowerShell)

```powershell
python main_greedy.py E:\models\MyModel.vox .obj; & "C:\Program Files\Blender Foundation\Blender 4.5\blender.exe" --background --python bake.py -- E:\models\MyModel_Greedy.obj --resolution 1024 --output E:\models\out --no-glb
```

---

## Configuration

### main_greedy.py

```python
VOXEL_SIZE  = 1.0     # UE5: 1.0 (cm)  |  O3DE: 0.01 (m)
TRIANGULATE = False   # False = quads   |  True  = triangles
```

### bake.py parameters

| Parameter      | Default             | Description                          |
| -------------- | ------------------- | ------------------------------------ |
| `--resolution` | `1024`              | Baked texture size (512, 1024, 2048) |
| `--output`     | same folder as .obj | Output directory                     |
| `--no-fbx`     | off                 | Skip FBX export                      |
| `--no-glb`     | off                 | Skip GLB export                      |
| `--palette`    | auto-detected       | Path to palette PNG                  |

---

## Project Structure

```
SquaredVoxGameReady/
├── main_greedy.py          # CLI entry point — VOX → OBJ/GLB
├── bake.py                 # Bake pipeline — OBJ → baked texture + FBX
├── pipeline.ps1            # PowerShell helper — full pipeline in one command
├── vox_reader.py           # .vox parser (SIZE, XYZI, RGBA, nTRN, nGRP, nSHP)
├── greedy_mesher.py        # Greedy meshing algorithm (numpy-accelerated)
├── greedy_obj_exporter.py  # OBJ + MTL + palette PNG exporter
├── glb_exporter.py         # GLB (glTF 2.0 binary) exporter
│
└── tools/                  # Diagnostic and inspection scripts
    ├── inspect_fbx.py      # Inspect FBX material/UV after import
    ├── inspect_mat.py      # Inspect OBJ material after import
    ├── debug_uv.py         # Debug UV values before/after decimate
    └── debug_blender_uv.py # Debug UV values stored by Blender
```

---

## How It Works

### 1. VOX Parsing (`vox_reader.py`)

Reads the full MagicaVoxel chunk tree (`nTRN` / `nGRP` / `nSHP`) to reconstruct the World View hierarchy. Each object's voxels are stored in **local coordinates** (0-based, for the greedy algorithm) with a separate **global offset** for correct world positioning.

### 2. Greedy Meshing (`greedy_mesher.py`)

For each of the 6 face directions:

1. Build a 3D numpy volume from voxel data (O(1) lookup, no dict hashing)
2. Slice into 2-D masks via numpy operations (vectorized, skips empty slices)
3. Expand each face greedily — first along U, then along V — merging same-color neighbors
4. Emit one quad per merged rectangle

```
Naive:   N faces  →  N quads  (one per voxel face)
Greedy:  N faces  →  M quads  (M << N for flat surfaces)

Speedup (numpy vs pure Python):
  Dense models:   ~2x  |  Surface models (typical voxel art): ~4–8x
```

### 3. T-junction Resolution (`greedy_obj_exporter.py`)

Greedy quads from adjacent planes can share an edge where a large quad meets several small ones, creating T-junctions. These cause visible seams in renderers. The resolver detects all open boundary edges and splits large quads at T-junction vertices, iterating until the mesh is fully sealed (manifold, no open edges).

### 4. Bake Pipeline (`bake.py`)

Runs in two stages without modifying the mesh geometry:

**Stage 1 — Pure Python baker:**

- Reads color index (`vt`) of each face directly from the OBJ
- Generates a texture with one solid-color tile per unique color
- Computes precise UV center coordinates per color

**Stage 2 — Blender headless:**

- Imports the OBJ with its original UV intact
- Rewrites UV layer (`UVMap_baked`) using exact `u → vt_idx` lookup — 0 misses
- Applies new material with baked texture and `interpolation = Closest`
- Removes original palette UV, sets `UVMap_baked` as active
- Exports FBX with textures packed

### 5. Export formats

| Format | Normals              | Colors           | Use case                              |
| ------ | -------------------- | ---------------- | ------------------------------------- |
| `.obj` | Flat (6 shared `vn`) | UV palette       | Blender editing, loop cuts            |
| `.glb` | Flat (per-vertex)    | RGB vertex color | Direct engine import                  |
| `.fbx` | Flat (face smooth)   | Baked UV texture | Engine (UE5, O3DE) — best performance |

---

## Roadmap

- [x] **Fase 1 — Performance** — numpy-accelerated greedy mesher (2–8x speedup)
- [x] **Fase 2 — Bake pipeline** — UV baked texture + FBX export via Blender headless
- [ ] **Fase 3 — C/C++ extension** — optional compiled core for 256³+ models
- [ ] **Fase 4 — Blender Add-on** — native add-on, in-memory mesh creation via `bpy`

---

## Known Limitations

- **Bake requires Blender** — the bake pipeline (`bake.py`) requires Blender 4.2+ installed. The greedy mesher (`main_greedy.py`) has no Blender dependency.
- **Performance** — large models (128³+) are slower in pure Python. Fase 3 addresses this.
- **Blender-focused** — tested against Blender 4.2–4.5. Import into other DCCs may work but is not validated.

---

## License

MIT — see `LICENSE` for details.
