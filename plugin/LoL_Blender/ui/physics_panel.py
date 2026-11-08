"""The Daza Physics tab: set buttons, bones, settings, collision, preview, clips, bake.

Bones are `prop_search` rows (typeable, all visible) rather than a list widget. The clip list is
the same data as the main panel's. Tab name and visibility come from the addon preferences.
"""

import os
import textwrap

import bpy
from bpy.types import Panel

from . import physics_props

#: Tab name, set from the addon preferences.
CATEGORY = "Daza Physics"

#: The tab's old default name, migrated in `apply_preferences`.
_RENAMED_FROM = "LoL Physics"

#: Labels shown under the intensity slider.
_FEEL = ((3, "Stiff"), (7, "Subtle"), (13, "Natural"), (17, "Flowing"), (20, "Wild!"))


def _preferences(context=None):
    context = context or bpy.context
    addon = context.preferences.addons.get(__package__.rsplit(".", 1)[0])
    return addon.preferences if addon else None


def armature_of(context):
    from ..core import shapes
    shape_rig = shapes.owner(context.object)
    if shape_rig is not None:
        return shape_rig
    obj = context.object
    if obj is not None and obj.type == 'ARMATURE':
        return obj
    for candidate in context.scene.objects:
        if candidate.type == 'ARMATURE':
            return candidate
    return None


def _shape_button(layout, context):
    """Body shape buttons. The shapes are ordinary objects, edited with G, R, S."""
    from ..core import shapes

    box = layout.box()
    column = box.column(align=True)
    column.scale_y = 1.2
    column.operator("lolblender.physics_set_shapes", icon='MESH_CUBE')
    armature = armature_of(context)
    if armature is not None:
        props = armature.lol_physics
        box.prop(props, "shape_mirror_enabled")
        row = box.row()
        row.enabled = props.shape_mirror_enabled
        row.prop(props, "shape_mirror_axis", expand=True)
        if props.shape_mirror_enabled:
            box.label(text="Rig local axes. Edit either side.", icon='MOD_MIRROR')
            for line in textwrap.wrap(props.shape_mirror_status, 42):
                box.label(text=line)

    if not shapes.exist(context.scene):
        note = box.column(align=True)
        note.scale_y = 0.7
        note.label(text="One shape per bone, on the collision bones")
        note.label(text="and on the branches. Move, rotate and")
        note.label(text="scale them; the collision is what you see.")
        return

    row = box.row(align=True)
    if shapes.shown(context.scene):
        row.operator("lolblender.physics_show_shapes", text="Hide Body Shapes",
                     icon='HIDE_ON', depress=True)
    else:
        row.operator("lolblender.physics_show_shapes", text="Show Body Shapes",
                     icon='HIDE_OFF')

    kinds = box.row(align=True)
    kinds.operator("lolblender.physics_shape_kind", text="Box",
                   icon='MESH_CUBE').kind = 'BOX'
    kinds.operator("lolblender.physics_shape_kind", text="Sphere",
                   icon='MESH_UVSPHERE').kind = 'SPHERE'
    hint = box.column(align=True)
    hint.scale_y = 0.7
    hint.label(text="Box or Sphere changes the selected shapes,")
    hint.label(text="or all of them when nothing is selected.")

    box.operator("lolblender.physics_reset_shapes", icon='LOOP_BACK')
    box.operator("lolblender.physics_remove_shapes", icon='TRASH')
    box.operator("lolblender.physics_group_shapes", icon='LINKED')
    row = box.row(align=True)
    row.operator("lolblender.physics_group_shapes", text="Select Group").operation = 'SELECT'
    row.operator("lolblender.physics_group_shapes", text="Ungroup").operation = 'UNGROUP'


def _feel(intensity):
    for limit, label in _FEEL:
        if intensity <= limit:
            return label
    return _FEEL[-1][1]


class _Base:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = CATEGORY

    @classmethod
    def poll(cls, context):
        return armature_of(context) is not None


