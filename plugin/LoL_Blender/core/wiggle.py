"""Bone physics: the old standalone addon's model (Wiggle 2), solved in Rust.

Parameter curves, collider spheres and per-bone values are copied from that addon. New: live
preview on every frame change, and the movement limit inside the solver (so the preview matches
the bake). The animated pose is computed as `parent_pose @ rest_delta` instead of a depsgraph
update per frame; a simulated bone's own animation channels are ignored.
"""

import math

from mathutils import Matrix, Vector

from .. import native

# ---------------------------------------------------------------------------
#  Parameter curves, from the standalone addon
# ---------------------------------------------------------------------------

#: The old addon's Ponytail preset, used by hair and cloth. `damp_lo` is smoothness 1 (bouncy),
#: `damp_hi` is smoothness 20 (calm).
PONYTAIL = {
    'stiff_hi': 400.0, 'stiff_lo': 40.0,
    'damp_lo': 0.35, 'damp_hi': 8.0,
    'grav_lo': 0.05, 'grav_hi': 0.35,
    'mass_hi': 0.15, 'mass_lo': 1.2,
    'stretch_hi': 0.0, 'stretch_lo': 0.04,
}

#: Cloth uses the hair curve (the old addon had no cloth set).
PRESETS = {'HAIR': PONYTAIL, 'CLOTH': PONYTAIL}


def lerp(a, b, t):
    return a + (b - a) * t


def lerp_exp(a, b, t):
    """Geometric interpolation, for values spanning an order of magnitude (stiffness)."""
    if a <= 0.0 or b <= 0.0:
        return lerp(a, b, t)
    return a * ((b / a) ** t)


def strand_params(intensity, smoothness, gravity_mult, preset):
    """`get_hair_params` from the standalone addon."""
    t = max(0.0, min(1.0, (intensity - 1) / 19.0))
    ts = max(0.0, min(1.0, (smoothness - 1) / 19.0))
    return {
        'stiff': lerp_exp(preset['stiff_hi'], preset['stiff_lo'], t),
        'damp': lerp_exp(preset['damp_lo'], preset['damp_hi'], ts),
        'gravity': lerp(preset['grav_lo'], preset['grav_hi'], t) * gravity_mult,
        'mass': lerp_exp(preset['mass_hi'], preset['mass_lo'], t),
        'stretch': lerp(preset['stretch_hi'], preset['stretch_lo'], t),
    }


#: Breast gravity at Gravity 1.0. Not the old addon's value: its 0.03 had no visible effect
#: (League units are about centimetres, so Blender's 9.81 is far too weak). Not scaled by intensity.
BREAST_GRAVITY = 20.0


def breast_params(intensity, smoothness, gravity_mult):
    """`get_jiggle_params` from the standalone addon (squared intensity, stiffer), except
    gravity: see `BREAST_GRAVITY`."""
    t = max(0.0, min(1.0, (intensity - 1) / 19.0))
    t = t * t
    ts = max(0.0, min(1.0, (smoothness - 1) / 19.0))
    return {
        'stiff': lerp_exp(650.0, 110.0, t),
        'damp': lerp_exp(0.60, 12.0, ts),
        'gravity': BREAST_GRAVITY * gravity_mult,
        'mass': lerp_exp(0.3, 1.5, t),
        'stretch': lerp(0.0, 0.02, t),
    }


def params_for(kind, intensity, smoothness, gravity_mult):
    if kind == 'BREAST':
        return breast_params(intensity, smoothness, gravity_mult)
    return strand_params(intensity, smoothness, gravity_mult, PRESETS.get(kind, PONYTAIL))


def settings_for(physics_set, kind=None):
    """The solver values for a set: its preset, or the rig's custom values while Custom is on.

    The only place this is decided, so preview and bake always agree.
    """
    from ..ui import physics_props

    kind = kind or physics_props.identifier_of(physics_set)
    values = params_for(kind, physics_set.intensity, physics_set.smoothness, physics_set.gravity)

    props = getattr(physics_set.id_data, "lol_physics", None)
    if props is not None and props.active == physics_props.CUSTOM:
        values['stiff'] = props.custom_stiff
        values['damp'] = props.custom_damp
        values['gravity'] = props.custom_gravity
        values['stretch'] = props.custom_stretch
    return values


#: Constraint iterations per frame, as the old addon's bake. Fewer makes long strands rubbery.
ITERATIONS = 20


