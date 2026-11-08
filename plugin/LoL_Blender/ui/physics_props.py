"""Physics data: three sets (Hair, Cloth, Breast), each with branches, bones and settings.

Stored on the armature object, so each rig has its own setup. All enabled sets simulate whatever
tab is shown. Every value has an `update` that pushes it to the running solver.
"""

import bpy
from bpy.props import (
    BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty,
    StringProperty)

#: The three sets, as `(id, attribute, label)`.
SETS = (
    ('HAIR', "hair", "Hair"),
    ('CLOTH', "cloth", "Cloth"),
    ('BREAST', "breast", "Breast"),
)

#: The fourth button, not a set: the whole rig runs on the custom values while it is active.
CUSTOM = 'CUSTOM'

#: Starting values per set, from the old standalone addon. Breasts are limited to 45 degrees (as
#: that addon always did); hair and cloth have no limit.
DEFAULTS = {
    'HAIR': dict(intensity=8, smoothness=12, gravity=1.0, limit_movement=False, max_angle=20.0),
    'CLOTH': dict(intensity=8, smoothness=12, gravity=1.0, limit_movement=False, max_angle=20.0),
    'BREAST': dict(intensity=10, smoothness=12, gravity=1.0, limit_movement=True, max_angle=45.0),
}

#: Suggested branch names.
BRANCH_LABELS = {
    2: ("Left", "Right"),
    3: ("Back", "Left", "Right"),
    4: ("Back", "Left", "Right", "Front"),
    5: ("Back", "Left", "Right", "Front", "Top"),
    6: ("Back", "Left", "Right", "Front", "Top", "Extra"),
}


def branch_label(index, count):
    labels = BRANCH_LABELS.get(count)
    if labels and index < len(labels):
        return "Branch %d: %s" % (index + 1, labels[index])
    return "Branch %d" % (index + 1)


def identifier_of(physics_set):
    """Which set this is, from its RNA path (`lol_physics.breast`)."""
    try:
        path = physics_set.path_from_id()
    except Exception:                               # noqa: BLE001 - not attached to an ID yet
        return SETS[0][0]
    for identifier, attribute, _label in SETS:
        if path.endswith("." + attribute):
            return identifier
    return SETS[0][0]


def _armature_of(prop):
    owner = prop.id_data
    return owner if getattr(owner, "type", None) == 'ARMATURE' else None


def sets_of(armature):
    """`[(id, set)]` for this rig, or an empty list when it has no physics data."""
    props = getattr(armature, "lol_physics", None)
    if props is None:
        return []
    return [(identifier, getattr(props, attribute)) for identifier, attribute, _label in SETS]


def edited_set_id(props):
    """Which of the three sets the panel is editing. Never `CUSTOM`."""
    active = props.active
    if active == CUSTOM:
        candidate = props.edited
        for identifier, _attribute, _label in SETS:
            if candidate == identifier:
                return identifier
        return SETS[0][0]
    return active


def get_set(armature, identifier=None):
    """The set being edited, or the one named."""
    props = getattr(armature, "lol_physics", None)
    if props is None:
        return None
    # `edited`, not `active`: Custom is a button, not a set
    identifier = identifier or edited_set_id(props)
    for candidate, attribute, _label in SETS:
        if candidate == identifier:
            return getattr(props, attribute)
    return None


def listed_bones(armature):
    """Every bone named by an ENABLED set on this rig."""
    return {item.name
            for _identifier, physics_set in sets_of(armature) if physics_set.enabled
            for item in physics_set.bones if item.name}


def bones_of(physics_set, branch=None):
    if branch is None:
        return [item.name for item in physics_set.bones if item.name]
    return [item.name for item in physics_set.bones if item.name and item.branch == branch]


def apply_defaults(physics_set, identifier):
    for name, value in DEFAULTS.get(identifier, {}).items():
        setattr(physics_set, name, value)


def _retune(self, _context):
    """A slider moved: push it to the running solver without restarting it."""
    armature = _armature_of(self)
    try:
        from ..ops import physics
        physics.settings_changed(armature)
    except Exception as error:                  # noqa: BLE001 - an update callback must not raise
        print("[LoL Blender] live update failed: %r" % (error,))


def _retune_object(self, _context):
    """A custom value moved: push it to the running solver."""
    armature = self.id_data
    if getattr(armature, "type", None) != 'ARMATURE':
        return
    try:
        from ..ops import physics
        physics.settings_changed(armature)
    except Exception as error:                  # noqa: BLE001
        print("[LoL Blender] live update failed: %r" % (error,))


def _rebuild(self, _context):
    """A bone list changed: the solver has to be rebuilt, not just re-tuned."""
    armature = _armature_of(self)
    try:
        from ..ops import physics
        physics.topology_changed(armature)
    except Exception as error:                  # noqa: BLE001
        print("[LoL Blender] live rebuild failed: %r" % (error,))


def _live_changed(self, _context):
    from ..ops import physics
    if self.live:
        physics.forget(self.id_data)
    else:
        physics.release_preview(self.id_data)


