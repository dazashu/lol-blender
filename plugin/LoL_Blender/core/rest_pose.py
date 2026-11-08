"""Turn a pose into the rest pose. Used by the skeleton lock, the bind at the current pose, set
bind pose and the `.skl` update.

Same contract as Maya's bind pose: the skeleton stays exactly where it is seen, the meshes bound
to it keep the shape they show (Maya's skin cluster keeps deforming them), and the actions are
reloaded from their `.anm` so they still play the same.
"""

import os

import bpy
import numpy as np
from mathutils import Matrix

from . import armature as arm_util
from . import convert
from . import legacy
from . import registry as reg

#: A pose channel further than this from identity counts as posed.
POSED_TOLERANCE = 1e-5

#: A rest that moved less than this (relative, as in skl_out) keeps its stamps untouched.
UNCHANGED_TOLERANCE = 1e-6

#: Object property: the skeleton is locked (see ops/lock.py).
LOCKED = "lol_skeleton_locked"
#: Object property: with the lock on, bound meshes keep the shape they show.
LOCK_MESHES = "lol_skeleton_lock_meshes"


class Result:
    def __init__(self):
        self.bones = 0
        self.meshes = []
        self.reloaded = 0
        self.orphaned = []
        self.skipped_meshes = []

    def message(self, armature):
        text = "%r: %d bone%s set as rest" % (armature.name, self.bones,
                                             "" if self.bones == 1 else "s")
        if self.meshes:
            text += ", %d mesh%s kept as shown" % (len(self.meshes),
                                                  "" if len(self.meshes) == 1 else "es")
        if self.reloaded:
            text += ", %d action%s reloaded" % (self.reloaded, "" if self.reloaded == 1 else "s")
        return text


def is_posed(armature, tolerance=POSED_TOLERANCE):
    """True when any pose channel is away from identity."""
    identity = Matrix.Identity(4)
    return any(convert.max_element_deviation(pb.matrix_basis, identity) > tolerance
               for pb in armature.pose.bones)


def is_animated(armature):
    """True when an action or an NLA track drives the pose."""
    data = armature.animation_data
    if data is None:
        return False
    return data.action is not None or any(t.strips for t in data.nla_tracks if not t.mute)


def is_locked(armature):
    return armature is not None and bool(armature.get(LOCKED, False))


def bound_meshes(armature, view_layer=None):
    """Meshes in the view layer with an armature modifier on this rig."""
    view_layer = view_layer or bpy.context.view_layer
    return [o for o in view_layer.objects
            if o.type == 'MESH'
            and any(m.type == 'ARMATURE' and m.object is armature for m in o.modifiers)]


def basis_for(armature, wanted):
    """Pose channels that put each bone at `wanted[name]` (armature space).

    Blender: pose(b) = pose(parent) @ rest(parent)^-1 @ rest(b) @ basis(b), so
    basis(b) = rest(b)^-1 @ rest(parent) @ wanted(parent)^-1 @ wanted(b). A bone missing from
    `wanted` keeps its current pose matrix.
    """
    current = {pb.name: pb.matrix.copy() for pb in armature.pose.bones}
    target = {name: wanted.get(name, current[name]) for name in current}
    out = {}
    for pose_bone in armature.pose.bones:
        bone = pose_bone.bone
        rest = bone.matrix_local
        if bone.parent is None:
            out[bone.name] = rest.inverted() @ target[bone.name]
        else:
            out[bone.name] = (rest.inverted() @ bone.parent.matrix_local
                              @ target[bone.parent.name].inverted() @ target[bone.name])
    return out