def scene_gravity(scene):
    """Scene gravity, or zero when the scene's gravity is off."""
    return list(scene.gravity) if scene.use_gravity else [0.0, 0.0, 0.0]

# ---------------------------------------------------------------------------
#  Collision: the standalone addon's spheres
# ---------------------------------------------------------------------------

#: Collider sphere radius as a fraction of the bone length, by bone type.
BONE_TYPE_FACTORS = {
    'upperarm': 0.22, 'lowerarm': 0.20, 'elbow': 0.35, 'clavicle': 0.20,
    'shoulder': 0.22, 'hand': 0.18, 'spine': 0.28, 'pelvis': 0.32,
    'neck': 0.30, 'head': 0.55, 'default': 0.20,
}

#: Per-set radius overrides by bone-name substring, from the old addon. First match wins.
RADIUS_OVERRIDES = {
    'HAIR': {'head': 0.90, 'neck': 0.50},
    'CLOTH': {'head': 0.90, 'neck': 0.50},
    'BREAST': {'clavicle': 0.22, 'shoulder': 0.25},
}

#: Fallback table for other sets.
STRAND_RADIUS_OVERRIDES = RADIUS_OVERRIDES['HAIR']


def radius_override(name, kind):
    """The override factor for this bone in this set, or None."""
    lowered = name.lower()
    for key, factor in RADIUS_OVERRIDES.get(kind, STRAND_RADIUS_OVERRIDES).items():
        if key in lowered:
            return factor
    return None


#: Simulated particle per set: (minimum radius, fraction of bone length, bounce), from the old
#: addon. Breasts are bigger: the bone stands for a volume, not a strand.
PARTICLE = {
    'HAIR': (0.008, 0.12, 0.10),
    'CLOTH': (0.008, 0.12, 0.10),
    'BREAST': (0.01, 0.35, 0.15),
}


def bone_type(name):
    n = name.lower()
    if any(k in n for k in ('upperarm', 'upper_arm', 'upper arm')):
        return 'upperarm'
    if any(k in n for k in ('lowerarm', 'lower_arm', 'forearm')):
        return 'lowerarm'
    if 'elbow' in n:
        return 'elbow'
    if any(k in n for k in ('clavicle', 'clav')):
        return 'clavicle'
    if 'shoulder' in n:
        return 'shoulder'
    if 'hand' in n:
        return 'hand'
    if any(k in n for k in ('spine', 'torso', 'chest')):
        return 'spine'
    if any(k in n for k in ('pelvis', 'hip')):
        return 'pelvis'
    if 'neck' in n:
        return 'neck'
    if 'head' in n:
        # same order as the original: upperarm names are caught above
        return 'head'
    return 'default'


def capsule_radius(name, world_length, scale):
    factor = BONE_TYPE_FACTORS.get(bone_type(name), BONE_TYPE_FACTORS['default'])
    return max(0.01, world_length * factor * scale)


#: Sets that use the special head and neck sphere layout (as in the old addon's hair builder).
SHAPED_HEAD_AND_NECK = ('HAIR', 'CLOTH')


def sphere_placements(name, world_length, radius, kind='HAIR'):
    """Spheres along one collider bone as `(t, radius)`, t from head (0) to tail (1).

    Same layout as the old addon, including its fourth head sphere clamped to the tail.
    """
    shaped = bone_type(name) if kind in SHAPED_HEAD_AND_NECK else 'default'
    if shaped == 'head':
        return [(0.5, radius), (0.0, radius * 0.75), (1.0, radius * 0.75), (1.0, radius * 0.55)]
    if shaped == 'neck':
        return [(0.0, radius), (0.5, radius), (1.0, radius)]
    # generic capsule: 2 to 4 evenly spaced spheres
    count = max(2, min(4, int(world_length / max(radius, 0.01)) + 1))
    return [(i / max(1, count - 1), radius) for i in range(count)]


def collider_placements(armature, names, scale=1.0, kind='HAIR'):
    """Every collision sphere as `(bone name, fraction along the bone, radius)`."""
    world = armature.matrix_world
    out = []
    for name in names:
        pose_bone = armature.pose.bones.get(name)
        if pose_bone is None:
            continue
        length = (world @ pose_bone.tail - world @ pose_bone.head).length
        if length < 1e-6:
            continue
        override = radius_override(name, kind)
        if override is not None:
            radius = max(0.01, length * override * scale)
        else:
            radius = capsule_radius(name, length, scale)
        for fraction, sphere_radius in sphere_placements(name, length, radius, kind):
            out.append((name, min(1.0, max(0.0, fraction)), sphere_radius))
    return out


