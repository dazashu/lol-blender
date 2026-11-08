"""Selection, mesh diagnostics and cleanup for Daza Weight. ARP binding is in arp_bind.py."""

import bpy
from mathutils import Vector

from .. import native

#: Name of the armature modifier created by a bind.
MODIFIER = "Daza Weight"

#: Blender's Merge by Distance default.
MERGE_DISTANCE = 0.0001

#: Triangle count above which a slow bind is reported.
HIGH_POLY = 150000


# ---------------------------------------------------------------------------
#  What is selected
# ---------------------------------------------------------------------------

#: Joints a League model is never weighted to (FX points, roll helpers, end markers). Not
#: `detect.NEVER`: that physics list includes `root` and `weapon`, which do carry weights.
NOT_DEFORMING_KEYS = ("buffbone", "snap", "world", "parenting", "roll", "socket")
NOT_DEFORMING_SUFFIXES = ("_loc", "_end")


def is_deforming(name):
    """Could a model be weighted to this joint? False for FX locators, roll helpers, end markers."""
    lowered = name.lower()
    return not (any(key in lowered for key in NOT_DEFORMING_KEYS)
                or lowered.endswith(NOT_DEFORMING_SUFFIXES))


def selected_bones(armature, context=None):
    """Selected bone names in any mode (`edit_bones` in Edit Mode, `bones` otherwise)."""
    context = context or bpy.context
    data = armature.data
    if armature.mode == 'EDIT' and getattr(data, "edit_bones", None):
        names = [b.name for b in data.edit_bones if b.select and not b.hide]
        if names:
            return names
    return [b.name for b in data.bones if b.select and not b.hide]


def selected_meshes(context=None):
    """Every selected mesh, also read from the view layer (context lists vary with the mode)."""
    context = context or bpy.context
    out = []
    seen = set()
    pools = [context.selected_objects or (),
             getattr(context, "selected_editable_objects", None) or ()]
    for pool in pools:
        for obj in pool:
            if obj.type == 'MESH' and obj.name not in seen:
                seen.add(obj.name)
                out.append(obj)
    for obj in context.view_layer.objects:
        if obj.type == 'MESH' and obj.select_get() and obj.name not in seen:
            seen.add(obj.name)
            out.append(obj)
    return out


def bound_meshes(meshes):
    """Meshes Rebind can use: one known armature and vertex groups. Called on every redraw."""
    from .rebind import rig_for
    out = []
    for obj in meshes:
        try:
            rig_for(obj)
        except ValueError:
            continue
        if obj.vertex_groups:
            out.append(obj)
    return out


def attached_meshes(meshes):
    """Selected meshes still attached to an armature, which Unbind detaches."""
    return [obj for obj in meshes
            if any(m.type == 'ARMATURE' for m in obj.modifiers)
            or (obj.parent is not None and obj.parent.type == 'ARMATURE')]


def armature_in(context=None):
    """The armature to bind to: active, then the one selected, then the one with selected
    bones, then the only one in the scene."""
    context = context or bpy.context
    obj = context.object
    if obj is not None and obj.type == 'ARMATURE':
        return obj
    found = [o for o in context.view_layer.objects if o.type == 'ARMATURE' and o.select_get()]
    if len(found) == 1:
        return found[0]
    pool = found or [o for o in context.view_layer.objects if o.type == 'ARMATURE']
    if len(pool) == 1:
        return pool[0]
    with_bones = [o for o in pool if selected_bones(o, context)]
    if len(with_bones) == 1:
        return with_bones[0]
    return None


# ---------------------------------------------------------------------------
#  Reading a mesh out of Blender
# ---------------------------------------------------------------------------

def _buffers(mesh):
    """Local-space (vertices, triangles, edges) for the mesh diagnostics."""
    count = len(mesh.vertices)
    flat = [0.0] * (count * 3)
    mesh.vertices.foreach_get("co", flat)

    mesh.calc_loop_triangles()
    triangles = [0] * (len(mesh.loop_triangles) * 3)
    mesh.loop_triangles.foreach_get("vertices", triangles)

    edges = [0] * (len(mesh.edges) * 2)
    mesh.edges.foreach_get("vertices", edges)
    return flat, triangles, edges


# ---------------------------------------------------------------------------
#  Diagnosis
# ---------------------------------------------------------------------------

