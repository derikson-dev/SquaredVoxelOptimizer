/*
 * greedy_mesher_ext.c — C extension for SquaredVoxGameReady
 *
 * Implements greedy_mesh_plane() in C.
 *
 * Public API (mirrors greedy_mesher.py):
 *   greedy_mesh_plane(model, axis: str, direction: int) -> list[tuple]
 *   greedy_mesh_xp/xn/yp/yn/zp/zn(model)               -> list[tuple]
 *
 * Each returned tuple: (u, v, w, width, height, color_idx)
 *
 * Build:
 *   python setup.py build_ext --inplace
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

/* ── Volume ──────────────────────────────────────────────────────────────── */

typedef struct {
    uint8_t *data;   /* flat, C order: [x][y][z] */
    int sx, sy, sz;
} Volume;

static void volume_free(Volume *v) {
    free(v->data);
    v->data = NULL;
}

/*
 * Build a volume from model.voxels (iterable of (x, y, z, c) tuples).
 * Returns 0 on success, -1 on error (Python exception set).
 */
static int volume_build(Volume *vol, PyObject *model) {
    PyObject *size_obj = PyObject_GetAttrString(model, "size");
    if (!size_obj) return -1;
    if (!PyTuple_Check(size_obj) || PyTuple_GET_SIZE(size_obj) < 3) {
        Py_DECREF(size_obj);
        PyErr_SetString(PyExc_TypeError, "model.size must be a 3-tuple");
        return -1;
    }
    vol->sx = (int)PyLong_AsLong(PyTuple_GET_ITEM(size_obj, 0));
    vol->sy = (int)PyLong_AsLong(PyTuple_GET_ITEM(size_obj, 1));
    vol->sz = (int)PyLong_AsLong(PyTuple_GET_ITEM(size_obj, 2));
    Py_DECREF(size_obj);

    if (vol->sx <= 0 || vol->sy <= 0 || vol->sz <= 0) {
        PyErr_SetString(PyExc_ValueError, "model size must be positive");
        return -1;
    }

    size_t total = (size_t)vol->sx * vol->sy * vol->sz;
    vol->data = (uint8_t *)calloc(total, 1);
    if (!vol->data) { PyErr_NoMemory(); return -1; }

    PyObject *voxels = PyObject_GetAttrString(model, "voxels");
    if (!voxels) { volume_free(vol); return -1; }

    PyObject *iter = PyObject_GetIter(voxels);
    Py_DECREF(voxels);
    if (!iter) { volume_free(vol); return -1; }

    PyObject *item;
    while ((item = PyIter_Next(iter))) {
        int x = (int)PyLong_AsLong(PyTuple_GET_ITEM(item, 0));
        int y = (int)PyLong_AsLong(PyTuple_GET_ITEM(item, 1));
        int z = (int)PyLong_AsLong(PyTuple_GET_ITEM(item, 2));
        int c = (int)PyLong_AsLong(PyTuple_GET_ITEM(item, 3));
        Py_DECREF(item);
        if (x >= 0 && x < vol->sx &&
            y >= 0 && y < vol->sy &&
            z >= 0 && z < vol->sz && c > 0 && c <= 255) {
            vol->data[(size_t)x * vol->sy * vol->sz +
                      (size_t)y * vol->sz + z] = (uint8_t)c;
        }
    }
    Py_DECREF(iter);

    if (PyErr_Occurred()) { volume_free(vol); return -1; }
    return 0;
}

#define VOL_AT(v, x, y, z) \
    ((v)->data[(size_t)(x) * (v)->sy * (v)->sz + (size_t)(y) * (v)->sz + (z)])

/* ── Mask helpers ────────────────────────────────────────────────────────── */

/*
 * Fill mask[size_u * size_v] for one slice.
 * mask[u * size_v + v] = color if the face is visible, 0 otherwise.
 */
