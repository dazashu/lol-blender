"""Drag and drop: `.skn`, `.skl`, `.scb` and `.sco` dropped on the 3D viewport are imported.

Blender 4.1+ file handlers. The import operators run straight away when a drop fills their
`directory` and `files`, with the options last used in the file browser.
"""

import bpy


def _in_view3d(context):
    return context.area is not None and context.area.type == 'VIEW_3D'


_HANDLERS = (
    ("LOLBLENDER_FH_skn", "League mesh", "lolblender.import_skn", ".skn"),
    ("LOLBLENDER_FH_skl", "League skeleton", "lolblender.import_skl", ".skl"),
    ("LOLBLENDER_FH_scb", "League static object", "lolblender.import_scb", ".scb"),
    ("LOLBLENDER_FH_sco", "League static object (legacy)", "lolblender.import_sco", ".sco"),
)


def _make(idname, label, operator, extension):
    return type(idname, (bpy.types.FileHandler,), {
        "bl_idname": idname,
        "bl_label": label,
        "bl_import_operator": operator,
        "bl_file_extensions": extension,
        "poll_drop": classmethod(lambda cls, context: _in_view3d(context)),
    })


_classes = ()


def register():
    global _classes
    if not hasattr(bpy.types, "FileHandler"):
        return                                          # Blender before 4.1: no drop import
    _classes = tuple(_make(*entry) for entry in _HANDLERS)
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    global _classes
    for cls in reversed(_classes):
        if cls.is_registered:
            bpy.utils.unregister_class(cls)
    _classes = ()
