"""`.mapgeo` map geometry on the Blender side.

Export goes through the original file (the donor), which carries the scene graph and vertex
declarations. Untouched models keep the donor's bytes, edited ones are re-encoded in their own
vertex layout, deleted ones are dropped, new ones are skipped and reported.
"""

import os
import zlib

import numpy as np

import bpy
from mathutils import Matrix

from . import donor as donor_mod

#: Object-level marks. `INDEX` ties an object to the donor's model list.
SOURCE_PATH = "riot_mapgeo_path"
INDEX = "riot_mapgeo_index"
LAYER = "riot_mapgeo_layer"
RENDER_FLAGS = "riot_mapgeo_render_flags"
QUALITY = "riot_mapgeo_quality"
TWO_SIDED = "riot_mapgeo_two_sided"
#: True when the vertex buffer is shared with other copies; its geometry cannot be edited.
SHARED = "riot_mapgeo_shared"
TEXTURES = "riot_mapgeo_textures"
BAKED_LIGHT = "riot_mapgeo_baked_light"
STATIONARY_LIGHT = "riot_mapgeo_stationary_light"
#: CRC of the imported geometry, to detect edits.
CHECKSUM = "riot_mapgeo_checksum"
#: The transform as imported, to detect moves.
TRANSFORM = "riot_mapgeo_transform"
#: Collection-level.
VERSION = "riot_mapgeo_version"


class MapReport:
    def __init__(self):
        self.path = ""
        self.version = 0
        self.models = 0
        self.imported = 0
        self.vertices = 0
        self.faces = 0
        self.scene_graphs = 0
        self.reflectors = 0
        self.edited = 0
        self.removed = 0
        self.added = 0
        self.bytes_written = 0
        self.donor = None
        self.warnings = []

    def summary(self):
        lines = []
        if self.models:
            lines.append("version %d: %d models, %d imported, %d vertices, %d faces"
                         % (self.version, self.models, self.imported, self.vertices, self.faces))
        if self.scene_graphs or self.reflectors:
            lines.append("%d scene graph%s and %d planar reflector%s kept verbatim"
                         % (self.scene_graphs, "" if self.scene_graphs == 1 else "s",
                            self.reflectors, "" if self.reflectors == 1 else "s"))
        if self.bytes_written:
            lines.append("wrote %d bytes: %d model%s re-encoded, %d removed"
                         % (self.bytes_written, self.edited, "" if self.edited == 1 else "s",
                            self.removed))
        if self.donor:
            lines.append(donor_mod.describe(self.donor))
        lines.extend(self.warnings)
        return lines


def checksum(positions, indices):
    """CRC32 of the geometry, as hex text (an ID property int would overflow)."""
    return "%08x" % (zlib.crc32(bytes(indices), zlib.crc32(bytes(positions))) & 0xFFFFFFFF)


def _differs(a, b):
    """True when a matrix element changed by more than float32 noise."""
    return abs(a - b) > max(1e-4, 1e-6 * abs(a))


def _material(name):
    return bpy.data.materials.get(name) or bpy.data.materials.new(name or "mapgeo")


