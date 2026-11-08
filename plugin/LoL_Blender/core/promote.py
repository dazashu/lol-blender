"""Promotion: turn bones the user added into real `.skl` joints.

A promoted bone gets a name hash, joint id, parent id and bind transform from its current rest,
written as custom properties and a registry entry. Nothing moves or is renamed, so it runs
automatically. Ambiguous cases (hash collisions, too many influences, reparenting) are reported,
never fixed silently.
"""

import bpy

from . import armature as arm_util
from . import convert
from . import registry as reg

#: `.skn` influence indices are u8, so at most 256 joints can carry weight.
MAX_INFLUENCES = 256


class PromotionReport:
    def __init__(self):
        self.promoted = []          # [(bone_name, hash, joint_id)]
        self.renamed = []           # [(old_name, new_name, old_hash, new_hash)]
        self.vanished = []          # [(hash, name)] registry entries whose bone is gone
        self.refused = []           # [(bone_name, reason)] guard hits, nothing was written
        self.reparented = []        # [(bone_name, skl_parent, blender_ancestor)]
        self.helpers = []           # names skipped on purpose

    @property
    def changed(self):
        return bool(self.promoted or self.renamed or self.vanished)

    def summary(self):
        lines = []
        if self.promoted:
            lines.append("promoted %d bone%s to real joints: %s" % (
                len(self.promoted), "" if len(self.promoted) == 1 else "s",
                ", ".join("%s (id %d)" % (n, i) for n, _, i in self.promoted[:8])))
        if self.renamed:
            lines.append(
                "%d renamed bone%s re-hashed - any animation keyed on the OLD name no longer "
                "matches: %s" % (
                    len(self.renamed), "" if len(self.renamed) == 1 else "s",
                    ", ".join("%s -> %s" % (a, b) for a, b, _, _ in self.renamed[:8])))
        if self.vanished:
            lines.append("%d joint%s lost their bone and were dropped from the registry: %s" % (
                len(self.vanished), "" if len(self.vanished) == 1 else "s",
                ", ".join(n for _, n in self.vanished[:8])))
        if self.helpers:
            lines.append("%d bone%s left out as helpers: %s" % (
                len(self.helpers), "" if len(self.helpers) == 1 else "s",
                ", ".join(self.helpers[:8])))
        if self.reparented:
            lines.append(
                "%d native bone%s whose Blender parent is not their .skl parent: %s" % (
                    len(self.reparented), "" if len(self.reparented) == 1 else "s",
                    ", ".join("%s (.skl: %s, blender: %s)" % t for t in self.reparented[:5])))
        for name, reason in self.refused:
            lines.append("REFUSED %r: %s" % (name, reason))
        return lines


def _next_joint_id(obj, registry):
    """One past the highest joint id in use, so ids stay dense and stable."""
    used = {-1}
    for bone in obj.data.bones:
        value = bone.get(reg.JOINT_ID)
        if value is not None:
            try:
                used.add(int(value))
            except (TypeError, ValueError):
                pass
    return max(used) + 1


def _elf_hash(name):
    from .. import native
    return int(native.rs().elf_hash(name)) & 0xFFFFFFFF


def _full_name(bone):
    """The full joint name (bone names are capped at 63 characters)."""
    stored = bone.get(reg.NAME)
    if stored:
        return str(stored)
    return bone.name


def detect_renames(obj, registry, report):
    """Find renamed native bones; they are re-hashed from the new name (the hash is the name).

    Reported, since a `.anm` keyed on the old name no longer matches.
    """
    by_hash = {}
    for bone in obj.data.bones:
        h = reg.bone_hash(bone)
        if h is not None:
            by_hash.setdefault(h, []).append(bone)

    changes = {}
    for h, registered_name in list(registry.items()):
        if registered_name in obj.data.bones:
            continue                                    # still there under its own name
        candidates = [b for b in by_hash.get(h, []) if b.name not in registry.values()]
        if len(candidates) == 1:
            bone = candidates[0]
            new_hash = _elf_hash(bone.name)
            report.renamed.append((registered_name, bone.name, h, new_hash))
            changes[h] = (bone, new_hash)
        else:
            report.vanished.append((h, registered_name))
    return changes


