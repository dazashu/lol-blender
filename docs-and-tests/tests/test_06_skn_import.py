"""Phase 2: SKN import, checked against the file's own buffers.

The load-bearing check is `test_weights_match_through_the_influence_indirection`. A `.skn` vertex's
blend index is a position in the SKELETON'S INFLUENCE LIST, and each entry there is a position in
the joint list. Skipping that indirection attaches every weight to the wrong bone, silently, and the
result looks exactly like bad weight painting rather than like a bug - so it gets a numeric test
rather than a look at the viewport.
"""

import struct

import bpy
import numpy as np

import _rsblender
from LoL_Blender.core import registry as reg
from LoL_Blender.core import skl_in, skn_in
from run import graves

_cache = {}


def _cache_is_live():
    """Have the cached datablocks survived?

    Other tests in this module wipe the scene with `read_factory_settings`, which frees the
    underlying objects and leaves the Python wrappers raising "StructRNA has been removed" on any
    attribute access. Caching an Object across a scene reset is therefore not safe, and the failure
    looks like a bug in the importer rather than in the test. So the cache is validated rather than
    trusted.
    """
    if "objects" not in _cache:
        return False
    try:
        for obj in _cache["objects"]:
            _ = obj.type
        _ = _cache["armature"].name
    except ReferenceError:
        return False
    return True


def _import_graves():
    if not _cache_is_live():
        bpy.ops.wm.read_factory_settings(use_empty=True)
        skn_path = graves("graves.skn")
        armature, objects, report, _skl_report = skn_in.import_skn(skn_path)
        _cache.update(
            armature=armature, objects=objects, report=report,
            data=_rsblender.Mesh.from_path(skn_path),
            skeleton=_rsblender.Skeleton.from_path(graves("graves.skl")),
        )
        for line in report.summary():
            print("      [report] %s" % line)
    return _cache


def test_native_reader_parses_the_mesh():
    m = _rsblender.Mesh.from_path(graves("graves.skn"))
    assert m.vertex_count > 0
    assert m.index_count % 3 == 0, "the index buffer must be whole triangles"
    assert m.triangle_count == m.index_count // 3
    assert m.submeshes, "no submeshes"
    assert m.vertex_type in ("Basic", "Color", "Tangent")

    # Buffer sizes must match the declared counts exactly; a mismatch here would be a silent
    # truncation that later shows up as missing geometry.
    assert len(m.positions) == m.vertex_count * 12
    assert len(m.normals) == m.vertex_count * 12
    assert len(m.uvs) == m.vertex_count * 8
    assert len(m.blend_indices) == m.vertex_count * 4
    assert len(m.blend_weights) == m.vertex_count * 16
    assert len(m.indices) == m.index_count * 2


def test_reader_agrees_with_lol_maya():
    """Cross-check the geometry against the independent implementation, vertex by vertex."""
    from oracle.lolmaya import read_skn

    m = _rsblender.Mesh.from_path(graves("graves.skn"))
    oracle = read_skn(graves("graves.skn"))

    assert len(oracle.vertices) == m.vertex_count
    assert len(oracle.indices) == m.index_count
    assert len(oracle.submeshes) == len(m.submeshes)

    mine_idx = np.frombuffer(m.indices, dtype=np.uint16)
    assert list(oracle.indices) == mine_idx.tolist(), "index buffers differ"

    for i, sub in enumerate(m.submeshes):
        theirs = oracle.submeshes[i]
        assert sub.name == theirs.name
        assert sub.vertex_start == theirs.vertex_start
        assert sub.vertex_count == theirs.vertex_count
        assert sub.index_start == theirs.index_start
        assert sub.index_count == theirs.index_count

    # Positions come back already converted to Blender space, so undo the conversion to compare
    # against what the Maya reader holds: (x, y, z) -> (-x, -z, y), inverted.
    mine_pos = np.frombuffer(m.positions, dtype=np.float32).reshape(-1, 3)
    mine_w = np.frombuffer(m.blend_weights, dtype=np.float32).reshape(-1, 4)
    mine_i = np.frombuffer(m.blend_indices, dtype=np.uint8).reshape(-1, 4)
    mine_uv = np.frombuffer(m.uvs, dtype=np.float32).reshape(-1, 2)

    for k, v in enumerate(oracle.vertices):
        bx, by, bz = mine_pos[k]
        assert abs(-bx - v.position.x) < 1e-4, "vertex %d x" % k
        assert abs(bz - v.position.y) < 1e-4, "vertex %d y" % k
        assert abs(-by - v.position.z) < 1e-4, "vertex %d z" % k
        assert abs(mine_uv[k][0] - v.uv.x) < 1e-6, "vertex %d u" % k
        assert abs(mine_uv[k][1] - v.uv.y) < 1e-6, "vertex %d v" % k
        for s in range(4):
            assert mine_i[k][s] == v.influences[s], "vertex %d influence %d" % (k, s)
            assert abs(mine_w[k][s] - v.weights[s]) < 1e-6, "vertex %d weight %d" % (k, s)


