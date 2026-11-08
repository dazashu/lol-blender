"""Build Blender meshes from a `.skn`, optionally bound to an armature.

Separated (default): one object per submesh under an Empty. Combined: one mesh with a material slot
per submesh. The submesh order is stamped so export keeps the file's order. Buffers go through
numpy and `foreach_set`, with no per-vertex Python loop.
"""

import numpy as np

import bpy

from . import legacy
from . import registry as reg

#: Object-level properties used by the exporter.
SUBMESH_INDEX = "riot_submesh_index"     # position of this submesh in the FILE's order
SUBMESH_NAME = "riot_submesh_name"       # the name a bin material override keys on
SUBMESH_OBJECT_NAME = "riot_submesh_object_name"
SKN_PATH = "riot_skn_path"
SKN_VERSION = "riot_skn_version"
SKN_VERTEX_TYPE = "riot_skn_vertex_type"
#: The v4 header flags word, written back as read.
SKN_FLAGS = "riot_skn_flags"
SKN_TRAILING = "riot_skn_trailing"     # the opaque end-tab bytes, kept for round trips

#: The file's own per-vertex normals. Blender's custom normal storage is lossy, so the exporter
#: writes these back for an unedited mesh.
RIOT_NORMAL = "riot_normal"

#: The file's bounding box and sphere (Riot's sphere cannot be recomputed exactly).
SKN_BOUNDS = "riot_skn_bounds"

#: The file's influence slot order per vertex (four u8 in one int). Vertex groups have no order,
#: so this keeps an unedited mesh byte-identical.
BLEND_ORDER = "riot_blend_order"
SUBMESH_ORDER = "riot_submesh_order"     # on the container: submesh names in file order

#: Report vertex weight sums further than this from 1.0.
WEIGHT_SUM_REPORT_THRESHOLD = 1e-3


class MeshReport:
    """What the mesh importer measured."""

    def __init__(self):
        self.vertex_count = 0
        self.triangle_count = 0
        self.submesh_count = 0
        self.separated = False
        self.vertex_type = ""
        self.version = (0, 0)
        self.weight_sum_error = 0.0
        self.normal_length_error = 0.0
        self.has_unweighted_vertices = False
        self.unbound = False
        self.influence_overflow = None
        self.validation_changed = False
        self.objects = []

    def summary(self):
        lines = [
            "skn v%d.%d %s: %d vertices, %d triangles, %d submesh%s (%s)" % (
                self.version[0], self.version[1], self.vertex_type,
                self.vertex_count, self.triangle_count, self.submesh_count,
                "" if self.submesh_count == 1 else "es",
                "separated" if self.separated else "combined"),
        ]
        if self.unbound:
            lines.append("no skeleton bound - vertex groups and the armature modifier were skipped")
        if self.influence_overflow is not None:
            lines.append(
                "MESH AND SKELETON DO NOT MATCH: a vertex references influence %d but the "
                "skeleton lists only %d. Weights were not applied."
                % self.influence_overflow)
        if self.weight_sum_error > WEIGHT_SUM_REPORT_THRESHOLD:
            lines.append(
                "worst vertex weight sum is off by %.6g; left as authored rather than renormalised"
                % self.weight_sum_error)
        if self.has_unweighted_vertices:
            lines.append(
                "some vertices have all four weights at zero - they will collapse onto the "
                "armature origin once bound")
        if self.normal_length_error > 1e-3:
            lines.append("worst normal is %.6g from unit length" % self.normal_length_error)
        if self.validation_changed:
            lines.append(
                "Blender's mesh validation CHANGED the geometry - the index buffer contains "
                "something it considers invalid (degenerate or duplicate faces)")
        return lines


