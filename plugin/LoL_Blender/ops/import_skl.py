"""File > Import > League of Legends Skeleton (.skl)."""

import os

import bpy
from bpy.props import BoolProperty, EnumProperty, CollectionProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from ..core import skl_in
from .. import native
from . import ask
from . import remember
from ..core import replace


class LOLBLENDER_OT_import_skl(bpy.types.Operator, ImportHelper):
    bl_idname = "lolblender.import_skl"
    bl_label = "Import League Skeleton"
    bl_description = "Import a League of Legends .skl as an armature"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".skl"
    filter_glob: StringProperty(default="*.skl", options={'HIDDEN'})

    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={'HIDDEN', 'SKIP_SAVE'})
    directory: StringProperty(subtype='DIR_PATH', options={'HIDDEN', 'SKIP_SAVE'})

    existing: EnumProperty(
        name="Existing rig",
        description=(
            "What to do with League objects already in the scene. Only objects this plugin "
            "imported are ever removed - anything you built yourself is left alone"
        ),
        items=(
            ('UPDATE', "Update the skeleton",
             "Like lol_maya: put the file's joints onto the skeleton already in the scene. "
             "Nothing is replaced: the pose goes back to the file's bind, missing joints are "
             "added and bound meshes keep their weights. Without a matching skeleton the file "
             "is imported normally"),
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
        default='UPDATE',
    )
    keep_meshes: BoolProperty(
        name="Bound meshes keep their shape",
        description=(
            "Update only: meshes bound to the skeleton keep the shape they show, as in Maya. "
            "Untick to let them follow the fresh bind pose"),
        default=True,
    )
    report_details: BoolProperty(
        name="Report details",
        description=(
            "Print per-file measurements to the console: bind divergence, bind scale, truncated "
            "names. Worth leaving on - it is how an unusual asset announces itself"
        ),
        default=True,
    )
    select_after: BoolProperty(
        name="Select imported",
        description="Leave the new armatures selected and the last one active",
        default=True,
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, "report_details")
        layout.separator()
        layout.prop(self, "existing")
        if self.existing in {'UPDATE', 'ASK'}:
            layout.prop(self, "keep_meshes")
        layout.prop(self, "select_after")

    def invoke(self, context, event):
        remember.load(self, context, remember.SKL)
        if self.directory and self.files:
            # dropped on the viewport: the file is already chosen
            return self.execute(context)
        return super().invoke(context, event)

    def execute(self, context):
        remember.save(self, context, remember.SKL)

        import os

        try:
            native.rs()
        except native.NativeMissing as e:
            self.report({'ERROR'}, str(e).splitlines()[0])
            return {'CANCELLED'}

        paths = self._paths()
        if not paths:
            self.report({'ERROR'}, "no .skl selected")
            return {'CANCELLED'}

        if self.existing == 'UPDATE':
            return _update_or_import(context, paths, self._options(), self.report)

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
            if mode == 'UPDATE':
                return _update_or_import(context, paths, options, report)
            return _import(context, paths, mode, options, report)

        return run

    def _options(self):
        return {
            "report_details": self.report_details,
            "select_after": self.select_after,
            "keep_meshes": self.keep_meshes,
        }

    def _run(self, context, paths, mode):
        return _import(context, paths, mode, self._options(), self.report)

    def _paths(self):
        import os

        if self.files and self.directory:
            named = [f.name for f in self.files if f.name]
            if named:
                return [os.path.join(self.directory, name) for name in named]
        return [self.filepath] if self.filepath else []


@replace.transactional_import
def _import(context, paths, mode, options, report):
    """The import itself, with no operator, so the dialog can run it later."""
    # once, for the first file, so several skeletons do not replace each other
    replaced = replace.apply(mode, paths[0])

    imported = []
    warnings = []
    for path in paths:
        try:
            obj, result = skl_in.import_skl(path)
        except Exception as e:                          # noqa: BLE001 - surfaced to the user
            report({'ERROR'}, "%s: %s" % (os.path.basename(path), e))
            return {'CANCELLED'}

        imported.append(obj)
        if options["report_details"]:
            print("[LoL Blender] %s" % os.path.basename(path))
            for line in result.summary():
                print("    %s" % line)

        if result.shear_joints:
            warnings.append(
                "%s: %d joints carry bind SHEAR (max %.4g), which nothing in Blender can hold "
                "and which was dropped"
                % (os.path.basename(path), len(result.shear_joints), result.max_shear))
        if result.degenerate_bind_joints:
            warnings.append(
                "%s: %d joints have an unusable inverse-bind and fell back to the local chain"
                % (os.path.basename(path), len(result.degenerate_bind_joints)))
        if result.truncated_names:
            warnings.append(
                "%s: %d joint names were truncated to Blender's 63-character bone limit"
                % (os.path.basename(path), len(result.truncated_names)))

    if options["select_after"] and imported:
        bpy.ops.object.select_all(action='DESELECT')
        for obj in imported:
            obj.select_set(True)
        context.view_layer.objects.active = imported[-1]

    total_bones = sum(len(o.data.bones) for o in imported)
    message = "imported %d skeleton%s, %d bones" % (
        len(imported), "" if len(imported) == 1 else "s", total_bones)

    if replaced:
        message += " (%s)" % replaced

    if warnings:
        report({'WARNING'}, message + " - " + "; ".join(warnings))
    else:
        report({'INFO'}, message)
    return {'FINISHED'}


def _update_or_import(context, paths, options, report):
    """lol_maya's SKL import: each file goes onto the skeleton sharing its joints, or is
    imported normally when there is none."""
    from ..core import rest_pose
    from ..core import skl_update
    from . import physics

    active = context.view_layer.objects.active
    if active is not None and active.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    fresh = []
    lines = []
    warnings = []
    for path in paths:
        name = os.path.basename(path)
        try:
            skeleton = native.rs().Skeleton.from_path(path)
        except Exception as e:                          # noqa: BLE001 - surfaced to the user
            report({'ERROR'}, "%s: %s" % (name, e))
            return {'CANCELLED'}
        target = skl_update.find_target(context, skeleton)
        if target is None:
            fresh.append(path)
            continue
        try:
            rest_pose.check_actions(target)
        except Exception as e:                          # noqa: BLE001
            report({'ERROR'}, "%s: cannot rebuild the animations of %s: %s"
                   % (name, target.name, e))
            return {'CANCELLED'}
        physics.forget(target)
        result = skl_update.update_skeleton(
            context, target, skeleton, source_path=path,
            keep_meshes=options.get("keep_meshes", True))
        if options["report_details"]:
            print("[LoL Blender] %s -> %s" % (name, target.name))
            for line in result.summary():
                print("    %s" % line)
        lines.append("%s updated from %s (%d joints%s)" % (
            target.name, name, len(result.matched),
            ", %d added" % len(result.created) if result.created else ""))
        if result.result is not None and result.result.orphaned:
            warnings.append("%d action%s with no source .anm now mean something else" % (
                len(result.result.orphaned), "" if len(result.result.orphaned) == 1 else "s"))
        if options["select_after"]:
            bpy.ops.object.select_all(action='DESELECT')
            target.select_set(True)
            context.view_layer.objects.active = target

    if fresh:
        quiet = []
        result = _import(context, fresh, 'KEEP', options, lambda level, text: quiet.append((level, text)))
        for level, text in quiet:
            if 'ERROR' in level:
                report(level, text)
                return result
            lines.append(text)

    message = "; ".join(lines) or "nothing imported"
    if warnings:
        report({'WARNING'}, message + " - " + "; ".join(warnings))
    else:
        report({'INFO'}, message)
    return {'FINISHED'}


def _menu_entry(self, context):
    self.layout.operator(
        LOLBLENDER_OT_import_skl.bl_idname,
        text="League of Legends Skeleton (.skl)",
    )


_classes = (LOLBLENDER_OT_import_skl,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(_menu_entry)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(_menu_entry)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
