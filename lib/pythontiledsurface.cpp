/* This file is part of MyPaint.
 * Copyright (C) 2008-2011 by Martin Renold <martinxyz@gmx.ch>
 * Copyright (C) 2011-2015 by the MyPaint Development Team
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 */

#include "pythontiledsurface.h"

#include "common.hpp"

#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#define NO_IMPORT_ARRAY
#include <numpy/arrayobject.h>

#include <mypaint-tiled-surface.h>
#include <mypaint-test-surface.h>

struct _MyPaintPythonTiledSurface {
    MyPaintTiledSurface parent;
    PyObject * py_obj;
};

// Forward declare
void free_tiledsurf(MyPaintSurface *surface);

static void
tile_request_start(MyPaintTiledSurface *tiled_surface, MyPaintTileRequest *request)
{
    MyPaintPythonTiledSurface *self = (MyPaintPythonTiledSurface *)tiled_surface;

    const gboolean readonly = request->readonly;
    const int tx = request->tx;
    const int ty = request->ty;
    PyArrayObject* rgba = NULL;

    rgba = (PyArrayObject*)PyObject_CallMethod(self->py_obj, "_get_tile_numpy", "(iii)", tx, ty, readonly);
    if (rgba == NULL) {
        request->buffer = NULL;
        printf("Python exception during _get_tile_numpy()!\n");
        if (PyErr_Occurred()) {
            PyErr_Print();
        }
    }
    else {

#ifdef HEAVY_DEBUG
        assert(PyArray_NDIM(rgba) == 3);
        assert(PyArray_DIM(rgba, 0) == tiled_surface->tile_size);
        assert(PyArray_DIM(rgba, 1) == tiled_surface->tile_size);
        assert(PyArray_DIM(rgba, 2) == 4);
        assert(PyArray_ISCARRAY(rgba));
        assert(PyArray_TYPE(rgba) == NPY_UINT16);
#endif
        // The underlying tile storage, for worker threads to process.
        request->buffer = (uint16_t*)PyArray_DATA(rgba);
        // Keep a reference to the array object itself,
        // till tile_request_end() is called.
        request->context = (gpointer)rgba;
    }
}

static void
tile_request_end(MyPaintTiledSurface *tiled_surface, MyPaintTileRequest *request)
{
    MyPaintPythonTiledSurface *self = (MyPaintPythonTiledSurface *)tiled_surface;

    const gboolean readonly = request->readonly;
    const int tx = request->tx;
    const int ty = request->ty;
    PyObject *result = NULL;
    PyArrayObject* rgba = NULL;

    rgba = (PyArrayObject *) request->context;
    result = (PyObject*)PyObject_CallMethod(self->py_obj,
                                            "_set_tile_numpy", "(iiOi)",
                                            tx, ty, rgba, readonly);
    if (rgba != NULL) {
        Py_DECREF((PyObject *)rgba);
        request->context = NULL;
        request->buffer = NULL;
    }

    if (result == NULL) {
        printf("Python exception during _set_tile_numpy()!\n");
        if (PyErr_Occurred()) {
            PyErr_Print();
        }
    }
    else {
        Py_DECREF((PyObject *)result);
    }
}


static void
mypaint_python_tiled_surface_process_tiles (MyPaintTiledSurface *self,
                                            MyPaintTileRequest **requests,
                                            int tiles_n)
{
    Py_BEGIN_ALLOW_THREADS
    mypaint_tiled_surface_process_tiles (self, requests, tiles_n);
    Py_END_ALLOW_THREADS
}


MyPaintPythonTiledSurface *
mypaint_python_tiled_surface_new(PyObject *py_object)
{
    MyPaintPythonTiledSurface *self = (MyPaintPythonTiledSurface *)malloc(sizeof(MyPaintPythonTiledSurface));

    mypaint_tiled_surface_init(&self->parent, tile_request_start, tile_request_end);
    self->parent.threadsafe_tile_requests = TRUE;

    // MyPaintTiledSurface vfuncs
    self->parent.process_tiles = mypaint_python_tiled_surface_process_tiles;

    // MyPaintSurface vfuncs
    self->parent.parent.destroy = free_tiledsurf;

    self->py_obj = py_object; // no need to incref

    return self;
}


void free_tiledsurf(MyPaintSurface *surface)
{
    MyPaintPythonTiledSurface *self = (MyPaintPythonTiledSurface *)surface;
    mypaint_tiled_surface_destroy(&self->parent);
    free(self);
}