def test_mesh_geometry_matches_the_file():
    cache = _import_graves()
    data = cache["data"]
    mesh_objects = [o for o in cache["objects"] if o.type == 'MESH']
    assert len(mesh_objects) == 1, "graves.skn has one submesh, so combined is the only sane layout"
    mesh = mesh_objects[0].data

    assert len(mesh.vertices) == data.vertex_count
    assert len(mesh.polygons) == data.triangle_count
    assert len(mesh.loops) == data.index_count

    expected = np.frombuffer(data.positions, dtype=np.float32).reshape(-1, 3)
    got = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", got)
    assert np.abs(got.reshape(-1, 3) - expected).max() == 0.0, \
        "vertex positions must be bit-identical to the converted file data"

    expected_idx = np.frombuffer(data.indices, dtype=np.uint16).astype(np.int32)
    got_idx = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", got_idx)
    assert np.array_equal(got_idx, expected_idx), "loop vertex indices differ from the index buffer"

    assert all(len(p.vertices) == 3 for p in mesh.polygons), "every face must be a triangle"


def test_uvs_are_v_flipped():
    """League's V origin is the opposite corner, so the importer flips it (lol_maya.py:1683). This
    checks the flip happened AND that it was applied per loop through the index buffer."""
    cache = _import_graves()
    data = cache["data"]
    mesh = [o for o in cache["objects"] if o.type == 'MESH'][0].data

    uvs = np.frombuffer(data.uvs, dtype=np.float32).reshape(-1, 2)
    indices = np.frombuffer(data.indices, dtype=np.uint16).astype(np.int32)
    expected = uvs[indices].copy()
    expected[:, 1] = 1.0 - expected[:, 1]

    got = np.empty(len(mesh.loops) * 2, dtype=np.float32)
    mesh.uv_layers[0].data.foreach_get("uv", got)
    assert np.abs(got.reshape(-1, 2) - expected).max() < 1e-6

    # And prove the flip is not a no-op on this asset, so a removed flip would fail the test above.
    assert np.abs(uvs[:, 1] - 0.5).max() > 0.01, \
        "this asset's V coordinates are all near 0.5, so it cannot detect a missing flip"


def test_custom_normals_match_the_file():
    cache = _import_graves()
    data = cache["data"]
    mesh = [o for o in cache["objects"] if o.type == 'MESH'][0].data

    expected_vertex = np.frombuffer(data.normals, dtype=np.float32).reshape(-1, 3)
    indices = np.frombuffer(data.indices, dtype=np.uint16).astype(np.int32)
    expected = expected_vertex[indices]

    got = np.empty(len(mesh.loops) * 3, dtype=np.float32)
    mesh.corner_normals.foreach_get("vector", got)
    # Blender renormalises custom normals on store; the file's are unit to 2e-07 so the direction
    # is what matters. Compare as directions, not element by element.
    got = got.reshape(-1, 3)
    dots = np.einsum("ij,ij->i", got, expected / np.linalg.norm(expected, axis=1, keepdims=True))
    assert dots.min() > 1.0 - 1e-4, \
        "worst loop normal is %.6f off alignment with the file's" % (1.0 - dots.min())


def test_weights_match_through_the_influence_indirection():
    """THE phase-2 check.

    For every vertex and every one of its four slots, the weight must sit on the bone the file
    points at - reached as `influence_list[blend_index] -> joint -> bone`, not as
    `joint_list[blend_index]`.
    """
    cache = _import_graves()
    data, skeleton, armature = cache["data"], cache["skeleton"], cache["armature"]
    obj = [o for o in cache["objects"] if o.type == 'MESH'][0]

    assert armature is not None, "the .skl beside the .skn should have been loaded"

    registry = reg.read_registry(armature)
    influence_bone_names = [
        registry[skeleton.joints[j].hash] for j in skeleton.influences
    ]
    group_index_of = {g.name: g.index for g in obj.vertex_groups}
    assert len(obj.vertex_groups) == len(influence_bone_names), \
        "expected one vertex group per influence, got %d for %d" % (
            len(obj.vertex_groups), len(influence_bone_names))

    blend_indices = np.frombuffer(data.blend_indices, dtype=np.uint8).reshape(-1, 4)
    blend_weights = np.frombuffer(data.blend_weights, dtype=np.float32).reshape(-1, 4)

    checked = 0
    for vertex_index, vertex in enumerate(obj.data.vertices):
        actual = {g.group: g.weight for g in vertex.groups}
        wanted = {}
        for slot in range(4):
            weight = float(blend_weights[vertex_index][slot])
            if weight <= 0.0:
                continue
            bone_name = influence_bone_names[int(blend_indices[vertex_index][slot])]
            # Two slots CAN name the same influence; the file's own weights then add up.
            wanted[group_index_of[bone_name]] = wanted.get(group_index_of[bone_name], 0.0) + weight

        assert set(actual) == set(wanted), \
            "vertex %d is weighted to groups %r, file says %r" % (
                vertex_index, sorted(actual), sorted(wanted))
        for group, weight in wanted.items():
            assert abs(actual[group] - weight) < 1e-6, \
                "vertex %d group %d: got %r, file says %r" % (
                    vertex_index, group, actual[group], weight)
            checked += 1

    assert checked > 1000, "only checked %d weights, the test is not covering the mesh" % checked