static void fill_mask_x(const Volume *vol, uint8_t *mask,
                         int w, int direction,
                         int size_u, int size_v) {
    int wn = w + direction;
    int in_bounds = (wn >= 0 && wn < vol->sx);
    for (int u = 0; u < size_u; u++) {
        for (int v = 0; v < size_v; v++) {
            uint8_t cur = VOL_AT(vol, w, u, v);
            uint8_t nxt = (in_bounds) ? VOL_AT(vol, wn, u, v) : 0;
            mask[u * size_v + v] = (cur != 0 && nxt == 0) ? cur : 0;
        }
    }
}

static void fill_mask_y(const Volume *vol, uint8_t *mask,
                         int w, int direction,
                         int size_u, int size_v) {
    int wn = w + direction;
    int in_bounds = (wn >= 0 && wn < vol->sy);
    for (int u = 0; u < size_u; u++) {
        for (int v = 0; v < size_v; v++) {
            uint8_t cur = VOL_AT(vol, u, w, v);
            uint8_t nxt = (in_bounds) ? VOL_AT(vol, u, wn, v) : 0;
            mask[u * size_v + v] = (cur != 0 && nxt == 0) ? cur : 0;
        }
    }
}

static void fill_mask_z(const Volume *vol, uint8_t *mask,
                         int w, int direction,
                         int size_u, int size_v) {
    int wn = w + direction;
    int in_bounds = (wn >= 0 && wn < vol->sz);
    for (int u = 0; u < size_u; u++) {
        for (int v = 0; v < size_v; v++) {
            uint8_t cur = VOL_AT(vol, u, v, w);
            uint8_t nxt = (in_bounds) ? VOL_AT(vol, u, v, wn) : 0;
            mask[u * size_v + v] = (cur != 0 && nxt == 0) ? cur : 0;
        }
    }
}

/* ── Greedy pack ─────────────────────────────────────────────────────────── */

/*
 * Greedy pack over a flat mask[size_u * size_v] (row-major).
 * Appends (u, v, w, width, height, color) tuples to result_list and
 * clears consumed cells in place. Returns 0 on success, -1 on error.
 */
static int greedy_pack(uint8_t *mask, int size_u, int size_v,
                       int w, PyObject *result_list) {
    for (int u = 0; u < size_u; u++) {
        int rs = u * size_v;
        for (int v = 0; v < size_v; v++) {
            uint8_t color = mask[rs + v];
            if (color == 0) continue;

            /* Expand width along U */
            int width = 1;
            while (u + width < size_u &&
                   mask[(u + width) * size_v + v] == color)
                width++;

            /* Expand height along V */
            int height = 1;
            while (v + height < size_v) {
                int ok = 1;
                for (int du = 0; du < width; du++) {
                    if (mask[(u + du) * size_v + (v + height)] != color) {
                        ok = 0; break;
                    }
                }
                if (!ok) break;
                height++;
            }

            for (int du = 0; du < width; du++) {
                memset(&mask[(u + du) * size_v + v], 0, height);
            }

            PyObject *t = Py_BuildValue("(iiiiii)",
                                        u, v, w, width, height, (int)color);
            if (!t) return -1;
            if (PyList_Append(result_list, t) < 0) { Py_DECREF(t); return -1; }
            Py_DECREF(t);
        }
    }
    return 0;
}

/* ── Main entry: greedy_mesh_plane ───────────────────────────────────────── */

