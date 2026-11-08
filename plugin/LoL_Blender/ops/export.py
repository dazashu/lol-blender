"""File > Export > League of Legends: skin (.skn + .skl), mesh, skeleton, animation.

Skin writes the .skn first: the mesh decides the influence table the .skl stores.
"""

import os

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ExportHelper

from .. import native
from ..core import anm_out
from ..core import registry as reg
from ..core import skl_out
from ..core import skn_out
from . import remember


def _target(context):
    from ..ui import anim_library
    return anim_library.find_armature(context)


def _write_skin_pair(armature, path, use_blender_normals=False, write_skeleton=True):
    """Write both files to temp first, then replace; restore the originals on failure."""
    import shutil
    import tempfile
    from pathlib import Path

    target = Path(path).absolute()
    kwargs, names, mesh_report = skn_out.build(
        armature, skn_out.collect_meshes(armature), str(target),
        use_blender_normals=use_blender_normals)
    jobs = [(target, kwargs, mesh_report, native.rs().write_skinned_mesh)]
    if write_skeleton:
        skl_target = target.with_suffix('.skl')
        skl_kwargs, skl_report = skl_out.build(armature, str(skl_target), influence_names=names)
        jobs.append((skl_target, skl_kwargs, skl_report, native.rs().write_skeleton))
    with tempfile.TemporaryDirectory(prefix='.daza-export-', dir=target.parent) as folder:
        staged = []
        for index, (destination, data, report, writer) in enumerate(jobs):
            temporary = Path(folder) / ('new-%d' % index)
            backup = Path(folder) / ('old-%d' % index)
            report.bytes_written = writer(str(temporary), **data)
            report.path = str(destination)
            existed = destination.exists()
            if existed:
                shutil.copy2(destination, backup)
            staged.append((temporary, destination, backup, existed))
        committed = []
        try:
            for temporary, destination, backup, existed in staged:
                os.replace(temporary, destination)
                committed.append((destination, backup, existed))
        except Exception:
            for destination, backup, existed in reversed(committed):
                if existed:
                    os.replace(backup, destination)
                else:
                    destination.unlink()
            raise
    return [job[2] for job in jobs]


