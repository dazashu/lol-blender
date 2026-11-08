"""Phase 5: export, checked by round trip rather than by opening the result in something.

The bar is byte equality where the format allows it and decoded equality where it does not:

  * `.skl` - import then export must reproduce the input BYTES. The importer stamps each joint's
    raw translation/quaternion/scale triple exactly so this is achievable; a matrix-only round trip
    could not be, because decomposing a matrix is not the inverse of composing one.
  * `.skn` - same, for a mesh nothing has touched.
  * `.anm` - decoded-track equality, not bytes. The exact check runs on an uncompressed export;
    the default export is compressed `r3d2canm`, which is lossy within fixed tolerances, so it is
    checked against the uncompressed export of the same pose.

Each of those has a positive control immediately after it, because "the bytes matched" only means
something if a wrong export would have made them differ.
"""

import os

import numpy as np

import bpy

import _rsblender
from LoL_Blender.core import anm_out
from LoL_Blender.core import convert
from LoL_Blender.core import donor as donor_mod
from LoL_Blender.core import registry as reg
from LoL_Blender.core import skl_out
from LoL_Blender.core import skn_out
from run import Skip, graves, graves_anim, riven, riven_anim, scratch


def _fresh_addon():
    import importlib
    import sys
    for name in [n for n in sys.modules if n.split(".")[0] == "LoL_Blender"]:
        del sys.modules[name]
    return importlib.import_module("LoL_Blender")


