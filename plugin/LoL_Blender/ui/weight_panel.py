"""The Daza Weight tab: ARP's Bind Engine and Bind, plus selection summary, Rebind, Check,
Problems, Clean and Preferences."""

import bpy
from bpy.types import Panel

from ..core import weights

#: Tab name, set from the addon preferences.
CATEGORY = "Daza Weight"


class _Base:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = CATEGORY


class LOLWEIGHT_PT_main(_Base, Panel):
    bl_idname = "VIEW3D_PT_lolweight"
    bl_label = "Daza Weight"

    def draw(self, context):
        layout = self.layout
        props = context.scene.lol_weight

        armature = weights.armature_in(context)
        bones = weights.selected_bones(armature, context) if armature else []
        if armature and not props.selected_bones_only:
            bones = [b.name for b in armature.data.bones if b.use_deform and b.name != 'Picker']
        meshes = weights.selected_meshes(context)

        # what the Bind button will act on
        box = layout.box()
        column = box.column(align=True)
        if armature is None:
            column.label(text="No armature selected", icon='ERROR')
        else:
            column.label(text=armature.name, icon='ARMATURE_DATA')
            if bones:
                column.label(text="%d bone%s to bind" % (len(bones),
                                                         "" if len(bones) == 1 else "s"),
                             icon='BONE_DATA')
            else:
                column.label(text="Bind: no bones selected", icon='INFO')
        if meshes:
            column.label(text="%d mesh%s selected" % (len(meshes),
                                                      "" if len(meshes) == 1 else "es"),
                         icon='OUTLINER_OB_MESH')
            bound = len(weights.bound_meshes(meshes))
            if bound:
                column.label(text="%d already bound: Rebind / Unbind" % bound, icon='LINKED')
        else:
            column.label(text="No meshes selected", icon='ERROR')


class LOLWEIGHT_PT_engine(_Base, Panel):
    """ARP Skin tab > Bind Engine."""
    bl_idname = "VIEW3D_PT_lolweight_engine"
    bl_parent_id = "VIEW3D_PT_lolweight"
    bl_label = "Bind Engine"

    def draw(self, context):
        layout = self.layout
        props = context.scene.lol_weight

        layout.prop(props, "engine", text="Engine")

        if props.engine == 'HEAT_MAP':
            col = layout.column()
            col.separator()
            col.prop(props, "split_parts", text="Split Parts")
            col = layout.column()
            col.prop(props, "optimize_highres", text="Optimize High Res")
            col = layout.column(align=True)
            col.enabled = props.optimize_highres
            col.prop(props, "highres_threshold", text="Polycount Threshold")

        elif props.engine == 'PSEUDO_VOXELS':
            col = layout.column()
            col.separator()
            col.prop(props, "voxel_type", text="")
            col.prop(props, "voxel_resolution", text="Voxel Resolution")

            col = layout.column(align=True)
            col.label(text='If results are incorrect, try', icon='INFO')
            col.label(text='changing "Type" and "Voxel Resol."')

        elif props.engine == 'VHDS':
            col = layout.column()
            col.separator()
            col.prop(props, "vhds_resolution", text="Voxel Resolution")


class LOLWEIGHT_PT_vhds(_Base, Panel):
    """The VHDS addon's settings. Its selection options come from the Bind panel."""
    bl_idname = "VIEW3D_PT_lolweight_vhds"
    bl_parent_id = "VIEW3D_PT_lolweight"
    bl_label = "Voxel Heat Diffuse Skinning"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.scene.lol_weight.engine == 'VHDS'

    def draw(self, context):
        layout = self.layout
        props = context.scene.lol_weight
        layout.prop(props, "vhds_resolution", icon='BLENDER', toggle=True)
        layout.prop(props, "vhds_loops", icon='BLENDER', toggle=True)
        layout.prop(props, "vhds_samples", icon='BLENDER', toggle=True)
        layout.prop(props, "vhds_influence", icon='BLENDER', toggle=True)
        layout.prop(props, "vhds_falloff", icon='BLENDER', toggle=True)
        layout.prop(props, "vhds_solidify")
        layout.prop(props, "vhds_half_cpu")


