"""`.skn` export.

A `.skn` vertex has one UV and one normal, so vertices on seams or sharp edges are split
automatically (and reported). The split keeps vertex order, so an unedited mesh exports
byte-identical. This module also builds the influence table the `.skl` stores, which is why the
`.skn` is written first.
"""

import os

import numpy as np

import bpy

from . import convert
from . import donor as donor_mod
from . import registry as reg
from . import skn_in

#: The file's own normals (`skn_in.RIOT_NORMAL`) are written by default, since Blender may alter
#: them. A difference from Blender's shading above this is reported.
NORMAL_DRIFT_REPORT = 1e-3

#: Influences per `.skn` vertex.
MAX_INFLUENCES_PER_VERTEX = 4

#: Influence table size (u8 index).
MAX_INFLUENCES = 256

MAX_VERTICES_PER_SUBMESH = 65535


def _pack_submesh_indices(chunks, preserve_relative=False):
    """Validate local indices before uint16 conversion; never silently wrap them."""
    total = sum(len(chunk["positions"]) for chunk in chunks)
    relative = total > MAX_VERTICES_PER_SUBMESH or preserve_relative
    buffers = []
    for chunk in chunks:
        count = len(chunk["positions"])
        if count > MAX_VERTICES_PER_SUBMESH:
            raise ValueError(
                "submesh %r has %d vertices after UV/normal splits; max allowed: "
                "65535 per submesh. Split this material before SKN export"
                % (chunk["name"], count))
        indices = np.asarray(chunk["indices"])
        if (indices.ndim != 1 or indices.size % 3
                or not np.issubdtype(indices.dtype, np.integer)
                or (indices.size and (indices.min() < 0 or indices.max() >= count))):
            raise ValueError("submesh %r has invalid triangle indices" % chunk["name"])
        buffers.append(indices.astype(np.int64) + (0 if relative else chunk["base"]))
    if not buffers:
        raise ValueError("no submesh geometry to export")
    return np.concatenate(buffers).astype(np.uint16), relative


class MeshExportReport:
    def __init__(self):
        self.path = ""
        self.vertex_count = 0
        self.triangle_count = 0
        self.submeshes = []
        self.split_vertices = 0
        self.dropped_influences = 0
        self.largest_dropped_weight = 0.0
        self.unweighted = 0
        self.donor = None
        self.bytes_written = 0
        self.warnings = []

    def summary(self):
        lines = ["wrote %d vertices, %d triangles, %d submeshes (%d bytes)" % (
            self.vertex_count, self.triangle_count, len(self.submeshes), self.bytes_written)]
        note = donor_mod.describe(self.donor, used=False)
        if note:
            lines.append(note)
        if self.split_vertices:
            lines.append(
                "%d vertices were split because they carried more than one uv or normal; a .skn "
                "stores one of each" % self.split_vertices)
        else:
            lines.append("no vertex needed splitting")
        if self.dropped_influences:
            lines.append(
                "%d vertices had more than %d influences; the smallest were dropped and the rest "
                "renormalised (largest weight thrown away: %.4f)"
                % (self.dropped_influences, MAX_INFLUENCES_PER_VERTEX, self.largest_dropped_weight))
        if self.unweighted:
            lines.append(
                "%d vertices have no weight at all and will collapse to the rig's origin in game"
                % self.unweighted)
        lines.extend(self.warnings)
        return lines


def collect_meshes(armature):
    """The meshes bound to this armature, in submesh order (`riot_submesh_index`)."""
    found = []
    for obj in bpy.data.objects:
        if obj.type != 'MESH':
            continue
        if not any(m.type == 'ARMATURE' and m.object is armature for m in obj.modifiers):
            continue
        found.append(obj)
    return sorted(found, key=lambda o: (int(o.get(skn_in.SUBMESH_INDEX, 1 << 30)), o.name))


