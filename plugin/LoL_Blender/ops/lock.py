"""Lock skeleton: the pose you see is the rest, in every mode.

With the lock on, leaving Pose Mode turns the pose into the rest pose (core/rest_pose.py), so
Object, Edit and Pose Mode all show the same skeleton and Bind binds it where it stands. Weight
Paint keeps the rig in Pose Mode, so painting is not affected.
"""

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty
from bpy.types import Operator

from ..core import rest_pose

#: Last seen mode per locked armature. Blender has no "mode changed" event.
_last_mode = {}
#: Armatures waiting for their bake, so one mode change schedules one timer.
_pending = set()


def _view3d_override(context):
    """A window and 3D view to run mode switches from a timer."""
    window = getattr(context, "window", None)
    windows = [window] if window else []
    windows += [w for w in context.window_manager.windows if w is not window]
    for win in windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                region = next((r for r in area.regions if r.type == 'WINDOW'), None)
                return {"window": win, "area": area, "region": region}
    return {}


def _bake_later(name):
    if name in _pending:
        return
    _pending.add(name)

    def run():
        _pending.discard(name)
        context = bpy.context
        obj = bpy.data.objects.get(name)
        if obj is None or not rest_pose.is_locked(obj) or not rest_pose.is_posed(obj):
            return None
        if obj.mode == 'POSE':
            return None                         # back in Pose Mode before the timer ran
        try:
            from . import physics
            physics.forget(obj)
            with context.temp_override(**_view3d_override(context)):
                rest_pose.pose_to_rest(bpy.context, obj,
                                       keep_meshes=bool(obj.get(rest_pose.LOCK_MESHES, True)))
                bpy.ops.ed.undo_push(message="Lock skeleton")
        except Exception as error:              # noqa: BLE001 - a timer must not raise
            print("[LoL Blender] skeleton lock failed on %s: %r" % (name, error))
        return None

    bpy.app.timers.register(run, first_interval=0.0)


@persistent
def _on_depsgraph_update(scene, _depsgraph=None):
    try:
        for obj in scene.objects:
            if obj.type != 'ARMATURE' or not rest_pose.is_locked(obj):
                continue
            previous = _last_mode.get(obj.name)
            current = obj.mode
            _last_mode[obj.name] = current
            if previous == 'POSE' and current != 'POSE' and rest_pose.is_posed(obj):
                _bake_later(obj.name)
    except Exception as error:                  # noqa: BLE001 - a raising handler is removed
        print("[LoL Blender] skeleton lock: %r" % (error,))


@persistent
def _on_load(_dummy=None):
    _last_mode.clear()
    _pending.clear()


def _armature(context):
    obj = context.view_layer.objects.active
    if obj is not None and obj.type == 'ARMATURE':
        return obj
    from ..ui import anim_library
    return anim_library.find_armature(context)


class LOLBLENDER_OT_lock_skeleton(Operator):
    bl_idname = "lolblender.lock_skeleton"
    bl_label = "Lock skeleton"
    bl_description = (
        "Keep the skeleton where you put it. The pose becomes the rest pose now and every time "
        "you leave Pose Mode, so Object, Edit and Pose Mode show the same skeleton and Bind binds "
        "it where it is. Click again to unlock")
    bl_options = {'REGISTER', 'UNDO'}

    keep_meshes: BoolProperty(
        name="Bound meshes keep their shape",
        description=(
            "Meshes already bound to the rig keep the shape they show, like Maya. Untick to let "
            "them go back to their modeled shape (the skeleton moves inside them)"),
        default=True,
    )

    @classmethod
    def poll(cls, context):
        return _armature(context) is not None

    def execute(self, context):
        armature = _armature(context)
        if rest_pose.is_locked(armature):
            del armature[rest_pose.LOCKED]
            _last_mode.pop(armature.name, None)
            self.report({'INFO'}, "%r unlocked" % armature.name)
            return {'FINISHED'}

        armature[rest_pose.LOCKED] = True
        armature[rest_pose.LOCK_MESHES] = self.keep_meshes
        _last_mode[armature.name] = armature.mode
        message = "%r locked" % armature.name
        if rest_pose.is_posed(armature):
            try:
                rest_pose.check_actions(armature)
            except Exception as error:          # noqa: BLE001 - surfaced to the user
                del armature[rest_pose.LOCKED]
                self.report({'ERROR'}, "cannot rebuild animation: %s" % error)
                return {'CANCELLED'}
            from . import physics
            physics.forget(armature)
            result = rest_pose.pose_to_rest(context, armature, keep_meshes=self.keep_meshes)
            message += " - " + result.message(armature)
        self.report({'INFO'}, message)
        return {'FINISHED'}


_classes = (LOLBLENDER_OT_lock_skeleton,)


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
    _pending.clear()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
