"""Custom bones: automatic promotion on leaving Edit Mode, plus the manual buttons.

The handler only acts when an armature leaves Edit Mode, writes only ID properties, and catches
every exception so a failure never removes it.
"""

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty
from bpy.types import Operator

from ..core import promote as promote_core
from ..core import registry as reg

#: Set while the handler runs, so a depsgraph update it causes cannot re-enter it.
_running = False

#: Last seen mode per armature. Blender has no "mode changed" event.
_last_mode = {}


def _armatures(scene):
    return [o for o in scene.objects if reg.is_lol_armature(o)]


@persistent
def _on_depsgraph_update(scene, _depsgraph=None):
    global _running
    if _running:
        return
    try:
        _running = True
        for obj in _armatures(scene):
            previous = _last_mode.get(obj.name)
            current = obj.mode
            _last_mode[obj.name] = current
            if previous != 'EDIT' or current == 'EDIT':
                continue

            report = promote_core.promote(obj)
            for line in report.summary():
                print("[LoL Blender] %s" % line)
    except Exception as error:                      # noqa: BLE001 - a raising handler is removed
        print("[LoL Blender] automatic promotion failed: %r" % (error,))
    finally:
        _running = False


@persistent
def _on_load(_dummy=None):
    _last_mode.clear()


class LOLBLENDER_OT_promote_bones(Operator):
    bl_idname = "lolblender.promote_bones"
    bl_label = "Promote custom bones"
    bl_description = (
        "Turn every bone you added into a real .skl joint: give it a name hash, a joint id, a "
        "parent and a bind transform. This normally happens by itself when you leave Edit Mode - "
        "the button is for seeing the result now, and for rigs built by script")
    bl_options = {'REGISTER', 'UNDO'}

    dry_run: BoolProperty(
        name="Only report",
        description="Say what would happen and change nothing",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        from ..ui import anim_library
        return anim_library.find_armature(context) is not None

    def execute(self, context):
        from ..ui import anim_library

        armature = anim_library.find_armature(context)
        report = promote_core.promote(armature, dry_run=self.dry_run)

        lines = report.summary()
        for line in lines:
            print("[LoL Blender] %s" % line)

        if report.refused:
            self.report({'ERROR'}, report.refused[0][1])
            return {'CANCELLED'}
        if not lines:
            self.report({'INFO'}, "nothing to promote - every bone is already a joint")
            return {'FINISHED'}
        self.report({'WARNING' if (report.renamed or report.vanished) else 'INFO'}, lines[0])
        return {'FINISHED'}


class LOLBLENDER_OT_mark_helper(Operator):
    bl_idname = "lolblender.mark_helper"
    bl_label = "Mark as helper"
    bl_description = (
        "Keep the selected bones OUT of the .skl. For IK targets, control shapes and physics "
        "helpers - things that drive the rig but are not joints the game should know about")
    bl_options = {'REGISTER', 'UNDO'}

    helper: BoolProperty(name="Helper", default=True)

    @classmethod
    def poll(cls, context):
        obj = context.view_layer.objects.active
        return (obj is not None and obj.type == 'ARMATURE'
                and context.mode in {'POSE', 'EDIT_ARMATURE'}
                and bool(context.selected_pose_bones or context.selected_editable_bones))

    def execute(self, context):
        armature = context.view_layer.objects.active
        registry = reg.read_registry(armature)

        selected = (
            [pb.bone for pb in (context.selected_pose_bones or [])]
            or [armature.data.bones[eb.name] for eb in (context.selected_editable_bones or [])
                if eb.name in armature.data.bones])

        changed, demoted, refused = 0, 0, []
        for bone in selected:
            if self.helper and reg.is_native(bone, registry):
                # a promoted bone can be demoted; a joint from the .skl cannot
                if reg.was_promoted(bone):
                    promote_core.demote(armature, bone)
                    registry = reg.read_registry(armature)
                    demoted += 1
                else:
                    refused.append(bone.name)
                    continue
            bone[reg.HELPER] = self.helper
            changed += 1

        if refused:
            self.report(
                {'ERROR'},
                "%s came from the .skl itself, so they cannot be helpers - a helper is a bone that "
                "must stay OUT of the file, and these are already in it" % ", ".join(refused[:4]))
            return {'CANCELLED'}

        message = "%d bone%s %s helpers" % (
            changed, "" if changed == 1 else "s", "are now" if self.helper else "are no longer")
        if demoted:
            message += " (%d taken back out of the skeleton)" % demoted
        self.report({'INFO'}, message)
        return {'FINISHED'}


class LOLBLENDER_OT_check_rig(Operator):
    bl_idname = "lolblender.check_rig"
    bl_label = "Check rig"
    bl_description = (
        "Report everything that would go wrong on export: unpromoted bones, hash collisions, "
        "reparented joints, bones whose transform flags were changed, and registry damage")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        from ..ui import anim_library
        return anim_library.find_armature(context) is not None

    def execute(self, context):
        from ..core import armature as arm_util
        from ..ui import anim_library

        armature = anim_library.find_armature(context)
        lines = list(reg.problems(armature))
        lines.extend(arm_util.check_transform_flags(armature))
        lines.extend(promote_core.promote(armature, dry_run=True).summary())

        native, custom, helper = reg.classify(armature)
        header = "%d joints, %d added by you, %d helpers" % (
            len(native), len(custom - helper), len(helper))
        print("[LoL Blender] %s" % header)
        for line in lines:
            print("    %s" % line)

        if lines:
            self.report({'WARNING'}, "%s - %d thing%s to look at, see the console" % (
                header, len(lines), "" if len(lines) == 1 else "s"))
        else:
            self.report({'INFO'}, "%s - clean" % header)
        return {'FINISHED'}


_classes = (
    LOLBLENDER_OT_promote_bones,
    LOLBLENDER_OT_mark_helper,
    LOLBLENDER_OT_check_rig,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    from .. import compat_handlers as handlers

    handlers.install(bpy.app.handlers.depsgraph_update_post, _on_depsgraph_update)
    handlers.install(bpy.app.handlers.load_post, _on_load)


def unregister():
    from .. import compat_handlers as handlers

    handlers.remove(bpy.app.handlers.load_post, _on_load)
    handlers.remove(bpy.app.handlers.depsgraph_update_post, _on_depsgraph_update)
    _last_mode.clear()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
