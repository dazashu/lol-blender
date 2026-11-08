"""`.sco` and `.scb` static meshes: import and export.

Positions, triangles, one material name per face, UVs per corner (Blender's loop layout, so no
vertex splitting).
"""

import os

import numpy as np

import bpy

from . import donor as donor_mod

#: Object-level marks written at import.
SOURCE_PATH = "riot_static_path"
CENTRAL = "riot_static_central"
IS_TEXT = "riot_static_text"

#: The name stored in the file (usually empty), written back instead of the object name.
NAME = "riot_static_name"

#: The file's material name per slot and the Blender material made for it. Blender cannot keep an
#: empty material name, so the file's name is written back while the slot holds the same material.
MATERIALS = "riot_static_materials"
MATERIAL_SLOTS = "riot_static_material_slots"

#: The file's UVs, unflipped. `1 - v` in float32 is not exactly reversible, so an unedited mesh
#: writes these instead of the flipped UV layer.
RIOT_UV = "riot_static_uv"

#: Container fields geometry does not describe (version, flags, vertex type, bounds, tail), kept
#: so an unedited mesh exports byte-identical.
CONTAINER = "riot_static_container"
TRAILING = "riot_static_trailing"


class StaticReport:
    def __init__(self):
        self.path = ""
        self.name = ""
        self.vertex_count = 0
        self.face_count = 0
        self.materials = []
        self.bytes_written = 0
        self.donor = None
        self.warnings = []

    def summary(self):
        lines = ["%s: %d vertices, %d faces, %d material%s" % (
            self.name or "(unnamed)", self.vertex_count, self.face_count,
            len(self.materials), "" if len(self.materials) == 1 else "s")]
        if self.materials:
            lines.append("materials: %s" % ", ".join(self.materials[:6]))
        if self.bytes_written:
            lines.append("wrote %d bytes" % self.bytes_written)
        note = donor_mod.describe(self.donor, used=False)
        if note:
            lines.append(note)
        lines.extend(self.warnings)
        return lines


def import_static(filepath, collection=None):
    """Read a `.sco` or `.scb` into one Blender mesh. Returns (object, StaticReport)."""
    from .. import native

    data = native.rs().StaticMesh.from_path(filepath)
    report = StaticReport()
    report.path = filepath
    report.name = data.name or os.path.splitext(os.path.basename(filepath))[0]
    report.vertex_count = data.vertex_count
    report.face_count = data.face_count

    positions = np.frombuffer(data.positions, dtype=np.float32).reshape(-1, 3)
    indices = np.frombuffer(data.indices, dtype=np.uint32).reshape(-1, 3)
    uvs = np.frombuffer(data.uvs, dtype=np.float32).reshape(-1, 2)

    mesh = bpy.data.meshes.new(report.name)
    mesh.vertices.add(len(positions))
    mesh.vertices.foreach_set("co", positions.ravel())
    mesh.loops.add(indices.size)
    mesh.loops.foreach_set("vertex_index", indices.ravel().astype(np.int32))
    mesh.polygons.add(len(indices))
    mesh.polygons.foreach_set("loop_start", np.arange(len(indices), dtype=np.int32) * 3)
    mesh.polygons.foreach_set("loop_total", np.full(len(indices), 3, dtype=np.int32))
    mesh.update(calc_edges=True)

    layer = mesh.uv_layers.new(name="UVMap")
    flipped = uvs.copy()
    flipped[:, 1] = 1.0 - flipped[:, 1]         # the same V flip the .skn importer does
    layer.data.foreach_set("uv", flipped.ravel())

    original = mesh.attributes.new(name=RIOT_UV, type='FLOAT2', domain='CORNER')
    original.data.foreach_set("vector", uvs.astype(np.float32).ravel())

    # per-vertex colours (some .scb 3.2); color_srgb so the bytes round-trip exactly
    if getattr(data, "colors", None):
        raw = np.frombuffer(data.colors, dtype=np.uint8).reshape(-1, 4)
        if len(raw) == len(positions):
            colors = mesh.color_attributes.new(name="Color", type='BYTE_COLOR', domain='POINT')
            colors.data.foreach_set("color_srgb", (raw.astype(np.float32) / 255.0).ravel())
        else:
            report.warnings.append(
                "%d colours for %d vertices, so the colour block was left out"
                % (len(raw), len(positions)))

    # one slot per distinct material name, assigned per face
    order = []
    for name in data.materials:
        if name not in order:
            order.append(name)
    report.materials = order
    for name in order:
        material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
        mesh.materials.append(material)
    slot_of = {name: i for i, name in enumerate(order)}
    if order:
        mesh.polygons.foreach_set(
            "material_index",
            np.array([slot_of[n] for n in data.materials], dtype=np.int32))

    if mesh.validate(verbose=False):
        report.warnings.append("Blender's mesh validation altered the geometry")

    obj = bpy.data.objects.new(report.name, mesh)
    (collection or bpy.context.collection).objects.link(obj)
    obj[SOURCE_PATH] = filepath
    obj[NAME] = str(data.name)
    obj[MATERIALS] = list(order)
    obj[MATERIAL_SLOTS] = [m.name if m else "" for m in mesh.materials]
    obj[CENTRAL] = list(data.central)
    obj[IS_TEXT] = bool(data.is_text)
    obj[CONTAINER] = ([float(v) for v in data.bounding_box]
                      + [float(data.version[0]), float(data.version[1]), float(data.flags),
                         float(-1 if data.vertex_type is None else data.vertex_type)])
    if data.trailing:
        obj[TRAILING] = list(data.trailing)
    return obj, report