def _bake_meshes(context, armature, meshes, result):
    """Write each mesh's skinned shape into its own data, shape keys included.

    Only this rig's armature modifiers are evaluated. With a shape key active and
    `show_only_shape_key` on, Blender evaluates that key's raw coordinates, so every key is
    deformed by the same skinning as the basis.
    """
    for obj in meshes:
        data = obj.data
        count = len(data.vertices)
        keys = data.shape_keys
        blocks = list(keys.key_blocks) if keys is not None else [None]
        saved_mods = [(m, m.show_viewport) for m in obj.modifiers]
        saved_only = obj.show_only_shape_key
        saved_index = obj.active_shape_key_index
        shapes = []
        try:
            for modifier in obj.modifiers:
                modifier.show_viewport = (modifier.show_viewport and modifier.type == 'ARMATURE'
                                          and modifier.object is armature)
            for index, block in enumerate(blocks):
                if block is not None:
                    obj.show_only_shape_key = True
                    obj.active_shape_key_index = index
                depsgraph = context.evaluated_depsgraph_get()
                depsgraph.update()
                evaluated = obj.evaluated_get(depsgraph)
                mesh = evaluated.to_mesh()
                try:
                    if len(mesh.vertices) != count:
                        shapes = None
                        break
                    co = np.empty(count * 3, dtype=np.float32)
                    mesh.vertices.foreach_get("co", co)
                    shapes.append(co)
                finally:
                    evaluated.to_mesh_clear()
        finally:
            for modifier, shown in saved_mods:
                modifier.show_viewport = shown
            obj.show_only_shape_key = saved_only
            obj.active_shape_key_index = saved_index
        if not shapes:
            result.skipped_meshes.append(obj.name)
            continue
        if blocks[0] is None:
            data.vertices.foreach_set("co", shapes[0])
        else:
            for block, co in zip(blocks, shapes):
                block.data.foreach_set("co", co)
            # the mesh holds the reference key
            reference = keys.reference_key
            data.vertices.foreach_set("co", shapes[blocks.index(reference)])
        data.update()
        result.meshes.append(obj.name)


def restamp(bone):
    """Point the stored League bind at the bone's current rest.

    The file's raw T/R/S no longer describe it, so they go: the `.skl` writer then rebuilds the
    joint from the rest instead of writing the old file values back.
    """
    bone[reg.REST_GLOBAL] = convert.matrix_to_flat(
        convert.blender_to_riot(arm_util.exact_rest(bone)))
    bone[reg.REST_LOCAL] = convert.matrix_to_flat(
        convert.blender_to_riot(arm_util.exact_rest_local(bone)))
    for key in (reg.LOCAL_TRS, reg.IBIND_TRS):
        if key in bone:
            del bone[key]


def stamp_legacy(armature):
    for index, bone in enumerate(armature.data.bones):
        legacy.stamp_bone(bone, armature.pose.bones[bone.name], index,
                          bone.parent.name if bone.parent else "")


def _rig_actions(armature):
    data = armature.animation_data
    assigned = data.action if data else None
    strips = [s for t in data.nla_tracks for s in t.strips if s.action] if data else []
    actions = set(a for a in bpy.data.actions if a.get("riot_animation_rig") == armature)
    actions.update(s.action for s in strips)
    if assigned:
        actions.add(assigned)
    return assigned, strips, actions


def check_actions(armature):
    """Read every source `.anm` first, so nothing changes when one is broken. Raises."""
    from .. import native
    _assigned, _strips, actions = _rig_actions(armature)
    for action in actions:
        source = action.get("lol_anm_filepath")
        if source and os.path.isfile(source):
            native.rs().Animation.from_path(source)


def _reload_actions(context, armature, result):
    from . import anm_in

    assigned, strips, actions = _rig_actions(armature)
    replacements = {}
    for action in actions:
        source = action.get("lol_anm_filepath")
        if not source or not os.path.isfile(source):
            if action.users:
                result.orphaned.append(action.name)
            continue
        rebuilt = anm_in.import_anm(source, armature, name=action.name, replace_existing=False,
                                    set_fps=False, set_frame_range=False).action
        replacements[action] = rebuilt
        result.reloaded += 1
    if armature.animation_data:
        armature.animation_data.action = replacements.get(assigned, assigned)
    for strip in strips:
        if strip.action in replacements:
            strip.action = replacements[strip.action]
    for old, new in replacements.items():
        # keep actions other objects still use
        if old.users <= int(old.use_fake_user):
            name = old.name
            bpy.data.actions.remove(old)
            new.name = name