def collider_shapes(armature, names, scale=1.0, kind='HAIR'):
    """The default sphere colliders as flat `(matrices, halves, radii)` for the solver.

    Same spheres as the old addon. A sphere is a box with zero half-extents. Matrices are rigid;
    the size travels in `radii`.
    """
    world = armature.matrix_world
    matrices = []
    halves = []
    radii = []
    for name, fraction, radius in collider_placements(armature, names, scale, kind):
        pose_bone = armature.pose.bones[name]
        head = world @ pose_bone.head
        tail = world @ pose_bone.tail
        rotation = (world @ pose_bone.matrix).to_quaternion().to_matrix().to_4x4()
        matrix = Matrix.Translation(head.lerp(tail, fraction)) @ rotation
        for row in matrix:
            matrices.extend(row)
        halves.extend((0.0, 0.0, 0.0))
        radii.append(radius)

    return matrices, halves, radii


# ---------------------------------------------------------------------------
#  Topology
# ---------------------------------------------------------------------------

#: Max head-to-parent-tail gap, as a fraction of the parent length, to count as connected.
CONNECTED_TOLERANCE = 1e-3


def _connected(pose_bone, parent):
    """True when the head sits on the parent's tail (geometry, not `use_connect`)."""
    length = parent.bone.length
    if length <= 0.0:
        return False
    gap = (pose_bone.bone.head_local - parent.bone.tail_local).length
    return gap <= length * CONNECTED_TOLERANCE


def rest_delta(pose_bone):
    """The rest transform relative to the parent (the fixed part of `parent_pose @ delta`)."""
    bone = pose_bone.bone
    if pose_bone.parent is None:
        return bone.matrix_local.copy()
    return pose_bone.parent.bone.matrix_local.inverted() @ bone.matrix_local


def ordered(armature, names):
    """The listed bones, parents first (the solver and the constraint pass depend on order)."""
    wanted = []
    seen = set()
    for name in names:
        if name and name not in seen and name in armature.pose.bones:
            seen.add(name)
            wanted.append(name)

    depth = {}

    def bone_depth(pose_bone):
        if pose_bone.name in depth:
            return depth[pose_bone.name]
        d = 0 if pose_bone.parent is None else bone_depth(pose_bone.parent) + 1
        depth[pose_bone.name] = d
        return d

    for name in wanted:
        bone_depth(armature.pose.bones[name])
    return sorted(wanted, key=lambda n: (depth[n], n))


class Topology:
    """The frame-independent data of a set of bones.

    Only one simulated child may pull on each parent (two make the chain diverge). Extra children
    are detached: they simulate as their own chain, following the parent's pose. The child with the
    most simulated bones below it keeps the link.
    """

    def __init__(self, armature, names):
        self.names = ordered(armature, names)
        index = {name: i for i, name in enumerate(self.names)}

        self.pose_bones = [armature.pose.bones[name] for name in self.names]
        self.rest_lengths = [b.bone.length for b in self.pose_bones]
        self.deltas = [rest_delta(b) for b in self.pose_bones]

        # nearest listed ancestor
        candidates = []
        for pose_bone in self.pose_bones:
            found = -1
            node = pose_bone.parent
            while node is not None:
                if node.name in index:
                    found = index[node.name]
                    break
                node = node.parent
            candidates.append(found)

        # keep one child per parent
        children = {}
        for i, parent in enumerate(candidates):
            if parent >= 0:
                children.setdefault(parent, []).append(i)
        below = [0] * len(self.names)
        for i in range(len(self.names) - 1, -1, -1):
            parent = candidates[i]
            if parent >= 0:
                below[parent] += below[i] + 1

        self.parents = list(candidates)
        self.detached = []
        for parent, kids in children.items():
            if len(kids) < 2:
                continue
            keep = max(kids, key=lambda i: (below[i], self.rest_lengths[i], -i))
            for i in kids:
                if i != keep:
                    self.parents[i] = -1
                    self.detached.append(self.names[i])

        # `follows`: who places the bone (a detached bone still follows, it just does not pull)
        self.follows = list(candidates)
        for i, pose_bone in enumerate(self.pose_bones):
            follow = self.follows[i]
            if follow >= 0 and pose_bone.parent is not None                     and pose_bone.parent.name != self.names[follow]:
                # unsimulated bones in between: use the evaluated pose
                self.follows[i] = -1

        # how to build each animated matrix
        self.direct_connected = []
        #: `('SIM', i)`, `('POSE', pose_bone)` or `('WORLD', None)`.
        self.sources = []
        for i, pose_bone in enumerate(self.pose_bones):
            parent = pose_bone.parent
            follow = self.follows[i]

            # connected from geometry: League rigs keep use_connect off, but Wiggle's stable
            # connected path depends on it
            self.direct_connected.append(
                self.parents[i] >= 0
                and parent is not None
                and self.names[self.parents[i]] == parent.name
                and _connected(pose_bone, parent))

            if parent is None:
                self.sources.append(('WORLD', None))
            elif follow >= 0:
                self.sources.append(('SIM', follow))
            else:
                # parent not in this set: read its pose
                self.sources.append(('POSE', parent))

    def __len__(self):
        return len(self.names)


