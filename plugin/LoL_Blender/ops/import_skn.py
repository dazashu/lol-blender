"""File > Import > League of Legends Mesh (.skn). Options match lol_maya's SKN translator."""

import os

import bpy
from bpy.props import BoolProperty, EnumProperty, CollectionProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from .. import native
from . import ask
from . import remember
from ..core import replace
from ..core import skn_in


class LOLBLENDER_OT_import_skn(bpy.types.Operator, ImportHelper):
    bl_idname = "lolblender.import_skn"
    bl_label = "Import League Mesh"
    bl_description = "Import a League of Legends .skn, optionally with its .skl"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".skn"
    filter_glob: StringProperty(default="*.skn", options={'HIDDEN'})

    files: CollectionProperty(
        type=bpy.types.OperatorFileListElement, options={'HIDDEN', 'SKIP_SAVE'})
    directory: StringProperty(subtype='DIR_PATH', options={'HIDDEN', 'SKIP_SAVE'})

    load_skeleton: BoolProperty(
        name="Import skeleton",
        description=(
            "Load the .skl of the same name from the same directory and bind the mesh to it. "
            "lol_maya calls this 'skl' and defaults it on"
        ),
        default=True,
    )
    separate_submeshes: BoolProperty(
        name="Separate submeshes",
        description=(
            "One object per submesh, grouped under an empty, instead of one mesh with a material "
            "slot per submesh. lol_maya calls this 'sepmat'. Switches itself off when the file has "
            "only one submesh"
        ),
        default=True,
    )
    existing: EnumProperty(
        name="Existing rig",
        description=(
            "What to do with League objects already in the scene. Only objects this plugin "
            "imported are ever removed - anything you built yourself is left alone"
        ),
        items=(
            ('ASK', "Ask each time",
             "Open a dialog when there is already a League model in the scene, showing what is "
             "there and what would be removed"),
            ('KEEP', "Keep", "Import alongside what is already there"),
            ('FILE', "Replace same file",
             "Remove only what was imported from this same file, then import. This is the one for "
             "iterating on a character without rebuilding the scene"),
            ('ALL', "Replace all",
             "Remove every League rig and mesh in the scene, then import"),
        ),
        default='ASK',
    )
    report_details: BoolProperty(
        name="Report details",
        description="Print per-file measurements to the console",
        default=True,
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, "load_skeleton")
        layout.prop(self, "separate_submeshes")
        layout.prop(self, "report_details")
        layout.separator()
        layout.prop(self, "existing")

    def invoke(self, context, event):
        remember.load(self, context, remember.SKN)
        if self.directory and self.files:
            # dropped on the viewport: the file is already chosen
            return self.execute(context)
        return super().invoke(context, event)

    def execute(self, context):
        remember.save(self, context, remember.SKN)

        try:
            native.rs()
        except native.NativeMissing as e:
            self.report({'ERROR'}, str(e).splitlines()[0])
            return {'CANCELLED'}

        paths = self._paths()
        if not paths:
            self.report({'ERROR'}, "no .skn selected")
            return {'CANCELLED'}

        # ASK hands the rest of the import to the dialog
        if self.existing == 'ASK' and ask.needed(context, paths[0]):
            return ask.open_dialog(
                context,
                self._parked(context, paths),
                paths[0],
                os.path.basename(paths[0]))

        return self._run(context, paths, 'KEEP' if self.existing == 'ASK' else self.existing)

    def _parked(self, context, paths):
        """The rest of this import, for the dialog. Copies the options: `self` is freed by then."""
        options = self._options()

        def run(mode, report):
            return _import(context, paths, mode, options, report)

        return run

    def _options(self):
        return {
            "load_skeleton": self.load_skeleton,
            "separate_submeshes": self.separate_submeshes,
            "report_details": self.report_details,
        }

    def _run(self, context, paths, mode):
        return _import(context, paths, mode, self._options(), self.report)

    def _paths(self):
        if self.files and self.directory:
            named = [f.name for f in self.files if f.name]
            if named:
                return [os.path.join(self.directory, name) for name in named]
        return [self.filepath] if self.filepath else []


def _warnings(path, mesh_report, skl_report, options):
    """Warnings the user can act on."""
    name = os.path.basename(path)
    out = []

    if mesh_report.influence_overflow is not None:
        index, count = mesh_report.influence_overflow
        out.append(
            "%s: mesh and skeleton do not match (a vertex wants influence %d of %d) so no "
            "weights were applied" % (name, index, count))
    elif mesh_report.unbound and options["load_skeleton"]:
        out.append("%s: no .skl of the same name beside it, so the mesh is unbound" % name)

    if mesh_report.has_unweighted_vertices:
        out.append("%s: some vertices have no weights at all" % name)
    if mesh_report.validation_changed:
        out.append("%s: Blender's mesh validation altered the geometry" % name)
    if skl_report is not None and skl_report.shear_joints:
        out.append(
            "%s: %d joints carry bind SHEAR (max %.4g), which nothing in Blender can hold and "
            "which was dropped" % (name, len(skl_report.shear_joints), skl_report.max_shear))
    return out


@replace.transactional_import
def _import(context, paths, mode, options, report):
    """The import itself, with no operator, so the dialog can run it later."""
    # once, for the first file, so several meshes do not replace each other
    replaced = replace.apply(mode, paths[0])

    created = []
    warnings = []
    armatures = 0
    meshes = 0

    for path in paths:
        try:
            armature, objects, mesh_report, skl_report = skn_in.import_skn(
                path,
                load_skeleton=options["load_skeleton"],
                separate_submeshes=options["separate_submeshes"],
            )
        except Exception as e:                          # noqa: BLE001 - surfaced to the user
            report({'ERROR'}, "%s: %s" % (os.path.basename(path), e))
            return {'CANCELLED'}

        created.extend(objects)
        meshes += sum(1 for o in objects if o.type == 'MESH')
        if armature is not None:
            created.append(armature)
            armatures += 1

        if options["report_details"]:
            print("[LoL Blender] %s" % os.path.basename(path))
            for line in (skl_report.summary() if skl_report else []):
                print("    %s" % line)
            for line in mesh_report.summary():
                print("    %s" % line)

        warnings.extend(_warnings(path, mesh_report, skl_report, options))

    if created:
        bpy.ops.object.select_all(action='DESELECT')
        for obj in created:
            obj.select_set(True)
        context.view_layer.objects.active = created[-1]

    message = "imported %d mesh object%s%s" % (
        meshes, "" if meshes == 1 else "s",
        "" if armatures == 0 else " and %d skeleton%s" % (
            armatures, "" if armatures == 1 else "s"))

    if replaced:
        message += " (%s)" % replaced

    if warnings:
        report({'WARNING'}, message + " - " + "; ".join(warnings))
    else:
        report({'INFO'}, message)
    return {'FINISHED'}


def _menu_entry(self, context):
    self.layout.operator(
        LOLBLENDER_OT_import_skn.bl_idname,
        text="League of Legends Mesh (.skn)",
    )


_classes = (LOLBLENDER_OT_import_skn,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(_menu_entry)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(_menu_entry)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
