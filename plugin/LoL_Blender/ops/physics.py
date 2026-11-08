"""Physics operators, the live preview handler and the bake.

The live handler (`frame_change_post`) steps every enabled set and writes only `matrix_basis`.
Sliders re-tune the running solver; bone list changes rebuild it. A playback wrap counts as one
frame forward, so loops continue instead of restarting.
"""

import os
import time

import bpy
from bpy.app.handlers import persistent
from bpy.props import EnumProperty, IntProperty, StringProperty
from bpy.types import Operator

from .. import compat_handlers as handlers
from ..core import bake, detect, wiggle
from ..ui import physics_props

#: One `wiggle.Rig` per armature, keyed by name, plus the signature it was built from.
_rigs = {}


# ---------------------------------------------------------------------------
#  Cache
# ---------------------------------------------------------------------------

def rig_for(armature, rebuild=False):
    """The live solver for this armature, built or rebuilt as needed."""
    entry = _rigs.get(armature.name)
    if entry is not None and not rebuild:
        rig, signature = entry
        try:
            # identity check first: after an undo the cached armature is a dead reference
            if rig.armature is armature and signature == rig.signature():
                return rig
        except ReferenceError:
            pass
    rig = wiggle.Rig(armature)
    rig.reset()
    _rigs[armature.name] = (rig, rig.signature())
    return rig


def forget(armature=None):
    if armature is None:
        _rigs.clear()
    else:
        _rigs.pop(armature.name, None)


def settings_changed(armature):
    """A slider moved. Re-tune, never rebuild."""
    if armature is None:
        return
    entry = _rigs.get(armature.name)
    if entry is None:
        return
    rig, _signature = entry
    try:
        if rig.armature is not armature:
            forget(armature)
            return
        rig.refresh_settings()
    except ReferenceError:
        forget(armature)
    _redraw()


def topology_changed(armature):
    """A bone list or a collider list changed. The solver has to be rebuilt."""
    if armature is None:
        return
    release_preview(armature)
    _redraw()


def release_preview(armature):
    """Drop the solver and give the bones back to the animation."""
    entry = _rigs.pop(armature.name, None)
    if entry is None:
        return
    rig, _signature = entry
    if rig.armature is not armature:
        return
    for name, basis in rig.initial_basis.items():
        bone = armature.pose.bones.get(name)
        if bone is not None:
            bone.matrix_basis = basis
    armature.update_tag(refresh={'OBJECT', 'TIME'})
    bpy.context.view_layer.update()


def _redraw():
    """Repaint the 3D views."""
    manager = getattr(bpy.context, "window_manager", None)
    for window in getattr(manager, "windows", ()):
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def _rigs_in(scene):
    for obj in scene.objects:
        if obj.type != 'ARMATURE':
            continue
        props = getattr(obj, "lol_physics", None)
        if props is None or not props.live:
            continue
        if physics_props.listed_bones(obj):
            yield obj


# ---------------------------------------------------------------------------
#  The handler
# ---------------------------------------------------------------------------

# persistent, or Blender drops the handlers when a file is loaded
@persistent
def _on_frame(scene, _depsgraph=None):
    for armature in _rigs_in(scene):
        try:
            rig = rig_for(armature)
        except Exception as error:                      # noqa: BLE001
            message = 'Physics: ' + str(error)
            if armature.lol_physics.status != message:
                print("[LoL Blender] physics build failed on %s: %r" % (armature.name, error))
            armature.lol_physics.status = message
            continue
        if not rig.sims:
            continue
        if armature.lol_physics.status.startswith('Physics: '):
            armature.lol_physics.status = 'Physics active'
        clip = rig.clip()
        if clip is not None and scene.frame_current < clip[1]:
            # reset only before the clip starts (frame 0 is a real frame of the clip)
            rig.reset()
            continue
        try:
            rig.step(scene)
        except Exception as error:                      # noqa: BLE001
            print("[LoL Blender] physics step failed on %s: %r" % (armature.name, error))
            forget(armature)


#: Re-entrancy guard: writing a pose triggers `_on_depsgraph` again.
_restoring = False


@persistent
def _on_depsgraph(scene, _depsgraph=None):
    """Re-apply the simulated pose when paused: the bones' own animation keys overwrite it on
    any re-evaluation. Never steps the simulation."""
    global _restoring
    if _restoring:
        return
    # not during playback: _on_frame already writes every frame
    screen = getattr(bpy.context, "screen", None)
    if screen is not None and getattr(screen, "is_animation_playing", False):
        return
    _restoring = True
    try:
        for armature in _rigs_in(scene):
            entry = _rigs.get(armature.name)
            if entry is None:
                continue
            rig, _signature = entry
            try:
                if rig.armature is armature and rig.last_frame == scene.frame_current:
                    rig.restore()
            except ReferenceError:
                # an undo replaced the datablock; rig_for rebuilds it
                forget(armature)
            except Exception as error:              # noqa: BLE001
                print("[LoL Blender] physics restore failed on %s: %r" % (armature.name, error))
    finally:
        _restoring = False


@persistent
def _on_load(_scene, _depsgraph=None):
    """A new file: drop every cached solver."""
    _rigs.clear()


