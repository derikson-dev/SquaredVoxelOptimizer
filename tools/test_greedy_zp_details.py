from vox_reader import load_vox
from greedy_mesher import greedy_mesh_zp

model = load_vox("ShapeTest.vox")

quads = greedy_mesh_zp(model)

print("Quantidade:", len(quads))
print()

for i, quad in enumerate(quads):
    print(f"{i+1}: {quad}")