def test_armature_modifier_and_parenting():
    cache = _import_graves()
    armature = cache["armature"]
    for obj in cache["objects"]:
        if obj.type != 'MESH':
            continue
        modifiers = [m for m in obj.modifiers if m.type == 'ARMATURE']
        assert len(modifiers) == 1, "%s has %d armature modifiers" % (obj.name, len(modifiers))
        assert modifiers[0].object is armature
        assert obj.parent is armature


def test_submesh_order_is_recorded():
    """Export has to rebuild vertex_start and index_start in the FILE's order. Inferring that from
    names breaks as soon as two submeshes sort differently than they were written."""
    cache = _import_graves()
    data = cache["data"]
    names = [s.name for s in data.submeshes]
    for obj in cache["objects"]:
        if skn_in.SUBMESH_ORDER in obj:
            assert list(obj[skn_in.SUBMESH_ORDER]) == names
    mesh = [o for o in cache["objects"] if o.type == 'MESH'][0].data
    for order, material in enumerate(mesh.materials):
        assert material[skn_in.SUBMESH_INDEX] == order
        assert material[skn_in.SUBMESH_NAME] == names[order]


def test_report_carries_the_measurements():
    cache = _import_graves()
    report = cache["report"]
    assert report.vertex_count == cache["data"].vertex_count
    assert report.triangle_count == cache["data"].triangle_count
    assert not report.unbound
    assert report.influence_overflow is None
    assert not report.validation_changed, \
        "Blender's validation changed the geometry, so the index buffer was not built correctly"
    assert report.summary()


# --------------------------------------------------------------------------------------------
# Separated mode needs a multi-submesh asset, and graves.skn has exactly one. Rather than skip the
# path entirely, a synthetic mesh with two submeshes drives the real `build_meshes` code.
# --------------------------------------------------------------------------------------------

class _FakeSubmesh:
    def __init__(self, name, vertex_start, vertex_count, index_start, index_count):
        self.name = name
        self.vertex_start = vertex_start
        self.vertex_count = vertex_count
        self.index_start = index_start
        self.index_count = index_count


class _FakeMesh:
    """Two quads, each its own submesh, with distinct UVs and weights per half."""

    def __init__(self):
        # Submesh A: vertices 0..3 at z=0. Submesh B: vertices 4..7 at z=1.
        positions = []
        for z in (0.0, 1.0):
            for x, y in ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)):
                positions += [x, y, z]
        self.positions = struct.pack("<%df" % len(positions), *positions)
        self.normals = struct.pack("<24f", *([0.0, 0.0, 1.0] * 8))
        uvs = []
        for half in range(2):
            for u, v in ((0.0, 0.0), (1.0, 0.0), (1.0, 0.9), (0.0, 0.9)):
                uvs += [u, v * (0.5 if half else 1.0)]
        self.uvs = struct.pack("<16f", *uvs)
        self.colors = None
        # Absolute indices, exactly as a .skn stores them: submesh B's indices are 4..7.
        indices = [0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7]
        self.indices = struct.pack("<12H", *indices)
        # Submesh A fully on influence 0, submesh B split between influences 1 and 2.
        bi, bw = [], []
        for k in range(8):
            if k < 4:
                bi += [0, 0, 0, 0]
                bw += [1.0, 0.0, 0.0, 0.0]
            else:
                bi += [1, 2, 0, 0]
                bw += [0.25, 0.75, 0.0, 0.0]
        self.blend_indices = bytes(bi)
        self.blend_weights = struct.pack("<32f", *bw)

        self.major, self.minor = 4, 1
        self.vertex_type = "Basic"
        self.vertex_count = 8
        self.index_count = 12
        self.triangle_count = 4
        self.submeshes = [
            _FakeSubmesh("Body", 0, 4, 0, 6),
            _FakeSubmesh("Cape", 4, 4, 6, 6),
        ]
        self.weight_sum_error = 0.0
        self.normal_length_error = 0.0
        self.max_blend_index = 2
        self.has_unweighted_vertices = False