class LOLWEIGHT_PT_bind(_Base, Panel):
    """ARP Skin tab > Bind, plus Rebind and Check."""
    bl_idname = "VIEW3D_PT_lolweight_bind"
    bl_parent_id = "VIEW3D_PT_lolweight"
    bl_label = "Bind"

    def draw(self, context):
        layout = self.layout
        props = context.scene.lol_weight

        col = layout.column()
        row = col.row()
        row.prop(props, "selected_bones_only", text="Selected Bones Only")
        if props.selected_bones_only:
            # where ARP shows its Deform collection: select the deforming bones
            row.operator("lolblender.weight_select_deform", text="", icon='HIDE_OFF')

        col.prop(props, "selected_vertices_only", text="Selected Vertices Only")
        col.prop(props, "bind_current_pose", text="Bind at Current Pose")
        col.prop(props, "apply_shape_keys", text="Apply Shape Keys")
        col.prop(props, "preserve_volume", text="Preserve Volume")
        col.prop(props, "scale_fix", text="Scale Fix")

        layout.separator()

        col_bind = layout.column(align=True)
        row = col_bind.row(align=True)
        row.scale_y = 1.3
        row.operator("lolblender.weight_bind", text="Bind")
        # Rebind and Unbind only show for already bound meshes
        meshes = weights.selected_meshes(context)
        bound = weights.bound_meshes(meshes)
        if weights.attached_meshes(meshes):
            row.operator("lolblender.weight_unbind", text="Unbind")
        row.enabled = not props.running

        row = col_bind.row(align=True)
        row.enabled = not props.running
        if bound:
            row.operator("lolblender.weight_rebind", text="Rebind", icon='FILE_REFRESH')
        row.operator("lolblender.weight_check", text="Check", icon='VIEWZOOM')

        if props.status and props.status != "Ready":
            layout.label(text=props.status, icon='INFO')


class LOLWEIGHT_PT_report(_Base, Panel):
    bl_idname = "VIEW3D_PT_lolweight_report"
    bl_parent_id = "VIEW3D_PT_lolweight"
    bl_label = "Problems"

    @classmethod
    def poll(cls, context):
        return bool(context.scene.lol_weight.report)

    def draw_header(self, context):
        report = context.scene.lol_weight.report
        blocking = sum(1 for item in report if item.blocking)
        self.layout.label(text="", icon='ERROR' if blocking else 'INFO')

    def draw(self, context):
        layout = self.layout
        props = context.scene.lol_weight
        blocking = [item for item in props.report if item.blocking]
        warnings = [item for item in props.report if not item.blocking]

        if blocking:
            box = layout.box()
            box.label(text="Must be fixed before binding:", icon='ERROR')
            for item in blocking:
                self._draw_one(box, item)

        if warnings:
            box = layout.box()
            box.label(text="Mesh warnings:", icon='INFO')
            for item in warnings:
                self._draw_one(box, item)

        row = layout.row(align=True)
        row.operator("lolblender.weight_clean", icon='BRUSH_DATA')

    @staticmethod
    def _draw_one(box, item):
        """One problem, with the fix wrapped by hand (labels do not wrap)."""
        column = box.column(align=True)
        column.label(text="%s: %s" % (item.target, item.summary),
                     icon='DOT')
        for line in _wrap(item.fix, 46):
            sub = column.row()
            sub.label(text="      " + line)
        column.separator()


def _wrap(text, width):
    """Greedy word wrap."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        candidate = word if not current else current + " " + word
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


class LOLWEIGHT_PT_settings(_Base, Panel):
    """ARP's binding preferences, stored on the scene."""
    bl_idname = "VIEW3D_PT_lolweight_settings"
    bl_parent_id = "VIEW3D_PT_lolweight"
    bl_label = "Preferences"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = context.scene.lol_weight
        col = layout.column(align=True)
        col.prop(props, "parent_objects", text="Parent Objects when Binding")
        col.prop(props, "remove_armature_modifiers",
                 text="Remove Existing Armature Modifiers when Binding")
        col.prop(props, "remove_vertex_groups", text="Remove Existing Vertex Groups when Binding")


class LOLWEIGHT_PT_clean(_Base, Panel):
    bl_idname = "VIEW3D_PT_lolweight_clean"
    bl_parent_id = "VIEW3D_PT_lolweight"
    bl_label = "Clean"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = context.scene.lol_weight
        layout.label(text="Run on the selected meshes:")
        column = layout.column(align=True)
        column.prop(props, "clean_transforms")
        column.prop(props, "clean_merge")
        column.prop(props, "clean_degenerate")
        column.prop(props, "clean_normals")
        row = layout.row()
        row.scale_y = 1.2
        row.operator("lolblender.weight_clean", icon='BRUSH_DATA')


# registration order is display order
_classes = (
    LOLWEIGHT_PT_main,
    LOLWEIGHT_PT_engine,
    LOLWEIGHT_PT_vhds,
    LOLWEIGHT_PT_bind,
    LOLWEIGHT_PT_report,
    LOLWEIGHT_PT_clean,
    LOLWEIGHT_PT_settings,
)


def apply_preferences(context=None):
    """Re-register the panels under the tab name from the preferences (or hide them)."""
    global CATEGORY
    context = context or bpy.context
    addon = context.preferences.addons.get(__package__.rsplit(".", 1)[0])
    prefs = addon.preferences if addon else None
    wanted = getattr(prefs, "weight_category", CATEGORY) or CATEGORY
    shown = getattr(prefs, "show_weight_panel", True)

    for cls in _classes:
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass                                    # not registered yet
    CATEGORY = wanted
    if not shown:
        return
    for cls in _classes:
        cls.bl_category = wanted
        bpy.utils.register_class(cls)


def register():
    # through apply_preferences so the saved tab name is used from the start
    apply_preferences()


def unregister():
    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
