from vox_reader import load_vox
from greedy_mesher import greedy_mesh_plane

model = load_vox("ShapeTest.vox")

for axis, direction in [
    ("x", 1),
    ("x", -1),
    ("y", 1),
    ("y", -1),
    ("z", 1),
    ("z", -1),
]:

    quads = greedy_mesh_plane(
        model,
        axis,
        direction
    )

    print(
        axis,
        direction,
        len(quads)
    )