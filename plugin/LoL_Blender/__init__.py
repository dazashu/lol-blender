"""LoL Blender - League of Legends import/export for Blender.

Licensed GPL-3.0-or-later. The `.sco` writer and the legacy `r3d2sklt` reader are derived from
lol_maya (GPL-3.0); the format crates it builds on are MIT/Apache-2.0.

Import and export for `.skn`, `.skl`, `.anm`, `.scb`, `.sco` and `.mapgeo`, a custom-bone contract
that keeps your own bones working, and a Rust bone-physics solver with live preview.
"""

bl_info = {
    "name": "Daza Tools",
    "author": "dazashu",
    "version": (2, 3, 0),
    "blender": (4, 2, 0),
    "location": "File > Import/Export, and View3D > Sidebar > Daza Tools",
    "description": "League of Legends SKN / SKL / ANM / SCB / SCO / MAPGEO import and export, with bone physics.",
    "doc_url": "",
    "support": "COMMUNITY",
    "category": "Import-Export",
}

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty

from . import compat
from . import native
from . import ops
from . import ui
from .core.vhds import local_path as _local_vhds_path


def _main_panel_changed(_self, context):
    from .ui import panel
    panel.apply_preferences(context)


def _toolbar_changed(_self, context):
    from .ui import toolbar
    toolbar.apply_preferences(context)


def _physics_panel_changed(_self, context):
    """Show, hide or rename the physics tab without a restart."""
    from .ui import physics_panel
    physics_panel.apply_preferences(context)


def _weight_panel_changed(_self, context):
    """Same, for the weight tab."""
    from .ui import weight_panel
    weight_panel.apply_preferences(context)