class Problem:
    """One problem and its fix. `blocking` problems stop the bind."""

    __slots__ = ("target", "summary", "fix", "blocking")

    def __init__(self, target, summary, fix, blocking=False):
        self.target = target
        self.summary = summary
        self.fix = fix
        self.blocking = blocking

    def line(self):
        return "%s: %s" % (self.target, self.summary)

    def full(self):
        return "%s: %s  ->  %s" % (self.target, self.summary, self.fix)


def _has_delta(obj):
    if any(abs(v) > 1e-6 for v in obj.delta_location):
        return True
    if any(abs(v) > 1e-6 for v in obj.delta_rotation_euler):
        return True
    if any(abs(v - 1.0) > 1e-6 for v in obj.delta_scale):
        return True
    quaternion = obj.delta_rotation_quaternion
    return abs(quaternion.w - 1.0) > 1e-6 or any(abs(v) > 1e-6 for v in quaternion[1:])


def _unapplied_transform(obj):
    """`(rotated, scaled, mirrored)` for an object's own transform."""
    matrix = obj.matrix_world
    rotated = abs(abs(matrix.to_quaternion().w) - 1.0) > 1e-5
    scaled = any(abs(v - 1.0) > 1e-4 for v in matrix.to_scale())
    return rotated, scaled, matrix.determinant() < 0.0


def diagnose(armature, meshes, bones, context=None):
    """Everything wrong with this bind, blockers first. Mesh checks run in Rust."""
    out = []

    if armature is None:
        out.append(Problem("Selection", "no armature is active or selected",
                           "Click the armature so it is the active object, then select the meshes "
                           "as well", blocking=True))
        return out

    if not bones:
        out.append(Problem(armature.name,
                           "no bones are selected, so there is nothing to bind to",
                           "Select the deforming bones - in Pose Mode, Edit Mode or the Outliner. "
                           "Use Select Deforming Bones, or disable Selected Bones Only", blocking=True))
    if not meshes:
        out.append(Problem("Selection", "no mesh is selected",
                           "Select the meshes to bind as well as the armature. The viewport or "
                           "the Outliner, either works", blocking=True))

    # the bind uses world space, but exporters write local space
    rotated, scaled, mirrored = _unapplied_transform(armature)
    if rotated or scaled or mirrored:
        parts = [word for word, flag in (("a rotation", rotated), ("a scale", scaled),
                                         ("a negative scale", mirrored)) if flag]
        out.append(Problem(armature.name,
                           "the armature object carries %s in its object transform"
                           % " and ".join(parts),
                           "Check the object transform before binding. Exported .skl and .anm files are written in "
                           "the armature's own space and the game never sees this transform, so "
                           "apply it (Ctrl+A > All Transforms) if the character has to look the "
                           "same in game as here"))
    if _has_delta(armature):
        out.append(Problem(armature.name, "the armature has non-zero Delta Transforms",
                           "The bind accounts for them, but Ctrl+A does not clear them and the "
                           "exporters ignore them. Object Properties > Transform > Delta "
                           "Transform: location and rotation to 0, scale to 1"))

    short = [name for name in bones
             if armature.data.bones.get(name) is not None
             and armature.data.bones[name].length < 1e-5]
    if short:
        out.append(Problem(armature.name,
                           "%d selected bone%s has no length (%s)"
                           % (len(short), "" if len(short) == 1 else "s", ", ".join(short[:3])),
                           "A zero-length bone has no direction to deform along. Give it a tail, "
                           "or leave it out of the selection"))

    rs = native.rs()
    bindable = 0
    for obj in meshes:
        name = obj.name
        rotated, scaled, mirrored = _unapplied_transform(obj)
        if rotated or scaled:
            parts = [word for word, flag in (("a rotation", rotated), ("a scale", scaled)) if flag]
            out.append(Problem(name, "carries %s in its object transform" % " and ".join(parts),
                               "Transforms affect the bind. Apply it before EXPORTING: the .skn "
                               "exporter writes vertex positions without the object transform, so "
                               "the mesh would land in the wrong place in game. Ctrl+A > All "
                               "Transforms, or press Clean Meshes"))
        if mirrored:
            out.append(Problem(name, "has a negative (mirrored) scale",
                               "Check surface orientation before binding. The "
                               "the exporter writes the unmirrored shape. Ctrl+A > Scale, then "
                               "Shift+N in Edit Mode"))
        if _has_delta(obj):
            out.append(Problem(name, "has non-zero Delta Transforms",
                               "The bind accounts for them, but Ctrl+A does not clear them and the "
                               "exporter ignores them. Object Properties > Transform > Delta "
                               "Transform: location and rotation to 0, scale to 1"))

        vertices, triangles, edges = _buffers(obj.data)
        report = rs.inspect_mesh(vertices, triangles, edges, MERGE_DISTANCE)
        if report.triangles:
            bindable += 1
        has_keys = bool(obj.data.shape_keys
                        and len(obj.data.shape_keys.key_blocks) > 1)
        for summary, fix, blocking in report.problems(HIGH_POLY, has_keys):
            out.append(Problem(name, summary, fix, blocking))

    if meshes and not bindable:
        out.append(Problem("Selection",
                           "none of the %d selected mesh%s has a single face"
                           % (len(meshes), "" if len(meshes) == 1 else "es"),
                           "Binding requires mesh faces. Select the model's "
                           "actual mesh objects - a curve, an empty or a mesh of loose edges "
                           "cannot be bound until it is converted", blocking=True))

    out.sort(key=lambda p: not p.blocking)
    return out