def _install():
    # see compat_handlers: replaces stale copies left by a reload
    handlers.install(bpy.app.handlers.frame_change_post, _on_frame)
    handlers.install(bpy.app.handlers.depsgraph_update_post, _on_depsgraph)
    handlers.install(bpy.app.handlers.load_post, _on_load)


def _remove():
    handlers.remove(bpy.app.handlers.frame_change_post, _on_frame)
    handlers.remove(bpy.app.handlers.depsgraph_update_post, _on_depsgraph)
    handlers.remove(bpy.app.handlers.load_post, _on_load)


# ---------------------------------------------------------------------------
#  Shared helpers
# ---------------------------------------------------------------------------

def _armature(context):
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


class _SetOperator(Operator):
    """Base for operators that edit one set. `UNDO` without `REGISTER`, so no redo panel."""

    bl_options = {'UNDO'}

    set_id: EnumProperty(
        items=[(i, label, label) for i, _a, label in physics_props.SETS], default='HAIR')

    def target_set(self, context):
        armature = _armature(context)
        if armature is None:
            self.report({'ERROR'}, "no armature in the scene")
            return None, None
        return armature, physics_props.get_set(armature, self.set_id)


# ---------------------------------------------------------------------------
#  Bone list
# ---------------------------------------------------------------------------

class LOLBLENDER_OT_physics_add_bone(_SetOperator):
    bl_idname = "lolblender.physics_add_bone"
    bl_label = "Add Bone"
    bl_description = "Add an empty row to this branch. Type a bone name or pick one"

    branch: IntProperty(default=0, min=0, max=7)

    def execute(self, context):
        armature, physics_set = self.target_set(context)
        if physics_set is None:
            return {'CANCELLED'}
        # the first bone loads the set's defaults
        if not len(physics_set.bones):
            physics_props.apply_defaults(physics_set, self.set_id)
        item = physics_set.bones.add()
        item.branch = self.branch
        if self.branch >= physics_set.num_branches:
            physics_set.num_branches = self.branch + 1
        return {'FINISHED'}


class LOLBLENDER_OT_physics_remove_bone(_SetOperator):
    bl_idname = "lolblender.physics_remove_bone"
    bl_label = "Remove Bone"
    bl_description = "Remove this row. The bone stops being simulated"

    index: IntProperty(default=-1)
    branch: IntProperty(default=-1)
    target: EnumProperty(items=[('BONES', "Bones", ""), ('COLLIDERS', "Colliders", "")],
                         default='BONES')

    def execute(self, context):
        armature, physics_set = self.target_set(context)
        if physics_set is None:
            return {'CANCELLED'}
        collection = physics_set.bones if self.target == 'BONES' else physics_set.colliders
        if not len(collection):
            return {'CANCELLED'}

        index = self.index
        if index < 0:
            # Remove Last: the last row of this branch (or of all)
            candidates = [i for i, item in enumerate(collection)
                          if self.branch < 0 or item.branch == self.branch]
            if not candidates:
                return {'CANCELLED'}
            index = candidates[-1]
        if not 0 <= index < len(collection):
            return {'CANCELLED'}

        collection.remove(index)
        topology_changed(armature)
        return {'FINISHED'}


class LOLBLENDER_OT_physics_delete_branch(_SetOperator):
    bl_idname = "lolblender.physics_delete_branch"
    bl_label = "Delete Branch"
    bl_description = "Delete this branch and every bone in it"

    branch: IntProperty(default=0, min=0, max=7)

    def execute(self, context):
        armature, physics_set = self.target_set(context)
        if physics_set is None:
            return {'CANCELLED'}
        for index in reversed([i for i, item in enumerate(physics_set.bones)
                               if item.branch == self.branch]):
            physics_set.bones.remove(index)
        # keep branch numbers contiguous
        for item in physics_set.bones:
            if item.branch > self.branch:
                item.branch -= 1
        physics_set.num_branches = max(1, physics_set.num_branches - 1)
        topology_changed(armature)
        self.report({'INFO'}, "deleted branch %d" % (self.branch + 1))
        return {'FINISHED'}


class LOLBLENDER_OT_physics_bones_from_selection(_SetOperator):
    bl_idname = "lolblender.physics_bones_from_selection"
    bl_label = "Use Selected Bones"
    bl_description = "Add the bones selected in Pose Mode to this branch, parents first"

    branch: IntProperty(default=0, min=0, max=7)
    target: EnumProperty(items=[('BONES', "Bones", ""), ('COLLIDERS', "Colliders", "")],
                         default='BONES')

    def execute(self, context):
        armature, physics_set = self.target_set(context)
        if physics_set is None:
            return {'CANCELLED'}
        selected = [b.name for b in context.selected_pose_bones or ()]
        if not selected:
            self.report({'WARNING'}, "select some bones in Pose Mode first")
            return {'CANCELLED'}

        # parents first
        ordered = wiggle.ordered(armature, selected)
        if self.target == 'COLLIDERS':
            existing = [item.name for item in physics_set.colliders]
            physics_set.collider_text = ", ".join(existing + [n for n in ordered
                                                              if n not in existing])
        else:
            if not len(physics_set.bones):
                physics_props.apply_defaults(physics_set, self.set_id)
            existing = {item.name for item in physics_set.bones}
            for name in ordered:
                if name in existing:
                    continue
                item = physics_set.bones.add()
                item.name = name
                item.branch = self.branch
            topology_changed(armature)
        self.report({'INFO'}, "added %d bone%s" % (len(ordered), "" if len(ordered) == 1 else "s"))
        return {'FINISHED'}