def _copied_identity(bone, registry):
    owner = registry.get(reg.bone_hash(bone))
    return owner is not None and owner != bone.name


def _promotion_name(bone, registry):
    return bone.name if _copied_identity(bone, registry) else _full_name(bone)


def promote(obj, dry_run=False):
    """Turn every custom, non-helper bone into a real joint. Idempotent. Returns a report."""
    report = PromotionReport()
    if not reg.is_lol_armature(obj):
        return report

    registry = reg.read_registry(obj)
    order = [int(h) for h in obj.get(reg.JOINT_ORDER, [])]

    # renames first, or a renamed bone would get a second joint
    renames = detect_renames(obj, registry, report)
    # Validate the final identities before touching the registry. ELF hashing
    # is case-insensitive, so a valid Blender rename can collide in League.
    if renames:
        renamed = {bone.name: new_hash for bone, new_hash in renames.values()}
        identities = {}
        for bone in obj.data.bones:
            if reg.is_helper(bone):
                continue
            h = renamed.get(bone.name)
            if h is None:
                h = (reg.bone_hash(bone) if reg.is_native(bone, registry)
                     else _elf_hash(_promotion_name(bone, registry)))
            identities.setdefault(h, []).append(bone.name)
        for h, names in identities.items():
            if len(names) > 1:
                report.refused.append((names[0],
                    "joint names %s share exported hash 0x%08X; rename one before exporting"
                    % (", ".join(repr(n) for n in names), h)))
        if report.refused:
            return report
    if not dry_run:
        for old_hash, (bone, new_hash) in renames.items():
            registry.pop(old_hash, None)
            order = [new_hash if h == old_hash else h for h in order]
            bone[reg.HASH] = new_hash
            bone[reg.NAME] = bone.name
            registry[new_hash] = bone.name
        for old_hash, name in report.vanished:
            registry.pop(old_hash, None)
            order = [h for h in order if h != old_hash]

    native_names = {name for h, name in registry.items()}

    # guard 1: hash collisions
    wanted = {}
    for bone in obj.data.bones:
        if bone.name in native_names or reg.is_helper(bone):
            continue
        h = _elf_hash(_promotion_name(bone, registry))
        wanted.setdefault(h, []).append(bone.name)

    colliding = set()
    for h, names in wanted.items():
        clash = [registry[h]] if h in registry else []
        if len(names) + len(clash) > 1:
            colliding.update(names)
            report.refused.append((
                names[0],
                "its name hashes to 0x%08X, which %s already uses. Two joints with the same hash "
                "cannot be told apart by any .anm - rename one of them"
                % (h, " and ".join(repr(n) for n in (clash + names[1:])))))

    # guard 2: at most 256 weighted joints (unweighted ones do not count)
    candidates = {name for names in wanted.values() for name in names} - colliding
    weighted = _weighted_bone_names(obj)
    projected = len((native_names | candidates) & weighted) if weighted else 0
    if projected > MAX_INFLUENCES:
        report.refused.append((
            sorted(candidates & weighted)[0] if (candidates & weighted) else "",
            "this would leave %d WEIGHTED joints. A .skn stores an influence as a u8 index, so no "
            "more than %d can carry weight" % (projected, MAX_INFLUENCES)))

    refused_names = {name for name, _ in report.refused}

    next_id = _next_joint_id(obj, registry)
    for bone in obj.data.bones:
        if bone.name in native_names:
            continue
        if reg.is_helper(bone):
            report.helpers.append(bone.name)
            continue
        if bone.name in colliding or bone.name in refused_names:
            continue

        joint_hash = _elf_hash(_promotion_name(bone, registry))
        parent = reg.nearest_native_ancestor(bone, native_names)
        parent_id = -1
        parent_hash = 0
        if parent is not None:
            parent_id = int(parent.get(reg.JOINT_ID, -1))
            parent_hash = int(parent.get(reg.HASH, 0))

        if not dry_run:
            # Only a duplicate inherited another joint's axes. Re-promoting
            # an imported joint must preserve its own exact bind metadata.
            if _copied_identity(bone, registry):
                for key in (reg.NAME, reg.REORIENT, reg.LOCAL_TRS, reg.IBIND_TRS):
                    if key in bone:
                        del bone[key]
            _stamp(obj, bone, joint_hash, next_id, parent, parent_id, parent_hash)
            registry[joint_hash] = bone.name
            order.append(joint_hash)
            native_names.add(bone.name)

        report.promoted.append((bone.name, joint_hash, next_id))
        next_id += 1

    # guard 3: report reparenting, never perform it
    for bone in obj.data.bones:
        if bone.name not in native_names:
            continue
        declared = bone.get(reg.PARENT_HASH)
        if declared is None:
            continue
        ancestor = reg.nearest_native_ancestor(bone, native_names)
        ancestor_hash = int(ancestor.get(reg.HASH, 0)) if ancestor is not None else 0
        if int(declared) != ancestor_hash:
            report.reparented.append((
                bone.name,
                registry.get(int(declared), "0x%08X" % int(declared)) if declared else "(root)",
                ancestor.name if ancestor is not None else "(root)"))

    if not dry_run and (report.promoted or renames or report.vanished):
        reg.write_registry(obj, registry)
        obj[reg.JOINT_ORDER] = order
    return report


