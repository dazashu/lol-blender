"""Scene state for the animation library.

The clip list is cached on Refresh, never rescanned in `draw()`. The current clip is tracked by
name so it stays highlighted after filtering.
"""

import bpy
from bpy.props import (
    CollectionProperty, EnumProperty, IntProperty, PointerProperty, StringProperty)


class LOLBLENDER_AnimItem(bpy.types.PropertyGroup):
    """One `.anm` found on disk."""

    name: StringProperty(name="Name")
    filepath: StringProperty(name="Path", subtype='FILE_PATH')


def _redraw(self, context):
    """Repaint the list after a filter change."""
    for area in getattr(context.screen, "areas", ()):
        if area.type == 'VIEW_3D':
            area.tag_redraw()


class LOLBLENDER_SceneProps(bpy.types.PropertyGroup):
    animations: CollectionProperty(type=LOLBLENDER_AnimItem)
    active_index: IntProperty(default=0)

    folder: StringProperty(
        name="Animations folder",
        description="Folder scanned for .anm files",
        subtype='DIR_PATH',
        default="",
    )
    custom_folder: StringProperty(
        name="Custom folder",
        description=(
            "Set when the folder was chosen by hand. Empty means it was derived from the imported "
            "rig, and it will follow the rig the next time Refresh is pressed"
        ),
        default="",
    )
    search: StringProperty(
        name="Search",
        description="Show only clips whose name contains this",
        default="",
        options={'TEXTEDIT_UPDATE'},
        update=_redraw,
    )
    current_clip: StringProperty(default="")

    # Physics settings live on the rig (`object.lol_physics`), not here.

    load_mode: EnumProperty(
        name="On click",
        description="What clicking a clip in the list does",
        items=(
            ('REPLACE', "Replace", "Clear the current action first, so clips never blend "
                                   "(lol_maya's 'delchannel')"),
            ('KEEP', "Keep", "Leave the existing action in place and add this one alongside it"),
        ),
        default='REPLACE',
    )


_classes = (
    LOLBLENDER_AnimItem,
    LOLBLENDER_SceneProps,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.lolblender = PointerProperty(type=LOLBLENDER_SceneProps)


def unregister():
    del bpy.types.Scene.lolblender
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