def _corner_arrays(mesh_object, depsgraph=None, use_blender_normals=False):
    """Per-loop arrays from the evaluated mesh, with the armature modifier turned off.

    A `.skn` stores the bind mesh, so the pose must not be applied. Other modifiers stay on.
    """
    depsgraph = depsgraph or bpy.context.evaluated_depsgraph_get()

    disabled = [m for m in mesh_object.modifiers if m.type == 'ARMATURE' and m.show_viewport]
    for modifier in disabled:
        modifier.show_viewport = False
    evaluated = None
    try:
        if disabled:
            depsgraph.update()
        evaluated = mesh_object.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        mesh.calc_loop_triangles()
        triangles = len(mesh.loop_triangles)
        loops = np.empty(triangles * 3, dtype=np.int32)
        mesh.loop_triangles.foreach_get("loops", loops)

        vertex_index = np.empty(len(mesh.loops), dtype=np.int32)
        mesh.loops.foreach_get("vertex_index", vertex_index)

        uv_layer = mesh.uv_layers.active
        if uv_layer is None:
            uvs = np.zeros((len(mesh.loops), 2), dtype=np.float32)
        else:
            uvs = np.empty(len(mesh.loops) * 2, dtype=np.float32)
            uv_layer.data.foreach_get("uv", uvs)
            uvs = uvs.reshape(-1, 2).copy()
        # undo the import V flip
        uvs[:, 1] = 1.0 - uvs[:, 1]

        normals = np.empty(len(mesh.loops) * 3, dtype=np.float32)
        mesh.corner_normals.foreach_get("vector", normals)
        normals = normals.reshape(-1, 3).copy()

        # prefer the file's own normals (see NORMAL_DRIFT_REPORT)
        normal_drift = 0.0
        stored = None if use_blender_normals else mesh.attributes.get(skn_in.RIOT_NORMAL)
        if stored is not None and len(stored.data) == len(mesh.vertices):
            per_vertex = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
            stored.data.foreach_get("vector", per_vertex)
            candidate = per_vertex.reshape(-1, 3)[vertex_index]
            normal_drift = float(np.abs(candidate - normals).max()) if len(normals) else 0.0
            normals = candidate.copy()

        positions = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", positions)
        positions = positions.reshape(-1, 3).copy()

        colors = None
        attribute = mesh.color_attributes.active_color
        if attribute is not None and attribute.domain == 'POINT':
            raw = np.empty(len(mesh.vertices) * 4, dtype=np.float32)
            # color_srgb, like the importer
            field = "color_srgb" if attribute.data_type == 'BYTE_COLOR' else "color"
            attribute.data.foreach_get(field, raw)
            colors = np.clip(raw.reshape(-1, 4) * 255.0 + 0.5, 0, 255).astype(np.uint8)

        blend_order = None
        stored_order = mesh.attributes.get(skn_in.BLEND_ORDER)
        if stored_order is not None and len(stored_order.data) == len(mesh.vertices):
            packed = np.empty(len(mesh.vertices), dtype=np.int32)
            stored_order.data.foreach_get("value", packed)
            unsigned = packed.astype(np.uint32)
            blend_order = np.stack([(unsigned >> shift) & 0xFF
                                    for shift in (0, 8, 16, 24)], axis=1).astype(np.int64)

        material_index = np.empty(len(mesh.loop_triangles), dtype=np.int32)
        mesh.loop_triangles.foreach_get("material_index", material_index)

        materials = [m.name if m else "" for m in mesh.materials]
        submesh_names = []
        for slot, material in enumerate(mesh.materials):
            name = None
            if material is not None:
                name = material.get(skn_in.SUBMESH_NAME)
            submesh_names.append(str(name) if name else (materials[slot] or mesh_object.name))

        weights = _weights(evaluated, mesh)
        return dict(
            triangle_loops=loops, loop_vertex=vertex_index, uvs=uvs, normals=normals,
            positions=positions, colors=colors, weights=weights,
            triangle_material=material_index, submesh_names=submesh_names,
            normal_drift=normal_drift, blend_order=blend_order)
    finally:
        try:
            if evaluated is not None:
                evaluated.to_mesh_clear()
        finally:
            for modifier in disabled:
                modifier.show_viewport = True
            if disabled:
                depsgraph.update()


