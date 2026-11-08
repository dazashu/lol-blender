"""Import a `.skl` onto the skeleton already in the scene, as lol_maya's SKL import does.

lol_maya (`SKL.load`) finds every joint already in the scene by name, sets its transform to the
file's, creates the joints that are missing and links them to their file parent. Nothing is
replaced: the skin keeps its weights, joints the file does not name are left as they are, and the
skeleton is back at a fresh bind pose. Used to repair a broken pose or start again from the file.

Here the same: matched bones get the file's bind as their rest (with the bone's own reorient), the
pose is cleared, missing joints are added, and the `riot_*` stamps take the file's values, so an
export writes the file back. Bound meshes keep the shape they show, as Maya's skin cluster does
when its joints move (core/rest_pose.py).
"""

from . import armature as arm_util
from . import convert
from . import registry as reg
from . import rest_pose
from . import skl_in


class UpdateReport:
    def __init__(self):
        self.matched = []
        self.created = []
        self.reparented = []
        self.untouched = []           # bones the file does not name, left following their parent
        self.import_report = skl_in.ImportReport()
        self.result = None

    def summary(self):
        lines = ["updated %d joint%s from the file" % (len(self.matched),
                                                       "" if len(self.matched) == 1 else "s")]
        if self.created:
            lines.append("added %d missing joint%s: %s" % (
                len(self.created), "" if len(self.created) == 1 else "s",
                ", ".join(self.created[:8])))
        if self.reparented:
            lines.append("re-linked to their file parent: %s" % ", ".join(self.reparented[:8]))
        if self.untouched:
            lines.append("%d bone%s not in the file, kept on their parent: %s" % (
                len(self.untouched), "" if len(self.untouched) == 1 else "s",
                ", ".join(self.untouched[:8])))
        if self.result is not None:
            if self.result.meshes:
                lines.append("%d bound mesh%s kept as shown" % (
                    len(self.result.meshes), "" if len(self.result.meshes) == 1 else "es"))
            if self.result.reloaded:
                lines.append("%d action%s reloaded" % (
                    self.result.reloaded, "" if self.result.reloaded == 1 else "s"))
            if self.result.orphaned:
                lines.append("actions with no source .anm, now meaning something else: %s"
                             % ", ".join(self.result.orphaned[:4]))
        lines.extend(self.import_report.summary()[1:])
        return lines


def _joint_bones(armature, skeleton):
    """{joint index: bone name} for the joints the rig already has.

    By registry hash first (survives a rename), then by exact name, as lol_maya matches.
    """
    registry = reg.read_registry(armature)
    bones = armature.data.bones
    out = {}
    taken = set()
    for index, joint in enumerate(skeleton.joints):
        name = registry.get(int(joint.hash))
        if name is not None and name in bones and name not in taken:
            out[index] = name
            taken.add(name)
    for index, joint in enumerate(skeleton.joints):
        if index in out:
            continue
        name = joint.name[:reg.MAX_BONE_NAME]
        if name in bones and name not in taken:
            out[index] = name
            taken.add(name)
    return out


def score(armature, skeleton):
    """How many of the file's joints this armature has."""
    if armature is None or armature.type != 'ARMATURE':
        return 0
    return len(_joint_bones(armature, skeleton))


def find_target(context, skeleton):
    """The armature a `.skl` updates: the active or selected one if it shares joints with the
    file, else the one sharing the most. None when no armature shares any."""
    view_layer = context.view_layer
    active = view_layer.objects.active
    if score(active, skeleton):
        return active
    pool = [o for o in view_layer.objects if o.type == 'ARMATURE']
    selected = [o for o in pool if o.select_get()]
    for group in (selected, pool):
        ranked = sorted(((score(o, skeleton), o.name, o) for o in group), reverse=True)
        if ranked and ranked[0][0] > 0:
            return ranked[0][2]
    return None


