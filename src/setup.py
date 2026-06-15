"""
setup.py — Build script for the C extensions.

Builds two extensions:
  greedy_mesher_ext   — greedy meshing algorithm
  tjunction_resolver  — T-junction resolver for quad meshes

Usage:
    python setup.py build_ext --inplace

Compiler flags:
    -O3 -march=native    maximum speed on the build machine
    -DNDEBUG             strip assertions
    -fvisibility=hidden  clean symbol table (Linux/macOS)

Windows note:
    MSVC is used automatically and -march=native is omitted.
"""
import platform
from setuptools import setup, Extension

EXTRA_COMPILE_ARGS = ["-O3", "-DNDEBUG"]
if platform.system() != "Windows":
    EXTRA_COMPILE_ARGS += ["-march=native", "-fvisibility=hidden"]

extensions = [
    Extension(
        name="greedy_mesher_ext",
        sources=["greedy_mesher_ext.c"],
        extra_compile_args=EXTRA_COMPILE_ARGS,
        language="c",
    ),
    Extension(
        name="tjunction_resolver",
        sources=["tjunction_resolver.c"],
        extra_compile_args=EXTRA_COMPILE_ARGS,
        language="c",
    ),
]

setup(
    name="vox_greedy_extensions",
    version="1.0.0",
    description="C extensions for SquaredVoxGameReady",
    ext_modules=extensions,
    python_requires=">=3.8",
)