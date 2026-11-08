"""View3D > Sidebar > Daza Tools: rig info, import/export, bones, clips, tools.

Physics and weights have their own tabs. Import options stay in the file browser.
"""

import os

import bpy
from bpy.types import Operator, Panel

from .. import native
from ..core import registry as reg
from . import anim_library


class _Base:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Daza Tools"


class LOLBLENDER_PT_main(_Base, Panel):
    bl_idname = "VIEW3D_PT_lolblender"
    bl_label = "Daza Tools"

    def draw(self, context):
        layout = self.layout

        if not native.available():
            box = layout.box()
            box.alert = True
            box.label(text="Native module not loaded", icon='ERROR')
            box.label(text=native.status())
            box.label(text="Run build.ps1, then restart Blender.")
            return

        armature = anim_library.find_armature(context)
        box = layout.box()
        if armature is None:
            box.label(text="No League rig in the scene", icon='INFO')
        else:
            row = box.row(align=True)
            row.label(text=armature.name, icon='ARMATURE_DATA')
            row.label(text="%d bones" % len(armature.data.bones))

            registry = reg.read_registry(armature)
            custom = sum(1 for b in armature.data.bones if not reg.is_native(b, registry))
            if custom:
                sub = box.row()
                sub.label(text="%d bone%s added by you" % (custom, "" if custom == 1 else "s"),
                          icon='BONE_DATA')
            action = (armature.animation_data.action
                      if armature.animation_data is not None else None)
            if action is not None:
                box.row().label(text=action.name, icon='ACTION')


class LOLBLENDER_PT_import(_Base, Panel):
    bl_idname = "VIEW3D_PT_lolblender_import"
    bl_parent_id = "VIEW3D_PT_lolblender"
    bl_label = "Import"

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.scale_y = 1.3
        col.operator("lolblender.import_skn", text="Mesh (.skn)", icon='MESH_DATA')
        col.operator("lolblender.import_skl", text="Skeleton (.skl)", icon='ARMATURE_DATA')
        col.operator("lolblender.import_anm", text="Animation (.anm)", icon='ANIM')
        col.operator("lolblender.import_scb", text="Static mesh (.scb)", icon='MESH_ICOSPHERE')
        col.operator("lolblender.import_sco", text="Static mesh (.sco, legacy)",
                     icon='MESH_ICOSPHERE')
        col.operator("lolblender.import_mapgeo", text="Map (.mapgeo)", icon='WORLD')

class LOLBLENDER_PT_export(_Base, Panel):
    bl_idname = "VIEW3D_PT_lolblender_export"
    bl_parent_id = "VIEW3D_PT_lolblender"
    bl_label = "Export"

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.scale_y = 1.3
        col.operator("lolblender.export_skin", text="Skin (.skn + .skl)", icon='EXPORT')
        col.operator("lolblender.export_skn", text="Mesh (.skn)", icon='MESH_DATA')
        col.operator("lolblender.export_skl", text="Skeleton (.skl)", icon='ARMATURE_DATA')
        col.operator("lolblender.export_anm", text="Animation (.anm)", icon='ANIM')
        col.operator("lolblender.export_scb", text="Static mesh (.scb)", icon='MESH_ICOSPHERE')
        col.operator("lolblender.export_sco", text="Static mesh (.sco, legacy)",
                     icon='MESH_ICOSPHERE')
        col.operator("lolblender.export_mapgeo", text="Map (.mapgeo)", icon='WORLD')

        note = layout.column(align=True)
        note.scale_y = 0.7
        note.label(text="Skin writes the .skn first: it decides the")
        note.label(text="influence table that the .skl has to store.")


class LOLBLENDER_PT_bones(_Base, Panel):
    bl_idname = "VIEW3D_PT_lolblender_bones"
    bl_parent_id = "VIEW3D_PT_lolblender"
    bl_label = "Bones"

    def draw(self, context):
        layout = self.layout
        armature = anim_library.find_armature(context)
        if armature is None:
            layout.label(text="No League rig", icon='INFO')
            return

        native, custom, helper = reg.classify(armature)
        added = custom - helper

        col = layout.column(align=True)
        col.scale_y = 0.8
        col.label(text="%d joints from the .skl" % len(native), icon='BONE_DATA')
        if added:
            row = col.row()
            row.alert = True
            row.label(text="%d not yet a joint" % len(added), icon='ERROR')
        if helper:
            col.label(text="%d helper%s, kept out of the file"
                           % (len(helper), "" if len(helper) == 1 else "s"), icon='CON_KINEMATIC')

        layout.operator("lolblender.check_rig", icon='CHECKMARK')
        layout.operator("lolblender.promote_bones", icon='ADD')

        row = layout.row(align=True)
        row.operator("lolblender.mark_helper", text="Mark helper", icon='CON_KINEMATIC').helper = True
        row.operator("lolblender.mark_helper", text="Unmark", icon='X').helper = False

        note = layout.column(align=True)
        note.scale_y = 0.7
        note.label(text="Bones you add become real joints by")
        note.label(text="themselves when you leave Edit Mode.")


