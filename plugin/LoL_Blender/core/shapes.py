"""Body shapes: collision volumes fitted to the mesh, then edited by the user.

Set Body Shapes fits a box (a sphere for breasts) around the vertices each bone owns (where it has
the largest weight). The shapes are ordinary objects the user can move, rotate and scale; the
solver reads them back exactly. `ensure` never touches an existing shape.

Each shape follows its bone through a `Child Of` constraint with an identity inverse and scale off,
so `matrix_basis` is its transform in bone space and squash poses cannot make it singular.

Collider shapes push, branch shapes get pushed. Overlap at rest is subtracted (rest allowance), so
neighbouring shapes only collide when they close further than at rest.
"""

import math

import bpy
from mathutils import Matrix, Vector

#: Collection holding every shape.
COLLECTION = "LoL Body Shapes"

#: Properties stamped on each shape.
BONE = "lol_shape_bone"
KIND = "lol_shape_kind"
ROLE = "lol_shape_role"
#: Half-extents before the object's scale (the user scales the object).
HALF = "lol_shape_half"
ROUND = "lol_shape_round"

BOX = 'BOX'
SPHERE = 'SPHERE'
COLLIDER = 'COLLIDER'
BODY = 'BODY'

#: Segments per circle of the three-circle wireframe sphere.
SEGMENTS = 16


def _collection(scene):
    found = bpy.data.collections.get(COLLECTION)
    if found is None:
        found = bpy.data.collections.new(COLLECTION)
    if found.name not in scene.collection.children:
        scene.collection.children.link(found)
    return found


# ---------------------------------------------------------------------------
#  The meshes
# ---------------------------------------------------------------------------