def _weights(mesh_object, mesh):
    """{vertex index: [(group name, weight), ...]} for every weighted vertex."""
    groups = mesh_object.vertex_groups
    names = [g.name for g in groups]
    out = {}
    for vertex in mesh.vertices:
        entries = [(names[e.group], e.weight)
                   for e in vertex.groups
                   if e.group < len(names) and e.weight > 0.0]
        if entries:
            out[vertex.index] = entries
    return out


def _split(data, triangle_mask=None):
    """Per-loop attributes to `.skn` vertices. Returns (unique key rows, per-loop index).

    The key is (vertex index, u, v, nx, ny, nz); the vertex index first keeps the order.
    """
    loops = data["triangle_loops"]
    if triangle_mask is not None:
        loops = loops.reshape(-1, 3)[triangle_mask].reshape(-1)
    key = np.concatenate([
        data["loop_vertex"][loops][:, None].astype(np.float64),
        data["uvs"][loops].astype(np.float64),
        data["normals"][loops].astype(np.float64),
    ], axis=1)
    unique, inverse = np.unique(key, axis=0, return_inverse=True)
    return unique, inverse.astype(np.int64)


def build_submeshes(mesh_object, influence_of, report, depsgraph=None,
                    use_blender_normals=False):
    """The submeshes this object contributes: one per used material slot."""
    data = _corner_arrays(mesh_object, depsgraph, use_blender_normals)
    if data["normal_drift"] > NORMAL_DRIFT_REPORT:
        report.warnings.append(
            "%s: Blender's shading differs from the file's normals by %.4g and the FILE's were "
            "written; tick 'Use Blender normals' if that mesh was reshaded on purpose"
            % (mesh_object.name, data["normal_drift"]))
    slots = data["triangle_material"]
    used = sorted(set(int(v) for v in slots)) or [0]

    if len(used) <= 1:
        # Separated submeshes are named by their object. Preserve the file name
        # only while the imported Blender name is unchanged (Blender may have
        # truncated/uniquified it). Duplicating copies ID properties, so a copied
        # riot_submesh_name must not override the duplicate's authored name.
        stored = mesh_object.get(skn_in.SUBMESH_NAME)
        imported_name = mesh_object.get(skn_in.SUBMESH_OBJECT_NAME, stored)
        hint = (str(stored) if mesh_object.name == imported_name else mesh_object.name) if stored else (
            data["submesh_names"][used[0]]
            if used and used[0] < len(data["submesh_names"]) else mesh_object.name)
        return [_one_submesh(mesh_object, data, None, influence_of, report,
                             name_hint=hint)]

    out = []
    for slot in used:
        mask = slots == slot
        name = data["submesh_names"][slot] if slot < len(data["submesh_names"]) else None
        out.append(_one_submesh(mesh_object, data, mask, influence_of, report, name_hint=name))
    return out


