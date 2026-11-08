"""The tools as an icon bar at the top of the 3D viewport, under the menus.

Drawn in the Tool Settings bar (or the header, see the preferences). The icons are lol_maya's shelf
icons where the button is the same tool; the rest are Blender's own.
"""

import os

import bpy
import bpy.utils.previews

#: icon name -> file in icons/, the lol_maya shelf image of the same tool.
_FILES = ("imp", "sepmat", "ef", "eye", "conform", "reverse", "uv", "gtbp", "setbp", "psw",
          "four", "rebind")

_previews = None


def _icon(name):
    if _previews is None or name not in _previews:
        return 0
    return _previews[name].icon_id


def _button(layout, operator, image=None, icon='NONE', **props):
    """One icon button: the lol_maya image when there is one, else a Blender icon."""
    value = _icon(image) if image else 0
    if value:
        op = layout.operator(operator, text="", icon_value=value)
    else:
        op = layout.operator(operator, text="", icon=icon)
    for key, item in props.items():
        setattr(op, key, item)
    return op


def _find_armature(context):
    from . import anim_library
    obj = context.view_layer.objects.active
    if obj is not None and obj.type == 'ARMATURE':
        return obj
    return anim_library.find_armature(context)


def draw(self, context):
    if context.area is None or context.area.type != 'VIEW_3D':
        return
    from .. import native
    if not native.available():
        return
    from ..core import rest_pose

    layout = self.layout
    if type(self).__name__ == "VIEW3D_HT_header":
        layout.separator_spacer()
    else:
        layout.separator()

    row = layout.row(align=True)
    _button(row, "lolblender.import_skn", "imp", 'IMPORT')
    _button(row, "lolblender.import_skl", icon='ARMATURE_DATA')

    row = layout.row(align=True)
    _button(row, "lolblender.lasso_select", icon='RESTRICT_SELECT_OFF')
    _button(row, "lolblender.new_submesh", icon='MATERIAL')
    _button(row, "lolblender.separate_by_material", "sepmat", 'MOD_EXPLODE')
    _button(row, "lolblender.split_shared_vertices", "ef", 'MOD_EDGESPLIT')
    space = context.space_data
    if space is not None and hasattr(space, "shading"):
        row.prop(space.shading, "show_xray", text="", icon='XRAY')

    row = layout.row(align=True)
    _button(row, "lolblender.show_face_normals", "eye", 'NORMALS_FACE')
    _button(row, "lolblender.unify_normals", "conform", 'MOD_NORMALEDIT')
    _button(row, "lolblender.flip_normals", "reverse", 'ARROW_LEFTRIGHT')
    _button(row, "lolblender.open_uv_editor", "uv", 'UV')

    row = layout.row(align=True)
    armature = _find_armature(context)
    locked = rest_pose.is_locked(armature)
    row.operator("lolblender.lock_skeleton", text="",
                 icon='LOCKED' if locked else 'UNLOCKED', depress=locked)
    _button(row, "lolblender.go_to_bind_pose", "gtbp", 'LOOP_BACK')
    _button(row, "lolblender.set_bind_pose", "setbp", 'PINNED')

    row = layout.row(align=True)
    row.operator("lolblender.weight_bind", text="", icon='MOD_ARMATURE')
    _button(row, "lolblender.weight_rebind", "rebind", 'FILE_REFRESH')
    _button(row, "lolblender.weight_paint", "psw", 'BRUSH_DATA')
    _button(row, "lolblender.prune_influences", "four", 'FILTER')
    row.operator("lolblender.weight_report", text="", icon='CHECKMARK')


_HEADERS = {
    'TOOL_HEADER': "VIEW3D_HT_tool_header",
    'HEADER': "VIEW3D_HT_header",
}


def _location(context=None):
    context = context or bpy.context
    addon = context.preferences.addons.get(__package__.rsplit(".", 1)[0])
    prefs = addon.preferences if addon else None
    return getattr(prefs, "toolbar_location", 'TOOL_HEADER')


def _detach():
    for name in _HEADERS.values():
        header = getattr(bpy.types, name, None)
        if header is not None:
            try:
                header.remove(draw)
            except ValueError:
                pass


def show_tool_header():
    """Turn on View > Tool Settings in every 3D view, so the bar is visible."""
    try:
        for screen in bpy.data.screens:
            for area in screen.areas:
                if area.type == 'VIEW_3D':
                    for space in area.spaces:
                        if space.type == 'VIEW_3D':
                            space.show_region_tool_header = True
    except (AttributeError, TypeError):
        pass                                    # restricted data during registration
    return None


def apply_preferences(context=None):
    _detach()
    location = _location(context)
    name = _HEADERS.get(location)
    if name is None:
        return
    getattr(bpy.types, name).append(draw)
    if location == 'TOOL_HEADER':
        if bpy.app.timers.is_registered(show_tool_header):
            return
        bpy.app.timers.register(show_tool_header, first_interval=0.0)


def register():
    global _previews
    _previews = bpy.utils.previews.new()
    folder = os.path.join(os.path.dirname(os.path.dirname(__file__)), "icons")
    for name in _FILES:
        path = os.path.join(folder, name + ".png")
        if os.path.isfile(path):
            _previews.load(name, path, 'IMAGE')
    apply_preferences()


def unregister():
    global _previews
    _detach()
    if bpy.app.timers.is_registered(show_tool_header):
        bpy.app.timers.unregister(show_tool_header)
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None