def _mode_set(context, mode):
    if context.view_layer.objects.active is not None:
        bpy.ops.object.mode_set(mode=mode)


def pose_to_rest(context, armature, wanted=None, keep_meshes=True, keep_animation=True,
                 stamp=None, edit=None):
    """Make the pose the rest. Returns a Result.

    wanted:  {bone name: armature-space matrix}. The skeleton is posed there first; by default
             it is the pose being shown (constraints included).
    stamp:   called with the names whose rest changed, after Edit Mode and before the actions
             are reloaded. Default: `restamp` each of them.
    edit:    called with the edit bones, in Edit Mode, after the rests are set.
    """
    result = Result()
    view_layer = context.view_layer
    frame = context.scene.frame_current
    subframe = context.scene.frame_subframe

    # Object Mode first: Edit Mode holds stale bones and meshes
    previous_active = view_layer.objects.active
    previous_mode = previous_active.mode if previous_active else 'OBJECT'
    hidden = armature.hide_get()
    if previous_mode != 'OBJECT':
        _mode_set(context, 'OBJECT')
        view_layer.update()

    if wanted is not None:
        for name, basis in basis_for(armature, wanted).items():
            armature.pose.bones[name].matrix_basis = basis
        view_layer.update()
    target = {pb.name: pb.matrix.copy() for pb in armature.pose.bones}
    if wanted is not None:
        target.update({k: v.copy() for k, v in wanted.items() if k in target})

    if keep_meshes:
        _bake_meshes(context, armature, bound_meshes(armature, view_layer), result)

    old_rest = {b.name: arm_util.exact_rest(b) for b in armature.data.bones}

    armature.hide_set(False)
    view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        for edit_bone in armature.data.edit_bones:
            matrix = target.get(edit_bone.name)
            if matrix is None:
                continue
            # orthonormal: an edit bone cannot hold scale
            arm_util.apply_transform_flags(edit_bone)
            length = edit_bone.length
            edit_bone.matrix = convert.orthonormalized(matrix)
            edit_bone.length = length
        if edit is not None:
            edit(armature.data.edit_bones)
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')

    changed = []
    for bone in armature.data.bones:
        before = old_rest.get(bone.name)
        now = arm_util.exact_rest(bone)
        radius = max(1.0, max(abs(v) for v in now.to_translation()))
        if before is None or convert.max_element_deviation(before, now) / radius > UNCHANGED_TOLERANCE:
            changed.append(bone.name)
    result.bones = len(changed)

    for pose_bone in armature.pose.bones:
        pose_bone.matrix_basis.identity()

    if stamp is None:
        for name in changed:
            if reg.REST_GLOBAL in armature.data.bones[name] or reg.HASH in armature.data.bones[name]:
                restamp(armature.data.bones[name])
    else:
        stamp(changed)
    stamp_legacy(armature)

    if keep_animation:
        _reload_actions(context, armature, result)
    context.scene.frame_set(frame, subframe=subframe)

    armature.hide_set(hidden)
    view_layer.objects.active = previous_active or armature
    if previous_mode != 'OBJECT' and view_layer.objects.active is not None:
        try:
            bpy.ops.object.mode_set(mode=previous_mode)
        except RuntimeError:
            pass
    return result


def settle_for_bind(context, armature, bind_current_pose=True):
    """Before a bind: make the shown pose the rest, so the bind happens where the skeleton is.

    Done when the skeleton is locked, or when Bind at Current Pose is on and no animation drives
    the pose (a playing clip is not a pose to bind at). Returns a Result or None.
    """
    if armature is None or not is_posed(armature):
        return None
    if not (is_locked(armature) or (bind_current_pose and not is_animated(armature))):
        return None
    keep = bool(armature.get(LOCK_MESHES, True))
    return pose_to_rest(context, armature, keep_meshes=keep)