class LOLPHYS_PT_main(_Base, Panel):
    bl_idname = "VIEW3D_PT_lolphysics"
    bl_label = "Daza Physics"

    @classmethod
    def poll(cls, _context):
        return True

    def draw(self, context):
        layout = self.layout
        armature = armature_of(context)
        if armature is None:
            layout.label(text="No armature in the scene", icon='INFO')
            return

        props = armature.lol_physics
        box = layout.box()
        row = box.row(align=True)
        row.label(text=armature.name, icon='ARMATURE_DATA')
        simulated = len(physics_props.listed_bones(armature))
        row.label(text="%d bone%s" % (simulated, "" if simulated == 1 else "s"))
        for line in textwrap.wrap(props.status, width=44):
            note = box.row()
            note.scale_y = 0.7
            note.label(text=line, icon='ERROR' if props.status.startswith('Physics: ') else 'INFO')

        # Hair / Cloth / Breast / Custom; every enabled set keeps simulating
        layout.prop(props, "active", expand=True)

        if props.active == physics_props.CUSTOM:
            self._custom(layout, props)

    def _custom(self, layout, props):
        """The four raw Wiggle 2 values, shown while Custom is active."""
        box = layout.box()
        column = box.column(align=True)
        column.prop(props, "custom_stiff")
        column.prop(props, "custom_damp")
        column.prop(props, "custom_gravity")
        column.prop(props, "custom_stretch")

        if props.custom_stiff > 450.0:
            warn = box.row()
            warn.scale_y = 0.7
            warn.label(text="Over ~450 the spring rings at 30 fps", icon='ERROR')

        box.operator("lolblender.physics_reset_custom",
                     text="Go Back to Default Values", icon='LOOP_BACK')


class LOLPHYS_PT_bones(_Base, Panel):
    bl_idname = "VIEW3D_PT_lolphysics_bones"
    bl_parent_id = "VIEW3D_PT_lolphysics"
    bl_label = "Bones"

    def draw(self, context):
        layout = self.layout
        armature = armature_of(context)
        props = armature.lol_physics
        identifier = physics_props.edited_set_id(props)
        physics_set = physics_props.get_set(armature, identifier)

        head = layout.row(align=True)
        head.prop(physics_set, "enabled", text="")
        filled = sum(1 for item in physics_set.bones if item.name)
        head.label(text="%s: %d bone%s" % (identifier.title(), filled, "" if filled == 1 else "s"))
        head.prop(physics_set, "num_branches", text="Branches")

        count = max(1, physics_set.num_branches)
        for branch in range(count):
            self._branch(layout, armature, physics_set, identifier, branch, count)


    def _branch(self, layout, armature, physics_set, identifier, branch, count):
        box = layout.box()
        rows = [(index, item) for index, item in enumerate(physics_set.bones)
                if item.branch == branch]

        header = box.row(align=True)
        header.label(text=physics_props.branch_label(branch, count), icon='GROUP_BONE')
        if count > 1:
            delete = header.operator("lolblender.physics_delete_branch", text="", icon='TRASH')
            delete.set_id = identifier
            delete.branch = branch

        if not rows:
            note = box.row()
            note.scale_y = 0.8
            note.label(text="(no bones yet)", icon='INFO')
        else:
            column = box.column(align=True)
            for slot, (index, item) in enumerate(rows):
                row = column.row(align=True)
                row.prop_search(item, "name", armature.pose, "bones", text="Bone %d" % (slot + 1))
                remove = row.operator("lolblender.physics_remove_bone", text="", icon='X')
                remove.set_id = identifier
                remove.index = index
                remove.target = 'BONES'

        row = box.row(align=True)
        add = row.operator("lolblender.physics_add_bone", text="Add Bone", icon='ADD')
        add.set_id = identifier
        add.branch = branch
        last = row.operator("lolblender.physics_remove_bone", text="Remove Last", icon='REMOVE')
        last.set_id = identifier
        last.index = -1
        last.branch = branch
        last.target = 'BONES'

        picked = box.operator("lolblender.physics_bones_from_selection",
                              text="Use Selected Bones", icon='RESTRICT_SELECT_OFF')
        picked.set_id = identifier
        picked.branch = branch
        picked.target = 'BONES'


