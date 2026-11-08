"""File > Import > League of Legends Animation (.anm).

Several files at once create one Action each; the last one stays assigned.
"""

import os

import bpy
from bpy.props import BoolProperty, CollectionProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from .. import native
from . import remember
from ..core import anm_in
from ..core import registry as reg


class LOLBLENDER_OT_import_anm(bpy.types.Operator, ImportHelper):
    bl_idname = "lolblender.import_anm"
    bl_label = "Import League Animation"
    bl_description = "Import one or more League of Legends .anm onto the active armature"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".anm"
    filter_glob: StringProperty(default="*.anm", options={'HIDDEN'})

    files: CollectionProperty(
        type=bpy.types.OperatorFileListElement, options={'HIDDEN', 'SKIP_SAVE'})
    directory: StringProperty(subtype='DIR_PATH', options={'HIDDEN', 'SKIP_SAVE'})

    replace_existing: BoolProperty(
        name="Replace existing action",
        description=(
            "Clear the armature's current action first. lol_maya calls this 'delchannel'. Without "
            "it the previous clip's channels keep driving bones the new one does not mention, and "
            "the result is a blend of two animations"
        ),
        default=False,
    )
    frame_offset: IntProperty(
        name="Frame offset",
        description="Start the animation at this frame instead of 0",
        default=0,
    )
    set_scene_range: BoolProperty(
        name="Set frame range and rate",
        description=(
            "Set the scene's frame range and playback rate from the clip. The rate is written as "
            "Blender's fps/fps_base pair, so a non-integer rate survives"
        ),
        default=True,
    )
    report_details: BoolProperty(
        name="Report details",
        description="Print per-clip measurements to the console",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        return reg.is_lol_armature(cls._target(context))

    @staticmethod
    def _target(context):
        """The active armature, or the armature parent of the active mesh."""
        obj = context.view_layer.objects.active
        if obj is None:
            return None
        if obj.type == 'ARMATURE':
            return obj
        if obj.parent is not None and obj.parent.type == 'ARMATURE':
            return obj.parent
        return None

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, "replace_existing")
        layout.prop(self, "set_scene_range")
        layout.prop(self, "frame_offset")
        layout.prop(self, "report_details")

    def invoke(self, context, event):
        remember.load(self, context, remember.ANM)
        return super().invoke(context, event)

    def execute(self, context):
        remember.save(self, context, remember.ANM)

        try:
            native.rs()
        except native.NativeMissing as e:
            self.report({'ERROR'}, str(e).splitlines()[0])
            return {'CANCELLED'}

        armature = self._target(context)
        if armature is None:
            self.report(
                {'ERROR'},
                "select a League armature (or a mesh parented to one) before importing an animation")
            return {'CANCELLED'}

        paths = self._paths()
        if not paths:
            self.report({'ERROR'}, "no .anm selected")
            return {'CANCELLED'}

        warnings = []
        imported = 0
        # only the first clip may clear the existing action
        replace = self.replace_existing

        for path in sorted(paths):
            try:
                report = anm_in.import_anm(
                    path, armature,
                    frame_offset=self.frame_offset,
                    replace_existing=replace,
                    set_fps=self.set_scene_range,
                    set_frame_range=self.set_scene_range,
                )
            except Exception as e:                      # noqa: BLE001 - surfaced to the user
                self.report({'ERROR'}, "%s: %s" % (os.path.basename(path), e))
                return {'CANCELLED'}

            replace = False
            imported += 1

            if self.report_details:
                print("[LoL Blender] %s" % os.path.basename(path))
                for line in report.summary():
                    print("    %s" % line)

            if report.matched == 0:
                warnings.append(
                    "%s: none of its %d tracks match this armature's bones - wrong skeleton?"
                    % (os.path.basename(path), report.track_count))
            elif report.unmatched_tracks:
                warnings.append(
                    "%s: %d tracks name joints this armature does not have"
                    % (os.path.basename(path), len(report.unmatched_tracks)))
            if not report.fps_exact:
                warnings.append(
                    "%s: its frame rate cannot be represented exactly" % os.path.basename(path))
            if report.max_decompose_error > anm_in.SHEAR_REPORT_THRESHOLD:
                warnings.append(
                    "%s: asks for shear a pose channel cannot hold (worst %.4g at %r), which has "
                    "been dropped" % (os.path.basename(path), report.max_decompose_error,
                                      report.worst_decompose_bone))

        message = "imported %d animation%s onto %r" % (
            imported, "" if imported == 1 else "s", armature.name)
        if warnings:
            self.report({'WARNING'}, message + " - " + "; ".join(warnings))
        else:
            self.report({'INFO'}, message)
        return {'FINISHED'}

    def _paths(self):
        if self.files and self.directory:
            named = [f.name for f in self.files if f.name]
            if named:
                return [os.path.join(self.directory, name) for name in named]
        return [self.filepath] if self.filepath else []


def _menu_entry(self, context):
    self.layout.operator(
        LOLBLENDER_OT_import_anm.bl_idname,
        text="League of Legends Animation (.anm)",
    )


_classes = (LOLBLENDER_OT_import_anm,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(_menu_entry)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(_menu_entry)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
