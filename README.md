# SquaredVoxGameReady

> **MagicaVoxel → Greedy Mesh → Blender / Engine-ready**
>
> CLI pipeline that converts `.vox` files into optimized meshes (`.obj` / `.glb`) using the Greedy Meshing algorithm, ready for import into Blender — with zero triangle waste.

---

## Overview

MagicaVoxel stores voxel art as a 3-D grid of cubes. Exported naively, a single 32³ model can generate hundreds of thousands of faces. **SquaredVoxGameReady** collapses coplanar, same-color faces into the largest possible quads before any polygon ever leaves Python, cutting geometry by an order of magnitude.

```
MagicaVoxel (.vox)  →  SquaredVoxGameReady CLI  →  .obj / .glb  →  Blender  →  .fbx / engine
```

> **FBX export is handled entirely by Blender.** This project outputs `.obj` and `.glb`; FBX is generated from Blender's native exporter after import.

---

## Features

- **Greedy Meshing** — all 6 face directions, producing minimal quads
- **T-junction resolution** — seam-free, manifold meshes (no cracks in Blender)
- **Flat normals** — correct hard-edge shading per face out of the box
- **Palette texture** — 256-color PNG atlas auto-generated alongside `.obj`
- **Vertex colors** — embedded in `.glb` (no texture bleeding, works with `NEAREST`)
- **`KHR_materials_unlit`** — flat shading in `.glb` without a PBR setup
- **Multi-object support** — World View hierarchy preserved with per-object offsets
- **Configurable scale** — `1.0` for UE5 (cm) or `0.01` for O3DE (m)
- **Quad or triangle output** — quads for Blender loop-cuts; tris for max compatibility

---

## Requirements

- Python **3.8+**
- No third-party dependencies — standard library only

---

## Installation

```bash
git clone https://github.com/derikson-dev/SquaredVoxGameReady.git
cd SquaredVoxGameReady
```

No `pip install` needed.

---

## Quick Start

```bash
# Generate both .obj and .glb (default)
python main_greedy.py MyModel.vox

# Generate only OBJ
python main_greedy.py MyModel.vox .obj

# Generate only GLB
python main_greedy.py MyModel.vox .glb

# Generate both explicitly
python main_greedy.py MyModel.vox .obj .glb
```

Output files are placed next to the source `.vox` file:

```
MyModel_Greedy.obj
MyModel_Greedy.mtl
MyModel_Greedy_palette.png   ← auto-generated palette texture
MyModel_Greedy.glb
```

---

## Configuration

Open `main_greedy.py` and edit the two constants at the top:

```python
# ── Configuration ─────────────────────────────────────────────
VOXEL_SIZE  = 1.0     # UE5: 1.0 (cm)  |  O3DE: 0.01 (m)
TRIANGULATE = False   # False = quads   |  True  = triangles
# ──────────────────────────────────────────────────────────────
```

| Setting | Value | Use case |
|---|---|---|
| `VOXEL_SIZE` | `1.0` | Unreal Engine 5 (centimeters) |
| `VOXEL_SIZE` | `0.01` | O3DE (meters) |
| `TRIANGULATE` | `False` | Blender (preserves loop cuts) |
| `TRIANGULATE` | `True` | Maximum engine compatibility |

---

## Importing into Blender

1. Export with `python main_greedy.py MyModel.vox .obj` (quads, `TRIANGULATE = False`)
2. In Blender: **File → Import → Wavefront (.obj)**
3. Select `MyModel_Greedy.obj` — material and palette texture are linked automatically
4. From Blender: **File → Export → FBX** (or any format your engine needs)

> For `.glb` with vertex colors, use **File → Import → glTF 2.0** and set the material to display `Vertex Color` in the shader editor.

---

## Project Structure

```
SquaredVoxGameReady/
├── main_greedy.py          # CLI entry point
├── vox_reader.py           # .vox parser (SIZE, XYZI, RGBA, nTRN, nGRP, nSHP)
├── greedy_mesher.py        # Core greedy meshing algorithm (all 6 planes)
├── greedy_obj_exporter.py  # OBJ + MTL + palette PNG exporter
├── glb_exporter.py         # GLB (glTF 2.0 binary) exporter
│
├── test_greedy_zp.py       # Unit test: Z+ plane quad count
├── test_greedy_zp_details.py
├── test_greedy_planes.py   # All 6 planes
├── test_greedy_all.py      # All planes + total
└── test_greedy_compare.py  # Greedy vs. naive face count comparison
```

---

## How It Works

### 1. VOX Parsing (`vox_reader.py`)

Reads the full MagicaVoxel chunk tree (`nTRN` / `nGRP` / `nSHP`) to reconstruct the World View hierarchy. Each object's voxels are stored in **local coordinates** (0-based, for the greedy algorithm) with a separate **global offset** for correct world positioning.

### 2. Greedy Meshing (`greedy_mesher.py`)

For each of the 6 face directions:

1. Slice the volume into 2-D planes perpendicular to the face normal
2. Build a mask of exposed faces (voxel present, neighbor absent)
3. Expand each face greedily — first along U, then along V — merging same-color neighbors into the largest possible rectangle
4. Emit one quad per merged rectangle

```
Naive:   N faces  →  N quads  (one per voxel face)
Greedy:  N faces  →  M quads  (M << N for flat surfaces)
```

### 3. T-junction Resolution (`greedy_obj_exporter.py`)

Greedy quads from adjacent planes can share an edge where a large quad meets several small ones, creating T-junctions. These cause visible seams in renderers. The resolver:

- Detects all open (boundary) edges after initial meshing
- Splits any large quad where a T-junction vertex lies on its edge
- Iterates until the mesh is fully sealed (manifold, no open edges)

### 4. Export

| Format | Normals | Colors | Notes |
|---|---|---|---|
| `.obj` | Flat (6 shared `vn`) | UV-mapped palette | Quad or tri; MTL auto-linked |
| `.glb` | Flat (per-vertex, duplicated) | RGB float vertex color | `KHR_materials_unlit`; uint16/uint32 indices |

---

## Running the Tests

```bash
# Count quads on Z+ face
python test_greedy_zp.py

# Inspect each quad on Z+ face
python test_greedy_zp_details.py

# Count quads for all 6 planes
python test_greedy_planes.py

# Summary totals for all planes
python test_greedy_all.py

# Compare greedy vs. naive face count
python test_greedy_compare.py
```

---

## Roadmap

- [ ] **Performance** — support for models of 256×256×256 voxels or larger (current bottleneck: Python dict lookups and per-plane mask allocation)
- [ ] **Blender Add-on** — native add-on that runs the greedy pipeline inside Blender and creates mesh objects directly in memory (`bpy.data.meshes`) without intermediate files
- [ ] **C/C++ extension** — optional compiled core for the inner meshing loop to handle large models at native speed
- [ ] **Palette material** — auto-configure Blender material nodes from the palette PNG on import

---

## Known Limitations

- **FBX not supported** — FBX requires the proprietary Autodesk SDK, which is not available in Python. Use Blender to convert from `.obj` / `.glb` to `.fbx`.
- **Performance** — large models (128³+) are slow in pure Python. The roadmap item above addresses this.
- **Blender-focused** — the project is tested against Blender. Import into other DCCs (Maya, 3ds Max, Cinema 4D) may work but is not validated.

---

## License

MIT — see `LICENSE` for details.