class _Base:
    bl_options = {'REGISTER', 'UNDO'}

    report_details: BoolProperty(
        name="Report details",
        description="Print what was written to the console",
        default=True,
    )
    use_blender_normals: BoolProperty(
        name="Use Blender normals",
        description=(
            "Write the normals Blender is showing instead of the ones the .skn came in with. Off "
            "by default because Blender does not store custom normals faithfully - on one real "
            "asset it differs from the file by 0.99 - so the file's own are the right answer "
            "unless the mesh really was reshaded"
        ),
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return _target(context) is not None

    def _print(self, report):
        if not self.report_details:
            return
        print("[LoL Blender] %s" % os.path.basename(getattr(report, "path", "") or "export"))
        for line in report.summary():
            print("    %s" % line)

    def _finish(self, reports):
        warnings = []
        for report in reports:
            warnings.extend(report.warnings)
        written = ", ".join(os.path.basename(r.path) for r in reports if r.path)
        if warnings:
            self.report({'WARNING'}, "%s - %s" % (written, "; ".join(warnings[:3])))
        else:
            self.report({'INFO'}, "wrote %s" % written)
        return {'FINISHED'}


class LOLBLENDER_OT_export_skin(bpy.types.Operator, ExportHelper, _Base):
    bl_idname = "lolblender.export_skin"
    bl_label = "Export League Skin"
    bl_description = "Write the .skn and its .skl together, which is the pair the game loads"

    filename_ext = ".skn"
    filter_glob: StringProperty(default="*.skn", options={'HIDDEN'})

    write_skeleton: BoolProperty(
        name="Also write the .skl",
        description=(
            "Write the skeleton of the same basename beside the mesh. The two have to agree on the "
            "influence table, so writing them together is the only way they cannot drift apart"
        ),
        default=True,
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, "write_skeleton")
        layout.prop(self, "use_blender_normals")
        layout.prop(self, "report_details")

    def invoke(self, context, event):
        remember.load(self, context, remember.EXPORT_SKIN)
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        remember.save(self, context, remember.EXPORT_SKIN)
        try:
            native.rs()
        except native.NativeMissing as e:
            self.report({'ERROR'}, str(e).splitlines()[0])
            return {'CANCELLED'}

        armature = _target(context)
        if armature is None:
            self.report({'ERROR'}, "select a League armature, or a mesh bound to one")
            return {'CANCELLED'}

        reports = []
        try:
            reports = _write_skin_pair(armature, self.filepath, self.use_blender_normals,
                                       self.write_skeleton)
            for report in reports:
                self._print(report)
        except Exception as e:                          # noqa: BLE001 - surfaced to the user
            self.report({'ERROR'}, "%s: %s" % (os.path.basename(self.filepath), e))
            return {'CANCELLED'}

        return self._finish(reports)


class LOLBLENDER_OT_export_skn(bpy.types.Operator, ExportHelper, _Base):
    bl_idname = "lolblender.export_skn"
    bl_label = "Export League Mesh"
    bl_description = "Write only the .skn"

    filename_ext = ".skn"
    filter_glob: StringProperty(default="*.skn", options={'HIDDEN'})

    def draw(self, context):
        self.layout.prop(self, "use_blender_normals")
        self.layout.prop(self, "report_details")

    def invoke(self, context, event):
        remember.load(self, context, remember.EXPORT_SKN)
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        remember.save(self, context, remember.EXPORT_SKN)
        armature = _target(context)
        try:
            native.rs()
            _names, report = skn_out.export_skn(
                armature, self.filepath, use_blender_normals=self.use_blender_normals)
        except Exception as e:                          # noqa: BLE001
            self.report({'ERROR'}, "%s: %s" % (os.path.basename(self.filepath), e))
            return {'CANCELLED'}
        self._print(report)
        return self._finish([report])


class LOLBLENDER_OT_export_skl(bpy.types.Operator, ExportHelper, _Base):
    bl_idname = "lolblender.export_skl"
    bl_label = "Export League Skeleton"
    bl_description = "Write only the .skl"

    filename_ext = ".skl"
    filter_glob: StringProperty(default="*.skl", options={'HIDDEN'})

    def draw(self, context):
        self.layout.prop(self, "report_details")

    def invoke(self, context, event):
        remember.load(self, context, remember.EXPORT_SKL)
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        remember.save(self, context, remember.EXPORT_SKL)
        armature = _target(context)
        try:
            native.rs()
            report = skl_out.export_skl(armature, self.filepath)
        except Exception as e:                          # noqa: BLE001
            self.report({'ERROR'}, "%s: %s" % (os.path.basename(self.filepath), e))
            return {'CANCELLED'}
        self._print(report)
        return self._finish([report])


class LOLBLENDER_OT_export_anm(bpy.types.Operator, ExportHelper, _Base):
    bl_idname = "lolblender.export_anm"
    bl_label = "Export League Animation"
    bl_description = (
        "Write the current animation as a compressed .anm. The EVALUATED pose is written, "
        "so constraints, drivers and physics end up in the file")

    filename_ext = ".anm"
    filter_glob: StringProperty(default="*.anm", options={'HIDDEN'})

    range_source: EnumProperty(
        name="Frame range",
        items=(
            ('ACTION', "Action", "The range the assigned action actually covers"),
            ('SCENE', "Scene", "The scene's start and end frames"),
            ('CUSTOM', "Custom", "The range set below"),
        ),
        default='ACTION',
    )
    frame_start: IntProperty(name="Start", default=0)
    frame_end: IntProperty(name="End", default=100)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, "range_source")
        if self.range_source == 'CUSTOM':
            layout.prop(self, "frame_start")
            layout.prop(self, "frame_end")
        layout.prop(self, "report_details")

    def invoke(self, context, event):
        remember.load(self, context, remember.EXPORT_ANM)
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        remember.save(self, context, remember.EXPORT_ANM)
        armature = _target(context)
        scene = context.scene

        if self.range_source == 'CUSTOM':
            start, end = self.frame_start, self.frame_end
        elif self.range_source == 'SCENE':
            start, end = scene.frame_start, scene.frame_end
        else:
            start, end = anm_out.frame_range(scene, armature, use_scene_range=False)

        if end < start:
            self.report({'ERROR'}, "the frame range ends before it starts (%d..%d)" % (start, end))
            return {'CANCELLED'}

        try:
            native.rs()
            report = anm_out.export_anm(armature, self.filepath, scene, start, end)
        except Exception as e:                          # noqa: BLE001
            self.report({'ERROR'}, "%s: %s" % (os.path.basename(self.filepath), e))
            return {'CANCELLED'}
        self._print(report)
        return self._finish([report])


def _menu_entry(self, context):
    layout = self.layout
    layout.operator(LOLBLENDER_OT_export_skin.bl_idname,
                    text="League of Legends Skin (.skn + .skl)")
    layout.operator(LOLBLENDER_OT_export_skn.bl_idname, text="League of Legends Mesh (.skn)")
    layout.operator(LOLBLENDER_OT_export_skl.bl_idname, text="League of Legends Skeleton (.skl)")
    layout.operator(LOLBLENDER_OT_export_anm.bl_idname, text="League of Legends Animation (.anm)")


_classes = (
    LOLBLENDER_OT_export_skin,
    LOLBLENDER_OT_export_skn,
    LOLBLENDER_OT_export_skl,
    LOLBLENDER_OT_export_anm,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_export.append(_menu_entry)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(_menu_entry)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