def _one_submesh(mesh_object, data, triangle_mask, influence_of, report, name_hint=None):
    """One submesh's worth of buffers, in LEAGUE space."""
    unique, per_loop = _split(data, triangle_mask)

    source_vertex = unique[:, 0].astype(np.int64)
    count = len(unique)
    report.split_vertices += count - len(np.unique(source_vertex))

    positions = data["positions"][source_vertex]
    uvs = unique[:, 1:3].astype(np.float32)
    normals = unique[:, 3:6].astype(np.float32)

    # Blender -> League: (x, y, z) -> (-x, z, -y), the inverse of convert.P
    def to_league(array):
        out = np.empty_like(array)
        out[:, 0] = -array[:, 0]
        out[:, 1] = array[:, 2]
        out[:, 2] = -array[:, 1]
        return out

    positions = to_league(positions)
    normals = to_league(normals)

    blend_indices = np.zeros((count, 4), dtype=np.uint16)
    blend_weights = np.zeros((count, 4), dtype=np.float32)
    weights = data["weights"]
    original_order = data.get("blend_order")
    for row, vertex in enumerate(source_vertex):
        entries = weights.get(int(vertex))
        if not entries:
            report.unweighted += 1
            continue
        usable = [(w, influence_of[n]) for n, w in entries if n in influence_of]
        if not usable:
            report.unweighted += 1
            continue
        # the file's slot order when still valid, else heaviest first
        placed = False
        if original_order is not None and len(usable) <= MAX_INFLUENCES_PER_VERTEX:
            wanted = [int(v) for v in original_order[int(vertex)]]
            by_influence = {}
            for weight, influence in usable:
                by_influence.setdefault(influence, weight)
            if set(by_influence) <= set(wanted):
                seen = set()
                ordered = []
                for influence in wanted:
                    if influence in by_influence and influence not in seen:
                        ordered.append((by_influence[influence], influence))
                        seen.add(influence)
                    else:
                        ordered.append((0.0, influence if influence not in seen else 0))
                usable = ordered[:MAX_INFLUENCES_PER_VERTEX]
                placed = True

        if not placed:
            usable.sort(key=lambda e: -e[0])
        rescale = 1.0
        if not placed and len(usable) > MAX_INFLUENCES_PER_VERTEX:
            dropped = sum(w for w, _ in usable[MAX_INFLUENCES_PER_VERTEX:])
            report.dropped_influences += 1
            report.largest_dropped_weight = max(report.largest_dropped_weight, dropped)
            usable = usable[:MAX_INFLUENCES_PER_VERTEX]
            # renormalise only when something was dropped, so unedited weights stay exact
            total = sum(w for w, _ in usable)
            rescale = (1.0 / total) if total > 0.0 else 1.0
        for slot, (weight, influence) in enumerate(usable):
            blend_indices[row, slot] = influence
            blend_weights[row, slot] = weight * rescale

    colors = None
    if data["colors"] is not None:
        colors = data["colors"][source_vertex]

    name = name_hint or mesh_object.name
    return dict(
        name=str(name),
        positions=positions, normals=normals, uvs=uvs,
        blend_indices=blend_indices, blend_weights=blend_weights,
        colors=colors, indices=per_loop)


def influence_table(armature, meshes, report):
    """(names in influence order, {name: index}).

    Keeps the imported table and appends any new weighted bone, so existing indices stay valid.
    """
    registry = reg.read_registry(armature)
    order = [int(h) for h in armature.get(reg.JOINT_ORDER, [])]
    stored = [int(i) for i in armature.get(reg.INFLUENCES, [])]

    names = []
    if stored and order:
        for index in stored:
            if 0 <= index < len(order):
                name = registry.get(order[index])
                if name is not None:
                    names.append(name)

    weighted = set()
    for mesh_object in meshes:
        weighted |= {g.name for g in mesh_object.vertex_groups}
    weighted &= set(armature.data.bones.keys())

    missing = sorted(weighted - set(names))
    if missing:
        if names:
            report.warnings.append(
                "%d weighted bones were not in the imported influence table and were appended: %s"
                % (len(missing), ", ".join(missing[:6])))
        names.extend(missing)

    if len(names) > 65536:
        raise ValueError("the skeleton exceeds the u16 influence-table limit")
    return names, {name: i for i, name in enumerate(names)}