def test_separated_mode_splits_into_one_object_per_submesh():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    fake = _FakeMesh()
    objects, report = skn_in.build_meshes(fake, name="Fake", separate_submeshes=True)

    assert report.separated
    empties = [o for o in objects if o.type == 'EMPTY']
    meshes = [o for o in objects if o.type == 'MESH']
    assert len(empties) == 1 and empties[0].name == "group_Fake"
    assert [o.name for o in meshes] == ["Body", "Cape"]

    for obj in meshes:
        assert obj.parent is empties[0]
        assert len(obj.data.vertices) == 4
        assert len(obj.data.polygons) == 2
        assert obj[skn_in.SUBMESH_NAME] == obj.name

    # The split must REBASE indices by the submesh's declared vertex_start. If it did not, Cape's
    # indices 4..7 would point outside its own 4-vertex mesh and the geometry would be wrong or
    # missing entirely.
    cape = meshes[1].data
    got = np.empty(len(cape.loops), dtype=np.int32)
    cape.loops.foreach_get("vertex_index", got)
    assert got.min() >= 0 and got.max() <= 3, \
        "Cape's indices were not rebased: %r" % got.tolist()
    assert got.tolist() == [0, 1, 2, 0, 2, 3]


def test_separated_and_combined_describe_the_same_geometry():
    """The two layouts are presentation choices, so they must not disagree about the mesh."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    fake = _FakeMesh()
    combined, _ = skn_in.build_meshes(fake, name="C", separate_submeshes=False)
    separated, _ = skn_in.build_meshes(fake, name="S", separate_submeshes=True)

    combined_mesh = combined[0].data
    parts = [o.data for o in separated if o.type == 'MESH']

    assert len(combined_mesh.vertices) == sum(len(p.vertices) for p in parts)
    assert len(combined_mesh.polygons) == sum(len(p.polygons) for p in parts)

    all_combined = np.empty(len(combined_mesh.vertices) * 3, dtype=np.float32)
    combined_mesh.vertices.foreach_get("co", all_combined)
    offset = 0
    for part in parts:
        chunk = np.empty(len(part.vertices) * 3, dtype=np.float32)
        part.vertices.foreach_get("co", chunk)
        assert np.array_equal(chunk, all_combined[offset:offset + len(chunk)]), \
            "separated vertices do not concatenate back to the combined ones"
        offset += len(chunk)


def test_combined_mode_assigns_each_submesh_its_own_faces():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    fake = _FakeMesh()
    objects, report = skn_in.build_meshes(fake, name="C", separate_submeshes=False)
    mesh = objects[0].data

    assert not report.separated
    assert [m.name for m in mesh.materials] == ["Body", "Cape"]
    got = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("material_index", got)
    assert got.tolist() == [0, 0, 1, 1], \
        "each submesh's face range must carry its own material slot: %r" % got.tolist()


def test_separate_submeshes_switches_itself_off_for_a_single_submesh():
    """lol_maya.py:73-74 does the same: a "group" holding one object is clutter, not structure."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    fake = _FakeMesh()
    fake.submeshes = [_FakeSubmesh("Only", 0, 8, 0, 12)]
    objects, report = skn_in.build_meshes(fake, name="One", separate_submeshes=True)

    assert not report.separated
    assert not [o for o in objects if o.type == 'EMPTY']
    assert len([o for o in objects if o.type == 'MESH']) == 1


def test_mismatched_skeleton_is_reported_and_weights_are_skipped():
    """A mesh whose blend indices exceed the skeleton's influence list is a mismatched pair. Better
    to say so and leave the weights off than to attach them to arbitrary bones."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    skeleton = _rsblender.Skeleton.from_path(graves("graves.skl"))
    armature, _ = skl_in.build_armature(skeleton, name="g")

    fake = _FakeMesh()
    fake.max_blend_index = 9999

    objects, report = skn_in.build_meshes(
        fake, armature_object=armature, skeleton=skeleton, name="Bad",
        separate_submeshes=False)

    assert report.influence_overflow == (9999, len(skeleton.influences))
    assert not objects[0].vertex_groups, "weights must not be applied to a mismatched skeleton"
    assert any("DO NOT MATCH" in line for line in report.summary())