def _weighted_bone_names(obj):
    """Bone names that actually carry weight on some mesh bound to this armature."""
    names = set()
    for mesh in bpy.data.objects:
        if mesh.type != 'MESH':
            continue
        if not any(m.type == 'ARMATURE' and m.object is obj for m in mesh.modifiers):
            continue
        group_names = {g.name for g in mesh.vertex_groups}
        names |= group_names & set(obj.data.bones.keys())
    return names


def _stamp(obj, bone, joint_hash, joint_id, parent, parent_id, parent_hash):
    """Write the importer's properties from the bone's current rest."""
    from . import legacy

    rest_global = arm_util.exact_rest(bone)
    rest_local = arm_util.exact_rest_local(bone)

    bone[reg.HASH] = joint_hash
    bone[reg.NAME] = _full_name(bone)
    bone[reg.JOINT_ID] = joint_id
    bone[reg.PARENT_ID] = parent_id
    bone[reg.PARENT_HASH] = parent_hash
    if reg.FLAGS not in bone:
        bone[reg.FLAGS] = 0
    if reg.RADIUS not in bone:
        bone[reg.RADIUS] = 2.1                  # lol_maya's default
    bone[reg.PROMOTED] = True
    bone[reg.REST_GLOBAL] = convert.matrix_to_flat(convert.blender_to_riot(rest_global))
    bone[reg.REST_LOCAL] = convert.matrix_to_flat(convert.blender_to_riot(rest_local))

    legacy.stamp_bone(
        bone, obj.pose.bones[bone.name], joint_id, parent.name if parent is not None else "")


def demote(obj, bone):
    """Undo the promotion of a bone (only `riot_promoted` ones). Returns True if changed."""
    if not reg.was_promoted(bone):
        return False

    joint_hash = reg.bone_hash(bone)
    registry = reg.read_registry(obj)
    if joint_hash is not None and registry.pop(joint_hash, None) is not None:
        reg.write_registry(obj, registry)
        obj[reg.JOINT_ORDER] = [h for h in obj.get(reg.JOINT_ORDER, []) if int(h) != joint_hash]

    for key in (reg.HASH, reg.NAME, reg.JOINT_ID, reg.PARENT_ID, reg.PARENT_HASH,
                reg.REST_GLOBAL, reg.REST_LOCAL, reg.FLAGS, reg.RADIUS, reg.PROMOTED):
        if key in bone:
            del bone[key]
    return True


def league_local_of(bone, native_names):
    """League local transform relative to the nearest native ancestor (skips helpers):

        league_local(B) = P^-1 @ (rest(ancestor)^-1 @ rest(B)) @ P
    """
    ancestor = reg.nearest_native_ancestor(bone, native_names)
    if ancestor is None:
        relative = arm_util.exact_rest(bone)
    else:
        relative = arm_util.exact_rest(ancestor).inverted() @ arm_util.exact_rest(bone)
    return convert.blender_to_riot(relative)


