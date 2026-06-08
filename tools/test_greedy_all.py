from vox_reader import load_vox
from greedy_mesher import (
    greedy_mesh_zp,
    greedy_mesh_xp,
    greedy_mesh_xn,
    greedy_mesh_yp,
    greedy_mesh_yn,
    greedy_mesh_zn,
)

model = load_vox("ShapeTest.vox")

zp = greedy_mesh_zp(model)
xp = greedy_mesh_xp(model)
xn = greedy_mesh_xn(model)
yp = greedy_mesh_yp(model)
yn = greedy_mesh_yn(model)
zn = greedy_mesh_zn(model)

print("Z+:", len(zp))
print("X+:", len(xp))
print("X-:", len(xn))
print("Y+:", len(yp))
print("Y-:", len(yn))
print("Z-:", len(zn))

total = (
    len(zp)
    + len(xp)
    + len(xn)
    + len(yp)
    + len(yn)
    + len(zn)
)

print("TOTAL:", total)