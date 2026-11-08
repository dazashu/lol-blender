"""File > Import/Export > League of Legends Static Object (.scb, and legacy text .sco).

Import detects the format from the file; export writes the format of the chosen entry. Import one
and export the other to convert.
"""

import os

import bpy
from bpy.props import BoolProperty, CollectionProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper

from .. import native
from ..core import static_mesh
from ..core import replace
from . import remember


class _ImportStatic(ImportHelper):
    """Shared importer. Subclasses only set the file filter."""

    files: CollectionProperty(
        type=bpy.types.OperatorFileListElement, options={'HIDDEN', 'SKIP_SAVE'})
    directory: StringProperty(subtype='DIR_PATH', options={'HIDDEN', 'SKIP_SAVE'})

    report_details: BoolProperty(name="Report details", default=True)

    def draw(self, context):
        self.layout.prop(self, "report_details")

    def invoke(self, context, event):
        remember.load(self, context, remember.IMPORT_STATIC)
        if self.directory and self.files:
            # dropped on the viewport: the file is already chosen
            return self.execute(context)
        return super().invoke(context, event)

    @replace.atomic_scene_import
    def execute(self, context):
        remember.save(self, context, remember.IMPORT_STATIC)
        try:
            native.rs()
        except native.NativeMissing as e:
            self.report({'ERROR'}, str(e).splitlines()[0])
            return {'CANCELLED'}

        paths = []
        if self.files and self.directory:
            paths = [os.path.join(self.directory, f.name) for f in self.files if f.name]
        if not paths and self.filepath:
            paths = [self.filepath]
        if not paths:
            self.report({'ERROR'}, "no file selected")
            return {'CANCELLED'}

        created = []
        for path in paths:
            try:
                obj, report = static_mesh.import_static(path)
            except Exception as e:                      # noqa: BLE001
                self.report({'ERROR'}, "%s: %s" % (os.path.basename(path), e))
                return {'CANCELLED'}
            created.append(obj)
            if self.report_details:
                print("[LoL Blender] %s" % os.path.basename(path))
                for line in report.summary():
                    print("    %s" % line)

        if created:
            bpy.ops.object.select_all(action='DESELECT')
            for obj in created:
                obj.select_set(True)
            context.view_layer.objects.active = created[-1]
        self.report({'INFO'}, "imported %d static object%s" % (
            len(created), "" if len(created) == 1 else "s"))
        return {'FINISHED'}


class LOLBLENDER_OT_import_scb(bpy.types.Operator, _ImportStatic):
    bl_idname = "lolblender.import_scb"
    bl_label = "Import League Static Object (.scb)"
    bl_description = "Import a .scb static mesh - a map prop, a weapon pickup, anything with no skeleton"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".scb"
    filter_glob: StringProperty(default="*.scb", options={'HIDDEN'})


class LOLBLENDER_OT_import_sco(bpy.types.Operator, _ImportStatic):
    bl_idname = "lolblender.import_sco"
    bl_label = "Import League Static Object (.sco)"
    bl_description = (
        "Import a .sco static mesh. .sco is the old TEXT container and the game no longer loads "
        "it - import one and export a .scb to convert it")
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".sco"
    filter_glob: StringProperty(default="*.sco", options={'HIDDEN'})


class _ExportStatic(ExportHelper):
    """Shared exporter. The subclass `filename_ext` picks the writer."""

    report_details: BoolProperty(name="Report details", default=True)

    @classmethod
    def poll(cls, context):
        obj = context.view_layer.objects.active
        return obj is not None and obj.type == 'MESH'

    def draw(self, context):
        self.layout.prop(self, "report_details")

    def invoke(self, context, event):
        remember.load(self, context, remember.EXPORT_STATIC)
        return super().invoke(context, event)

    def execute(self, context):
        remember.save(self, context, remember.EXPORT_STATIC)
        obj = context.view_layer.objects.active
        try:
            native.rs()
            report = static_mesh.export_static(obj, self.filepath)
        except Exception as e:                          # noqa: BLE001
            self.report({'ERROR'}, "%s: %s" % (os.path.basename(self.filepath), e))
            return {'CANCELLED'}

        if self.report_details:
            print("[LoL Blender] %s" % os.path.basename(self.filepath))
            for line in report.summary():
                print("    %s" % line)
        self.report({'INFO'}, "wrote %s (%d faces)" % (
            os.path.basename(self.filepath), report.face_count))
        return {'FINISHED'}


class LOLBLENDER_OT_export_scb(bpy.types.Operator, _ExportStatic):
    bl_idname = "lolblender.export_scb"
    bl_label = "Export League Static Object (.scb)"
    bl_description = "Write the active mesh as a .scb static mesh, the binary container the game loads"
    bl_options = {'REGISTER'}

    filename_ext = ".scb"
    filter_glob: StringProperty(default="*.scb", options={'HIDDEN'})


class LOLBLENDER_OT_export_sco(bpy.types.Operator, _ExportStatic):
    bl_idname = "lolblender.export_sco"
    bl_label = "Export League Static Object (.sco)"
    bl_description = (
        "Write the active mesh as a .sco static mesh. .sco is the old TEXT container and the game "
        "no longer loads it - write a .scb unless something else needs the text form")
    bl_options = {'REGISTER'}

    filename_ext = ".sco"
    filter_glob: StringProperty(default="*.sco", options={'HIDDEN'})


def _import_entry(self, context):
    layout = self.layout
    layout.operator(LOLBLENDER_OT_import_scb.bl_idname,
                    text="League of Legends Static Object (.scb)")
    layout.operator(LOLBLENDER_OT_import_sco.bl_idname,
                    text="League of Legends Static Object (.sco, legacy)")


def _export_entry(self, context):
    layout = self.layout
    layout.operator(LOLBLENDER_OT_export_scb.bl_idname,
                    text="League of Legends Static Object (.scb)")
    layout.operator(LOLBLENDER_OT_export_sco.bl_idname,
                    text="League of Legends Static Object (.sco, legacy)")


_classes = (
    LOLBLENDER_OT_import_scb,
    LOLBLENDER_OT_import_sco,
    LOLBLENDER_OT_export_scb,
    LOLBLENDER_OT_export_sco,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(_import_entry)
    bpy.types.TOPBAR_MT_file_export.append(_export_entry)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(_export_entry)
    bpy.types.TOPBAR_MT_file_import.remove(_import_entry)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
