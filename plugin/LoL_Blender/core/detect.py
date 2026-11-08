"""Find physics bones by name: hair, cloth and breast chains, and their colliders.

The name tables come from the old standalone physics addon. Detection returns whole chains (one
strand = root to tip). VFX buffbones are always excluded.
"""

#: Hair name substrings. Plain substring match, so avoid short keys ("ear" matched "Rear").
HAIR_KEYS = ("hair", "ponytail", "tail", "braid", "bang", "fringe", "ahoge", "earring")

#: Cloth and everything that hangs off the costume.
CLOTH_KEYS = (
    "cloth", "cape", "coat", "skirt", "dress", "robe", "scarf", "ribbon",
    "chain", "rope", "strap", "belt", "tassel", "feather", "fur",
)

BREAST_KEYS = ("breast", "boob", "bust")

#: The three physics sets `suggest` fills (ids from `ui.physics_props.SETS`). Each strand becomes
#: a branch inside its set.
KINDS = (
    ("HAIR", HAIR_KEYS),
    ("CLOTH", CLOTH_KEYS),
    ("BREAST", BREAST_KEYS),
)

#: Never simulated, whatever else they match: attachment points, roots and the world bone.
NEVER = ("buffbone", "snap", "world", "root", "c_bone", "weapon")

#: BREAST colliders (arms, shoulders, clavicles), same list and order as the old addon. Order
#: matters: each collision overwrites the previous push.
BODY_COLLIDERS = (
    "L_Clavicle", "R_Clavicle",
    "L_Shoulder", "R_Shoulder",
    "Bip001 L Clavicle", "Bip001 R Clavicle",
    "Bip001_L_Clavicle", "Bip001_R_Clavicle",
    "L_UpperArm", "R_UpperArm",
    "Bip001 L UpperArm", "Bip001 R UpperArm",
    "Bip001_L_UpperArm", "Bip001_R_UpperArm",
    "L_LowerArm", "R_LowerArm",
    "L_Forearm", "R_Forearm",
    "L_Arm", "R_Arm",
    "Bip001 L Forearm", "Bip001 R Forearm",
    "Bip001_L_Forearm", "Bip001_R_Forearm",
    "L_Elbow", "R_Elbow",
    "L_Hand", "R_Hand",
    "Bip001 L Hand", "Bip001 R Hand",
    "Bip001_L_Hand", "Bip001_R_Hand",
)

#: HAIR colliders: the upper body.
HAIR_COLLIDERS = (
    "Spine", "Spine1", "Spine2", "Spine3",
    "Bip001 Spine", "Bip001 Spine1", "Bip001 Spine2", "Bip001 Spine3",
    "Bip001_Spine", "Bip001_Spine1", "Bip001_Spine2", "Bip001_Spine3",
    "Neck", "Neck1",
    "Bip001 Neck", "Bip001_Neck",
    "Head",
    "Bip001 Head", "Bip001_Head",
    "L_Clavicle", "R_Clavicle",
    "L_Shoulder", "R_Shoulder",
    "Bip001 L Clavicle", "Bip001 R Clavicle",
    "Bip001_L_Clavicle", "Bip001_R_Clavicle",
    "L_UpperArm", "R_UpperArm",
    "Bip001 L UpperArm", "Bip001 R UpperArm",
    "Bip001_L_UpperArm", "Bip001_R_UpperArm",
    "L_Elbow", "R_Elbow",
    "L_LowerArm", "R_LowerArm",
    "L_Forearm", "R_Forearm",
    "Bip001 L Forearm", "Bip001 R Forearm",
    "Bip001_L_Forearm", "Bip001_R_Forearm",
)

#: CLOTH colliders: the upper body plus the legs. Not the pelvis, which skirts hang from.
CLOTH_COLLIDERS = HAIR_COLLIDERS + (
    "L_Hip", "R_Hip",
    "L_Thigh", "R_Thigh",
    "Bip001 L Thigh", "Bip001 R Thigh",
    "Bip001_L_Thigh", "Bip001_R_Thigh",
    "L_KneeUpper", "R_KneeUpper",
    "L_Knee", "R_Knee",
    "L_Calf", "R_Calf",
    "Bip001 L Calf", "Bip001 R Calf",
    "Bip001_L_Calf", "Bip001_R_Calf",
)

#: Keyword fallback for rigs without Riot's names.
COLLIDER_KEYS = ("upperarm", "upper_arm", "lowerarm", "lower_arm", "forearm",
                 "clavicle", "shoulder")


def excluded(name):
    lowered = name.lower()
    return any(key in lowered for key in NEVER)


def matches(name, keys):
    lowered = name.lower()
    return any(key in lowered for key in keys)


def descendants(pose_bone, out=None):
    """`pose_bone` and everything under it, parents before children."""
    out = [] if out is None else out
    out.append(pose_bone)
    for child in pose_bone.children:
        descendants(child, out)
    return out


def chain_roots(armature, keys):
    """The topmost matching bone of each chain."""
    matched = [pb for pb in armature.pose.bones
               if matches(pb.name, keys) and not excluded(pb.name)]
    names = {pb.name for pb in matched}
    roots = []
    for pose_bone in matched:
        parent = pose_bone.parent
        if parent is None or parent.name not in names:
            roots.append(pose_bone)
    return roots


def chains_of(armature, keys):
    """Bone names per chain matching `keys`, parents first, one list per strand."""
    return [[pb.name for pb in descendants(root) if not excluded(pb.name)]
            for root in chain_roots(armature, keys)]