def export_static(obj, filepath, report=None):
    """Write a mesh object as a `.sco` or `.scb`, chosen by the extension."""
    from .. import native

    report = report or StaticReport()
    report.path = filepath
    report.name = str(obj.get(NAME, obj.name))

    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        mesh.calc_loop_triangles()
        triangles = len(mesh.loop_triangles)

        positions = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", positions)

        loops = np.empty(triangles * 3, dtype=np.int32)
        mesh.loop_triangles.foreach_get("loops", loops)
        loop_vertex = np.empty(len(mesh.loops), dtype=np.int32)
        mesh.loops.foreach_get("vertex_index", loop_vertex)
        indices = loop_vertex[loops].astype(np.uint32)

        # the file's own UVs while they still match the mesh (see RIOT_UV)
        stored_uv = mesh.attributes.get(RIOT_UV)
        if stored_uv is not None and len(stored_uv.data) == len(mesh.loops):
            raw = np.empty(len(mesh.loops) * 2, dtype=np.float32)
            stored_uv.data.foreach_get("vector", raw)
            uvs = raw.reshape(-1, 2)[loops].copy()
        else:
            uv_layer = mesh.uv_layers.active
            if uv_layer is None:
                uvs = np.zeros((triangles * 3, 2), dtype=np.float32)
                report.warnings.append("no uv layer; wrote zeros")
            else:
                raw = np.empty(len(mesh.loops) * 2, dtype=np.float32)
                uv_layer.data.foreach_get("uv", raw)
                uvs = raw.reshape(-1, 2)[loops].copy()
            uvs[:, 1] = 1.0 - uvs[:, 1]

        # colours must be written whenever the header's vertex type promises them
        colors = None
        attribute = mesh.color_attributes.active_color
        if attribute is not None and attribute.domain == 'POINT':
            field = "color_srgb" if attribute.data_type == 'BYTE_COLOR' else "color"
            raw = np.empty(len(mesh.vertices) * 4, dtype=np.float32)
            attribute.data.foreach_get(field, raw)
            colors = np.clip(raw.reshape(-1, 4) * 255.0 + 0.5, 0, 255).astype(np.uint8)

        slots = np.empty(triangles, dtype=np.int32)
        mesh.loop_triangles.foreach_get("material_index", slots)
        names = [m.name if m else "lambert1" for m in mesh.materials] or ["lambert1"]
        stored_files = obj.get(MATERIALS)
        stored_slots = obj.get(MATERIAL_SLOTS)
        if stored_files is not None and stored_slots is not None:
            for slot in range(min(len(names), len(stored_files), len(stored_slots))):
                if names[slot] == str(stored_slots[slot]):
                    names[slot] = str(stored_files[slot])
        materials = [names[min(int(s), len(names) - 1)] for s in slots]

        report.vertex_count = len(mesh.vertices)
        report.face_count = triangles
        report.materials = sorted(set(materials))
        report.donor = donor_mod.find(filepath, os.path.splitext(filepath)[1])

        central = obj.get(CENTRAL)
        central = tuple(float(v) for v in central) if central and len(central) == 3 else None

        # reuse the container fields only with the same vertex count (else the bounds are stale)
        bounds = version = vertex_type = None
        flags = 0
        stored = obj.get(CONTAINER)
        if stored is not None and len(stored) == 10 and len(mesh.vertices) == len(obj.data.vertices):
            values = [float(v) for v in stored]
            bounds = tuple(values[0:6])
            version = (int(values[6]), int(values[7]))
            flags = int(values[8])
            vertex_type = None if int(values[9]) < 0 else int(values[9])

        if vertex_type is not None and vertex_type != 0 and colors is None:
            report.warnings.append(
                "the source carried a per-vertex colour block and this mesh no longer has one, so "
                "the vertex type was written as plain rather than promising colours that are "
                "not in the file")
            vertex_type = None

        raw_tail = obj.get(TRAILING)
        trailing = bytes(int(v) & 0xFF for v in raw_tail) if raw_tail else None

        report.bytes_written = native.rs().write_static_mesh(
            filepath, report.name, positions.tobytes(), indices.tobytes(),
            uvs.astype(np.float32).tobytes(), materials, central,
            bounds, version, flags, vertex_type,
            colors.tobytes() if colors is not None else None, trailing)
    finally:
        evaluated.to_mesh_clear()
    return report