# ---------------------------------------------------------------------------
#  Auto setup
# ---------------------------------------------------------------------------

class LOLBLENDER_OT_physics_auto_setup(Operator):
    bl_idname = "lolblender.physics_auto_setup"
    bl_label = "Find Bones Automatically"
    bl_description = ("Fill every set from the bone names on this rig - hair, cape and skirt "
                      "chains, breast bones - one branch per chain")
    bl_options = {'UNDO'}

    def execute(self, context):
        armature = _armature(context)
        if armature is None:
            self.report({'ERROR'}, "no armature in the scene")
            return {'CANCELLED'}

        found = detect.suggest(armature)
        if not found:
            self.report({'WARNING'}, "nothing recognised - add the bones by hand")
            return {'CANCELLED'}

        total = 0
        for identifier, strands, colliders in found:
            physics_set = physics_props.get_set(armature, identifier)
            if physics_set is None:
                continue
            physics_props.apply_defaults(physics_set, identifier)
            physics_set.bones.clear()
            physics_set.num_branches = max(1, min(8, len(strands)))
            for branch, strand in enumerate(strands[:8]):
                for name in strand:
                    item = physics_set.bones.add()
                    item.name = name
                    item.branch = branch
                    total += 1
            physics_set.collision_enabled = bool(colliders)
            physics_set.collider_text = ", ".join(colliders)

        topology_changed(armature)
        self.report({'INFO'}, "%d bones across %d sets" % (total, len(found)))
        return {'FINISHED'}


class LOLBLENDER_OT_physics_auto_colliders(_SetOperator):
    bl_idname = "lolblender.physics_auto_colliders"
    bl_label = "Auto-Detect"
    bl_description = "Fill the collision list with the body bones this set should bump into"

    def execute(self, context):
        armature, physics_set = self.target_set(context)
        if physics_set is None:
            return {'CANCELLED'}
        names = detect.colliders_for_set(armature, self.set_id)
        # never a set's own bones
        own = {item.name for item in physics_set.bones}
        names = [n for n in names if n not in own]
        if not names:
            self.report({'WARNING'}, "no spine, head or arm bones found - enter names by hand")
            return {'CANCELLED'}
        physics_set.collider_text = ", ".join(names)
        physics_set.collision_enabled = True
        self.report({'INFO'}, "%d collision bones" % len(names))
        return {'FINISHED'}


class LOLBLENDER_OT_physics_all_colliders(_SetOperator):
    bl_idname = "lolblender.physics_all_colliders"
    bl_label = "Select All Bones"
    bl_description = ("Use every bone on the rig as a collider, minus this set's own bones and "
                      "the utility ones")

    def execute(self, context):
        armature, physics_set = self.target_set(context)
        if physics_set is None:
            return {'CANCELLED'}
        own = {item.name.lower() for item in physics_set.bones if item.name}
        names = [b.name for b in armature.pose.bones
                 if b.name.lower() not in own and not detect.excluded(b.name)]
        if not names:
            self.report({'WARNING'}, "no eligible bones")
            return {'CANCELLED'}
        physics_set.collider_text = ", ".join(names)
        physics_set.collision_enabled = True
        self.report({'INFO'}, "%d collision bones" % len(names))
        return {'FINISHED'}


class LOLBLENDER_OT_physics_motion_bone(_SetOperator):
    bl_idname = "lolblender.physics_motion_bone"
    bl_label = "Find the Reference Bone"
    bl_description = ("Fill the Watch field with the bone that carries the character - Root, or "
                      "the pelvis. That is the one whose jerk tells running from jumping")

    def execute(self, context):
        armature, physics_set = self.target_set(context)
        if physics_set is None:
            return {'CANCELLED'}
        name = detect.motion_reference(armature)
        if name is None:
            self.report({'WARNING'}, "no root or pelvis bone found - pick one yourself")
            return {'CANCELLED'}
        physics_set.motion_bone = name
        self.report({'INFO'}, "Motion is watching %r" % name)
        return {'FINISHED'}


class LOLBLENDER_OT_physics_reset_settings(_SetOperator):
    bl_idname = "lolblender.physics_reset_settings"
    bl_label = "Reset to Defaults"
    bl_description = "Put this set's sliders back where they started. The bone list is untouched"

    def execute(self, context):
        armature, physics_set = self.target_set(context)
        if physics_set is None:
            return {'CANCELLED'}
        physics_props.apply_defaults(physics_set, self.set_id)
        settings_changed(armature)
        return {'FINISHED'}


class LOLBLENDER_OT_physics_reset_custom(Operator):
    bl_idname = "lolblender.physics_reset_custom"
    bl_label = "Go Back to Default Values"
    bl_description = ("Put the four custom Wiggle values back where they started. Nothing else is "
                      "touched")
    bl_options = {'UNDO'}

    def execute(self, context):
        armature = _armature(context)
        if armature is None:
            self.report({'ERROR'}, "no armature in the scene")
            return {'CANCELLED'}
        props = armature.lol_physics
        for name in ("custom_stiff", "custom_damp", "custom_gravity", "custom_stretch"):
            props.property_unset(name)
        settings_changed(armature)
        self.report({'INFO'}, "custom values back to their defaults")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