def blockers(problems):
    return [p for p in problems if p.blocking]


# ---------------------------------------------------------------------------
#  Cleaning
# ---------------------------------------------------------------------------

def clean(meshes, merge=True, normals=True, degenerate=True, apply_transforms=True,
          context=None):
    """Clean the meshes. Returns `{object name: [what was done]}`.

    Order: transforms, merge, degenerate, normals (each step depends on the previous ones).
    """
    context = context or bpy.context
    done = {}
    if not meshes:
        return done

    previous_mode = context.object.mode if context.object else 'OBJECT'
    previous_active = context.view_layer.objects.active
    if context.object is not None and context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    try:
        if apply_transforms:
            for obj in meshes:
                if not any(_unapplied_transform(obj)):
                    continue
                bpy.ops.object.select_all(action='DESELECT')
                obj.select_set(True)
                context.view_layer.objects.active = obj
                try:
                    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
                    done.setdefault(obj.name, []).append("applied rotation and scale")
                except RuntimeError as error:
                    done.setdefault(obj.name, []).append("could not apply transforms: %s" % error)

        for obj in meshes:
            if not obj.data.vertices:
                continue
            bpy.ops.object.select_all(action='DESELECT')
            obj.select_set(True)
            context.view_layer.objects.active = obj
            before_v = len(obj.data.vertices)
            before_f = len(obj.data.polygons)
            bpy.ops.object.mode_set(mode='EDIT')
            try:
                bpy.ops.mesh.select_all(action='SELECT')
                if merge:
                    bpy.ops.mesh.remove_doubles(threshold=MERGE_DISTANCE)
                if degenerate:
                    bpy.ops.mesh.dissolve_degenerate(threshold=MERGE_DISTANCE)
                if normals:
                    bpy.ops.mesh.normals_make_consistent(inside=False)
            finally:
                bpy.ops.object.mode_set(mode='OBJECT')
            removed_v = before_v - len(obj.data.vertices)
            removed_f = before_f - len(obj.data.polygons)
            if removed_v:
                done.setdefault(obj.name, []).append("merged %d vertices" % removed_v)
            if removed_f:
                done.setdefault(obj.name, []).append("dissolved %d faces" % removed_f)
            if normals:
                done.setdefault(obj.name, []).append("recalculated normals")
    finally:
        bpy.ops.object.select_all(action='DESELECT')
        for obj in meshes:
            obj.select_set(True)
        if previous_active is not None:
            context.view_layer.objects.active = previous_active
            if previous_mode != 'OBJECT':
                try:
                    bpy.ops.object.mode_set(mode=previous_mode)
                except RuntimeError:
                    pass
    return done


# ---------------------------------------------------------------------------
#  The bind
# ---------------------------------------------------------------------------

from .arp_bind import bind, Result


def unbind(armature, meshes, remove_groups=True):
    from .arp_bind import unbind_mesh, options, select
    from .rebind import rig_for, RIG_KEY
    for obj in meshes:
        if obj.library or obj.data.library or (remove_groups and obj.data.users > 1):
            raise ValueError(f'{obj.name}: make mesh data local and single-user before unbinding')
    settings = options(remove_vertex_groups=remove_groups,
                       selected_bones_only=False, selected_vertices_only=False)
    selected = list(bpy.context.selected_objects)
    active = bpy.context.view_layer.objects.active
    mode = active.mode if active else 'OBJECT'
    try:
        select(meshes)
        for obj in meshes:
            try:
                obj[RIG_KEY] = rig_for(obj)
            except ValueError:
                pass
            unbind_mesh(obj, settings)
    finally:
        select(selected, active)
        if active and mode != 'OBJECT':
            bpy.ops.object.mode_set(mode=mode)
    return len(meshes)
