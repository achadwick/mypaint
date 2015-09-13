/* This file is part of MyPaint.
 * Copyright (C) 2008-2011 by Martin Renold <martinxyz@gmx.ch>
 * Copyright (C) 2011-2015 by the MyPaint Development Team
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 */


#ifndef PYTHONTILEDSURFACE_H
#define PYTHONTILEDSURFACE_H

#include <Python.h>
#include <mypaint-surface.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct _MyPaintPythonTiledSurface MyPaintPythonTiledSurface;

MyPaintPythonTiledSurface *
mypaint_python_tiled_surface_new(PyObject *py_object);

MyPaintSurface *
mypaint_python_surface_factory(gpointer user_data);

#ifdef __cplusplus
}
#endif

#endif // PYTHONTILEDSURFACE_H