#  Preview
# ---------------------------------------------------------------------------

class LOLBLENDER_OT_physics_reset(Operator):
    bl_idname = "lolblender.physics_reset"
    bl_label = "Reset"
    bl_description = "Put every simulated bone back on the animated pose and clear its motion"

    def execute(self, context):
        armature = _armature(context)
        if armature is None:
            self.report({'ERROR'}, "no armature in the scene")
            return {'CANCELLED'}
        rig_for(armature, rebuild=True)
        _redraw()
        return {'FINISHED'}


class LOLBLENDER_OT_physics_set_shapes(Operator):
    bl_idname = "lolblender.physics_set_shapes"
    bl_label = "Set Body Shapes"
    bl_description = (
        "Give every bone in this rig's lists a collision shape you can move, rotate and scale in "
        "the viewport. Bones already carrying one are left exactly as you placed them, so this is "
        "safe to press again after adding a bone")
    bl_options = {'UNDO'}

    def execute(self, context):
        from ..core import shapes

        armature = _armature(context)
        if armature is None:
            self.report({'ERROR'}, "no armature in the scene")
            return {'CANCELLED'}
        made, kept = shapes.ensure(armature, context.scene)
        shapes.show(True, context.scene)
        if not made and not kept:
            self.report({'WARNING'},
                        "no bones listed yet - fill in the branches or the collision bones first")
        elif made:
            self.report({'INFO'}, "%d new shape%s, %d left as you placed them"
                        % (made, "" if made == 1 else "s", kept))
        else:
            self.report({'INFO'}, "every bone already has a shape, all %d left alone" % kept)
        topology_changed(armature)
        _redraw()
        return {'FINISHED'}


class LOLBLENDER_OT_physics_show_shapes(Operator):
    bl_idname = "lolblender.physics_show_shapes"
    bl_label = "Show Body Shapes"
    bl_description = (
        "Show or hide the collision shapes. HIDING NEVER DELETES THEM - anything you moved, "
        "rotated or scaled is still there when you show them again")
    bl_options = {'UNDO'}

    def execute(self, context):
        from ..core import shapes

        armature = _armature(context)
        if armature is None:
            self.report({'ERROR'}, "no armature in the scene")
            return {'CANCELLED'}
        if not shapes.exist(context.scene):
            self.report({'WARNING'}, "no shapes yet - press Set Body Shapes first")
            return {'CANCELLED'}
        visible = not shapes.shown(context.scene)
        count = shapes.show(visible, context.scene)
        self.report({'INFO'}, "%s %d body shape%s"
                    % ("showing" if visible else "hidden", count, "" if count == 1 else "s"))
        _redraw()
        return {'FINISHED'}


class LOLBLENDER_OT_physics_shape_kind(Operator):
    bl_idname = "lolblender.physics_shape_kind"
    bl_label = "Shape"
    bl_description = (
        "Swap the selected body shapes between a box and a sphere, keeping where you put them. "
        "With nothing selected it swaps every shape on the rig")
    bl_options = {'UNDO'}

    kind: EnumProperty(items=(('BOX', "Box", "A box"), ('SPHERE', "Sphere", "A sphere")),
                       default='BOX')

    def execute(self, context):
        from ..core import shapes

        armature = _armature(context)
        if armature is None:
            self.report({'ERROR'}, "no armature in the scene")
            return {'CANCELLED'}
        collection = bpy.data.collections.get(shapes.COLLECTION)
        if collection is None or not collection.objects:
            self.report({'WARNING'}, "no shapes yet - press Set Body Shapes first")
            return {'CANCELLED'}

        rig_shapes = [o for o in collection.objects if shapes.owner(o) == armature]
        chosen = [o for o in rig_shapes if o.select_get()]
        if not chosen:
            chosen = rig_shapes
        changed = sum(1 for obj in chosen if shapes.set_kind(obj, self.kind))
        self.report({'INFO'}, "%d shape%s now %s"
                    % (changed, "" if changed == 1 else "s", self.kind.lower()))
        _redraw()
        return {'FINISHED'}


