"""Create and check bones so that bone space equals League joint space.

A bone's rest matrix is its joint transform (through `convert.P`), so a pose channel is the League
local delta under one constant matrix, with no per-bone correction.
"""

from mathutils import Matrix, Vector

from . import convert

#: Length of a bone with no child. Cosmetic: length does not change `Bone.matrix_local`.
DEFAULT_BONE_LENGTH = 1.0

#: Blender deletes zero-length bones when leaving Edit Mode.
MIN_BONE_LENGTH = 1e-4

#: Minimum bone length as a fraction of the head's distance from the origin. Head and tail are
#: float32, so a short bone far from the origin loses precision in its direction (its rotation).
DIRECTION_CONDITION_RATIO = 0.02


class BoneFlagError(Exception):
    """An armature carries a flag that breaks the bone-space == joint-space property."""


def apply_transform_flags(bone):
    """Set the flags League's transform model needs: `child_world = parent_world @ local`.

    `use_connect` must be off, or Blender moves the head onto the parent's tail.
    """
    bone.use_connect = False
    bone.use_inherit_rotation = True
    bone.inherit_scale = 'FULL'
    bone.use_local_location = True


def add_bone(edit_bones, name, matrix, length=DEFAULT_BONE_LENGTH, parent=None):
    """Create an edit bone whose rest matrix is exactly `matrix` (orthonormal). Returns it."""
    bone = edit_bones.new(name)

    # needs a length before `matrix` is set: the setter keeps the length
    bone.head = (0.0, 0.0, 0.0)
    bone.tail = (0.0, 1.0, 0.0)

    if parent is not None:
        bone.parent = parent

    # flags before the matrix, or use_connect would move the head
    apply_transform_flags(bone)

    bone.matrix = matrix
    bone.length = max(float(length), MIN_BONE_LENGTH)
    return bone


def suggest_length(head, children_heads, fallback=DEFAULT_BONE_LENGTH):
    """Length reaching the nearest child, clamped to `DIRECTION_CONDITION_RATIO`."""
    best = None
    for child in children_heads:
        d = (Vector(child) - Vector(head)).length
        if d > MIN_BONE_LENGTH and (best is None or d < best):
            best = d
    chosen = best if best is not None else fallback

    floor = max(MIN_BONE_LENGTH, Vector(head).length * DIRECTION_CONDITION_RATIO)
    return max(chosen, floor)


def reorient_of(bone):
    """The rotation the importer applied to this bone, or identity."""
    from . import convert
    from . import registry as reg

    stored = bone.get(reg.REORIENT) if bone is not None else None
    if stored is None or len(stored) != 16:
        return Matrix.Identity(4)
    return convert.flat_to_matrix([float(v) for v in stored])


def exact_rest(bone):
    """The rest matrix as the .skl had it, before the bone was turned to face its child.

    Use this to write League values back (.skl writer, promotion); Blender-space code uses
    `Bone.matrix_local`.
    """
    return bone.matrix_local @ reorient_of(bone).inverted()


def exact_rest_local(bone):
    """`exact_rest` relative to the parent: the `.skl` local transform."""
    if bone.parent is None:
        return exact_rest(bone)
    return exact_rest(bone.parent).inverted() @ exact_rest(bone)


def check_transform_flags(armature_object):
    """Bones whose flags break the transform model, one line each. Empty means clean."""
    problems = []
    for bone in armature_object.data.bones:
        if bone.use_connect:
            problems.append(
                "%s: use_connect is on - its head is welded to its parent's tail, so its "
                "direction is not what was authored" % bone.name)
        if not bone.use_inherit_rotation:
            problems.append("%s: use_inherit_rotation is off" % bone.name)
        if bone.inherit_scale != 'FULL':
            problems.append(
                "%s: inherit_scale is %r, League needs 'FULL'" % (bone.name, bone.inherit_scale))
        if not bone.use_local_location:
            problems.append("%s: use_local_location is off" % bone.name)
    return problems


def rest_matrix(bone):
    """Rest transform in armature space."""
    return bone.matrix_local.copy()


def rest_local(bone):
    """Rest transform relative to the parent."""
    if bone.parent is None:
        return bone.matrix_local.copy()
    return bone.parent.matrix_local.inverted() @ bone.matrix_local


def rebase_pose_matrix(old_rest, new_rest, old_parent_rest, new_parent_rest, basis):
    """Re-express a pose channel after the rest changed, keeping the same pose.

        basis' = R'^-1 @ R_p' @ R_p^-1 @ R @ basis

    Parent rests are None for a root bone.
    """
    if old_parent_rest is None or new_parent_rest is None:
        parent_term = Matrix.Identity(4)
    else:
        parent_term = new_parent_rest @ old_parent_rest.inverted()
    return new_rest.inverted() @ parent_term @ old_rest @ basis


def _selftest():
    """`rebase_pose_matrix` must be the identity when nothing moved."""
    a = Matrix.Translation((1.0, 2.0, 3.0))
    b = Matrix.Translation((-4.0, 0.5, 2.0))
    basis = Matrix.Rotation(0.7, 4, 'Z')
    same = rebase_pose_matrix(a, a, b, b, basis)
    assert convert.max_element_deviation(same, basis) < 1e-6