def update_skeleton(context, armature, skeleton, source_path="", keep_meshes=True,
                    keep_animation=True):
    """Put `skeleton` onto `armature`. Returns an UpdateReport."""
    report = UpdateReport()
    rest = skl_in._blender_rest_matrices(skeleton, report.import_report)
    joints = list(skeleton.joints)
    matched = _joint_bones(armature, skeleton)
    joint_of = {name: index for index, name in matched.items()}
    report.matched = [matched[i] for i in sorted(matched)]
    bones = armature.data.bones
    pose = armature.pose.bones

    # where every existing bone goes. File joints: the file's bind, keeping the bone's own turn
    # towards its child. Other bones: Maya keeps their transform under their parent.
    wanted = {}

    def place(bone):
        if bone.name in wanted:
            return wanted[bone.name]
        index = joint_of.get(bone.name)
        if index is not None:
            matrix = rest[index][0] @ arm_util.reorient_of(bone)
        elif bone.parent is None:
            matrix = pose[bone.name].matrix.copy()
        else:
            local = pose[bone.parent.name].matrix.inverted() @ pose[bone.name].matrix
            matrix = place(bone.parent) @ local
        wanted[bone.name] = matrix
        return matrix

    for bone in bones:
        place(bone)
        if bone.name not in joint_of and not reg.is_helper(bone):
            report.untouched.append(bone.name)

    missing = [i for i in range(len(joints)) if i not in matched]
    names = dict(matched)

    def edit(edit_bones):
        # missing joints first, parents after: a parent may come later in the file
        for index in missing:
            wanted_name = joints[index].name[:reg.MAX_BONE_NAME]
            bone = arm_util.add_bone(edit_bones, wanted_name, rest[index][0])
            names[index] = bone.name
            report.created.append(bone.name)
        for index, joint in enumerate(joints):
            edit_bone = edit_bones.get(names[index])
            if edit_bone is None:
                continue
            parent = edit_bones.get(names[joint.parent_id]) if joint.parent_id >= 0 else None
            if edit_bone.parent != parent:
                if index not in missing:
                    report.reparented.append(edit_bone.name)
                edit_bone.parent = parent
        for index in missing:
            edit_bone = edit_bones[names[index]]
            heads = [edit_bones[names[i]].head for i, j in enumerate(joints)
                     if j.parent_id == index and names.get(i) in edit_bones]
            edit_bone.length = arm_util.suggest_length(edit_bone.head, heads)

    def stamp(changed):
        _stamp(armature, skeleton, names, source_path, set(missing), changed, joint_of)

    report.result = rest_pose.pose_to_rest(
        context, armature, wanted=wanted, keep_meshes=keep_meshes,
        keep_animation=keep_animation, stamp=stamp, edit=edit)
    return report


def _stamp(armature, skeleton, names, source_path, created, changed, joint_of):
    """The file's values on every joint it names, as skl_in stamps a fresh import."""
    joints = list(skeleton.joints)
    bones = armature.data.bones
    hash_by_index = [int(j.hash) for j in joints]
    file_bones = {names[i] for i in range(len(joints))}

    for index, joint in enumerate(joints):
        bone = bones[names[index]]
        bone[reg.HASH] = joint.hash
        bone[reg.NAME] = joint.name
        bone[reg.JOINT_ID] = joint.id
        bone[reg.PARENT_ID] = joint.parent_id
        bone[reg.PARENT_HASH] = hash_by_index[joint.parent_id] if joint.parent_id >= 0 else 0
        bone[reg.FLAGS] = joint.flags
        bone[reg.RADIUS] = joint.radius
        bone[reg.REST_LOCAL] = list(joint.local_matrix)
        bone[reg.REST_GLOBAL] = list(joint.bind_global)
        bone[reg.LOCAL_TRS] = (
            list(joint.local_translation) + list(joint.local_rotation) + list(joint.local_scale))
        bone[reg.IBIND_TRS] = (
            list(joint.inverse_bind_translation) + list(joint.inverse_bind_rotation)
            + list(joint.inverse_bind_scale))
        for key in (reg.PROMOTED, reg.HELPER):
            if key in bone:
                del bone[key]
        if index in created and reg.REORIENT in bone:
            del bone[reg.REORIENT]

    # bones the file does not name: a native or promoted one keeps its entry, re-stamped if its
    # rest moved with its parent
    old = reg.read_registry(armature)
    entries = {int(j.hash): names[i] for i, j in enumerate(joints)}
    for joint_hash, name in old.items():
        if name in bones and name not in file_bones and joint_hash not in entries:
            entries[joint_hash] = name
    for name in changed:
        bone = bones[name]
        if name not in file_bones and reg.REST_GLOBAL in bone:
            rest_pose.restamp(bone)

    reg.write_registry(armature, entries)
    armature[reg.JOINT_ORDER] = hash_by_index
    armature[reg.INFLUENCES] = [int(i) for i in skeleton.influences]
    armature[reg.SKELETON_NAME] = skeleton.name
    armature[reg.SKELETON_ASSET] = skeleton.asset
    armature[reg.SKELETON_FLAGS] = skeleton.flags
    if source_path:
        armature[reg.SOURCE_PATH] = source_path
        from . import legacy
        legacy.stamp_skeleton_source(armature, source_path)


def update_from_path(context, armature, filepath, **options):
    from .. import native
    skeleton = native.rs().Skeleton.from_path(filepath)
    return update_skeleton(context, armature, skeleton, source_path=filepath, **options)