class LOLBLENDER_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    main_category: StringProperty(
        name="Main tab name",
        description="What the main tools tab is called in the sidebar",
        default="Daza Tools",
        update=_main_panel_changed,
    )

    toolbar_location: EnumProperty(
        name="Tool bar",
        description="Where the icon bar of the tools sits in the 3D viewport",
        items=(
            ('TOOL_HEADER', "Under the menus",
             "In the Tool Settings bar, just under the viewport menus (turned on if hidden)"),
            ('HEADER', "In the menu bar", "At the right end of the viewport header"),
            ('NONE', "Hidden", "No icon bar; the tools stay in the sidebar"),
        ),
        default='TOOL_HEADER',
        update=_toolbar_changed,
    )

    show_physics_panel: bpy.props.BoolProperty(
        name="Show the physics tab",
        description=(
            "Give the bone physics its own tab in the 3D view sidebar. Turn it off to get the tab "
            "out of the way; nothing that is already set up stops working"),
        default=True,
        update=_physics_panel_changed,
    )
    physics_category: bpy.props.StringProperty(
        name="Physics tab name",
        description="What the physics tab is called in the sidebar",
        default="Daza Physics",
        update=_physics_panel_changed,
    )

    show_weight_panel: bpy.props.BoolProperty(
        name="Show the weight tab",
        description=(
            "Give the automatic skin weights their own tab in the 3D view sidebar. Turn it off to "
            "get the tab out of the way; nothing already bound stops working"),
        default=True,
        update=_weight_panel_changed,
    )
    weight_category: bpy.props.StringProperty(
        name="Weight tab name",
        description="What the weight tab is called in the sidebar",
        default="Daza Weight",
        update=_weight_panel_changed,
    )


    vhds_path: StringProperty(
        name="VHDS folder or executable", subtype='FILE_PATH', default=_local_vhds_path(),
        description="Purchased VHDS installation folder or vhd.exe. The engine is not bundled")

    # --- import and export options, remembered across files (see ops/remember.py) ------

    skn_load_skeleton: BoolProperty(
        name="Import skeleton", default=True,
        description="Load the .skl of the same name beside the .skn and bind the mesh to it")
    skn_separate_submeshes: BoolProperty(
        name="Separate submeshes", default=True,
        description="One object per submesh instead of one mesh with a material slot each")
    skl_select_after: BoolProperty(
        name="Select after import", default=True,
        description="Leave the new armature selected and active")
    import_existing: EnumProperty(
        name="Existing rig",
        description="What to do with League objects already in the scene",
        items=(
            ('ASK', "Ask each time",
             "Open a dialog when there is already a League model in the scene, showing what is "
             "there and what would be removed"),
            ('KEEP', "Keep", "Import alongside what is already there"),
            ('FILE', "Replace same file", "Remove what came from this same file, then import"),
            ('ALL', "Replace all", "Remove every League rig and mesh, then import"),
        ),
        default='ASK')
    skl_existing: EnumProperty(
        name="Existing skeleton",
        description="What a .skl import does when the scene already has a skeleton",
        items=(
            ('UPDATE', "Update it",
             "Like lol_maya: put the file's joints onto the skeleton already in the scene"),
            ('ASK', "Ask each time", "Open a dialog when there is already a League model"),
            ('KEEP', "Keep", "Import alongside what is already there"),
            ('FILE', "Replace same file", "Remove what came from this same file, then import"),
            ('ALL', "Replace all", "Remove every League rig and mesh, then import"),
        ),
        default='UPDATE')
    skl_keep_meshes: BoolProperty(
        name="Bound meshes keep their shape", default=True,
        description="When a .skl updates a skeleton, bound meshes keep the shape they show")
    import_report_details: BoolProperty(
        name="Report details", default=True,
        description="Print per-file measurements to the console")
    anm_replace_existing: BoolProperty(
        name="Replace existing action", default=False,
        description="Clear the armature's current action first (lol_maya's 'delchannel')")
    anm_frame_offset: IntProperty(
        name="Frame offset", default=0,
        description="Start the animation at this frame instead of 0")
    anm_set_scene_range: BoolProperty(
        name="Set frame range and rate", default=True,
        description="Set the scene's frame range and playback rate from the clip")
    export_report_details: BoolProperty(
        name="Report details", default=True,
        description="Print what was written to the console")
    export_use_blender_normals: BoolProperty(
        name="Use Blender normals", default=False,
        description="Write the normals Blender is showing instead of the ones the .skn came with")
    export_write_skeleton: BoolProperty(
        name="Also write the .skl", default=True,
        description="Write the skeleton of the same basename beside the mesh")
    export_range_source: EnumProperty(
        name="Frame range",
        description="Which range a .anm export covers",
        items=(
            ('ACTION', "Action", "The range the assigned action actually covers"),
            ('SCENE', "Scene", "The scene's start and end frames"),
            ('CUSTOM', "Custom", "The range set on the operator"),
        ),
        default='ACTION')

    def draw(self, context):
        layout = self.layout

        warning = compat.version_warning()
        if warning:
            box = layout.box()
            box.alert = True
            for line in warning.split(" - "):
                box.label(text=line, icon='ERROR')

        row = layout.row()
        ok = native.available()
        row.alert = not ok
        row.label(
            text="Native module: %s" % native.status(),
            icon='CHECKMARK' if ok else 'ERROR',
        )
        if not ok:
            col = layout.column(align=True)
            col.label(text="Run build.ps1 to build it, or install a release zip.")

        box = layout.box()
        box.label(text="Sidebar", icon='MENU_PANEL')
        box.prop(self, "main_category")
        box.prop(self, "toolbar_location")
        box.prop(self, "show_physics_panel")
        row = box.row()
        row.enabled = self.show_physics_panel
        row.prop(self, "physics_category")
        box.prop(self, "show_weight_panel")
        row = box.row()
        row.enabled = self.show_weight_panel
        row.prop(self, "weight_category")


_classes = (
    LOLBLENDER_AddonPreferences,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    ops.register()
    ui.register()

    # report, do not raise: the preferences panel explains a missing native module
    if not native.available():
        print("[LoL Blender] %s" % native.status())


def unregister():
    ui.unregister()
    ops.unregister()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
