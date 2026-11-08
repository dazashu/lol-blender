"""`.skl` export.

Joint order matters (influences are indices): the order stored at import first, then new joints
parents-first. Unedited joints are written from the raw values stamped at import, so an untouched
rig exports byte-identical; moved joints are rebuilt from their current rest.
"""

import os

from mathutils import Matrix

from . import armature as arm_util
from . import convert
from . import donor as donor_mod
from . import promote
from . import registry as reg

#: A rest that moved less than this, relative to its distance from the origin, counts as unchanged.
#: Blender's own float32 storage of a bone already moves it by about 5e-05.
UNCHANGED_TOLERANCE = 1e-3


class ExportReport:
    def __init__(self):
        self.path = ""
        self.joint_count = 0
        self.influence_count = 0
        self.exact_joints = 0          # written from the file's own numbers
        self.recomposed_joints = []    # edited, so rebuilt from the current rest
        self.scale_lost = []           # recomposed AND their bind carried scale, which is gone
        self.promoted = []             # bones turned into joints on the way out
        self.helpers = []              # left out on purpose
        self.donor = None
        self.donor_used = False
        self.bytes_written = 0
        self.warnings = []

    def summary(self):
        lines = ["wrote %d joints and %d influences (%d bytes)" % (
            self.joint_count, self.influence_count, self.bytes_written)]
        note = donor_mod.describe(self.donor, used=self.donor_used)
        if note:
            lines.append(note)
        if self.scale_lost:
            lines.append(
                "%d joints had a bind SCALE that could not survive being rebuilt from a Blender "
                "rest bone, so it was written as 1.0: %s"
                % (len(self.scale_lost), ", ".join(self.scale_lost[:6])))
        if self.recomposed_joints:
            lines.append(
                "%d joints were edited and had to be recomposed from their current rest, so this "
                "file is not byte-identical to the original: %s"
                % (len(self.recomposed_joints), ", ".join(self.recomposed_joints[:8])))
        else:
            lines.append("every joint written from the file's own numbers")
        if self.promoted:
            lines.append("promoted on the way out: %s" % ", ".join(self.promoted[:8]))
        if self.helpers:
            lines.append("left out as helpers: %s" % ", ".join(self.helpers[:8]))
        lines.extend(self.warnings)
        return lines


def _trs_from_matrix(matrix):
    """(translation xyz, rotation xyzw, scale xyz) from a League-space matrix."""
    translation, rotation, scale = matrix.decompose()
    return (
        [translation.x, translation.y, translation.z],
        [rotation.x, rotation.y, rotation.z, rotation.w],
        [scale.x, scale.y, scale.z],
    )


def _split_trs(stored):
    """The stamped 10 floats back into the three lists."""
    values = [float(v) for v in stored]
    return values[0:3], values[3:7], values[7:10]


def carries_bind_scale(bone, tolerance=1e-3):
    """True when the joint's STORED bind has scale a Blender rest bone cannot hold."""
    stored = bone.get(reg.REST_GLOBAL)
    if stored is None or len(stored) != 16:
        return False
    scale = convert.riot_to_blender(
        convert.flat_to_matrix([float(v) for v in stored])).to_scale()
    return max(abs(v - 1.0) for v in scale) > tolerance


def _is_unchanged(bone):
    """True when the bone's rest still matches what was stamped at import.

    Compared orthonormalised on both sides, since rest bones cannot hold the bind scale, and with
    `exact_rest` so the importer's own reorientation does not count as an edit.
    """
    stored = bone.get(reg.REST_GLOBAL)
    if stored is None or len(stored) != 16:
        return False
    want = convert.orthonormalized(
        convert.riot_to_blender(convert.flat_to_matrix([float(v) for v in stored])))
    deviation = convert.max_element_deviation(want, arm_util.exact_rest(bone))
    radius = max(1.0, max(abs(v) for v in want.to_translation()))
    return deviation / radius <= UNCHANGED_TOLERANCE


