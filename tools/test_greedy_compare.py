from vox_reader import load_vox
from mesh_builder import build_visible_faces
from greedy_mesher import greedy_mesh_zp

model = load_vox("ShapeTest.vox")

faces = build_visible_faces(model)

quads = greedy_mesh_zp(model)

print("Faces visíveis:", len(faces))
print("Greedy Z+:", len(quads))