class LOLPHYS_PT_settings(_Base, Panel):
    bl_idname = "VIEW3D_PT_lolphysics_settings"
    bl_parent_id = "VIEW3D_PT_lolphysics"
    bl_label = "Settings"

    def draw(self, context):
        layout = self.layout
        armature = armature_of(context)
        props = armature.lol_physics
        identifier = physics_props.edited_set_id(props)
        physics_set = physics_props.get_set(armature, identifier)

        column = layout.column(align=True)
        column.prop(physics_set, "intensity", slider=True)
        feel = column.row()
        feel.alignment = 'CENTER'
        feel.scale_y = 0.7
        feel.label(text=_feel(physics_set.intensity))
        column.prop(physics_set, "smoothness", slider=True)

        column.prop(physics_set, "gravity", slider=True)

        limit = layout.box()
        limit.prop(physics_set, "limit_movement")
        if physics_set.limit_movement:
            limit.prop(physics_set, "max_angle", slider=True)
            hint = limit.row()
            hint.scale_y = 0.7
            hint.alignment = 'CENTER'
            hint.label(text="degrees from the animated pose")
        elif identifier == 'BREAST':
            note = limit.column(align=True)
            note.scale_y = 0.7
            note.alert = True
            note.label(text="Off, a breast bone can turn past 140 degrees on a", icon='ERROR')
            note.label(text="fall or a fast attack. This is on by default for a")
            note.label(text="reason - turn it back on if the physics looks wrong.")

        motion = layout.box()
        row = motion.row(align=True)
        row.prop(physics_set, "show_motion", text="", emboss=False,
                 icon='DISCLOSURE_TRI_DOWN' if physics_set.show_motion
                 else 'DISCLOSURE_TRI_RIGHT')
        row.label(text="Motion (bake only)", icon='FORCE_WIND')
        if physics_set.show_motion:
            hint = motion.column(align=True)
            hint.scale_y = 0.7
            hint.label(text="On a run or a shaky attack the hair whips harder")
            hint.label(text="than it should. Motion measures how violently one")
            hint.label(text="bone changes direction and calms the swing on")
            hint.label(text="exactly those frames, leaving smooth ones alone.")
            motion.prop(physics_set, "motion", slider=True)
            if physics_set.motion < 1.0:
                row = motion.row(align=True)
                row.prop_search(physics_set, "motion_bone", armature.pose, "bones",
                                text="Watch")
                row.operator("lolblender.physics_motion_bone", text="", icon='EYEDROPPER')
                tail = motion.column(align=True)
                tail.scale_y = 0.7
                if physics_set.motion_bone.strip() in armature.pose.bones:
                    tail.label(text="1.00 = off. Lower calms the fast frames more.")
                else:
                    tail.label(text="Pick the bone that carries the character", icon='ERROR')
                    tail.label(text="(Root or the pelvis), or press the dropper.")

        layout.operator("lolblender.physics_reset_settings",
                        icon='LOOP_BACK').set_id = identifier


class LOLPHYS_PT_collision(_Base, Panel):
    bl_idname = "VIEW3D_PT_lolphysics_collision"
    bl_parent_id = "VIEW3D_PT_lolphysics"
    bl_label = "Body Collision"

    def draw_header(self, context):
        armature = armature_of(context)
        physics_set = physics_props.get_set(armature)
        if physics_set is not None:
            self.layout.prop(physics_set, "collision_enabled", text="")

    def draw(self, context):
        layout = self.layout
        armature = armature_of(context)
        identifier = physics_props.edited_set_id(armature.lol_physics)
        physics_set = physics_props.get_set(armature, identifier)

        layout.enabled = physics_set.collision_enabled
        column = layout.column(align=True)
        column.label(text="Collision Bones (comma-separated):", icon='BONE_DATA')
        column.prop(physics_set, "collider_text", text="")

        # a set's own bones are ignored as colliders; say so when pasted in
        own = {item.name for item in physics_set.bones if item.name}
        clashing = [item.name for item in physics_set.colliders if item.name in own]
        if clashing:
            box = column.box()
            box.label(text="Ignored - this set's own bones:", icon='ERROR')
            box.label(text=", ".join(clashing[:4])
                      + ("..." if len(clashing) > 4 else ""))

        row = layout.row(align=True)
        row.operator("lolblender.physics_all_colliders",
                     text="Select All Bones", icon='GROUP_BONE').set_id = identifier
        row.operator("lolblender.physics_auto_colliders",
                     text="Auto-Detect", icon='VIEWZOOM').set_id = identifier
        picked = layout.operator("lolblender.physics_bones_from_selection",
                                 text="Add Selected Bones", icon='RESTRICT_SELECT_OFF')
        picked.set_id = identifier
        picked.target = 'COLLIDERS'

        _shape_button(layout, context)


class LOLPHYS_PT_preview(_Base, Panel):
    bl_idname = "VIEW3D_PT_lolphysics_preview"
    bl_parent_id = "VIEW3D_PT_lolphysics"
    bl_label = "Preview"

    def draw(self, context):
        layout = self.layout
        armature = armature_of(context)
        props = armature.lol_physics

        row = layout.row(align=True)
        row.scale_y = 1.4
        row.prop(props, "live", icon='PHYSICS')
        row.operator("lolblender.physics_reset", text="", icon='LOOP_BACK')

        action = armature.animation_data.action if armature.animation_data else None
        info = layout.row()
        info.scale_y = 0.8
        info.label(text="Animation: %s" % (action.name if action else "none loaded"), icon='ANIM')


