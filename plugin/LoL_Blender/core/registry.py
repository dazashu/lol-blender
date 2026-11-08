"""The bone registry: which bones came from the `.skl`, and which the user added.

The importer stores `{joint_hash: bone_name}` on the armature object. A bone is native if the
registry maps its hash to that exact name, so a duplicate (`Bone.001`) is custom automatically.
"""

#: Object-level properties.
REGISTRY = "riot_bone_registry"       # {str(hash): bone_name}
JOINT_ORDER = "riot_joint_order"      # [hash, ...] in FILE order, so export can preserve it
SKELETON_NAME = "riot_skeleton_name"
SKELETON_ASSET = "riot_skeleton_asset"
SKELETON_FLAGS = "riot_skeleton_flags"
INFLUENCES = "riot_influences"        # [joint_index, ...] as stored in the .skl
SOURCE_PATH = "riot_skl_path"

#: Bone-level properties.
HASH = "riot_hash"
NAME = "riot_name"                    # the FULL joint name; Blender caps bone names at 63 chars
JOINT_ID = "riot_joint_id"
PARENT_HASH = "riot_parent_hash"      # 0 for a root; roots are identified by PARENT_ID == -1
PARENT_ID = "riot_parent_id"
FLAGS = "riot_flags"
RADIUS = "riot_radius"
REST_LOCAL = "riot_rest_local"        # League-space local transform, row-major 16 floats
REST_GLOBAL = "riot_rest_global"      # League-space bind transform, row-major 16 floats
LOCAL_TRS = "riot_local_trs"          # the file's OWN 10 floats: t.xyz, r.xyzw, s.xyz
IBIND_TRS = "riot_ibind_trs"          # same, for the inverse bind. See skl_in for why.
HELPER = "riot_helper"                # True = never write this bone into the .skl
PROMOTED = "riot_promoted"            # True = WE made this a joint; it did not come from the file

#: Rotation applied so the bone points at its child, row-major 16 floats. `Bone.matrix_local` is
#: the bind matrix times this; absent means identity. The .skl writer undoes it.
REORIENT = "riot_reorient"

#: Blender's bone name limit; longer names are truncated.
MAX_BONE_NAME = 63


def is_lol_armature(obj):
    """True for an armature this plugin imported."""
    return (
        obj is not None
        and obj.type == 'ARMATURE'
        and REGISTRY in obj
    )


def write_registry(obj, entries):
    """Store `{hash: bone_name}`. ID property keys must be strings, so hashes are decimal text."""
    obj[REGISTRY] = {str(int(h)): str(name) for h, name in entries.items()}


def read_registry(obj):
    """Return `{hash: bone_name}` with integer keys, or an empty dict."""
    raw = obj.get(REGISTRY)
    if raw is None:
        return {}
    items = raw.to_dict().items() if hasattr(raw, "to_dict") else dict(raw).items()
    out = {}
    for key, value in items:
        try:
            out[int(key)] = str(value)
        except (TypeError, ValueError):
            continue
    return out


def bone_hash(bone):
    """The joint hash stamped on a bone, or None if it carries none."""
    value = bone.get(HASH)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def is_helper(bone):
    """True for a bone marked never-export (IK targets, controls, physics helpers)."""
    return bool(bone.get(HELPER, False))


def is_native(bone, registry):
    """A bone is native iff the registry maps its hash to this exact name."""
    h = bone_hash(bone)
    if h is None:
        return False
    return registry.get(h) == bone.name


def classify(obj):
    """Split the bones into (native_names, custom_names, helper_names).

    A native bone marked helper is a contradiction, reported by `problems()`.
    """
    registry = read_registry(obj)
    native, custom, helper = set(), set(), set()
    for bone in obj.data.bones:
        if is_helper(bone):
            helper.add(bone.name)
        if is_native(bone, registry):
            native.add(bone.name)
        else:
            custom.add(bone.name)
    return native, custom, helper


def was_promoted(bone):
    """True for a joint this plugin promoted from a user bone, so it may be demoted again."""
    return bool(bone.get(PROMOTED, False))


def exportable_names(obj):
    """Bone names written to the `.skl`: native minus helpers."""
    native, _custom, helper = classify(obj)
    return native - helper


def nearest_native_ancestor(bone, native_names):
    """The first native ancestor, skipping custom bones in between, or None."""
    cur = bone.parent
    while cur is not None:
        if cur.name in native_names:
            return cur
        cur = cur.parent
    return None


def problems(obj):
    """Registry inconsistencies, one line each. Empty means clean."""
    out = []
    registry = read_registry(obj)
    bones = obj.data.bones

    for h, name in registry.items():
        if name not in bones:
            out.append(
                "joint 0x%08X was imported as bone %r, which no longer exists - it was renamed or "
                "deleted, so that joint has no bone" % (h, name))

    seen = {}
    for bone in bones:
        h = bone_hash(bone)
        if h is None:
            continue
        if is_native(bone, registry):
            if h in seen:
                out.append(
                    "bones %r and %r both claim joint 0x%08X" % (seen[h], bone.name, h))
            seen[h] = bone.name
            if is_helper(bone):
                out.append(
                    "%r is a real .skl joint but is marked as a helper; helpers are for bones "
                    "that must stay out of the file" % bone.name)
    return out