class LOLBLENDER_PT_animations(_Base, Panel):
    bl_idname = "VIEW3D_PT_lolblender_animations"
    bl_parent_id = "VIEW3D_PT_lolblender"
    bl_label = "Animations"

    def draw(self, context):
        layout = self.layout
        props = context.scene.lolblender

        row = layout.row(align=True)
        row.operator("lolblender.refresh_clips", text="Refresh", icon='FILE_REFRESH')
        row.operator("lolblender.reload_clips_from_disk", text="Reload disk", icon='IMPORT')
        row.operator("lolblender.pick_folder", text="", icon='FILEBROWSER')
        if props.custom_folder:
            row.operator("lolblender.clear_folder", text="", icon='X')

        line = layout.row()
        line.scale_y = 0.75
        if props.folder:
            parent = os.path.basename(os.path.dirname(props.folder))
            line.label(text=".../%s/%s" % (parent, os.path.basename(props.folder)),
                       icon='FILE_FOLDER')
        else:
            line.label(text="No folder yet - press Refresh", icon='FILE_FOLDER')

        if not len(props.animations):
            return

        needle = props.search.lower()
        if needle:
            visible = sum(1 for i in props.animations if needle in i.name.lower())
            title = "Clips (%d/%d)" % (visible, len(props.animations))
        else:
            title = "Clips (%d)" % len(props.animations)
        layout.label(text=title, icon='ANIM')
        layout.template_list(
            "LOLBLENDER_UL_animations", "",
            props, "animations", props, "active_index", rows=8)
        layout.prop(props, "search", text="", icon='VIEWZOOM')

        layout.prop(props, "load_mode", text="On click")
        layout.operator("lolblender.load_all_clips", text="Load all as Actions", icon='NLA')


class LOLBLENDER_PT_tools(_Base, Panel):
    bl_idname = "VIEW3D_PT_lolblender_tools"
    bl_parent_id = "VIEW3D_PT_lolblender"
    bl_label = "Tools"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        box.label(text="Mesh", icon='MESH_DATA')
        box.operator("lolblender.separate_by_material", icon='MOD_EXPLODE')
        box.operator("lolblender.split_shared_vertices", icon='MOD_EDGESPLIT')

        box = layout.box()
        box.label(text="Submesh", icon='MATERIAL')
        row = box.row(align=True)
        row.operator("lolblender.lasso_select", icon='RESTRICT_SELECT_OFF')
        row.operator("lolblender.new_submesh", icon='ADD')

        box = layout.box()
        box.label(text="Normals", icon='NORMALS_FACE')
        row = box.row(align=True)
        row.operator("lolblender.show_face_normals", text="Show", icon='HIDE_OFF')
        row.operator("lolblender.unify_normals", text="Unify", icon='MOD_NORMALEDIT')
        box.operator("lolblender.flip_normals", icon='ARROW_LEFTRIGHT')

        box = layout.box()
        box.label(text="UV", icon='UV')
        box.operator("lolblender.open_uv_editor", icon='UV_DATA')
        grid = box.grid_flow(row_major=True, columns=2, align=True)
        for corner, label in (('TL', "Top left"), ('TR', "Top right"),
                              ('BL', "Bottom left"), ('BR', "Bottom right")):
            grid.operator("lolblender.uv_to_corner", text=label).corner = corner

        box = layout.box()
        box.label(text="Pose", icon='ARMATURE_DATA')
        from ..core import rest_pose
        armature = anim_library.find_armature(context)
        locked = rest_pose.is_locked(armature)
        box.operator("lolblender.lock_skeleton",
                     text="Skeleton locked" if locked else "Lock skeleton",
                     icon='LOCKED' if locked else 'UNLOCKED', depress=locked)
        box.operator("lolblender.go_to_bind_pose", icon='LOOP_BACK')
        box.operator("lolblender.set_bind_pose", icon='PINNED')

        box = layout.box()
        box.label(text="Weights", icon='MOD_VERTEX_WEIGHT')
        box.operator("lolblender.weight_paint", icon='BRUSH_DATA')
        box.operator("lolblender.weight_report", icon='CHECKMARK')
        box.operator("lolblender.prune_influences", icon='FILTER')


_classes = (
    LOLBLENDER_PT_main,
    LOLBLENDER_PT_import,
    LOLBLENDER_PT_export,
    LOLBLENDER_PT_bones,
    LOLBLENDER_PT_animations,
    LOLBLENDER_PT_tools,
)


def apply_preferences(context=None):
    """Apply the tab name from the preferences."""
    context = context or bpy.context
    addon = context.preferences.addons.get(__package__.rsplit(".", 1)[0])
    prefs = addon.preferences if addon else None
    wanted = (getattr(prefs, "main_category", "Daza Tools") or "").strip() or "Daza Tools"
    unregister()
    for cls in _classes:
        cls.bl_category = wanted
        bpy.utils.register_class(cls)


def register():
    apply_preferences()


def unregister():
    for cls in reversed(_classes):
        if cls.is_registered:
            bpy.utils.unregister_class(cls)
