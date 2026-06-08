from vox_reader import load_vox
from greedy_mesher import greedy_mesh_zp

model = load_vox("ShapeTest.vox")

quads = greedy_mesh_zp(model)

print("Greedy Z+ Quads:", len(quads))