def _active_changed(self, context):
    """Remember the last real set shown, so the panels keep drawing it while Custom is active."""
    if self.active != CUSTOM:
        self.edited = self.active
    armature = self.id_data
    if getattr(armature, "type", None) != 'ARMATURE':
        return
    try:
        from ..ops import physics
        physics.settings_changed(armature)
    except Exception as error:                  # noqa: BLE001 - an update callback must not raise
        print("[LoL Blender] live update failed: %r" % (error,))


def _branches_changed(self, context):
    """Fewer branches: move the orphaned bones onto the last branch instead of losing them."""
    highest = max(0, self.num_branches - 1)
    for item in self.bones:
        if item.branch > highest:
            item.branch = highest
    _rebuild(self, context)


class LOLBLENDER_BoneItem(bpy.types.PropertyGroup):
    """One bone name and its branch. Changing the name rebuilds the solver."""

    name: StringProperty(name="Bone", update=_rebuild)
    branch: IntProperty(default=0, min=0, max=7)


def _colliders_get(self):
    return ", ".join(item.name for item in self.colliders if item.name)


def _colliders_set(self, value):
    """Set the colliders from a comma-separated list."""
    names = []
    for chunk in value.replace(";", ",").replace("\n", ",").split(","):
        name = chunk.strip()
        if name and name not in names:
            names.append(name)
    self.colliders.clear()
    for name in names:
        self.colliders.add().name = name
    _rebuild(self, bpy.context)


class LOLBLENDER_PhysicsSet(bpy.types.PropertyGroup):
    """One physics set: Hair, Cloth or Breast. Settings are shared by all its bones.

    A template, never registered: `_set_class` builds one class per set with its own defaults.
    """

    enabled: BoolProperty(
        name="Enabled", default=True,
        description="Simulate this set. Off leaves its bones on the animated pose",
        update=_rebuild)

    num_branches: IntProperty(
        name="Branches", default=1, min=1, max=8,
        description=(
            "How many separate chains this set has. 1 for a ponytail, 2 for twin tails, more for "
            "a back tail plus side tails. Branches only organise the bone list - what links two "
            "bones in the simulation is that one is the other's parent"),
        update=_branches_changed)

    bones: CollectionProperty(type=LOLBLENDER_BoneItem)

    intensity: IntProperty(
        name="Intensity", default=8, min=1, max=20,
        description="1 = barely moves, 10 = natural, 20 = exaggerated bounce",
        update=_retune)
    smoothness: IntProperty(
        name="Smoothness", default=12, min=1, max=20,
        description="1 = bouncy and springy, 20 = slow and calm (heavy damping)",
        update=_retune)
    gravity: FloatProperty(
        name="Gravity", default=1.0, min=0.0, max=1.0, step=5, precision=2,
        description="1.0 = normal, lower = floaty and slow-motion",
        update=_retune)

    motion: FloatProperty(
        name="Motion", default=1.0, min=0.0, max=1.0, step=5, precision=2,
        description=(
            "Bake only. Tames the swing on fast, shaky frames by measuring how violently the "
            "reference bone changes direction. 1.0 = off"))
    motion_bone: StringProperty(
        name="Ref Bone", default="",
        description=(
            "The bone Motion watches to tell running from jumping. Empty means Motion does "
            "nothing - you pick the bone yourself"))

    limit_movement: BoolProperty(
        name="Limit movement", default=False,
        description=(
            "Cap how far a bone may turn away from the pose the animation put it in. On for "
            "breasts, which hang off the chest and must not swing through it"),
        update=_retune)
    max_angle: FloatProperty(
        name="Max angle", default=20.0, min=1.0, max=180.0, step=100, precision=1,
        description="Degrees a bone may turn away from the animated pose",
        update=_retune)

    collision_enabled: BoolProperty(
        name="Body Collision", default=False,
        description=(
            "Put invisible spheres on the listed body bones so these bones bounce off them "
            "instead of passing through"),
        update=_rebuild)
    colliders: CollectionProperty(type=LOLBLENDER_BoneItem)
    collider_text: StringProperty(
        name="Collision bones",
        description=(
            "The body bones this set bumps into, comma-separated. Type them, paste them, or press "
            "Auto-Detect and delete the ones you do not want"),
        get=_colliders_get, set=_colliders_set)
    # collider size is per body shape (scaled in the viewport)

    show_collision: BoolProperty(default=False)
    show_motion: BoolProperty(default=False)



def _set_class(suffix, identifier):
    """One PropertyGroup per set, with that set's `DEFAULTS` as its RNA defaults.

    So an untouched set shows its own defaults, not hair's.
    """
    starting = DEFAULTS.get(identifier, {})
    annotations = {}
    for name, prop in LOLBLENDER_PhysicsSet.__annotations__.items():
        if name in starting:
            prop = prop.function(**dict(prop.keywords, default=starting[name]))
        annotations[name] = prop
    return type("LOLBLENDER_PhysicsSet" + suffix, (bpy.types.PropertyGroup,),
                {"__annotations__": annotations,
                 "__doc__": LOLBLENDER_PhysicsSet.__doc__})