# ---------------------------------------------------------------------------
#  One simulated set
# ---------------------------------------------------------------------------

class Sim:
    """One Hair, Cloth or Breast set: its bones, its shared settings, its colliders."""

    def __init__(self, armature, kind, names, settings, colliders=(), max_angle=0.0):
        self.armature = armature
        self.kind = kind
        self.topology = Topology(armature, names)
        # a set never collides with its own bones
        own = set(self.topology.names)
        self.collider_names = [n for n in colliders if n and n not in own]
        #: The dropped own bones, for the panel.
        self.self_colliders = [n for n in colliders if n in own]
        #: Set by `colliders()`, so the allowances line up with its list.
        self._missing = []
        self._fallback_spheres = 0
        #: The frame's `shapes.by_bone()` map, shared by all readers. None until built.
        self._shapes = None
        #: The pose of the last `reset`, for the rest allowances. None until the first reset.
        self._start_bones = None
        self._start_places = {}
        #: What the rest allowances were measured for; re-measured only when shapes change.
        self._allowance_key = None
        self.settings = dict(settings)
        self.max_angle = max_angle
        #: The last deltas written, see `restore`.
        self.written = None
        self.solver = None
        if len(self.topology):
            self.solver = native.rs().Wiggle(
                self.topology.parents,
                self.topology.direct_connected,
                self.topology.rest_lengths,
                self.topology.follows)
            self.push_settings()

    def __len__(self):
        return len(self.topology)

    # -- settings ---------------------------------------------------------

    def push_settings(self):
        """Send the settings to the solver without resetting its state."""
        if self.solver is None:
            return
        n = len(self.topology)
        s = self.settings
        world = self.armature.matrix_world
        floor, factor, bounce = PARTICLE[self.kind if self.kind in PARTICLE else 'HAIR']
        radii = []
        for pose_bone in self.topology.pose_bones:
            length = (world @ pose_bone.tail - world @ pose_bone.head).length
            radii.append(max(floor, length * factor))
        self.solver.set_params(
            [s['stiff']] * n, [s['damp']] * n, [s['gravity']] * n,
            [s['mass']] * n, [s['stretch']] * n, [True] * n,
            radii,
            # low friction and zero sticky (old addon values), or strands stick to the body
            [0.05] * n, [bounce] * n, [0.0] * n)
        self.solver.set_max_angle(self.max_angle)

    # -- per frame --------------------------------------------------------

    def animated(self):
        """The pose of every simulated bone with its own channels zeroed: flat (matrices, scales)."""
        world = self.armature.matrix_world
        inverse = world.inverted()
        topology = self.topology
        built = []
        matrices = []
        scales = []
        for i in range(len(topology)):
            kind, ref = topology.sources[i]
            if kind == 'SIM':
                base = built[ref]
            elif kind == 'POSE':
                base = world @ ref.matrix
            else:
                base = world
            matrix = base @ topology.deltas[i]
            built.append(matrix)
            for row in matrix:
                matrices.extend(row)
            scales.extend((inverse @ matrix).to_scale())
        return matrices, scales

    def colliders(self):
        """What pushes this set: `(matrices, halves, radii)`. A placed shape replaces that bone's
        default spheres."""
        from . import shapes

        if not self.collider_names:
            self._missing = []
            self._fallback_spheres = 0
            return [], [], []
        matrices, halves, radii, missing = shapes.colliders_for(self.armature,
                                                               self.collider_names,
                                                               self._shapes)
        before = len(radii)
        if missing:
            more = collider_shapes(self.armature, missing, 1.0, self.kind)
            matrices.extend(more[0])
            halves.extend(more[1])
            radii.extend(more[2])
        # remembered so push_bodies uses the same order
        self._missing = missing
        self._fallback_spheres = len(radii) - before
        return matrices, halves, radii

    def push_bodies(self):
        """Send each simulated bone's shape (zeros: a point) and the rest allowances: how deep
        each bone and collider already overlap at the start pose. Measured in Rust."""
        from . import shapes

        if self.solver is None:
            return
        self.solver.set_bodies(*shapes.bodies_for(self.armature, self.topology.names,
                                                  self._shapes))
        if not self.collider_names:
            self._allowance_key = None
            self.solver.set_body_allowances([])
            return
        key = (tuple(self._missing), self._fallback_spheres,
               shapes.placement_signature(
                   list(self.topology.names) + list(self.collider_names), self._shapes))
        if key == self._allowance_key:
            return
        # measured at the pose the simulation started from (the clip's first frame), not the
        # bind pose; the bind pose only before the first reset
        if self._start_bones is not None:
            rest_bones = self._start_bones
            places = self._start_places
        else:
            world = self.armature.matrix_world
            rest_bones = []
            for pose_bone in self.topology.pose_bones:
                for row in world @ pose_bone.bone.matrix_local:
                    rest_bones.extend(row)
            places = None
        matrices, halves, radii, _missing = shapes.colliders_for(
            self.armature, self.collider_names, self._shapes, rest=places is None, places=places)
        # fallback spheres: no allowance (negative radius), slot kept for alignment
        for _sphere in range(self._fallback_spheres):
            matrices.extend((1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
                             0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0))
            halves.extend((0.0, 0.0, 0.0))
            radii.append(-1.0)
        self.solver.set_rest_allowances(rest_bones, matrices, halves, radii)
        self._allowance_key = key

    def reset(self):
        from . import shapes

        if self.solver is None:
            return
        matrices, _scales = self.animated()
        self.solver.reset(matrices)
        self.written = None
        # the start pose for the rest allowances (see push_bodies)
        self._start_bones = list(matrices)
        self._start_places = {name: shapes.bone_place(self.armature, self.armature.pose.bones[name])
                              for name in self.collider_names
                              if name in self.armature.pose.bones}
        self._allowance_key = None
        identity = Matrix.Identity(4)
        for pose_bone in self.topology.pose_bones:
            pose_bone.matrix_basis = identity

    def step(self, dt, frames_elapsed, gravity, write=True):
        """One frame. Returns the per-bone local deltas, and writes them unless told not to."""
        from . import shapes as shapes_module

        if self.solver is None:
            return []
        matrices, scales = self.animated()
        # shapes re-read every frame: dragging one fires no callback
        self._shapes = shapes_module.by_bone(self.armature)
        self.solver.set_colliders(*self.colliders())
        self.push_bodies()
        flat = self.solver.step(matrices, scales, dt, frames_elapsed, gravity, ITERATIONS)
        deltas = [Matrix([flat[i * 16 + r * 4:i * 16 + r * 4 + 4] for r in range(4)])
                  for i in range(len(self.topology))]
        if write:
            for pose_bone, delta in zip(self.topology.pose_bones, deltas):
                pose_bone.matrix_basis = delta
            self.written = deltas
        return deltas

    def restore(self, tolerance=1e-5):
        """Write the last simulated pose back after the animation overwrote it. True if written.

        Only writes what differs, so the depsgraph handler calling it settles.
        """
        if not self.written:
            return False
        wrote = False
        for pose_bone, delta in zip(self.topology.pose_bones, self.written):
            current = pose_bone.matrix_basis
            if max(abs(current[r][c] - delta[r][c])
                   for r in range(4) for c in range(4)) > tolerance:
                pose_bone.matrix_basis = delta
                wrote = True
        return wrote