def _import(skn_path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon = _fresh_addon()
    addon.register()
    result = bpy.ops.lolblender.import_skn(
        filepath=skn_path, report_details=False)
    assert result == {'FINISHED'}, result
    return addon, [o for o in bpy.data.objects if o.type == 'ARMATURE'][0]


def _read(path):
    with open(path, "rb") as handle:
        return handle.read()


# ------------------------------------------------------------------------------- skl round trip

def test_skl_round_trip_is_byte_identical():
    addon, armature = _import(graves("graves.skn"))
    try:
        source = graves("graves.skl")
        out = scratch("graves_roundtrip.skl")
        report = skl_out.export_skl(armature, out)
        for line in report.summary():
            print("      %s" % line)

        assert not report.recomposed_joints, (
            "%d joints were recomposed, so nothing was edited but the exporter thought otherwise: "
            "%s" % (len(report.recomposed_joints), report.recomposed_joints[:5]))
        assert _read(out) == _read(source), (
            "the exported .skl differs from the input (%d vs %d bytes)"
            % (os.path.getsize(out), os.path.getsize(source)))
    finally:
        addon.unregister()


def test_skl_round_trip_notices_a_moved_bone():
    """Positive control. If the exporter wrote the stamped numbers regardless of what the user did,
    the test above would pass on a rig that had been completely rebuilt."""
    addon, armature = _import(graves("graves.skn"))
    try:
        from mathutils import Matrix, Vector

        bpy.context.view_layer.objects.active = armature
        bpy.ops.object.mode_set(mode='EDIT')
        try:
            edit_bone = armature.data.edit_bones["r_hand"]
            edit_bone.matrix = Matrix.Translation(Vector((4.0, 0.0, 0.0))) @ edit_bone.matrix
        finally:
            bpy.ops.object.mode_set(mode='OBJECT')

        out = scratch("graves_moved.skl")
        report = skl_out.export_skl(armature, out)
        assert "r_hand" in report.recomposed_joints, (
            "a moved bone was still written from its stale stamped values")
        assert _read(out) != _read(graves("graves.skl")), "moving a bone changed nothing in the file"
    finally:
        addon.unregister()


def test_the_exported_skl_reads_back_the_same_rig():
    """Bytes are one thing; the file also has to parse into the same skeleton."""
    addon, armature = _import(graves("graves.skn"))
    try:
        out = scratch("graves_reread.skl")
        skl_out.export_skl(armature, out)

        original = _rsblender.Skeleton.from_path(graves("graves.skl"))
        rebuilt = _rsblender.Skeleton.from_path(out)
        assert len(rebuilt.joints) == len(original.joints)
        assert list(rebuilt.influences) == list(original.influences)
        for a, b in zip(original.joints, rebuilt.joints):
            assert a.name == b.name and a.hash == b.hash and a.parent_id == b.parent_id
            # Byte-identical output makes this exact, but the assertion is written against the
            # decoded values so it still means something if the layout ever changes.
            assert convert.max_element_deviation(
                convert.flat_to_matrix(list(a.bind_global)),
                convert.flat_to_matrix(list(b.bind_global))) < 1e-5, a.name
    finally:
        addon.unregister()


# ------------------------------------------------------------------------------- skn round trip

def test_skn_round_trip_is_byte_identical():
    addon, armature = _import(graves("graves.skn"))
    try:
        out = scratch("graves_roundtrip.skn")
        _names, report = skn_out.export_skn(armature, out)
        for line in report.summary():
            print("      %s" % line)

        assert report.split_vertices == 0, (
            "%d vertices were split on a mesh that came straight out of a .skn, where every vertex "
            "already has exactly one uv and one normal" % report.split_vertices)
        assert _read(out) == _read(graves("graves.skn")), (
            "the exported .skn differs from the input (%d vs %d bytes)"
            % (os.path.getsize(out), os.path.getsize(graves("graves.skn"))))
    finally:
        addon.unregister()


def test_skn_round_trip_survives_four_submeshes():
    """Graves has one submesh, so on its own it says nothing about range bookkeeping."""
    addon, armature = _import(riven("riven_skin23.skn"))
    try:
        out = scratch("riven_roundtrip.skn")
        _names, report = skn_out.export_skn(armature, out)
        for line in report.summary():
            print("      %s" % line)
        assert len(report.submeshes) == 4, report.submeshes
        assert _read(out) == _read(riven("riven_skin23.skn")), (
            "the exported .skn differs from the input (%d vs %d bytes)"
            % (os.path.getsize(out), os.path.getsize(riven("riven_skin23.skn"))))
    finally:
        addon.unregister()


def test_skn_export_notices_a_moved_vertex():
    """Positive control for both .skn tests above."""
    addon, armature = _import(graves("graves.skn"))
    try:
        mesh = [o for o in bpy.data.objects if o.type == 'MESH'][0]
        mesh.data.vertices[0].co.x += 5.0

        out = scratch("graves_moved.skn")
        skn_out.export_skn(armature, out)
        assert _read(out) != _read(graves("graves.skn")), "moving a vertex changed nothing"
    finally:
        addon.unregister()


def test_a_uv_seam_splits_the_vertex_rather_than_losing_one_of_its_uvs():
    """A .skn stores one uv per vertex. Blender stores one per loop, so a seam has to become two
    vertices - the alternative is picking one uv and silently discarding the other."""
    addon, armature = _import(graves("graves.skn"))
    try:
        mesh = [o for o in bpy.data.objects if o.type == 'MESH'][0]
        uv_layer = mesh.data.uv_layers.active

        # A vertex that SEVERAL loops share. A .skn is already split, so most of graves' vertices
        # are used by exactly one loop, and giving one of those a new uv creates no conflict at all
        # - which is how this test passed for the wrong reason the first time it was written.
        seen = {}
        for loop_index, loop in enumerate(mesh.data.loops):
            seen.setdefault(loop.vertex_index, []).append(loop_index)
        shared = next(loops for loops in seen.values() if len(loops) > 1)
        uv_layer.data[shared[0]].uv = (0.123456, 0.654321)

        out = scratch("graves_seam.skn")
        _names, report = skn_out.export_skn(armature, out)
        assert report.split_vertices >= 1, (
            "a vertex with two different uvs was written as one, so one of them was thrown away")

        rebuilt = _rsblender.Mesh.from_path(out)
        assert rebuilt.vertex_count == 7088 + report.split_vertices, (
            "%d vertices, expected %d" % (rebuilt.vertex_count, 7088 + report.split_vertices))
    finally:
        addon.unregister()


def test_export_prunes_to_four_influences_and_says_so():
    addon, armature = _import(graves("graves.skn"))
    try:
        mesh = [o for o in bpy.data.objects if o.type == 'MESH'][0]
        for name in [b.name for b in armature.data.bones][:6]:
            mesh.vertex_groups[name].add([0], 0.25, 'REPLACE')

        out = scratch("graves_overweighted.skn")
        _names, report = skn_out.export_skn(armature, out)
        assert report.dropped_influences >= 1, "six influences went into a four-influence format"

        rebuilt = _rsblender.Mesh.from_path(out)
        weights = np.frombuffer(rebuilt.blend_weights, dtype=np.float32).reshape(-1, 4)
        assert abs(float(weights[0].sum()) - 1.0) < 1e-5, (
            "the surviving weights were not renormalised: %r" % (weights[0],))
    finally:
        addon.unregister()


# ------------------------------------------------------------------------------- anm round trip

def test_anm_round_trip_reproduces_the_tracks():
    """The Blender side of the export, checked exactly: uncompressed, so the file holds every frame
    as sampled. Compression is checked separately below."""
    addon, armature = _import(graves("graves.skn"))
    try:
        bpy.context.view_layer.objects.active = armature
        source = graves_anim("graves_idle1.anm")
        bpy.ops.lolblender.import_anm(filepath=source, report_details=False)

        original = _rsblender.Animation.from_path(source)
        frames = original.frame_count
        out = scratch("graves_idle1_roundtrip.anm")
        report = anm_out.export_anm(armature, out, bpy.context.scene, 0, frames - 1,
                                    compressed=False)
        assert not report.compressed
        for line in report.summary():
            print("      %s" % line)

        rebuilt = _rsblender.Animation.from_path(out)
        assert abs(rebuilt.fps - original.fps) < 1e-3, (rebuilt.fps, original.fps)

        skeleton = _rsblender.Skeleton.from_path(graves("graves.skl"))
        P = np.array(convert.P, dtype=np.float64)
        P_inv = np.array(convert.P_INV, dtype=np.float64)

        worst, who = 0.0, ""
        for frame in (0, 7, 23, frames - 1):
            time = frame / original.fps
            a = P @ np.array(original.palette(skeleton, time), dtype=np.float64).reshape(-1, 4, 4) @ P_inv
            b = P @ np.array(rebuilt.palette(skeleton, time), dtype=np.float64).reshape(-1, 4, 4) @ P_inv
            deviation = np.abs(a - b)
            index = int(np.unravel_index(deviation.argmax(), deviation.shape)[0])
            if deviation.max() > worst:
                worst, who = float(deviation.max()), skeleton.joints[index].name
        print("      worst palette difference after a round trip: %.4g at %r" % (worst, who))
        assert worst < 5e-3, (
            "the exported animation deviates from the original by %.4g at %r" % (worst, who))
    finally:
        addon.unregister()


def test_anm_export_is_compressed_by_default_and_within_tolerance():
    """Every export path goes through `export_anm`, whose default is Riot's compressed container.
    The compressed file is compared joint by joint against the uncompressed export of the same
    frames, which isolates the compression error from everything else in the pipeline."""
    addon, armature = _import(graves("graves.skn"))
    try:
        bpy.context.view_layer.objects.active = armature
        source = graves_anim("graves_idle1.anm")
        bpy.ops.lolblender.import_anm(filepath=source, report_details=False)
        frames = _rsblender.Animation.from_path(source).frame_count

        packed_path = scratch("graves_idle1_compressed.anm")
        exact_path = scratch("graves_idle1_exact.anm")
        report = anm_out.export_anm(armature, packed_path, bpy.context.scene, 0, frames - 1)
        anm_out.export_anm(armature, exact_path, bpy.context.scene, 0, frames - 1,
                           compressed=False)
        for line in report.summary():
            print("      %s" % line)

        with open(packed_path, "rb") as f:
            assert f.read(8) == b"r3d2canm", "the default export is not compressed"
        assert report.compressed
        packed = _rsblender.Animation.from_path(packed_path)
        exact = _rsblender.Animation.from_path(exact_path)
        assert packed.compressed and not exact.compressed
        assert packed.frame_count == exact.frame_count == frames, (
            packed.frame_count, exact.frame_count, frames)
        assert sorted(packed.track_hashes) == sorted(exact.track_hashes)
        print("      %d bytes compressed, %d uncompressed"
              % (os.path.getsize(packed_path), os.path.getsize(exact_path)))
        assert os.path.getsize(packed_path) * 4 < os.path.getsize(exact_path)

        # rs_anim::CompressionTolerances: 0.002 per quaternion component (a rotation-matrix element
        # moves by at most about 4x that) and 0.1 game units, plus the 16-bit quantisation step.
        worst_rot, worst_trans = 0.0, 0.0
        for joint_hash in exact.track_hashes:
            for frame in range(frames):
                time = frame / exact.fps
                a = np.array(exact.sample_local(joint_hash, time)).reshape(4, 4)
                b = np.array(packed.sample_local(joint_hash, time)).reshape(4, 4)
                worst_rot = max(worst_rot, float(np.abs(a[:3, :3] - b[:3, :3]).max()))
                worst_trans = max(worst_trans, float(np.abs(a[:3, 3] - b[:3, 3]).max()))
        print("      worst compression error: rotation element %.4g, translation %.4g"
              % (worst_rot, worst_trans))
        assert worst_rot < 0.01, worst_rot
        assert worst_trans < 0.15, worst_trans
    finally:
        addon.unregister()


def test_anm_export_captures_a_constraint_rather_than_the_channel():
    """The evaluated pose is what gets written, which is the reason the physics addon's output can
    be exported at all. A channel-reading exporter would write the un-driven pose."""
    addon, armature = _import(graves("graves.skn"))
    try:
        from mathutils import Vector

        bpy.context.view_layer.objects.active = armature
        bpy.ops.lolblender.import_anm(
            filepath=graves_anim("graves_idle1.anm"), report_details=False)

        target = bpy.data.objects.new("driver", None)
        bpy.context.collection.objects.link(target)
        target.location = Vector((0.0, 0.0, 400.0))

        pose_bone = armature.pose.bones["r_hand"]
        constraint = pose_bone.constraints.new('COPY_LOCATION')
        constraint.target = target
        bpy.context.view_layer.update()

        out = scratch("graves_constrained.anm")
        anm_out.export_anm(armature, out, bpy.context.scene, 0, 4)

        rebuilt = _rsblender.Animation.from_path(out)
        skeleton = _rsblender.Skeleton.from_path(graves("graves.skl"))
        palette = np.array(rebuilt.palette(skeleton, 0.0), dtype=np.float64).reshape(-1, 4, 4)

        index = next(i for i, j in enumerate(skeleton.joints) if j.name == "r_hand")
        bind = convert.flat_to_matrix(list(skeleton.joints[index].bind_global))
        world = np.array(palette[index], dtype=np.float64) @ np.array(bind, dtype=np.float64)
        # 400 Blender units up is +400 on League's Y.
        assert world[1][3] > 300.0, (
            "the constraint did not reach the file: the joint is at y=%.1f" % world[1][3])
    finally:
        addon.unregister()


# ------------------------------------------------------------------------------------- the donor

def test_the_donor_lookup_matches_lol_mayas_order():
    directory = scratch("donor_probe", directory=True)
    for name in os.listdir(directory):
        os.remove(os.path.join(directory, name))

    target = os.path.join(directory, "graves.skn")
    assert donor_mod.find(target, ".skn") is None

    generic = os.path.join(directory, "riot.skn")
    open(generic, "wb").close()
    assert donor_mod.find(target, ".skn") == generic, "riot.skn was not found"

    specific = os.path.join(directory, "riot_graves.skn")
    open(specific, "wb").close()
    assert donor_mod.find(target, ".skn") == specific, (
        "riot_<basename> must win over riot.<ext> - lol_maya looks for it first")

    # The irregular case: the .skl donor is derived from the .skn TARGET name (lol_maya.py:150).
    skl_donor = os.path.join(directory, "riot_graves.skl")
    open(skl_donor, "wb").close()
    assert donor_mod.skeleton_donor(target) == skl_donor