LOLBLENDER_PhysicsSetHair = _set_class("Hair", 'HAIR')
LOLBLENDER_PhysicsSetCloth = _set_class("Cloth", 'CLOTH')
LOLBLENDER_PhysicsSetBreast = _set_class("Breast", 'BREAST')


def _shape_mirror_changed(self, context):
    from ..core import shape_mirror
    shape_mirror.settings_changed(self, context)


class LOLBLENDER_ObjectPhysics(bpy.types.PropertyGroup):
    shape_mirror_enabled: BoolProperty(
        name="Mirror body shapes", default=False,
        description="Mirror edits to the opposite bone's shape. Turning off keeps both shapes as placed",
        update=_shape_mirror_changed)
    shape_mirror_axis: EnumProperty(
        name="Mirror axis", items=tuple((axis, axis, "Rig local " + axis) for axis in ('X','Y','Z')),
        default='X', update=_shape_mirror_changed)
    shape_mirror_status: StringProperty(default="", options={'SKIP_SAVE'})
    active: EnumProperty(
        name="Set",
        description="Which set you are editing. Every set keeps running whichever one is shown",
        items=(
            ('HAIR', "Hair", "Hair, braids, ponytails, earrings"),
            ('CLOTH', "Cloth", "Capes, skirts, coats, scarves, straps"),
            ('BREAST', "Breast", "Breast bones, with a tighter movement limit"),
            (CUSTOM, "Custom",
             "Run the physics on the four raw Wiggle 2 values shown below instead of on the "
             "presets. Pressing Hair, Cloth or Breast goes back to the presets"),
        ),
        default='HAIR',
        update=_active_changed)

    #: The last real set shown, drawn while Custom is active.
    edited: StringProperty(default='HAIR')

    # Raw Wiggle 2 values for the whole rig, used only while Custom is active. Defaults are the
    # ponytail curve at 8 / 12. No mass: with equal masses it has no effect.

    custom_stiff: FloatProperty(
        name="Stiffness", default=171.2533, min=0.0, max=2000.0, step=100, precision=1,
        description=(
            "How hard a bone is pulled back onto the pose the animation gives it. High is rigid, "
            "low lets it lag and swing. Above roughly 450 at 30 fps the spring rings instead of "
            "settling, which reads as a buzz rather than as movement"),
        update=_retune_object)
    custom_damp: FloatProperty(
        name="Damping", default=2.1423, min=0.0, max=50.0, step=10, precision=3,
        description="How fast the motion dies away. High is calm and heavy, low keeps bouncing",
        update=_retune_object)
    custom_gravity: FloatProperty(
        name="Gravity", default=0.16053, min=0.0, max=20.0, step=1, precision=3,
        description=(
            "How much of the scene gravity a bone feels. This is the raw Wiggle value, not the 0 "
            "to 1 multiplier the presets use"),
        update=_retune_object)
    custom_stretch: FloatProperty(
        name="Stretch", default=0.01474, min=0.0, max=1.0, step=1, precision=4,
        description="How much a bone may lengthen under a pull. 0 keeps it rigid",
        update=_retune_object)

    live: BoolProperty(
        name="Live preview", default=True,
        description=(
            "Step the simulation on every frame change, so playing the clip on a loop shows the "
            "physics as you tune it. Off leaves the bones on the animated pose"),
        update=_live_changed)

    loop_mode: EnumProperty(
        name="Loop",
        description=(
            "Whether the bake should close the clip back onto its first frame. Automatic reads it "
            "off the body bones the way the old addon did, and gets it right on a run or a channel "
            "but not on every idle - so it can be overridden"),
        items=(
            ('AUTO', "Automatic", "Decide from the animation, comparing the first frame to the last"),
            ('LOOP', "Loop", "Close the clip onto its first frame, whatever the animation looks like"),
            ('ONCE', "Plays once", "Start from rest and ease in. For attacks, spells and deaths"),
        ),
        default='AUTO')

    export_folder: StringProperty(
        name="Output folder", default="", subtype='DIR_PATH',
        description="Where Apply to all animations writes its .anm files. Empty = beside the "
                    "clips it read")

    status: StringProperty(default="Ready")

    #: The action copied aside by the last bake, so Clear Baked Keys restores the original keys.
    backup_action: StringProperty(default="")

    hair: PointerProperty(type=LOLBLENDER_PhysicsSetHair)
    cloth: PointerProperty(type=LOLBLENDER_PhysicsSetCloth)
    breast: PointerProperty(type=LOLBLENDER_PhysicsSetBreast)


_classes = (
    LOLBLENDER_BoneItem,
    LOLBLENDER_PhysicsSetHair,
    LOLBLENDER_PhysicsSetCloth,
    LOLBLENDER_PhysicsSetBreast,
    LOLBLENDER_ObjectPhysics,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Object.lol_physics = PointerProperty(type=LOLBLENDER_ObjectPhysics)


def unregister():
    del bpy.types.Object.lol_physics
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