class LOLBLENDER_OT_physics_group_shapes(Operator):
    bl_idname = "lolblender.physics_group_shapes"
    bl_label = "Group Body Shapes (Articulated)"
    bl_description = "Group selected shapes for selection together; each keeps its own bone and collision volume"
    bl_options = {'UNDO'}

    operation: EnumProperty(items=[('GROUP', 'Group', ''), ('SELECT', 'Select Group', ''),
                                  ('UNGROUP', 'Ungroup', '')], default='GROUP')

    def execute(self, context):
        from ..core import shapes
        import uuid

        active = context.object
        armature = shapes.owner(active)
        if armature is None:
            self.report({'ERROR'}, "Select a body shape as the active object")
            return {'CANCELLED'}
        key = "lol_shape_group"
        collection = bpy.data.collections.get(shapes.COLLECTION)
        members = [obj for obj in collection.objects if shapes.owner(obj) == armature]
        if self.operation == 'GROUP':
            chosen = [obj for obj in context.selected_objects if shapes.owner(obj) == armature]
            if len(chosen) < 2 or any(shapes.owner(obj) != armature for obj in context.selected_objects):
                self.report({'ERROR'}, "Select at least two body shapes from the same rig")
                return {'CANCELLED'}
            groups = {obj.get(key) for obj in chosen if obj.get(key)}
            chosen = [obj for obj in members if obj in chosen or obj.get(key) in groups]
            group = uuid.uuid4().hex
            for obj in chosen:
                obj[key] = group
            self.report({'INFO'}, "%d shapes grouped; each still follows its own bone" % len(chosen))
        else:
            group = active.get(key)
            if not group:
                self.report({'WARNING'}, "The active shape has no group")
                return {'CANCELLED'}
            chosen = [obj for obj in members if obj.get(key) == group]
            if self.operation == 'SELECT':
                for obj in list(context.selected_objects):
                    obj.select_set(False)
                for obj in chosen:
                    if obj.name in context.view_layer.objects:
                        obj.hide_set(False)
                        obj.select_set(True)
            else:
                for obj in chosen:
                    del obj[key]
        _redraw()
        return {'FINISHED'}


class LOLBLENDER_OT_physics_remove_shapes(Operator):
    bl_idname = "lolblender.physics_remove_shapes"
    bl_label = "Remove All Body Shapes"
    bl_description = "Remove all body shapes belonging to this rig, keeping its physics lists"
    bl_options = {'UNDO'}

    def execute(self, context):
        from ..core import shapes, shape_mirror

        armature = _armature(context)
        if armature is None:
            self.report({'ERROR'}, "no armature in the scene")
            return {'CANCELLED'}
        gone = shapes.clear(context.scene, armature=armature)
        shape_mirror.reset()
        topology_changed(armature)
        _redraw()
        self.report({'INFO'}, "%d body shapes removed from %s" % (gone, armature.name))
        return {'FINISHED'}


class LOLBLENDER_OT_physics_reset_shapes(Operator):
    bl_idname = "lolblender.physics_reset_shapes"
    bl_label = "Reset Body Shapes"
    bl_description = (
        "Throw every body shape away and build them again at their starting size and place. "
        "Everything you moved is lost")
    bl_options = {'UNDO'}

    def execute(self, context):
        from ..core import shapes

        armature = _armature(context)
        if armature is None:
            self.report({'ERROR'}, "no armature in the scene")
            return {'CANCELLED'}
        gone = shapes.clear(context.scene, armature=armature)
        made, _kept = shapes.ensure(armature, context.scene)
        shapes.show(True, context.scene)
        self.report({'INFO'}, "%d shape%s rebuilt from %d"
                    % (made, "" if made == 1 else "s", gone))
        topology_changed(armature)
        _redraw()
        return {'FINISHED'}


# ---------------------------------------------------------------------------
#  Bake
# ---------------------------------------------------------------------------

#: Settling passes over a looping clip before recording, as the old addon did.
LOOP_PASSES = 5

def detected_loop(action):
    """What Automatic makes of this clip."""
    if action is None:
        return False
    return bake.detects_loop(action, int(round(action.frame_range[0])),
                             int(round(action.frame_range[1])))


def _simulated_names(armature):
    names = []
    for _identifier, physics_set in physics_props.sets_of(armature):
        if not physics_set.enabled:
            continue
        names.extend(item.name for item in physics_set.bones if item.name)
    return names


def run_bake(context, armature, report=None):
    """Keep the immediately preceding Action intact if any bake stage fails."""
    action=armature.animation_data.action if armature.animation_data else None
    if action is None:return False,"load an animation first"
    snapshot=action.copy()
    original_name=action.name
    scene=context.scene
    frame=(scene.frame_current,scene.frame_subframe)
    live=armature.lol_physics.live
    try:
        return _run_bake(context,armature,report)
    except Exception as error:
        # Remap shared Action users as well: none should retain partly stripped keys.
        armature.lol_physics.live=False
        action.user_remap(snapshot)
        bpy.data.actions.remove(action)
        snapshot.name=original_name
        snapshot=None
        forget(armature)
        return False,"Bake failed; previous animation restored: "+str(error)
    finally:
        if snapshot is not None:bpy.data.actions.remove(snapshot)
        scene.frame_set(frame[0],subframe=frame[1])
        armature.lol_physics.live=live