def import_mapgeo(filepath, collection=None, limit=0):
    """Read a `.mapgeo` into one collection of mesh objects. Returns (collection, MapReport)."""
    from .. import native

    data = native.rs().MapGeo.from_path(filepath)
    report = MapReport()
    report.path = filepath
    report.version = data.version
    report.models = len(data.models)
    report.scene_graphs = data.scene_graph_count
    report.reflectors = data.planar_reflector_count

    name = os.path.splitext(os.path.basename(filepath))[0]
    target = bpy.data.collections.new(name)
    (collection or bpy.context.scene.collection).children.link(target)
    target[SOURCE_PATH] = filepath
    target[VERSION] = int(data.version)

    for index, model in enumerate(data.models):
        if limit and index >= limit:
            report.warnings.append(
                "stopped after %d models; the file has %d" % (limit, len(data.models)))
            break

        positions = np.frombuffer(model.positions, dtype=np.float32).reshape(-1, 3)
        indices = np.frombuffer(model.indices, dtype=np.uint32).reshape(-1, 3)
        if not len(positions) or not len(indices):
            continue

        mesh = bpy.data.meshes.new(model.name)
        mesh.vertices.add(len(positions))
        mesh.vertices.foreach_set("co", positions.ravel())
        mesh.loops.add(indices.size)
        mesh.loops.foreach_set("vertex_index", indices.ravel().astype(np.int32))
        mesh.polygons.add(len(indices))
        mesh.polygons.foreach_set("loop_start", np.arange(len(indices), dtype=np.int32) * 3)
        mesh.polygons.foreach_set("loop_total", np.full(len(indices), 3, dtype=np.int32))
        mesh.update(calc_edges=True)

        corner_vertex = indices.ravel()
        for layer_name, buffer in (("UVMap", model.uvs), ("Lightmap", model.uvs2)):
            if not buffer:
                continue
            uvs = np.frombuffer(buffer, dtype=np.float32).reshape(-1, 2)
            layer = mesh.uv_layers.new(name=layer_name)
            layer.data.foreach_set("uv", uvs[corner_vertex].ravel())

        if model.colors:
            colors = np.frombuffer(model.colors, dtype=np.float32).reshape(-1, 4)
            attribute = mesh.color_attributes.new("Color", 'FLOAT_COLOR', 'POINT')
            attribute.data.foreach_set("color", colors.ravel())

        if model.normals:
            normals = np.frombuffer(model.normals, dtype=np.float32).reshape(-1, 3)
            mesh.normals_split_custom_set_from_vertices(normals)

        # one material slot per submesh face range
        for submesh_name, _start, _count in model.submeshes:
            mesh.materials.append(_material(submesh_name))
        if len(model.submeshes) > 1:
            slots = np.zeros(len(indices), dtype=np.int32)
            for slot, (_name, start, count) in enumerate(model.submeshes):
                slots[start // 3:(start + count) // 3] = slot
            mesh.polygons.foreach_set("material_index", slots)

        if mesh.validate(verbose=False):
            report.warnings.append("%s: Blender's mesh validation altered the geometry"
                                   % model.name)

        obj = bpy.data.objects.new(model.name, mesh)
        obj.matrix_world = Matrix([model.transform[i * 4:i * 4 + 4] for i in range(4)])
        target.objects.link(obj)

        obj[SOURCE_PATH] = filepath
        obj[INDEX] = index
        obj[LAYER] = int(model.layer)
        obj[RENDER_FLAGS] = int(model.render_flags)
        obj[QUALITY] = int(model.quality)
        obj[TWO_SIDED] = bool(model.disable_backface_culling)
        obj[SHARED] = bool(model.shared)
        obj[CHECKSUM] = checksum(model.positions, model.indices)
        # read back: the object stores its transform in float32
        obj[TRANSFORM] = [float(v) for row in obj.matrix_world for v in row]
        if model.texture_overrides:
            obj[TEXTURES] = ["%d:%s" % (i, path) for i, path in model.texture_overrides]
        if model.baked_light:
            obj[BAKED_LIGHT] = model.baked_light
        if model.stationary_light:
            obj[STATIONARY_LIGHT] = model.stationary_light

        report.imported += 1
        report.vertices += len(positions)
        report.faces += len(indices)

    return target, report


def _mesh_arrays(obj):
    """(positions, normals, uvs, uvs2, colors, indices, warnings), per vertex, as bytes.

    A vertex with several UVs must be split first; that is reported, not averaged.
    """
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    warnings = []
    try:
        mesh.calc_loop_triangles()
        count = len(mesh.vertices)

        positions = np.empty(count * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", positions)

        normals = np.empty(count * 3, dtype=np.float32)
        mesh.vertices.foreach_get("normal", normals)

        loops = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
        mesh.loop_triangles.foreach_get("loops", loops)
        loop_vertex = np.empty(len(mesh.loops), dtype=np.int32)
        mesh.loops.foreach_get("vertex_index", loop_vertex)
        indices = loop_vertex[loops].astype(np.uint32)

        def per_vertex_uv(layer):
            if layer is None:
                return None
            raw = np.empty(len(mesh.loops) * 2, dtype=np.float32)
            layer.data.foreach_get("uv", raw)
            raw = raw.reshape(-1, 2)
            out = np.zeros((count, 2), dtype=np.float32)
            # last corner wins; report vertices whose corners disagree
            out[loop_vertex] = raw
            if len(mesh.loops):
                first = np.zeros((count, 2), dtype=np.float32)
                first[loop_vertex[::-1]] = raw[::-1]
                if not np.allclose(first, out, atol=1e-6):
                    warnings.append("%s: some vertices carry more than one UV; split them first"
                                    % obj.name)
            return out

        layers = list(mesh.uv_layers)
        uvs = per_vertex_uv(layers[0] if layers else None)
        uvs2 = per_vertex_uv(layers[1] if len(layers) > 1 else None)

        colors = None
        attribute = mesh.color_attributes.active_color
        if attribute is not None and attribute.domain == 'POINT':
            raw = np.empty(count * 4, dtype=np.float32)
            attribute.data.foreach_get("color", raw)
            colors = raw.reshape(-1, 4)

        return (positions.tobytes(), normals.tobytes(),
                uvs.tobytes() if uvs is not None else None,
                uvs2.tobytes() if uvs2 is not None else None,
                colors.tobytes() if colors is not None else None,
                indices.tobytes(), warnings)
    finally:
        evaluated.to_mesh_clear()


def export_mapgeo(objects, filepath, donor=None, version=None, report=None):
    """Write a `.mapgeo` from these objects, through a donor. Returns a MapReport."""
    from .. import native

    report = report or MapReport()
    report.path = filepath

    sources = {obj.get(SOURCE_PATH) for obj in objects if obj.get(SOURCE_PATH)}
    if donor is None:
        donor = donor_mod.find(filepath, ".mapgeo")
    if donor is None and len(sources) == 1:
        donor = next(iter(sources))
    if donor is None:
        raise ValueError(
            "a .mapgeo export needs the original file as a donor: it carries the scene graph and "
            "the vertex declarations, and neither can be rebuilt from a Blender scene. Put "
            "riot_%s beside the target, or export a map this plugin imported"
            % os.path.basename(filepath))
    report.donor = donor if os.path.isfile(str(donor)) else None

    by_index = {}
    for obj in objects:
        if obj.type != 'MESH':
            continue
        index = obj.get(INDEX)
        if index is None:
            report.added += 1
            report.warnings.append(
                "%s is not one of the donor's models, so it was left out - a new model needs a "
                "place in the scene graph, which only the map compiler can give it" % obj.name)
            continue
        by_index[int(index)] = obj

    original = native.rs().MapGeo.from_path(donor)
    report.version = int(version or original.version)
    report.models = len(original.models)

    edits = []
    module = native.rs()
    for index in range(len(original.models)):
        obj = by_index.get(index)
        if obj is None:
            edits.append(module.ModelEdit(index, removed=True))
            report.removed += 1
            continue

        positions, normals, uvs, uvs2, colors, indices, warnings = _mesh_arrays(obj)
        report.warnings.extend(warnings)
        transform = [v for row in obj.matrix_world for v in row]

        stored = obj.get(TRANSFORM)
        moved = stored is None or any(
            _differs(float(a), float(b)) for a, b in zip(stored, transform))
        if obj.get(CHECKSUM) is not None and checksum(positions, indices) == str(obj[CHECKSUM]):
            # untouched geometry: no edit at all unless it moved
            if moved:
                edits.append(module.ModelEdit(index, transform=transform))
            continue

        edits.append(module.ModelEdit(
            index, transform=transform, positions=positions, normals=normals,
            uvs=uvs, uvs2=uvs2, colors=colors, indices=indices))
        report.edited += 1
        report.vertices += len(positions) // 12
        report.faces += len(indices) // 12

    report.bytes_written = module.write_map_geometry(
        filepath, donor, int(version) if version else None, edits)
    return report
