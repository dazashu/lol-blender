"""File > Import/Export > League of Legends Map Geometry (.mapgeo).

Export offers every version the crate writes. Submeshes import as material slots on one mesh.
"""

import os

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper

from .. import native
from ..core import mapgeo as core
from ..core import replace

#: Every version `rs_mapgeo` writes.
_VERSIONS = (
    ('KEEP', "Same as the file", "Write the version the map was read as"),
    ('17', "17 (latest)", "The current map format"),
    ('15', "15", ""),
    ('14', "14", ""),
    ('13', "13 (Aram)", "The version ARAM maps use"),
    ('12', "12", ""),
    ('11', "11", ""),
    ('9', "9", ""),
    ('7', "7", ""),
    ('6', "6", ""),
    ('5', "5", ""),
    ('18', "18", "Newer than 17, and read by fewer tools"),
)


class LOLBLENDER_OT_import_mapgeo(bpy.types.Operator, ImportHelper):
    bl_idname = "lolblender.import_mapgeo"
    bl_label = "Import League Map"
    bl_description = "Import a League of Legends .mapgeo as a collection of meshes"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".mapgeo"
    filter_glob: StringProperty(default="*.mapgeo", options={'HIDDEN'})

    files: CollectionProperty(
        type=bpy.types.OperatorFileListElement, options={'HIDDEN', 'SKIP_SAVE'})
    directory: StringProperty(subtype='DIR_PATH', options={'HIDDEN', 'SKIP_SAVE'})

    limit: IntProperty(
        name="Model limit", default=0, min=0, max=100000,
        description=(
            "Stop after this many models. 0 imports the whole map. A full map is thousands of "
            "objects, and a limit is how you look at one without waiting for all of them"))
    report_details: BoolProperty(name="Report details", default=True)

    @replace.atomic_scene_import
    def execute(self, context):
        try:
            native.rs()
        except native.NativeMissing as e:
            self.report({'ERROR'}, str(e).splitlines()[0])
            return {'CANCELLED'}

        paths = ([os.path.join(self.directory, f.name) for f in self.files if f.name]
                 if self.files and self.directory else [self.filepath])
        paths = [p for p in paths if p]
        if not paths:
            self.report({'ERROR'}, "no .mapgeo selected")
            return {'CANCELLED'}

        total = 0
        warnings = []
        for path in paths:
            try:
                _collection, report = core.import_mapgeo(path, limit=self.limit)
            except Exception as e:                      # noqa: BLE001 - surfaced to the user
                self.report({'ERROR'}, "%s: %s" % (os.path.basename(path), e))
                return {'CANCELLED'}
            total += report.imported
            if self.report_details:
                print("[LoL Blender] %s" % os.path.basename(path))
                for line in report.summary():
                    print("    %s" % line)
            warnings.extend(report.warnings[:3])

        message = "imported %d model%s" % (total, "" if total == 1 else "s")
        if warnings:
            self.report({'WARNING'}, message + " - " + "; ".join(warnings))
        else:
            self.report({'INFO'}, message)
        return {'FINISHED'}


class LOLBLENDER_OT_export_mapgeo(bpy.types.Operator, ExportHelper):
    bl_idname = "lolblender.export_mapgeo"
    bl_label = "Export League Map"
    bl_description = (
        "Write the selected map objects back to a .mapgeo, using the original file as a donor for "
        "the scene graph and the vertex declarations")
    bl_options = {'REGISTER'}

    filename_ext = ".mapgeo"
    filter_glob: StringProperty(default="*.mapgeo", options={'HIDDEN'})

    version: EnumProperty(
        name="Version", items=_VERSIONS, default='KEEP',
        description="On-disk version to write. Keep it unless a tool downstream needs another")
    selected_only: BoolProperty(
        name="Selected only", default=False,
        description="Export the selected objects instead of every imported map object")
    donor: StringProperty(
        name="Donor", subtype='FILE_PATH', default="",
        description=(
            "The original .mapgeo. Left empty, the riot_ lookup runs and then the file the objects "
            "were imported from is used"))
    report_details: BoolProperty(name="Report details", default=True)

    def _objects(self, context):
        pool = context.selected_objects if self.selected_only else context.scene.objects
        return [o for o in pool if o.type == 'MESH' and core.INDEX in o]

    @classmethod
    def poll(cls, context):
        return any(o.type == 'MESH' and core.INDEX in o for o in context.scene.objects)

    def execute(self, context):
        try:
            native.rs()
        except native.NativeMissing as e:
            self.report({'ERROR'}, str(e).splitlines()[0])
            return {'CANCELLED'}

        objects = self._objects(context)
        if not objects:
            self.report({'ERROR'},
                        "no imported map objects to export - a .mapgeo export needs the models "
                        "this plugin imported, because the donor is what carries the scene graph")
            return {'CANCELLED'}

        version = None if self.version == 'KEEP' else int(self.version)
        donor = bpy.path.abspath(self.donor) if self.donor else None
        try:
            report = core.export_mapgeo(objects, self.filepath, donor=donor, version=version)
        except Exception as e:                          # noqa: BLE001 - surfaced to the user
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        if self.report_details:
            print("[LoL Blender] %s" % os.path.basename(self.filepath))
            for line in report.summary():
                print("    %s" % line)

        message = "wrote %d bytes, %d model%s re-encoded" % (
            report.bytes_written, report.edited, "" if report.edited == 1 else "s")
        if report.warnings:
            self.report({'WARNING'}, message + " - " + "; ".join(report.warnings[:2]))
        else:
            self.report({'INFO'}, message)
        return {'FINISHED'}


def _import_entry(self, _context):
    self.layout.operator(LOLBLENDER_OT_import_mapgeo.bl_idname,
                         text="League of Legends Map (.mapgeo)")


def _export_entry(self, _context):
    self.layout.operator(LOLBLENDER_OT_export_mapgeo.bl_idname,
                         text="League of Legends Map (.mapgeo)")


_classes = (LOLBLENDER_OT_import_mapgeo, LOLBLENDER_OT_export_mapgeo)


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