def _arrays(mesh_data):
    """The native buffers as numpy views (no copy)."""
    return {
        "positions": np.frombuffer(mesh_data.positions, dtype=np.float32).reshape(-1, 3),
        "normals": np.frombuffer(mesh_data.normals, dtype=np.float32).reshape(-1, 3),
        "uvs": np.frombuffer(mesh_data.uvs, dtype=np.float32).reshape(-1, 2),
        "indices": (np.frombuffer(mesh_data.absolute_indices, dtype=np.uint32).astype(np.int32)
                    if hasattr(mesh_data, "absolute_indices") else
                    np.frombuffer(mesh_data.indices, dtype=np.uint16).astype(np.int32)),
        "blend_indices": (np.frombuffer(mesh_data.resolved_blend_indices, dtype=np.uint16).reshape(-1, 4)
                          if hasattr(mesh_data, "resolved_blend_indices") else
                          np.frombuffer(mesh_data.blend_indices, dtype=np.uint8).reshape(-1, 4)),
        "blend_weights": np.frombuffer(mesh_data.blend_weights, dtype=np.float32).reshape(-1, 4),
        "colors": (
            np.frombuffer(mesh_data.colors, dtype=np.uint8).reshape(-1, 4)
            if mesh_data.colors is not None else None),
    }


def _build_mesh_datablock(name, positions, normals, uvs, colors, indices, report,
                          blend_indices=None):
    """One Blender mesh from flat arrays. `indices` are vertex indices, three per triangle."""
    mesh = bpy.data.meshes.new(name)
    vertex_count = len(positions)
    loop_count = len(indices)
    face_count = loop_count // 3

    mesh.vertices.add(vertex_count)
    mesh.vertices.foreach_set("co", positions.ravel())

    mesh.loops.add(loop_count)
    mesh.loops.foreach_set("vertex_index", indices)

    mesh.polygons.add(face_count)
    mesh.polygons.foreach_set("loop_start", np.arange(0, loop_count, 3, dtype=np.int32))
    mesh.polygons.foreach_set("loop_total", np.full(face_count, 3, dtype=np.int32))

    mesh.update(calc_edges=True)

    # per-vertex UVs gathered per loop; V is flipped (League's origin is the other corner)
    loop_uv = uvs[indices].astype(np.float32)
    loop_uv[:, 1] = 1.0 - loop_uv[:, 1]
    mesh.uv_layers.new(name="UVMap").data.foreach_set("uv", loop_uv.ravel())

    if colors is not None:
        layer = mesh.color_attributes.new(name="Color", type='BYTE_COLOR', domain='POINT')
        # color_srgb: `.color` converts to linear and loses a bit per channel
        layer.data.foreach_set("color_srgb", (colors.astype(np.float32) / 255.0).ravel())

    mesh.normals_split_custom_set_from_vertices(normals.astype(np.float32).tolist())

    # and the originals, see RIOT_NORMAL
    attribute = mesh.attributes.new(name=RIOT_NORMAL, type='FLOAT_VECTOR', domain='POINT')
    attribute.data.foreach_set("vector", normals.astype(np.float32).ravel())

    if blend_indices is not None and (not blend_indices.size or blend_indices.max() <= 255):
        packed = (blend_indices[:, 0].astype(np.int32)
                  | (blend_indices[:, 1].astype(np.int32) << 8)
                  | (blend_indices[:, 2].astype(np.int32) << 16)
                  | (blend_indices[:, 3].astype(np.int32) << 24))
        order = mesh.attributes.new(name=BLEND_ORDER, type='INT', domain='POINT')
        order.data.foreach_set("value", packed.ravel())

    # validate() returns True when it changed the geometry
    if mesh.validate(verbose=False):
        report.validation_changed = True

    return mesh


def _apply_weights(obj, influence_names, blend_indices, blend_weights, vertex_count):
    """One vertex group per influence, filled in batches of equal (influence, weight)."""
    groups = [obj.vertex_groups.new(name=name) for name in influence_names]

    for slot in range(4):
        weights = blend_weights[:, slot]
        active = np.nonzero(weights > 0.0)[0]
        if len(active) == 0:
            continue

        influences = blend_indices[active, slot].astype(np.int32)
        values = weights[active]

        order = np.lexsort((values, influences))
        influences, values, vertices = influences[order], values[order], active[order]
        boundaries = np.nonzero((np.diff(influences) != 0) | (np.diff(values) != 0))[0] + 1

        for chunk in np.split(np.arange(len(vertices)), boundaries):
            if len(chunk) == 0:
                continue
            group_index = int(influences[chunk[0]])
            if group_index >= len(groups):
                continue  # already reported as an influence overflow
            groups[group_index].add(
                vertices[chunk].tolist(), float(values[chunk[0]]), 'ADD')