def _run_bake(context, armature, report=None):
    """Simulate the current action and key the result. Returns `(ok, message)`."""
    scene = context.scene
    if armature.animation_data is None or armature.animation_data.action is None:
        return False, "load an animation first"
    action = armature.animation_data.action
    names = _simulated_names(armature)
    if not names:
        return False, "no bones listed"
    try:
        wiggle.validate_sets(armature)
    except ValueError as error:
        return False, str(error)

    frame_start = int(round(action.frame_range[0]))
    frame_end = int(round(action.frame_range[1]))
    if frame_end <= frame_start:
        return False, "the animation has no frames"

    # backup before stripping, for Clear Baked Keys
    _take_backup(armature, action)

    bake.strip(action, names)

    mode = armature.lol_physics.loop_mode
    looping = (detected_loop(action) if mode == 'AUTO' else mode == 'LOOP')
    rig = rig_for(armature, rebuild=True)
    saved_frame = scene.frame_current
    saved_subframe = scene.frame_subframe
    live = armature.lol_physics.live
    armature.lol_physics.live = False        # the handler must not also step while we drive it
    try:
        fps = scene.render.fps / max(1e-6, scene.render.fps_base)
        dt = 1.0 / fps

        # a loop settles first (it has no start); a clip that plays once starts from rest
        if looping:
            # always from the clip's first frame, wherever the playhead was
            scene.frame_set(frame_start)
            rig.reset()
            for _ in range(LOOP_PASSES):
                # skip the last frame: it repeats the first
                for frame in range(frame_start, frame_end):
                    scene.frame_set(frame)
                    rig.refresh_settings()
                    gravity = wiggle.scene_gravity(scene)
                    for sim in rig.sims:
                        # written, like the recorded pass, so both see the same poses
                        sim.step(dt, 1.0, gravity, write=True)
        else:
            scene.frame_set(frame_start)
            rig.reset()

        samples = {}
        recorded = range(frame_start, frame_end + 1)

        for frame in recorded:
            scene.frame_set(frame)
            rig.refresh_settings()
            gravity = wiggle.scene_gravity(scene)
            for sim in rig.sims:
                deltas = sim.step(dt, 1.0, gravity, write=True)
                for pose_bone, delta in zip(sim.topology.pose_bones, deltas):
                    location, rotation, scale = delta.decompose()
                    values = (tuple(rotation), tuple(location), tuple(scale))
                    if not all(v == v and abs(v) != float("inf")
                               for group in values for v in group):
                        # non-finite: leave unkeyed, LINEAR bridges the gap
                        continue
                    samples[(pose_bone.name, frame)] = values

        bake.write(action, samples, names, frame_start, frame_end)
        bake.finish(action, names, frame_start, frame_end, looping,
                    motion=lambda: _apply_motion(context, armature, action,
                                                 frame_start, frame_end))
    finally:
        armature.lol_physics.live = live
        scene.frame_set(saved_frame, subframe=saved_subframe)

    action["lol_physics_substeps"] = 1  # this bake uses the 2.2.0 frame rate
    action["lol_physics_unexported"] = True
    if report is not None:
        report({'INFO'}, "baked %d bones over %d frames%s"
               % (len(names), frame_end - frame_start + 1, ", looping" if looping else ""))
    return True, "baked"


#: Name prefix of the pre-bake backup action (fake user).
BACKUP_PREFIX = "__lolphys_"


def _take_backup(armature, action):
    """Copy the action before the bake. A second bake keeps the first copy (the original)."""
    props = armature.lol_physics
    existing = bpy.data.actions.get(props.backup_action) if props.backup_action else None
    if existing is not None and existing.name == BACKUP_PREFIX + action.name:
        return existing

    # copy before cleanup: the backup itself may be the active action in old files
    backup = action.copy()
    backup.name = BACKUP_PREFIX + action.name
    _drop_backup(armature)
    backup.use_fake_user = True
    props.backup_action = backup.name
    return backup


def _drop_backup(armature):
    props = armature.lol_physics
    if not props.backup_action:
        return
    previous = bpy.data.actions.get(props.backup_action)
    if previous is not None:
        # never delete an action still in use
        if previous.users <= int(previous.use_fake_user):
            previous.use_fake_user = False
            bpy.data.actions.remove(previous)
    props.backup_action = ""


def _restore_backup(armature):
    """Put the pre-bake action back. Returns True when there was one."""
    props = armature.lol_physics
    backup = bpy.data.actions.get(props.backup_action) if props.backup_action else None
    if backup is None or armature.animation_data is None:
        props.backup_action = ""
        return False
    baked = armature.animation_data.action
    name = backup.name[len(BACKUP_PREFIX):] if backup.name.startswith(BACKUP_PREFIX) else None
    armature.animation_data.action = backup
    if baked is not None and baked is not backup and baked.users<=int(baked.use_fake_user):
        bpy.data.actions.remove(baked)
    if name:
        backup.name = name
    backup.use_fake_user = False
    props.backup_action = ""
    return True


def _apply_motion(context, armature, action, frame_start, frame_end):
    """The Motion pass (see core/motion.py), for sets with a reference bone."""
    from ..core import motion

    for identifier, physics_set in physics_props.sets_of(armature):
        if not physics_set.enabled or physics_set.motion >= 0.999:
            continue
        name = physics_set.motion_bone.strip()
        if not name or name not in armature.pose.bones:
            continue
        names = [item.name for item in physics_set.bones if item.name]
        motion.calm(context, armature, action, names, frame_start, frame_end,
                    name, physics_set.motion)


