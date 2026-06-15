#!/usr/bin/env python3
"""Build the Squared Voxel Optimizer add-on into an installable zip.

Run with the SAME Python your Blender uses, so the C extensions get the matching
ABI. Blender 5.1 uses Python 3.13:

    py -3.13 build_addon.py

What it does:
  1. Compiles the C extensions in src/  (setup.py build_ext --inplace)
  2. Copies the freshly built binaries INTO the squared_voxel_optimizer/ package
  3. Zips the package into dist/squared_voxel_optimizer.zip

Install the resulting zip once in Blender (Preferences > Add-ons > Install from
Disk). The binaries travel inside the zip, so you never copy .pyd by hand again.
If no compiler/binaries are available the add-on still works via the slower
pure-Python fallback.
"""
import os
import sys
import glob
import shutil
import subprocess
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "src")
PKG_NAME = "squared_voxel_optimizer"
PKG = os.path.join(ROOT, PKG_NAME)
DIST = os.path.join(ROOT, "dist")
BIN_GLOBS = ("*.pyd", "*.so")  # Windows / Linux+macOS


def run(cmd, cwd):
    print(">", " ".join(cmd), f"(cwd={os.path.relpath(cwd, ROOT)})")
    subprocess.run(cmd, cwd=cwd, check=True)


def main():
    if not os.path.isfile(os.path.join(SRC, "setup.py")):
        sys.exit("Missing src/setup.py — put greedy_mesher_ext.c, "
                 "tjunction_resolver.c and setup.py in src/.")
    if not os.path.isfile(os.path.join(PKG, "__init__.py")):
        sys.exit(f"Missing {PKG_NAME}/__init__.py")

    # 1) Build with THIS interpreter (its ABI == the .pyd tag == your Blender).
    run([sys.executable, "setup.py", "build_ext", "--inplace"], cwd=SRC)

    # 2) Refresh the bundled binaries in the package.
    for pat in BIN_GLOBS:
        for old in glob.glob(os.path.join(PKG, pat)):
            os.remove(old)
    copied = []
    for pat in BIN_GLOBS:
        for b in glob.glob(os.path.join(SRC, pat)):
            shutil.copy2(b, PKG)
            copied.append(os.path.basename(b))
    if copied:
        print("Bundled binaries:", ", ".join(copied))
    else:
        print("WARNING: no compiled binaries found — the add-on will fall back "
              "to the slower pure-Python mesher.")

    # 3) Zip the package, keeping the folder as the zip's top-level entry.
    os.makedirs(DIST, exist_ok=True)
    zip_path = os.path.join(DIST, f"{PKG_NAME}.zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for folder, _dirs, files in os.walk(PKG):
            if "__pycache__" in folder:
                continue
            for f in files:
                if f.endswith(".pyc"):
                    continue
                full = os.path.join(folder, f)
                rel = os.path.relpath(full, ROOT)  # "squared_voxel_optimizer/..."
                z.write(full, rel)

    print(f"\nDone -> {os.path.relpath(zip_path, ROOT)}")
    print("Install: Blender > Preferences > Add-ons > Install from Disk -> pick the zip.")


if __name__ == "__main__":
    main()
