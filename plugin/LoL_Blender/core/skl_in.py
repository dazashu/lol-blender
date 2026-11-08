"""Build a Blender armature from a `.skl`, with bone space equal to joint space.

The rest pose comes from each joint's bind transform (the engine skins with the inverse bind),
not from the local chain. Joints where the two disagree are reported.
"""

import bpy
from mathutils import Matrix

from . import armature as arm_util
from . import convert
from . import legacy
from . import registry as reg

#: Bind vs local-chain difference worth reporting.
DIVERGENCE_REPORT_THRESHOLD = 1e-3

#: Bind scale above this is reported. Rest bones cannot hold scale.
SCALE_REPORT_THRESHOLD = 1e-4

#: Relative tolerance, see convert.is_uniform_scale.
SCALE_UNIFORMITY_TOLERANCE = 1e-5

#: Bind shear above this is reported and dropped: nothing in Blender can hold it.
SHEAR_REPORT_THRESHOLD = 1e-4


class ImportReport:
    """What the importer measured, for the console and the operator report."""

    def __init__(self):
        self.joint_count = 0
        self.bone_count = 0
        self.parents_precede_children = True
        self.max_bind_divergence = 0.0
        self.worst_divergence_joint = None
        self.diverging_joints = []
        self.max_scale_residue = 0.0
        self.scaled_joints = []
        self.non_uniform_scale_joints = []
        self.shear_joints = []
        self.max_shear = 0.0
        self.degenerate_bind_joints = []
        self.truncated_names = []
        self.renamed_bones = []

    def summary(self):
        lines = [
            "joints %d -> bones %d" % (self.joint_count, self.bone_count),
        ]
        if not self.parents_precede_children:
            lines.append(
                "joint list is NOT topologically sorted - parents resolved by lookup, not by order")
        lines.append("max bind divergence %.6g%s" % (
            self.max_bind_divergence,
            "" if self.worst_divergence_joint is None else " at %r" % self.worst_divergence_joint))
        if self.diverging_joints:
            lines.append(
                "%d joints where the bind transform and the local chain disagree above %g: %s"
                % (len(self.diverging_joints), DIVERGENCE_REPORT_THRESHOLD,
                   ", ".join(self.diverging_joints[:8])
                   + (" ..." if len(self.diverging_joints) > 8 else "")))
        if self.degenerate_bind_joints:
            lines.append(
                "%d joints have a non-invertible inverse-bind and fell back to the local chain: %s"
                % (len(self.degenerate_bind_joints),
                   ", ".join(self.degenerate_bind_joints[:8])))
        if self.scaled_joints:
            lines.append(
                "%d joints carry bind scale (max residue %.6g); carried in the pose channel, not "
                "the rest bone: %s"
                % (len(self.scaled_joints), self.max_scale_residue,
                   ", ".join(self.scaled_joints[:8])))
        if self.non_uniform_scale_joints:
            lines.append(
                "%d of those are non-uniform (relative spread above %g): %s"
                % (len(self.non_uniform_scale_joints), SCALE_UNIFORMITY_TOLERANCE,
                   ", ".join(self.non_uniform_scale_joints[:8])))
        if self.shear_joints:
            lines.append(
                "%d joints whose bind carries SHEAR (max %.6g), which nothing in Blender can "
                "represent and which is therefore dropped: %s"
                % (len(self.shear_joints), self.max_shear, ", ".join(self.shear_joints[:8])))
        if self.truncated_names:
            lines.append(
                "%d joint names exceeded Blender's %d-character bone limit and were truncated "
                "(full names kept in %s): %s"
                % (len(self.truncated_names), reg.MAX_BONE_NAME, reg.NAME,
                   ", ".join(self.truncated_names[:4])))
        if self.renamed_bones:
            lines.append(
                "%d bone names collided and Blender suffixed them: %s"
                % (len(self.renamed_bones), ", ".join(self.renamed_bones[:8])))
        return lines