def rebase_actions(obj, old_rests, report_lines=None):
    """Re-express every action's keys after rest changes, so the pose does not move.

    `old_rests` is {bone_name: matrix_local before the edit}. Returns the channels rewritten.
    """
    identity = _identity()
    rewritten = 0
    animation_data = obj.animation_data
    if animation_data is None:
        return 0

    actions = {animation_data.action} if animation_data.action else set()
    for track in animation_data.nla_tracks:
        for strip in track.strips:
            if strip.action:
                actions.add(strip.action)

    for action in actions:
        for pose_bone in obj.pose.bones:
            name = pose_bone.name
            old_rest = old_rests.get(name)
            if old_rest is None:
                continue
            new_rest = pose_bone.bone.matrix_local
            if convert.max_element_deviation(old_rest, new_rest) < 1e-9:
                continue

            parent = pose_bone.bone.parent
            old_parent = old_rests.get(parent.name, parent.matrix_local) if parent else identity
            new_parent = parent.matrix_local if parent else identity

            curves = _curves_for(action, name)
            if not curves:
                continue
            rewritten += _rewrite(curves, pose_bone, old_rest, new_rest, old_parent, new_parent)

    if report_lines is not None and rewritten:
        report_lines.append(
            "re-expressed %d animation channels against the new rest, so the pose did not move"
            % rewritten)
    return rewritten


def _identity():
    from mathutils import Matrix
    return Matrix.Identity(4)


def _curves_for(action, bone_name):
    prefix = 'pose.bones["%s"].' % bone_name.replace('"', '\\"')
    return [fc for fc in action.fcurves if fc.data_path.startswith(prefix)]


def _rewrite(curves, pose_bone, old_rest, new_rest, old_parent, new_parent):
    """Rewrite one bone's channels frame by frame. Returns how many curves were touched."""
    from mathutils import Matrix

    frames = sorted({round(kp.co[0], 5) for fc in curves for kp in fc.keyframe_points})
    if not frames:
        return 0

    by_path = {}
    for fc in curves:
        by_path.setdefault(fc.data_path.rsplit(".", 1)[-1], {})[fc.array_index] = fc

    rebased = {}
    for frame in frames:
        basis = _sample_basis(by_path, frame, pose_bone)
        rebased[frame] = arm_util.rebase_pose_matrix(
            old_rest, new_rest, old_parent, new_parent, basis)

    touched = 0
    for channel, curves_by_index in by_path.items():
        for index, fc in curves_by_index.items():
            for kp in fc.keyframe_points:
                frame = round(kp.co[0], 5)
                matrix = rebased.get(frame)
                if matrix is None:
                    continue
                kp.co[1] = _component(matrix, channel, index)
                kp.handle_left[1] = kp.co[1]
                kp.handle_right[1] = kp.co[1]
            fc.update()
            touched += 1
    return touched


def _sample_basis(by_path, frame, pose_bone):
    from mathutils import Matrix, Quaternion, Vector

    def value(channel, index, default):
        fc = by_path.get(channel, {}).get(index)
        return fc.evaluate(frame) if fc is not None else default

    location = Vector((value("location", i, 0.0) for i in range(3)))
    quaternion = Quaternion((value("rotation_quaternion", 0, 1.0),
                             value("rotation_quaternion", 1, 0.0),
                             value("rotation_quaternion", 2, 0.0),
                             value("rotation_quaternion", 3, 0.0)))
    scale = Vector((value("scale", i, 1.0) for i in range(3)))
    if quaternion.magnitude == 0.0:
        quaternion = Quaternion((1.0, 0.0, 0.0, 0.0))
    return (Matrix.Translation(location)
            @ quaternion.normalized().to_matrix().to_4x4()
            @ Matrix.Diagonal(scale).to_4x4())


def _component(matrix, channel, index):
    translation, rotation, scale = matrix.decompose()
    if channel == "location":
        return translation[index]
    if channel == "rotation_quaternion":
        return (rotation.w, rotation.x, rotation.y, rotation.z)[index]
    if channel == "scale":
        return scale[index]
    return 0.0