class LOLPHYS_PT_animations(_Base, Panel):
    bl_idname = "VIEW3D_PT_lolphysics_animations"
    bl_parent_id = "VIEW3D_PT_lolphysics"
    bl_label = "Animations"

    def draw(self, context):
        layout = self.layout
        scene = context.scene.lolblender

        row = layout.row(align=True)
        row.operator("lolblender.refresh_clips", text="Refresh", icon='FILE_REFRESH')
        row.operator("lolblender.pick_folder", text="", icon='FILEBROWSER')

        path = layout.row()
        path.scale_y = 0.7
        if scene.folder:
            parent = os.path.basename(os.path.dirname(scene.folder))
            path.label(text=".../%s/%s" % (parent, os.path.basename(scene.folder)),
                       icon='FILE_FOLDER')
            if scene.custom_folder:
                path.operator("lolblender.clear_folder", text="", icon='X')
        else:
            path.label(text="No folder selected", icon='FILE_FOLDER')

        if len(scene.animations):
            layout.template_list("LOLBLENDER_UL_animations", "physics",
                                 scene, "animations", scene, "active_index", rows=8)
            layout.prop(scene, "search", text="", icon='VIEWZOOM')


class LOLPHYS_PT_bake(_Base, Panel):
    bl_idname = "VIEW3D_PT_lolphysics_bake"
    bl_parent_id = "VIEW3D_PT_lolphysics"
    bl_label = "Bake"

    def draw(self, context):
        layout = self.layout
        armature = armature_of(context)
        props = armature.lol_physics

        row = layout.row(align=True)
        row.prop(props, "loop_mode", expand=True)
        if props.loop_mode == 'AUTO':
            from ..ops import physics as ops_physics

            action = armature.animation_data.action if armature.animation_data else None
            note = layout.row()
            note.scale_y = 0.7
            if action is None:
                note.label(text="no animation loaded")
            elif ops_physics.detected_loop(action):
                note.label(text="Loop detected: baked physics will close at the seam",
                           icon='FILE_REFRESH')
            else:
                note.label(text="reads as playing once: it will start from rest and ease in",
                           icon='FORWARD')

        column = layout.column(align=True)
        column.scale_y = 1.3
        column.operator("lolblender.physics_bake", icon='RENDER_ANIMATION')
        column.operator("lolblender.physics_fast_save_anm", icon='FILE_TICK')
        layout.operator("lolblender.physics_clear_bake", icon='TRASH')

        layout.separator()
        row = layout.row(align=True)
        row.label(text="Output Folder:", icon='FILE_FOLDER')
        row.operator("lolblender.physics_pick_export", text="", icon='FILEBROWSER')
        path = layout.row()
        path.scale_y = 0.7
        if props.export_folder:
            parent = os.path.basename(os.path.dirname(props.export_folder.rstrip("/\\")))
            path.label(text=".../%s/%s" % (parent,
                                           os.path.basename(props.export_folder.rstrip("/\\"))))
        else:
            path.label(text="(defaults to the folder the clips came from)")

        column = layout.column(align=True)
        column.scale_y = 1.5
        column.enabled = len(context.scene.lolblender.animations) > 0
        column.operator("lolblender.physics_apply_all", icon='NLA')

        note = layout.column(align=True)
        note.scale_y = 0.7
        note.label(text="A bake keys the rotation of every simulated")
        note.label(text="bone, so the exporter writes it like any")
        note.label(text="other animation.")


_panels = (
    LOLPHYS_PT_main,
    LOLPHYS_PT_bones,
    LOLPHYS_PT_settings,
    LOLPHYS_PT_collision,
    LOLPHYS_PT_preview,
    LOLPHYS_PT_animations,
    LOLPHYS_PT_bake,
)

_registered = []


def _unregister_panels():
    for cls in reversed(_registered):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    _registered.clear()


def apply_preferences(context=None):
    """Show, hide or rename the tab from the addon preferences. Safe to call at any time."""
    preferences = _preferences(context)
    show = getattr(preferences, "show_physics_panel", True)
    category = getattr(preferences, "physics_category", CATEGORY) or CATEGORY

    # migrate the old default name; a custom name is kept
    if category == _RENAMED_FROM:
        category = CATEGORY
        if preferences is not None:
            preferences["physics_category"] = CATEGORY

    _unregister_panels()
    if not show:
        return
    for cls in _panels:
        cls.bl_category = category
        bpy.utils.register_class(cls)
        _registered.append(cls)


def register():
    apply_preferences()


def unregister():
    _unregister_panels()
