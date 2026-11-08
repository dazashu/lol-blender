"""Every `bpy.app.version` gate lives here. Only Blender 4.2 is tested."""

import bpy

VERSION = bpy.app.version

#: The version the suite runs against.
SUPPORTED = (4, 2)


def is_supported():
    return VERSION[:2] == SUPPORTED


def version_warning():
    """A message for the preferences panel when running on an untested version, or None."""
    if is_supported():
        return None
    return (
        "Running on Blender %s. This addon is tested only on %d.%d - anything here may be "
        "wrong rather than merely unsupported." % (
            bpy.app.version_string, SUPPORTED[0], SUPPORTED[1])
    )


def set_custom_normals(mesh, normals):
    """Apply per-loop custom split normals (no auto smooth needed since 4.1)."""
    mesh.normals_split_custom_set(normals)