static PyObject *
py_greedy_mesh_plane(PyObject *self, PyObject *args) {
    PyObject *model;
    const char *axis;
    int direction;

    if (!PyArg_ParseTuple(args, "Osi", &model, &axis, &direction))
        return NULL;

    Volume vol;
    if (volume_build(&vol, model) < 0) return NULL;

    int size_w, size_u, size_v;
    int axis_id;  /* 0 = x, 1 = y, 2 = z */

    if (axis[0] == 'x' && axis[1] == '\0') {
        axis_id = 0; size_w = vol.sx; size_u = vol.sy; size_v = vol.sz;
    } else if (axis[0] == 'y' && axis[1] == '\0') {
        axis_id = 1; size_w = vol.sy; size_u = vol.sx; size_v = vol.sz;
    } else if (axis[0] == 'z' && axis[1] == '\0') {
        axis_id = 2; size_w = vol.sz; size_u = vol.sx; size_v = vol.sy;
    } else {
        volume_free(&vol);
        PyErr_Format(PyExc_ValueError, "Invalid axis: '%s' (expected x, y or z)", axis);
        return NULL;
    }

    uint8_t *mask = (uint8_t *)malloc((size_t)size_u * size_v);
    if (!mask) { volume_free(&vol); return PyErr_NoMemory(); }

    PyObject *result = PyList_New(0);
    if (!result) { free(mask); volume_free(&vol); return NULL; }

    for (int w = 0; w < size_w; w++) {
        switch (axis_id) {
            case 0: fill_mask_x(&vol, mask, w, direction, size_u, size_v); break;
            case 1: fill_mask_y(&vol, mask, w, direction, size_u, size_v); break;
            case 2: fill_mask_z(&vol, mask, w, direction, size_u, size_v); break;
        }

        /* Skip empty slices before packing */
        int any = 0;
        for (int i = 0; i < size_u * size_v && !any; i++)
            any = mask[i] != 0;
        if (!any) continue;

        if (greedy_pack(mask, size_u, size_v, w, result) < 0) {
            Py_DECREF(result);
            free(mask);
            volume_free(&vol);
            return NULL;
        }
    }

    free(mask);
    volume_free(&vol);
    return result;
}

/* ── Convenience wrappers ────────────────────────────────────────────────── */

#define MAKE_WRAPPER(name, ax, dir) \
static PyObject * \
py_##name(PyObject *self, PyObject *args) { \
    PyObject *model; \
    if (!PyArg_ParseTuple(args, "O", &model)) return NULL; \
    PyObject *call_args = Py_BuildValue("(Osi)", model, ax, dir); \
    if (!call_args) return NULL; \
    PyObject *r = py_greedy_mesh_plane(self, call_args); \
    Py_DECREF(call_args); return r; \
}

MAKE_WRAPPER(greedy_mesh_xp, "x",  1)
MAKE_WRAPPER(greedy_mesh_xn, "x", -1)
MAKE_WRAPPER(greedy_mesh_yp, "y",  1)
MAKE_WRAPPER(greedy_mesh_yn, "y", -1)
MAKE_WRAPPER(greedy_mesh_zp, "z",  1)
MAKE_WRAPPER(greedy_mesh_zn, "z", -1)

/* ── Module definition ───────────────────────────────────────────────────── */

static PyMethodDef GreedyMesherMethods[] = {
    {"greedy_mesh_plane", py_greedy_mesh_plane, METH_VARARGS,
     "greedy_mesh_plane(model, axis, direction) -> list of (u,v,w,width,height,color)\n"
     "\n"
     "C implementation of the greedy meshing algorithm.\n"
     "axis: 'x', 'y', or 'z'. direction: +1 or -1.\n"},
    {"greedy_mesh_xp", py_greedy_mesh_xp, METH_VARARGS, "X+ faces"},
    {"greedy_mesh_xn", py_greedy_mesh_xn, METH_VARARGS, "X- faces"},
    {"greedy_mesh_yp", py_greedy_mesh_yp, METH_VARARGS, "Y+ faces"},
    {"greedy_mesh_yn", py_greedy_mesh_yn, METH_VARARGS, "Y- faces"},
    {"greedy_mesh_zp", py_greedy_mesh_zp, METH_VARARGS, "Z+ faces"},
    {"greedy_mesh_zn", py_greedy_mesh_zn, METH_VARARGS, "Z- faces"},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef greedy_mesher_ext_module = {
    PyModuleDef_HEAD_INIT,
    "greedy_mesher_ext",
    "C extension for the greedy voxel mesher (SquaredVoxGameReady).",
    -1,
    GreedyMesherMethods
};

PyMODINIT_FUNC
PyInit_greedy_mesher_ext(void) {
    return PyModule_Create(&greedy_mesher_ext_module);
}