def hair_chains(armature):
    """Bone names per hair chain, parents first."""
    return chains_of(armature, HAIR_KEYS)


def cloth_chains(armature):
    return chains_of(armature, CLOTH_KEYS)


#: Side markers in bone names, longest first so `_left` is not read as `_l`.
SIDES = (("_left", "l"), ("_right", "r"), ("_l", "l"), ("_r", "r"), (".l", "l"), (".r", "r"))
SIDE_TOKENS = (("left", "l"), ("right", "r"), ("_l_", "l"), ("_r_", "r"))


def side_of(name):
    """`"l"`, `"r"`, or `""` when the name does not say."""
    lowered = name.lower()
    for suffix, side in SIDES:
        if lowered.endswith(suffix):
            return side
    for token, side in SIDE_TOKENS:
        if token in lowered:
            return side
    return ""


def _depth(pose_bone):
    depth = 0
    node = pose_bone.parent
    while node is not None:
        depth += 1
        node = node.parent
    return depth


def breast_chains(armature):
    """One breast bone per side, never a chain (a whole bust chain is unstable).

    Picks the bone whose subtree carries the most skin weight, then the shallowest.
    """
    matched = [pb for pb in armature.pose.bones
               if matches(pb.name, BREAST_KEYS) and not excluded(pb.name)]
    if not matched:
        return []

    # subtree weight, not own weight: own weight picks the tip, which leaves the base rigid
    carried = _subtree_weight(armature)

    sided = {}
    for pose_bone in matched:
        side = side_of(pose_bone.name)
        if not side:
            continue
        best = sided.get(side)
        score = (-carried.get(pose_bone.name, 0.0), _depth(pose_bone), pose_bone.name)
        if best is None or score < best[0]:
            sided[side] = (score, pose_bone)
    if sided:
        return [[entry[1].name] for _side_id, entry in sorted(sided.items())]

    # no side in any name: the one that moves the most
    return [[min(matched,
                 key=lambda pb: (-carried.get(pb.name, 0.0), _depth(pb), pb.name)).name]]


def _subtree_weight(armature):
    """`{bone name: total skin weight of the bone and everything under it}`."""
    import bpy

    own = {}
    for obj in bpy.data.objects:
        if obj.type != 'MESH':
            continue
        if not any(m.type == 'ARMATURE' and m.object is armature for m in obj.modifiers):
            continue
        groups = {group.index: group.name for group in obj.vertex_groups}
        for vertex in obj.data.vertices:
            for group in vertex.groups:
                name = groups.get(group.group)
                if name is not None:
                    own[name] = own.get(name, 0.0) + group.weight

    totals = {}

    def accumulate(bone):
        total = own.get(bone.name, 0.0)
        for child in bone.children:
            total += accumulate(child)
        totals[bone.name] = total
        return total

    for bone in armature.data.bones:
        if bone.parent is None:
            accumulate(bone)
    return totals


def _existing(armature, candidates):
    """The candidates this rig has, matched case-insensitively (Riot mixes casing)."""
    have = {}
    for pose_bone in armature.pose.bones:
        have.setdefault(pose_bone.name.lower(), pose_bone.name)
    out = []
    for name in candidates:
        actual = have.get(name.lower())
        if actual is not None and not excluded(actual) and actual not in out:
            out.append(actual)
    return out


#: Names of the bone that carries the character, best first.
MOTION_REF_NAMES = ("root", "c_root", "root_motion", "rootmotion",
                    "pelvis", "hips", "hip", "c_pelvis", "spine1", "spine")


def motion_reference(armature):
    """The bone Motion watches (one that travels with the character), or None."""
    lowered = {b.name.lower(): b.name for b in armature.pose.bones}
    for wanted in MOTION_REF_NAMES:
        if wanted in lowered:
            return lowered[wanted]
    for wanted in MOTION_REF_NAMES:
        for name in sorted(lowered):
            if wanted in name:
                return lowered[name]
    return None


def body_colliders(armature):
    """Arms and shoulders, by exact name then by keyword."""
    found = _existing(armature, BODY_COLLIDERS)
    seen = set(found)
    for pose_bone in armature.pose.bones:
        name = pose_bone.name
        if name in seen or excluded(name):
            continue
        if matches(name, COLLIDER_KEYS):
            found.append(name)
            seen.add(name)
    return found


def hair_colliders(armature):
    """The upper body: spine, neck, head, shoulders, arms."""
    return _existing(armature, HAIR_COLLIDERS)


def cloth_colliders(armature):
    """The upper body and the legs - see `CLOTH_COLLIDERS`."""
    return _existing(armature, CLOTH_COLLIDERS)


def colliders_for_set(armature, identifier):
    """The default collider list for one set."""
    if identifier == "BREAST":
        return body_colliders(armature)
    if identifier == "CLOTH":
        return cloth_colliders(armature)
    return hair_colliders(armature)


def suggest(armature):
    """`(set id, strands, collider names)` per physics set found on this rig."""
    out = []
    claimed = set()
    for identifier, keys in KINDS:
        strands = []
        for strand in (breast_chains(armature) if identifier == "BREAST"
                       else chains_of(armature, keys)):
            # a bone belongs to the first set that claims it
            kept = [name for name in strand if name not in claimed]
            claimed.update(kept)
            if kept:
                strands.append(kept)
        if not strands:
            continue
        colliders = colliders_for_set(armature, identifier)
        own = {name for strand in strands for name in strand}
        out.append((identifier, strands, [name for name in colliders if name not in own]))
    return out