class LOLBLENDER_OT_physics_bake(Operator):
    bl_idname = "lolblender.physics_bake"
    bl_label = "Bake to Action"
    bl_description = ("Run the simulation over the whole clip and key it, so the exporter writes "
                      "it like any other animation")
    bl_options = {'UNDO'}

    def execute(self, context):
        armature = _armature(context)
        if armature is None:
            self.report({'ERROR'}, "no armature in the scene")
            return {'CANCELLED'}
        started = time.perf_counter()
        ok, message = run_bake(context, armature, self.report)
        if not ok:
            armature.lol_physics.status = message
            self.report({'ERROR'}, message)
            return {'CANCELLED'}
        armature.lol_physics.status = "Baked in %.1fs" % (time.perf_counter() - started)
        return {'FINISHED'}


def fast_save_anm(armature, scene):
    """Export the active action over its own source `.anm`, via a temp file."""
    import tempfile
    from ..core import anm_out
    from .. import native
    action = armature.animation_data.action if armature.animation_data else None
    if action is None or action.get("riot_animation_rig") != armature:
        raise ValueError("Load an animation onto this rig first")
    if action.name.startswith(BACKUP_PREFIX):
        raise ValueError("The active Action is a physics backup; load the intended animation first")
    source = action.get("lol_anm_filepath", "")
    if not source:
        raise ValueError("This Action has no source .anm; use the regular export")
    target = os.path.realpath(bpy.path.abspath(source))
    if os.path.splitext(target)[1].lower() != '.anm' or not os.path.isfile(target):
        raise ValueError("Source .anm not found: " + target)
    start, end = anm_out.frame_range(scene, armature, use_scene_range=False)
    if end < start:
        raise ValueError("Invalid Action frame range")
    before = os.stat(target)
    handle, pending = tempfile.mkstemp(prefix='.daza-save-', suffix='.anm', dir=os.path.dirname(target))
    os.close(handle)
    live = armature.lol_physics.live
    try:
        # Live off, so sampling does not re-simulate
        armature.lol_physics.live = False
        anm_out.export_anm(armature, pending, scene, start, end)
        native.rs().Animation.from_path(pending)
        now = os.stat(target)
        if (now.st_mtime_ns, now.st_size) != (before.st_mtime_ns, before.st_size):
            raise RuntimeError("Source file changed during export; nothing was replaced")
        if armature.animation_data.action != action or action.get("lol_anm_filepath") != source:
            raise RuntimeError("Active animation changed during export; nothing was replaced")
        os.replace(pending, target)
        action["lol_physics_unexported"] = False
        return target
    finally:
        if os.path.exists(pending):
            os.remove(pending)
        armature.lol_physics.live = live


class LOLBLENDER_OT_physics_fast_save_anm(Operator):
    bl_idname = "lolblender.physics_fast_save_anm"
    bl_label = "Fast Save ANM"
    bl_description = "Save the active Action's keys over its exact source .anm file, without running Live physics"
    bl_options = {'REGISTER'}

    def execute(self, context):
        armature = _armature(context)
        if armature is None:
            self.report({'ERROR'}, "No armature in the scene")
            return {'CANCELLED'}
        try:
            target = fast_save_anm(armature, context.scene)
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        armature.lol_physics.status = "Saved " + os.path.basename(target)
        self.report({'INFO'}, "Saved " + target)
        return {'FINISHED'}


class LOLBLENDER_OT_physics_clear_bake(Operator):
    bl_idname = "lolblender.physics_clear_bake"
    bl_label = "Clear Baked Keys"
    bl_description = ("Undo the bake: put back the clip exactly as it was imported, and the bones "
                      "back on the animated pose")
    bl_options = {'UNDO'}

    def execute(self, context):
        armature = _armature(context)
        if armature is None:
            self.report({'ERROR'}, "no armature in the scene")
            return {'CANCELLED'}
        names = _simulated_names(armature)
        removed = 0
        restored = _restore_backup(armature)
        action = armature.animation_data.action if armature.animation_data else None
        if action is not None and not restored:
            # no backup: just remove the physics channels
            removed = bake.strip(action, names)

        # reset the pose too, or the last simulated frame stays
        wiggle.clear_pose(armature, names)
        rig_for(armature, rebuild=True)
        context.scene.frame_set(context.scene.frame_current)
        self.report({'INFO'}, ("restored the clip as it was imported" if restored
                               else "removed %d channels" % removed))
        return {'FINISHED'}