def build(armature, meshes, target_path, report=None, use_blender_normals=False):
    """Everything a `.skn` needs. Returns (kwargs, influence_names, report)."""
    report = report or MeshExportReport()
    if not meshes:
        raise ValueError("no mesh is bound to %r, so there is nothing to export" % armature.name)

    from . import promote
    promotion = promote.promote(armature)
    if promotion.refused:
        raise ValueError("Cannot export skeleton identities: " + "; ".join(
            "%s: %s" % item for item in promotion.refused))
    names, influence_of = influence_table(armature, meshes, report)
    depsgraph = bpy.context.evaluated_depsgraph_get()

    ranges, chunks = [], []
    vertex_start = index_start = 0
    for mesh_object in meshes:
        for chunk in build_submeshes(
                mesh_object, influence_of, report, depsgraph, use_blender_normals):
            count = len(chunk["positions"])
            index_count = len(chunk["indices"])
            ranges.append((chunk["name"], vertex_start, count, index_start, index_count))
            chunk["base"] = vertex_start
            chunks.append(chunk)
            report.submeshes.append((chunk["name"], count, index_count // 3))
            vertex_start += count
            index_start += index_count

    def stack(key):
        return np.concatenate([c[key] for c in chunks], axis=0)

    indices, normalized = _pack_submesh_indices(
        chunks, preserve_relative=any(int(m.get(skn_in.SKN_FLAGS, 0)) & 2 for m in meshes))

    has_colors = all(c["colors"] is not None for c in chunks)
    report.vertex_count = vertex_start
    report.triangle_count = len(indices) // 3
    report.donor = donor_mod.find(target_path, ".skn")

    source = next((m.get(skn_in.SKN_VERSION) for m in meshes if m.get(skn_in.SKN_VERSION)), None)
    major, minor = (int(source[0]), int(source[1])) if source else (4, 1)
    flags = int(next((m.get(skn_in.SKN_FLAGS) for m in meshes
                      if m.get(skn_in.SKN_FLAGS)), 0) or 0)
    from .. import native
    packed_weights, skinning_block = native.rs().pack_skin_weights(
        [(r[1], r[2]) for r in ranges], stack("blend_indices").astype(np.uint16).tobytes())
    flags = (flags & ~3) | (2 if normalized else 0) | (1 if skinning_block else 0)
    if flags & 3:
        major, minor = 4, 1

    # tangents are not kept (they go stale when vertices move)
    stored_type = next((m.get(skn_in.SKN_VERTEX_TYPE) for m in meshes
                        if m.get(skn_in.SKN_VERTEX_TYPE)), None)
    if stored_type == "Tangent":
        report.warnings.append(
            "the source carried per-vertex tangents, which are not kept - it is written as a "
            "colour layout instead")
    # not bytes(idprop): an ID property array reads as int32, 4 bytes per value
    stored_trailing = next((m.get(skn_in.SKN_TRAILING) for m in meshes
                            if m.get(skn_in.SKN_TRAILING)), None)
    trailing = bytes(int(v) & 0xFF for v in stored_trailing) if stored_trailing else None

    # every game v4 .skn ends with 12 zero bytes; a file missing them crashes the client
    if trailing is None and major == 4:
        trailing = bytes(12)
        report.warnings.append(
            "this mesh had no end tab - the 12 zero bytes every v4 .skn ends with were missing, "
            "which crashes the game on load. They have been written back")

    # the file's bounds while the geometry still fits them; a stale sphere makes the mesh vanish
    bounds = None
    stored_bounds = next((m.get(skn_in.SKN_BOUNDS) for m in meshes
                          if m.get(skn_in.SKN_BOUNDS)), None)
    if stored_bounds is not None and len(stored_bounds) == 10:
        values = [float(v) for v in stored_bounds]
        every = stack("positions")
        low, high = every.min(axis=0), every.max(axis=0)
        drift = max(float(np.abs(low - np.array(values[0:3])).max()),
                    float(np.abs(high - np.array(values[3:6])).max()))
        if drift <= 1e-4:
            bounds = tuple(values)
        else:
            report.warnings.append(
                "the mesh no longer fits its stored bounds (off by %.4g), so the bounding sphere "
                "was recomputed and will not match the original byte for byte" % drift)

    return {
        "ranges": ranges,
        "positions": stack("positions").astype(np.float32).tobytes(),
        "normals": stack("normals").astype(np.float32).tobytes(),
        "uvs": stack("uvs").astype(np.float32).tobytes(),
        "blend_indices": packed_weights,
        "skinning_block": skinning_block,
        "blend_weights": stack("blend_weights").astype(np.float32).tobytes(),
        "indices": indices.tobytes(),
        "colors": stack("colors").astype(np.uint8).tobytes() if has_colors else None,
        "trailing": trailing,
        "bounds": bounds,
        "major": major,
        "minor": minor,
        "flags": flags,
    }, names, report


def export_skn(armature, filepath, meshes=None, report=None, use_blender_normals=False):
    """Write the armature's meshes as a `.skn`. Returns (influence_names, MeshExportReport)."""
    from .. import native

    meshes = meshes if meshes is not None else collect_meshes(armature)
    kwargs, names, report = build(armature, meshes, filepath, report, use_blender_normals)
    report.path = filepath
    report.bytes_written = native.rs().write_skinned_mesh(filepath, **kwargs)
    return names, report