def joint_order(armature, exportable):
    """The joint names in write order: stored order first, then the rest parents-first."""
    stored = [int(h) for h in armature.get(reg.JOINT_ORDER, [])]
    registry = reg.read_registry(armature)

    ordered = []
    seen = set()
    for joint_hash in stored:
        name = registry.get(joint_hash)
        if name in exportable and name not in seen:
            ordered.append(name)
            seen.add(name)

    remaining = [n for n in exportable if n not in seen]
    bones = armature.data.bones

    def depth(name):
        count, bone = 0, bones[name].parent
        while bone is not None:
            count += 1
            bone = bone.parent
        return count

    ordered.extend(sorted(remaining, key=lambda n: (depth(n), n)))
    return ordered


class _DonorJoint:
    """A joint of riot.skl the rig does not have. lol_maya writes it as an empty joint (zero
    transform, no parent) so the joints after it keep the riot index."""

    def __init__(self, joint):
        self.name = joint.name
        self.radius = float(joint.radius)
        self.hash = int(joint.hash)


def donor_order(donor, ordered, bones):
    """lol_maya's `SKL.dump(riot)`: the riot joints in riot order, matched by name ignoring case,
    an empty joint for each one the rig lacks, then the rig's extra joints.

    Returns (entries, radius): an entry is a bone name or a _DonorJoint; radius is the riot radius
    of each matched bone (a Maya joint has none, so lol_maya takes the riot one).
    """
    remaining = list(ordered)
    entries, radius, missing = [], {}, []
    for joint in donor.joints:
        key = joint.name.lower()
        match = next((n for n in remaining
                      if str(bones[n].get(reg.NAME, n)).lower() == key), None)
        if match is None:
            entries.append(_DonorJoint(joint))
            missing.append(joint.name)
            continue
        remaining.remove(match)
        entries.append(match)
        radius[match] = float(joint.radius)
    entries.extend(remaining)
    return entries, radius, missing


def influence_list(armature, ordered, index_of, report, influence_names=None):
    """The influence table: the joints a `.skn` indexes, in order.

    `influence_names` is the table a `.skn` just wrote and always wins, so mesh and skeleton agree.
    Otherwise the imported table is kept, since reordering it rebinds vertices.
    """
    registry = reg.read_registry(armature)

    if influence_names is not None:
        influences = []
        for name in influence_names:
            if name in index_of:
                influences.append(index_of[name])
            else:
                influences.append(0)
                report.warnings.append(
                    "%s is an influence of the mesh but not a joint in the skeleton, so that "
                    "influence was pinned to the root" % name)
        if len(influences) > 256:
            report.warnings.append(
                "%d influences, and a .skn addresses them with a u8 - only the first 256 are "
                "reachable" % len(influences))
        return influences

    stored = [int(i) for i in armature.get(reg.INFLUENCES, [])]
    stored_hashes = [int(h) for h in armature.get(reg.JOINT_ORDER, [])]

    influences = []
    if stored and stored_hashes:
        for original_index in stored:
            if 0 <= original_index < len(stored_hashes):
                name = registry.get(stored_hashes[original_index])
                if name in index_of:
                    influences.append(index_of[name])
                    continue
            influences.append(0)
            report.warnings.append(
                "an influence pointed at a joint that no longer exists and was pinned to the root")

    if not influences:
        influences = [index_of[name] for name in ordered]
    if len(influences) > 256:
        report.warnings.append(
            "%d influences, and a .skn addresses them with a u8 - only the first 256 are reachable"
            % len(influences))
    return influences