# ---------------------------------------------------------------------------
#  One rig
# ---------------------------------------------------------------------------

def validate_sets(armature):
    from ..ui import physics_props
    owners = {}
    for identifier, physics_set in physics_props.sets_of(armature):
        if physics_set.enabled:
            for item in physics_set.bones:
                if item.name and item.name in armature.data.bones:
                    previous = owners.setdefault(item.name, identifier)
                    if previous != identifier:
                        raise ValueError(f'{armature.name}: bone {item.name} is in both '
                                         f'{previous} and {identifier}. Keep it in one enabled physics set')


class Rig:
    """Every enabled set on one armature, plus the frame bookkeeping Wiggle kept on the scene."""

    def __init__(self, armature):
        self.armature = armature
        self.sims = []
        self.last_frame = 0
        self.build()
        self.initial_basis = {bone.name: bone.matrix_basis.copy()
                              for sim in self.sims for bone in sim.topology.pose_bones}

    def build(self):
        from ..ui import physics_props

        validate_sets(self.armature)
        self.sims = []
        for identifier, physics_set in physics_props.sets_of(self.armature):
            if not physics_set.enabled:
                continue
            names = [item.name for item in physics_set.bones if item.name]
            if not names:
                continue
            colliders = ([item.name for item in physics_set.colliders]
                         if physics_set.collision_enabled else [])
            self.sims.append(Sim(
                self.armature, identifier, names,
                settings_for(physics_set, identifier),
                colliders,
                physics_set.max_angle if physics_set.limit_movement else 0.0))
        return self.sims

    def refresh_settings(self):
        from ..ui import physics_props

        for sim in self.sims:
            physics_set = physics_props.get_set(self.armature, sim.kind)
            if physics_set is None:
                continue
            values = settings_for(physics_set, sim.kind)
            angle = physics_set.max_angle if physics_set.limit_movement else 0.0
            if sim.settings != values or sim.max_angle != angle:
                sim.settings = values
                sim.max_angle = angle
                sim.push_settings()

    def clip(self):
        """`(name, first frame, last frame)` of the rig's action, or None."""
        data = self.armature.animation_data
        action = data.action if data is not None else None
        if action is None:
            return None
        return (action.name,
                int(round(action.frame_range[0])),
                int(round(action.frame_range[1])))

    def signature(self):
        """What the sims were built from (clip included). A change means a rebuild."""
        from ..ui import physics_props

        parts = [self.clip()]
        for identifier, physics_set in physics_props.sets_of(self.armature):
            parts.append((
                identifier,
                bool(physics_set.enabled),
                tuple(item.name for item in physics_set.bones),
                bool(physics_set.collision_enabled),
                tuple(item.name for item in physics_set.colliders),
            ))
        return tuple(parts)

    @property
    def bone_count(self):
        return sum(len(sim) for sim in self.sims)

    def reset(self):
        for sim in self.sims:
            sim.reset()
        self.last_frame = 0

    def restore(self):
        """Re-apply the last simulated pose. True when anything had to be written."""
        wrote = False
        for sim in self.sims:
            if sim.restore():
                wrote = True
        return wrote

    def elapsed(self, scene):
        """Frames elapsed, as Wiggle counts them: a playback wrap counts as one frame forward."""
        frame = scene.frame_current
        last = self.last_frame
        if frame >= last:
            elapsed = frame - last
        else:
            forward = (scene.frame_end - last) + (frame - scene.frame_start) + 1
            backward = last - frame
            elapsed = min(forward, backward)
        if elapsed > 4:
            elapsed = 1
        self.last_frame = frame
        return elapsed

    def step(self, scene, elapsed=None):
        # animated properties do not fire the slider callbacks
        self.refresh_settings()
        if elapsed is None:
            elapsed = self.elapsed(scene)
        fps = scene.render.fps / max(1e-6, scene.render.fps_base)
        dt = elapsed / fps
        gravity = scene_gravity(scene)
        for sim in self.sims:
            sim.step(dt, elapsed, gravity)


# ---------------------------------------------------------------------------
#  Reporting
# ---------------------------------------------------------------------------

def listed_bones(armature):
    from ..ui import physics_props

    return physics_props.listed_bones(armature)


def clear_pose(armature, names):
    """Put the listed bones back on the animated pose (identity basis)."""
    identity = Matrix.Identity(4)
    for name in names:
        pose_bone = armature.pose.bones.get(name)
        if pose_bone is not None:
            pose_bone.matrix_basis = identity


def angle_between(a, b):
    """Degrees between two rotations, for tests and reports."""
    dot = min(1.0, abs(a.to_quaternion().dot(b.to_quaternion())))
    return math.degrees(2.0 * math.acos(dot))


def world_tail(armature, name):
    pose_bone = armature.pose.bones.get(name)
    if pose_bone is None:
        return Vector((0.0, 0.0, 0.0))
    return armature.matrix_world @ pose_bone.tail