class LOLBLENDER_OT_physics_pick_export(Operator):
    bl_idname = "lolblender.physics_pick_export"
    bl_label = "Choose Output Folder"
    bl_description = "Where Apply to all animations writes its .anm files"

    directory: StringProperty(subtype='DIR_PATH')

    def execute(self, context):
        armature = _armature(context)
        if armature is not None and self.directory:
            armature.lol_physics.export_folder = self.directory
        return {'FINISHED'}

    def invoke(self, context, _event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class LOLBLENDER_OT_physics_apply_all(Operator):
    bl_idname = "lolblender.physics_apply_all"
    bl_label = "Apply to All Animations"
    bl_description = ("Load every clip in the list, bake the physics into it and write it back "
                      "out as .anm")
    bl_options = {'REGISTER'}

    def execute(self, context):
        from ..core import anm_in, anm_out

        armature = _armature(context)
        if armature is None:
            self.report({'ERROR'}, "no armature in the scene")
            return {'CANCELLED'}
        clips = list(context.scene.lolblender.animations)
        if not clips:
            self.report({'ERROR'}, "no clips listed - press Refresh first")
            return {'CANCELLED'}
        if not _simulated_names(armature):
            self.report({'ERROR'}, "no bones listed")
            return {'CANCELLED'}

        props = armature.lol_physics
        out_dir = bpy.path.abspath(props.export_folder) if props.export_folder else ""
        if out_dir and not os.path.isdir(out_dir):
            self.report({'ERROR'}, "output folder does not exist: %s" % out_dir)
            return {'CANCELLED'}

        if armature.animation_data is None:
            armature.animation_data_create()
        original = armature.animation_data.action
        scene = context.scene
        saved_timeline = (scene.frame_current, scene.frame_subframe, scene.frame_start, scene.frame_end,
                          scene.render.fps, scene.render.fps_base)
        undo = context.preferences.edit.use_global_undo
        context.preferences.edit.use_global_undo = False
        done = 0
        failed = []
        live = props.live
        props.live = False  # no live simulation while exporting the bakes
        import time
        try:
            for clip in clips:
                props.status = "%s..." % clip.name
                try:
                    started = time.perf_counter()
                    source = os.path.normcase(os.path.abspath(clip.filepath))
                    previous = [a for a in bpy.data.actions
                                if a.get("riot_animation_rig") == armature
                                and not a.name.startswith(BACKUP_PREFIX)
                                and os.path.normcase(os.path.abspath(a.get("lol_anm_filepath", ""))) == source]
                    anm_in.import_anm(clip.filepath, armature, replace_existing=True)
                    imported = time.perf_counter()
                    ok, message = run_bake(context, armature)
                    _drop_backup(armature)
                    if not ok:
                        failed.append("%s: %s" % (clip.name, message))
                        continue
                    target = os.path.join(out_dir or os.path.dirname(clip.filepath),
                                          "%s.anm" % clip.name)
                    # the action's range: the scene stops one frame short on loops
                    start, end = anm_out.frame_range(armature=armature, scene=context.scene,
                                                     use_scene_range=False)
                    baked_at = time.perf_counter()
                    anm_out.export_anm(armature, target, context.scene, start, end)
                    baked_action = armature.animation_data.action
                    from ..core import legacy
                    legacy.stamp_action_source(baked_action, target)
                    baked_action["lol_physics_unexported"] = False
                    clip.filepath = target
                    # after a successful export, replace the old unbaked actions
                    for old in previous:
                        if old == original:
                            original = baked_action
                        old.user_remap(baked_action)
                        bpy.data.actions.remove(old)
                    print("[LoL Physics] %s: import %.2fs, bake %.2fs, export %.2fs" %
                          (clip.name, imported-started, baked_at-imported, time.perf_counter()-baked_at))
                    done += 1
                except Exception as error:              # noqa: BLE001 - one bad clip, not a stop
                    failed.append("%s: %s" % (clip.name, error))
        finally:
            context.preferences.edit.use_global_undo = undo
            armature.animation_data.action = original
            frame, subframe, scene.frame_start, scene.frame_end, scene.render.fps, scene.render.fps_base = saved_timeline
            scene.frame_set(frame, subframe=subframe)
            props.live = live
            props.status = "Ready"

        if not failed and out_dir:
            context.scene.lolblender.custom_folder = out_dir
            context.scene.lolblender.folder = out_dir
        if failed:
            print("[LoL Blender] apply to all - failures:\n  " + "\n  ".join(failed))
            self.report({'WARNING'}, "%d of %d written, %d failed (see the console)"
                        % (done, len(clips), len(failed)))
        else:
            self.report({'INFO'}, "%d animations written" % done)
        return {'FINISHED'}


_classes = (
    LOLBLENDER_OT_physics_add_bone,
    LOLBLENDER_OT_physics_remove_bone,
    LOLBLENDER_OT_physics_delete_branch,
    LOLBLENDER_OT_physics_bones_from_selection,
    LOLBLENDER_OT_physics_auto_setup,
    LOLBLENDER_OT_physics_auto_colliders,
    LOLBLENDER_OT_physics_all_colliders,
    LOLBLENDER_OT_physics_motion_bone,
    LOLBLENDER_OT_physics_reset_settings,
    LOLBLENDER_OT_physics_reset_custom,
    LOLBLENDER_OT_physics_reset,
    LOLBLENDER_OT_physics_set_shapes,
    LOLBLENDER_OT_physics_show_shapes,
    LOLBLENDER_OT_physics_shape_kind,
    LOLBLENDER_OT_physics_reset_shapes,
    LOLBLENDER_OT_physics_remove_shapes,
    LOLBLENDER_OT_physics_group_shapes,
    LOLBLENDER_OT_physics_bake,
    LOLBLENDER_OT_physics_fast_save_anm,
    LOLBLENDER_OT_physics_clear_bake,
    LOLBLENDER_OT_physics_pick_export,
    LOLBLENDER_OT_physics_apply_all,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    _install()
    from ..core import shape_mirror
    shape_mirror.register()


def unregister():
    from ..core import shape_mirror
    shape_mirror.unregister()
    _remove()
    for rig, _signature in list(_rigs.values()):
        try:
            release_preview(rig.armature)
        except ReferenceError:
            pass  # stale cached datablock after an undo or file load
    forget()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