def _blender_rest_matrices(skeleton, report):
    """Orthonormal Blender rest matrices from the binds: list of (matrix, used_fallback).

    A singular inverse bind falls back to the local chain (decided in Rust, `bind_usable`).
    """
    out = []
    for joint in skeleton.joints:
        bind = convert.flat_to_matrix(joint.bind_global)
        fallback = not joint.bind_usable
        if fallback:
            report.degenerate_bind_joints.append(joint.name)

        blender = convert.riot_to_blender(bind)

        residue = convert.scale_residue(blender)
        if residue > SCALE_REPORT_THRESHOLD:
            report.max_scale_residue = max(report.max_scale_residue, residue)
            report.scaled_joints.append(joint.name)
            if not convert.is_uniform_scale(blender, SCALE_UNIFORMITY_TOLERANCE):
                report.non_uniform_scale_joints.append(joint.name)

        # shear is separate: scale survives in the pose, shear does not
        shear = convert.shear_residue(blender)
        if shear > SHEAR_REPORT_THRESHOLD:
            report.max_shear = max(report.max_shear, shear)
            report.shear_joints.append(joint.name)

        out.append((convert.orthonormalized(blender), fallback))
    return out


def _reorient_edit_bones(created, children_of):
    """Turn every bone so its Y axis points at its child. Returns the correction per bone.

    League joints do not point down the chain, so Blender would draw them sideways. The turn `C`
    is a rotation about the head, so the rest becomes `M @ C` and cancels out of import, export
    and skinning, which all read the live rest. The `.skl` writer undoes it via `reg.REORIENT`.

    `C` is measured from what Blender stored (`requested^-1 @ stored`), since a mirrored rest
    cannot be stored exactly. Bones without a child continue their parent's direction.
    """
    parent_of = {}
    for parent, kids in children_of.items():
        for child in kids:
            parent_of[child] = parent

    def aim(index, bone, target):
        """Turn `bone` so its Y lands on `target`. Returns the correction that was applied."""
        requested = bone.matrix.copy()
        if target is not None and target.length > arm_util.MIN_BONE_LENGTH:
            axis = requested.to_3x3().col[1].normalized()
            turn = axis.rotation_difference(target.normalized()).to_matrix().to_4x4()
            turned = turn @ requested.to_3x3().to_4x4()
            turned.translation = requested.translation
            bone.matrix = turned
        return requested.inverted() @ bone.matrix

    corrections = [None] * len(created)

    # pass A: aim at the nearest child
    for index, bone in enumerate(created):
        target = None
        best = None
        for child in children_of.get(index, ()):
            offset = created[child].head - bone.head
            distance = offset.length
            if distance > arm_util.MIN_BONE_LENGTH and (best is None or distance < best):
                best = distance
                target = offset
        corrections[index] = aim(index, bone, target)

    # pass B: a leaf continues its parent's (already turned) direction
    for index, bone in enumerate(created):
        if children_of.get(index):
            continue
        parent = parent_of.get(index)
        if parent is None:
            continue
        direction = created[parent].matrix.to_3x3().col[1]
        corrections[index] = corrections[index] @ aim(index, bone, direction.copy())

    return corrections