def build_meshes(mesh_data, armature_object=None, skeleton=None, name="Mesh",
                 separate_submeshes=True, source_path="", collection=None):
    """Create objects from a native `Mesh`. Returns (objects, MeshReport).

    `separate_submeshes` is ignored with a single submesh.
    """
    report = MeshReport()
    report.vertex_count = mesh_data.vertex_count
    report.triangle_count = mesh_data.triangle_count
    report.submesh_count = len(mesh_data.submeshes)
    report.vertex_type = mesh_data.vertex_type
    report.version = (mesh_data.major, mesh_data.minor)
    report.weight_sum_error = mesh_data.weight_sum_error
    report.normal_length_error = mesh_data.normal_length_error
    report.has_unweighted_vertices = mesh_data.has_unweighted_vertices

    separated = bool(separate_submeshes) and len(mesh_data.submeshes) > 1
    report.separated = separated

    influence_names = _influence_names(mesh_data, armature_object, skeleton, report)
    arrays = _arrays(mesh_data)
    collection = collection or bpy.context.collection

    if separated:
        objects = _build_separated(
            mesh_data, arrays, name, influence_names, collection, report)
    else:
        objects = _build_combined(
            mesh_data, arrays, name, influence_names, collection, report)

    for obj in objects:
        obj[SKN_PATH] = source_path
        if source_path:
            legacy.stamp_mesh_source(obj, armature_object, source_path)
        obj[SKN_VERSION] = [mesh_data.major, mesh_data.minor]
        obj[SKN_VERTEX_TYPE] = mesh_data.vertex_type
        obj[SKN_FLAGS] = int(getattr(mesh_data, "flags", 0) or 0)
        trailing = getattr(mesh_data, "trailing", None)
        if trailing:
            obj[SKN_TRAILING] = list(trailing)
        bounds = getattr(mesh_data, "bounding_box", None)
        sphere = getattr(mesh_data, "bounding_sphere", None)
        if bounds is not None and sphere is not None:
            obj[SKN_BOUNDS] = [float(v) for v in tuple(bounds) + tuple(sphere)]

        if armature_object is None or not influence_names:
            continue

        # meshes only: the group Empty cannot hold modifiers
        if obj.type != 'MESH':
            continue

        modifier = obj.modifiers.new(name="Armature", type='ARMATURE')
        modifier.object = armature_object

        # objects already in a group keep it; the group is parented to the rig below
        if obj.parent is None:
            obj.parent = armature_object

    if armature_object is not None:
        for obj in objects:
            if obj.type == 'EMPTY' and obj.parent is None:
                obj.parent = armature_object

    report.objects = objects
    return objects, report


def _influence_names(mesh_data, armature_object, skeleton, report):
    """Bone names in influence order. A blend index points into the influence list, which
    points into the joint list."""
    if armature_object is None or skeleton is None:
        report.unbound = True
        return []

    joints = skeleton.joints
    names = []
    registry = reg.read_registry(armature_object)
    for joint_index in skeleton.influences:
        if joint_index >= len(joints):
            report.influence_overflow = (int(joint_index), len(joints))
            return []
        joint = joints[joint_index]
        # through the registry, so truncated or suffixed names still match
        names.append(registry.get(joint.hash, joint.name[:reg.MAX_BONE_NAME]))

    if mesh_data.max_blend_index >= len(names):
        report.influence_overflow = (int(mesh_data.max_blend_index), len(names))
        return []
    return names