def _box_mesh(name, half):
    mesh = bpy.data.meshes.new(name)
    verts = [(sx * half.x, sy * half.y, sz * half.z)
             for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
    edges = [(a, b) for a in range(8) for b in range(a + 1, 8)
             if bin(a ^ b).count("1") == 1]
    mesh.from_pydata(verts, edges, [])
    mesh.update()
    return mesh


def _sphere_mesh(name, radius):
    mesh = bpy.data.meshes.new(name)
    verts = []
    edges = []
    for axis in range(3):
        first = len(verts)
        for step in range(SEGMENTS):
            angle = 2.0 * math.pi * step / SEGMENTS
            point = [0.0, 0.0, 0.0]
            point[(axis + 1) % 3] = math.cos(angle) * radius
            point[(axis + 2) % 3] = math.sin(angle) * radius
            verts.append(tuple(point))
        edges.extend([(first + i, first + (i + 1) % SEGMENTS) for i in range(SEGMENTS)])
    mesh.from_pydata(verts, edges, [])
    mesh.update()
    return mesh


def _mesh_for(name, kind, half, rounding):
    if kind == SPHERE:
        return _sphere_mesh(name, rounding)
    return _box_mesh(name, half)


# ---------------------------------------------------------------------------
#  Making them
# ---------------------------------------------------------------------------

#: Weight left outside the fitted box on each side (a weighted quantile, so stray far
#: vertices do not inflate the box).
FIT_QUANTILE = 0.04

#: Ignore weights below this when fitting.
FIT_MIN_WEIGHT = 0.05

#: Fit only the vertices where the bone has the largest weight (otherwise a bust bone fits the
#: whole chest).
FIT_DOMINANT_ONLY = True

#: Corner rounding as a fraction of the box thickness, so boxes do not collide at the corners
#: first. The outer size is unchanged.
FIT_ROUNDING = 0.35


def _bound_meshes(armature):
    """Every mesh deformed by this armature."""
    out = []
    for obj in bpy.data.objects:
        if obj.type != 'MESH' or not obj.vertex_groups:
            continue
        if any(m.type == 'ARMATURE' and m.object is armature for m in obj.modifiers):
            out.append(obj)
    return out


def _weighted_span(values, weights, quantile):
    """`(low, high)` holding all but `quantile` of the WEIGHT at each end."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    total = sum(weights)
    if total <= 0.0:
        return None
    want = total * quantile
    low = high = values[order[0]]
    run = 0.0
    for i in order:
        run += weights[i]
        if run >= want:
            low = values[i]
            break
    run = 0.0
    for i in reversed(order):
        run += weights[i]
        if run >= want:
            high = values[i]
            break
    if high < low:
        low = high = 0.5 * (low + high)
    return low, high


def fit_all(armature, bone_names):
    """`{bone name: (half, centre)}` in each bone's rest space, fitted to its weighted vertices.

    One pass over the bind mesh for all bones. Bones with nothing to fit are left out.
    """
    wanted_names = {n for n in bone_names if n}
    bones = armature.data.bones
    buckets = {n: ([], []) for n in wanted_names if n in bones}
    if not buckets:
        return {}

    rig_inverse = armature.matrix_world.inverted()
    for obj in _bound_meshes(armature):
        index_of = {}
        for group in obj.vertex_groups:
            if group.name in buckets:
                index_of[group.index] = group.name
        if not index_of:
            continue
        to_armature = rig_inverse @ obj.matrix_world
        for vertex in obj.data.vertices:
            hits = None
            # largest weight across every group, not only the fitted ones
            strongest = 0.0
            if FIT_DOMINANT_ONLY:
                for element in vertex.groups:
                    if element.weight > strongest:
                        strongest = element.weight
            for element in vertex.groups:
                name = index_of.get(element.group)
                if name is None or element.weight < FIT_MIN_WEIGHT:
                    continue
                if FIT_DOMINANT_ONLY and element.weight < strongest - 1e-6:
                    continue
                if hits is None:
                    hits = []
                hits.append((name, element.weight))
            if not hits:
                continue
            point = to_armature @ vertex.co
            for name, weight in hits:
                points, weights = buckets[name]
                points.append(point)
                weights.append(weight)

    out = {}
    for name, (points, weights) in buckets.items():
        if len(weights) < 4:
            continue
        to_bone = bones[name].matrix_local.inverted()
        local = [to_bone @ p for p in points]
        spans = [_weighted_span([p[axis] for p in local], weights, FIT_QUANTILE)
                 for axis in range(3)]
        if any(span is None for span in spans):
            continue
        half = Vector(tuple((hi - lo) * 0.5 for lo, hi in spans))
        centre = Vector(tuple((hi + lo) * 0.5 for lo, hi in spans))
        if max(half) < 1e-5:
            continue
        # a flat sheet (cape, hem) gets some thickness
        floor = max(1e-4, min(v for v in half if v > 1e-5) * 0.25)
        out[name] = (Vector((max(half.x, floor), max(half.y, floor), max(half.z, floor))), centre)
    return out


def fit_to_weights(armature, bone_name):
    """One bone's fit, or None."""
    return fit_all(armature, [bone_name]).get(bone_name)


def default_shape(armature, bone_name, kind, role=COLLIDER, set_id='HAIR', fitted=None):
    """A new shape's `(half, rounding, centre)` in bone space.

    From the mesh fit when there is one: a box keeps it, a sphere takes its smallest half-extent.
    Otherwise a size from the bone length, like the old addon.
    """
    from . import wiggle

    pose_bone = armature.pose.bones.get(bone_name)
    if pose_bone is None:
        return None
    world = armature.matrix_world
    length = (world @ pose_bone.tail - world @ pose_bone.head).length
    if length < 1e-6:
        return None

    if fitted is not None:
        half, centre = fitted
        if kind == SPHERE:
            radius = min(half.x, half.y, half.z)
            return Vector((radius, radius, radius)), radius, centre
        return half, min(half) * FIT_ROUNDING, centre

    if role == BODY:
        floor, factor, _bounce = wiggle.PARTICLE.get(set_id, wiggle.PARTICLE['HAIR'])
        radius = max(floor, length * factor)
    else:
        override = wiggle.radius_override(bone_name, set_id)
        radius = (max(0.01, length * override) if override is not None
                  else wiggle.capsule_radius(bone_name, length, 1.0))

    centre = Vector((0.0, length * 0.5, 0.0))
    if kind == SPHERE:
        return Vector((radius, radius, radius)), radius, centre
    return Vector((radius, max(radius, length * 0.5), radius)), 0.0, centre


def name_for(bone_name):
    return "%s %s" % (COLLECTION, bone_name)


def owner(obj):
    if obj is None or not obj.get(BONE):
        return None
    return next((c.target for c in obj.constraints if c.type == 'CHILD_OF'
                 and c.target is not None and c.target.type == 'ARMATURE'), None)


def by_bone(armature=None):
    """`{bone name: shape object}` in one pass (the solver reads all shapes every frame)."""
    collection = bpy.data.collections.get(COLLECTION)
    if collection is None:
        return {}
    out = {}
    for obj in collection.objects:
        if armature is not None and owner(obj) not in (None, armature):
            continue
        bone_name = obj.get(BONE)
        if bone_name and (bone_name not in out or owner(obj) == armature):
            out[bone_name] = obj
    return out


def find(armature, bone_name):
    """The shape for this bone, or None. Matched by the stamp, not the object name."""
    collection = bpy.data.collections.get(COLLECTION)
    if collection is None:
        return None
    matches = [obj for obj in collection.objects if obj.get(BONE) == bone_name]
    found = next((obj for obj in matches if owner(obj) == armature), None)
    if found is not None:
        return found
    orphaned = [obj for obj in matches if owner(obj) is None]
    return orphaned[0] if len(orphaned) == 1 else None


def create(armature, bone_name, kind, role, scene=None, set_id='HAIR', fitted=None):
    """Make one shape and put it on its bone. Returns the object, or None if the bone is unusable."""
    scene = scene or bpy.context.scene
    if fitted is None:
        fitted = fit_to_weights(armature, bone_name)
    made = default_shape(armature, bone_name, kind, role, set_id, fitted)
    if made is None:
        return None
    half, rounding, centre = made

    label = name_for(bone_name)
    obj = bpy.data.objects.new(label, _mesh_for(label, kind, half, rounding))
    obj[BONE] = bone_name
    obj[KIND] = kind
    obj[ROLE] = role
    obj[HALF] = [half.x, half.y, half.z]
    obj[ROUND] = rounding
    obj.display_type = 'WIRE'
    obj.show_in_front = True
    obj.hide_render = True
    _collection(scene).objects.link(obj)

    constraint = obj.constraints.new(type='CHILD_OF')
    constraint.target = armature
    constraint.subtarget = bone_name
    # identity inverse: matrix_basis is the transform in bone space
    constraint.inverse_matrix = Matrix.Identity(4)
    # no bone scale: squash poses would flatten it, and the solver ignores bone scale too
    constraint.use_scale_x = False
    constraint.use_scale_y = False
    constraint.use_scale_z = False
    obj.matrix_basis = Matrix.Translation(centre)
    return obj


def heal(obj, armature=None):
    """Repair a shape's constraint (scale channels, lost target after a re-import).

    Returns True on a fix. The placement is never changed.
    """
    fixed = False
    bone_name = obj.get(BONE)
    for constraint in obj.constraints:
        if constraint.type != 'CHILD_OF':
            continue
        for axis in ("use_scale_x", "use_scale_y", "use_scale_z"):
            if getattr(constraint, axis):
                setattr(constraint, axis, False)
                fixed = True
        if armature is not None and bone_name and armature.pose.bones.get(bone_name) is not None:
            if constraint.target is not armature or constraint.subtarget != bone_name:
                constraint.target = armature
                constraint.subtarget = bone_name
                constraint.inverse_matrix = Matrix.Identity(4)
                fixed = True
    return fixed


def wanted(armature):
    """`{bone name: (kind, role, set id)}` for every bone an enabled set needs a shape for."""
    from ..ui import physics_props

    out = {}
    for identifier, physics_set in physics_props.sets_of(armature):
        if not physics_set.enabled:
            continue
        # spheres for breast bones only; colliders (even in the breast set) are boxes
        for item in physics_set.bones:
            if item.name and item.name not in out:
                out[item.name] = (SPHERE if identifier == 'BREAST' else BOX, BODY, identifier)
        if physics_set.collision_enabled:
            for item in physics_set.colliders:
                if item.name and item.name not in out:
                    out[item.name] = (BOX, COLLIDER, identifier)
    return out


def ensure(armature, scene=None):
    """Create the missing shapes; existing ones are never changed. Returns `(made, kept)`."""
    scene = scene or bpy.context.scene
    made = 0
    kept = 0
    needed = wanted(armature)
    missing = [name for name in needed if find(armature, name) is None]
    fits = fit_all(armature, missing) if missing else {}
    for bone_name, (kind, role, set_id) in sorted(needed.items()):
        existing = find(armature, bone_name)
        if existing is not None:
            heal(existing, armature)
            kept += 1
            continue
        if create(armature, bone_name, kind, role, scene, set_id,
                  fits.get(bone_name)) is not None:
            made += 1
    return made, kept


def set_kind(obj, kind):
    """Swap a shape between box and sphere, keeping its placement and thickness."""
    if obj.get(KIND) == kind:
        return False
    half = Vector(obj.get(HALF, (1.0, 1.0, 1.0)))
    rounding = float(obj.get(ROUND, 0.0))
    if kind == SPHERE:
        rounding = min(half) if min(half) > 1e-6 else max(half)
        half = Vector((rounding, rounding, rounding))
    else:
        if rounding > 1e-6:
            half = Vector((rounding, rounding, rounding))
        rounding = 0.0
    old = obj.data
    obj.data = _mesh_for(obj.name, kind, half, rounding)
    if old is not None and old.users == 0:
        bpy.data.meshes.remove(old)
    obj[KIND] = kind
    obj[HALF] = [half.x, half.y, half.z]
    obj[ROUND] = rounding
    return True


# ---------------------------------------------------------------------------
#  Showing them
# ---------------------------------------------------------------------------

def exist(scene=None):
    collection = bpy.data.collections.get(COLLECTION)
    return bool(collection and collection.objects)


def shown(scene=None):
    scene = scene or bpy.context.scene
    collection = bpy.data.collections.get(COLLECTION)
    if not collection or not collection.objects:
        return False
    return not collection.hide_viewport


def show(visible, scene=None):
    """Show or hide the shapes (never deletes). Returns how many there are."""
    scene = scene or bpy.context.scene
    collection = bpy.data.collections.get(COLLECTION)
    if collection is None:
        return 0
    if collection.name not in scene.collection.children and visible:
        scene.collection.children.link(collection)
    collection.hide_viewport = not visible
    for obj in collection.objects:
        obj.hide_viewport = not visible
    return len(collection.objects)


def clear(scene=None, armature=None):
    """Remove body shapes, optionally restricted to one rig."""
    scene = scene or bpy.context.scene
    collection = bpy.data.collections.get(COLLECTION)
    if collection is None:
        return 0
    gone = 0
    for obj in list(collection.objects):
        if armature is not None and owner(obj) != armature:
            continue
        mesh = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        gone += 1
    if not collection.objects and collection.name in scene.collection.children:
        scene.collection.children.unlink(collection)
    if collection.users == 0:
        bpy.data.collections.remove(collection)
    return gone


# ---------------------------------------------------------------------------
#  Reading them back
# ---------------------------------------------------------------------------

def _armature_scale(armature):
    """The rig's world scale as one number (1.0 on imported rigs)."""
    scale = armature.matrix_world.to_scale()
    return (abs(scale.x) + abs(scale.y) + abs(scale.z)) / 3.0


def read(armature, bone_name, obj=None):
    """One shape as `(rigid local matrix, half, rounding)` in bone space and world units.

    The object's scale is folded into `half`.
    """
    obj = find(armature, bone_name) if obj is None else obj
    if obj is None or obj.get(HALF) is None:
        return None
    rig = _armature_scale(armature)
    basis = obj.matrix_basis
    scale = basis.to_scale()
    local = Matrix.LocRotScale(basis.to_translation() * rig, basis.to_quaternion(),
                               Vector((1.0, 1.0, 1.0)))
    base = Vector(obj[HALF])
    half = Vector((base.x * abs(scale.x), base.y * abs(scale.y), base.z * abs(scale.z))) * rig
    rounding = float(obj.get(ROUND, 0.0)) * min(abs(scale.x), abs(scale.y), abs(scale.z)) * rig
    return local, half, rounding


def kind_of(armature, bone_name):
    obj = find(armature, bone_name)
    return None if obj is None else obj.get(KIND, BOX)


def bone_place(armature, pose_bone):
    """The bone's world transform without scale: the frame a shape sits in."""
    matrix = armature.matrix_world @ pose_bone.matrix
    # euler like Child Of (mat3_to_eul), so a sheared bone matches the drawn shape
    return Matrix.LocRotScale(matrix.to_translation(), matrix.to_euler(),
                              Vector((1.0, 1.0, 1.0)))


def colliders_for(armature, names, index=None, rest=False, places=None):
    """`(matrices, halves, radii, missing)` for the named bones with a shape.

    `missing` names fall back to spheres. `rest` uses the bind pose and `places` a recorded pose,
    for measuring the rest allowances.
    """
    matrices = []
    halves = []
    radii = []
    missing = []
    index = by_bone() if index is None else index
    for name in names:
        shape = read(armature, name, index.get(name))
        pose_bone = armature.pose.bones.get(name)
        if shape is None or pose_bone is None:
            missing.append(name)
            continue
        local, half, rounding = shape
        if places is not None and name in places:
            matrix = places[name] @ local
        elif rest:
            matrix = _rest_place(armature, pose_bone.bone, local)
        else:
            matrix = bone_place(armature, pose_bone) @ local
        for row in matrix:
            matrices.extend(row)
        halves.extend(half)
        radii.append(rounding)
    return matrices, halves, radii, missing


def _rest_place(armature, bone, local):
    """A shape's world placement at rest (bind pose). The overlap is measured in Rust."""
    matrix = armature.matrix_world @ bone.matrix_local
    return Matrix.LocRotScale(matrix.to_translation(), matrix.to_euler(),
                              Vector((1.0, 1.0, 1.0))) @ local


def placement_signature(names, index=None):
    """A hashable snapshot of the shapes, to detect viewport edits (they fire no callback)."""
    index = by_bone() if index is None else index
    out = []
    for name in names:
        obj = index.get(name)
        if obj is None:
            out.append((name, None))
            continue
        out.append((name,
                    tuple(round(v, 6) for row in obj.matrix_basis for v in row),
                    tuple(obj.get(HALF, ())),
                    round(float(obj.get(ROUND, 0.0)), 6)))
    return tuple(out)


def bodies_for(armature, names, index=None):
    """`(centres, halves, radii, turns)` per name; zeros where there is no shape (a point)."""
    centres = []
    halves = []
    radii = []
    turns = []
    index = by_bone() if index is None else index
    for name in names:
        shape = read(armature, name, index.get(name))
        if shape is None:
            centres.extend((0.0, 0.0, 0.0))
            halves.extend((0.0, 0.0, 0.0))
            radii.append(0.0)
            turns.extend((1.0, 0.0, 0.0, 0.0))
            continue
        local, half, rounding = shape
        centres.extend(local.to_translation())
        halves.extend(half)
        radii.append(rounding)
        turns.extend(local.to_quaternion())
    return centres, halves, radii, turns