def build_armature(skeleton, name="Skeleton", source_path="", collection=None):
    """Create an armature object from a native `Skeleton`. Returns (object, ImportReport).

    Leaves the object in OBJECT mode, selected and active.
    """
    report = ImportReport()
    report.joint_count = len(skeleton.joints)
    report.parents_precede_children = skeleton.parents_precede_children

    for joint in skeleton.joints:
        # unusable binds (infinite divergence) are listed separately
        d = joint.bind_divergence
        if d == float("inf"):
            continue
        if d > report.max_bind_divergence:
            report.max_bind_divergence = d
            report.worst_divergence_joint = joint.name
        if d > DIVERGENCE_REPORT_THRESHOLD:
            report.diverging_joints.append(joint.name)

    rest = _blender_rest_matrices(skeleton, report)

    armature_data = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, armature_data)
    (collection or bpy.context.collection).objects.link(obj)

    previous_active = bpy.context.view_layer.objects.active
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        edit_bones = armature_data.edit_bones

        # pass 1: bones at their rest matrix; parents later, since a parent may come after
        # its child in the file
        created = []
        for index, joint in enumerate(skeleton.joints):
            wanted = joint.name[:reg.MAX_BONE_NAME]
            if wanted != joint.name:
                report.truncated_names.append(joint.name)

            bone = arm_util.add_bone(edit_bones, wanted, rest[index][0])
            if bone.name != wanted:
                report.renamed_bones.append("%s -> %s" % (wanted, bone.name))
            created.append(bone)

        # pass 2: parents
        for index, joint in enumerate(skeleton.joints):
            if joint.parent_id >= 0:
                created[index].parent = created[joint.parent_id]

        children_of = {}
        for index, joint in enumerate(skeleton.joints):
            if joint.parent_id >= 0:
                children_of.setdefault(joint.parent_id, []).append(index)

        # pass 3: turn each bone to face its child
        reorient = _reorient_edit_bones(created, children_of)

        # pass 4: lengths reach the child. The physics uses bone length as the segment length,
        # so a leaf takes its parent's length instead of a tiny stub.
        resolved = {}
        parent_of = {i: joint.parent_id for i, joint in enumerate(skeleton.joints)}

        def length_of(index, seen=None):
            if index in resolved:
                return resolved[index]
            seen = seen or set()
            if index in seen:                       # a cycle cannot happen in a .skl, but a
                return arm_util.DEFAULT_BONE_LENGTH  # corrupt file must not hang the importer
            seen.add(index)
            heads = [created[c].head for c in children_of.get(index, ())]
            if heads:
                value = arm_util.suggest_length(created[index].head, heads)
            else:
                parent = parent_of.get(index, -1)
                fallback = (length_of(parent, seen) if parent >= 0
                            else arm_util.DEFAULT_BONE_LENGTH)
                value = arm_util.suggest_length(created[index].head, (), fallback)
            resolved[index] = value
            return value

        for index, bone in enumerate(created):
            bone.length = length_of(index)

        bone_names = [b.name for b in created]
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
        if previous_active is not None:
            pass  # leave the new armature active; the caller expects it selected

    report.bone_count = len(armature_data.bones)
    _stamp_properties(obj, skeleton, bone_names, source_path, reorient)
    return obj, report


def _stamp_properties(obj, skeleton, bone_names, source_path, reorient=None):
    """Write the registry and every per-bone property the rest of the plugin reads."""
    registry_entries = {}
    hash_by_index = [j.hash for j in skeleton.joints]

    for index, joint in enumerate(skeleton.joints):
        bone = obj.data.bones[bone_names[index]]

        bone[reg.HASH] = joint.hash
        bone[reg.NAME] = joint.name
        bone[reg.JOINT_ID] = joint.id
        bone[reg.PARENT_ID] = joint.parent_id
        bone[reg.PARENT_HASH] = (
            hash_by_index[joint.parent_id] if joint.parent_id >= 0 else 0)
        bone[reg.FLAGS] = joint.flags
        bone[reg.RADIUS] = joint.radius
        bone[reg.REST_LOCAL] = list(joint.local_matrix)
        bone[reg.REST_GLOBAL] = list(joint.bind_global)
        if reorient is not None:
            bone[reg.REORIENT] = convert.matrix_to_flat(reorient[index])

        # the file's raw T/R/S, so an unedited joint exports byte-identical
        bone[reg.LOCAL_TRS] = (
            list(joint.local_translation) + list(joint.local_rotation) + list(joint.local_scale))
        bone[reg.IBIND_TRS] = (
            list(joint.inverse_bind_translation) + list(joint.inverse_bind_rotation)
            + list(joint.inverse_bind_scale))

        registry_entries[joint.hash] = bone.name

        legacy.stamp_bone(
            bone, obj.pose.bones[bone.name], index,
            bone_names[joint.parent_id] if joint.parent_id >= 0 else "")

    reg.write_registry(obj, registry_entries)
    obj[reg.JOINT_ORDER] = [int(h) for h in hash_by_index]
    obj[reg.INFLUENCES] = [int(i) for i in skeleton.influences]
    obj[reg.SKELETON_NAME] = skeleton.name
    obj[reg.SKELETON_ASSET] = skeleton.asset
    obj[reg.SKELETON_FLAGS] = skeleton.flags
    obj[reg.SOURCE_PATH] = source_path
    if source_path:
        legacy.stamp_skeleton_source(obj, source_path)


def import_skl(filepath, name=None, collection=None):
    """Read a `.skl` and build its armature. Returns (object, ImportReport)."""
    from .. import native

    import os
    skeleton = native.rs().Skeleton.from_path(filepath)
    if name is None:
        name = os.path.splitext(os.path.basename(filepath))[0]
    return build_armature(skeleton, name=name, source_path=filepath, collection=collection)