def _build_combined(mesh_data, arrays, name, influence_names, collection, report):
    """One mesh, one material slot per submesh, each on its own face range."""
    mesh = _build_mesh_datablock(
        name, arrays["positions"], arrays["normals"], arrays["uvs"],
        arrays["colors"], arrays["indices"], report, arrays["blend_indices"])

    for order, submesh in enumerate(mesh_data.submeshes):
        material = bpy.data.materials.new(name=submesh.name)
        material[SUBMESH_INDEX] = order
        material[SUBMESH_NAME] = submesh.name
        mesh.materials.append(material)

    if len(mesh_data.submeshes) > 1:
        # each submesh owns a contiguous range of faces
        material_index = np.zeros(len(mesh.polygons), dtype=np.int32)
        for order, submesh in enumerate(mesh_data.submeshes):
            start = submesh.index_start // 3
            end = (submesh.index_start + submesh.index_count) // 3
            material_index[start:end] = order
        mesh.polygons.foreach_set("material_index", material_index)

    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    obj[SUBMESH_ORDER] = [s.name for s in mesh_data.submeshes]

    if influence_names:
        _apply_weights(
            obj, influence_names, arrays["blend_indices"], arrays["blend_weights"],
            mesh_data.vertex_count)
    return [obj]


def _build_separated(mesh_data, arrays, name, influence_names, collection, report):
    """One object per submesh under an Empty. Indices are rebased by each `vertex_start`."""
    group = bpy.data.objects.new("group_%s" % name, None)
    collection.objects.link(group)
    group[SUBMESH_ORDER] = [s.name for s in mesh_data.submeshes]

    objects = [group]
    for order, submesh in enumerate(mesh_data.submeshes):
        v0, v1 = submesh.vertex_start, submesh.vertex_start + submesh.vertex_count
        i0, i1 = submesh.index_start, submesh.index_start + submesh.index_count

        indices = arrays["indices"][i0:i1] - submesh.vertex_start
        sub_mesh = _build_mesh_datablock(
            submesh.name,
            arrays["positions"][v0:v1],
            arrays["normals"][v0:v1],
            arrays["uvs"][v0:v1],
            None if arrays["colors"] is None else arrays["colors"][v0:v1],
            indices,
            report,
            arrays["blend_indices"][v0:v1],
        )

        material = bpy.data.materials.new(name=submesh.name)
        material[SUBMESH_INDEX] = order
        material[SUBMESH_NAME] = submesh.name
        sub_mesh.materials.append(material)

        obj = bpy.data.objects.new(submesh.name, sub_mesh)
        collection.objects.link(obj)
        obj.parent = group
        obj[SUBMESH_INDEX] = order
        obj[SUBMESH_NAME] = submesh.name
        obj[SUBMESH_OBJECT_NAME] = obj.name

        if influence_names:
            _apply_weights(
                obj, influence_names,
                arrays["blend_indices"][v0:v1], arrays["blend_weights"][v0:v1],
                submesh.vertex_count)
        objects.append(obj)

    return objects


def import_skn(filepath, load_skeleton=True, separate_submeshes=True, collection=None):
    """Read a `.skn`, and by default the `.skl` with the same name beside it.

    Returns (armature_object_or_None, objects, mesh_report, skeleton_report_or_None).
    """
    import os

    from .. import native
    from . import skl_in

    rs = native.rs()
    # decode everything before creating any datablock
    mesh_data = rs.Mesh.from_path(filepath)
    base = os.path.splitext(filepath)[0]
    name = os.path.basename(base)

    armature = None
    skeleton = None
    skl_report = None
    if load_skeleton:
        skl_path = base + ".skl"
        if os.path.isfile(skl_path):
            skeleton = rs.Skeleton.from_path(skl_path)
            armature, skl_report = skl_in.build_armature(
                skeleton, name=name, source_path=skl_path, collection=collection)

    objects, mesh_report = build_meshes(
        mesh_data,
        armature_object=armature,
        skeleton=skeleton,
        name=name,
        separate_submeshes=separate_submeshes,
        source_path=filepath,
        collection=collection,
    )
    return armature, objects, mesh_report, skl_report