def build(armature, target_path, report=None, influence_names=None):
    """Collect everything a `.skl` needs. Returns (kwargs, report)."""
    report = report or ExportReport()

    # promote first, or a bone added without leaving Edit Mode would be dropped
    promotion = promote.promote(armature)
    if promotion.refused:
        raise ValueError("Cannot export skeleton identities: " + "; ".join(
            "%s: %s" % item for item in promotion.refused))
    report.promoted = [name for name, _, _ in promotion.promoted]
    report.helpers = list(promotion.helpers)
    for name, reason in promotion.refused:
        report.warnings.append("%s: %s" % (name, reason))

    exportable = reg.exportable_names(armature)
    ordered = joint_order(armature, exportable)
    bones = armature.data.bones

    # riot_<name>.skl or riot.skl beside the target: its joint order and name fields win, as in
    # lol_maya (keeps animation layers blending on the right joints)
    report.donor = donor_mod.find(target_path, ".skl")
    entries, donor_radius, donor = list(ordered), {}, None
    if report.donor:
        from .. import native
        try:
            donor = native.rs().Skeleton.from_path(report.donor)
        except Exception as error:                      # noqa: BLE001 - reported, not fatal
            report.warnings.append("%s could not be read, ignored: %s"
                                   % (os.path.basename(report.donor), error))
            donor = None
    if donor is not None:
        entries, donor_radius, missing = donor_order(donor, ordered, bones)
        report.donor_used = True
        if missing:
            report.warnings.append(
                "%d joint%s of %s missing from the rig, written empty to keep the order: %s"
                % (len(missing), "" if len(missing) == 1 else "s",
                   os.path.basename(report.donor), ", ".join(missing[:8])))
        extra = len(entries) - len(donor.joints)
        if extra:
            report.warnings.append("%d joint%s not in %s, written after its joints" % (
                extra, "" if extra == 1 else "s", os.path.basename(report.donor)))
        ordered = [e for e in entries if isinstance(e, str)]
    index_of = {e: i for i, e in enumerate(entries) if isinstance(e, str)}

    joints = []
    for index, name in enumerate(entries):
        if isinstance(name, _DonorJoint):
            joints.append((name.name, 0, index, -1, name.radius, name.hash,
                           ([0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0], [0.0, 0.0, 0.0]),
                           ([0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0], [0.0, 0.0, 0.0])))
            continue
        bone = bones[name]
        parent = reg.nearest_native_ancestor(bone, exportable)
        parent_id = index_of.get(parent.name, -1) if parent is not None else -1

        if _is_unchanged(bone) and reg.LOCAL_TRS in bone and reg.IBIND_TRS in bone:
            local = _split_trs(bone[reg.LOCAL_TRS])
            ibind = _split_trs(bone[reg.IBIND_TRS])
            report.exact_joints += 1
        else:
            # rebuilt from the live rig, skipping helper bones in the chain
            local_matrix = promote.league_local_of(bone, exportable)
            bind_matrix = convert.blender_to_riot(arm_util.exact_rest(bone))
            local = _trs_from_matrix(local_matrix)
            ibind = _trs_from_matrix(bind_matrix.inverted())
            if reg.LOCAL_TRS in bone:
                report.recomposed_joints.append(name)
            if carries_bind_scale(bone):
                # the bind scale cannot survive a rebuild from a rest bone
                report.scale_lost.append(name)

        joints.append((
            str(bone.get(reg.NAME, name)),
            int(bone.get(reg.FLAGS, 0)),
            index,
            parent_id,
            donor_radius.get(name, float(bone.get(reg.RADIUS, 2.1))),
            int(reg.bone_hash(bone) or 0),
            local,
            ibind,
        ))

    report.joint_count = len(joints)
    influences = influence_list(armature, ordered, index_of, report, influence_names)
    report.influence_count = len(influences)

    return {
        "joints": joints,
        "influences": influences,
        "name": donor.name if donor is not None else str(armature.get(reg.SKELETON_NAME, "")),
        "asset": donor.asset if donor is not None else str(armature.get(reg.SKELETON_ASSET, "")),
        "flags": int(armature.get(reg.SKELETON_FLAGS, 0)),
    }, report


def export_skl(armature, filepath, report=None, influence_names=None):
    """Write the armature as a `.skl`. Returns an ExportReport.

    Pass `influence_names` when a `.skn` was just written from the same rig.
    """
    from .. import native

    kwargs, report = build(armature, filepath, report, influence_names)
    report.path = filepath
    report.bytes_written = native.rs().write_skeleton(filepath, **kwargs)
    return report
