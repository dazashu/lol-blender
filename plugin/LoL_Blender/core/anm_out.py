"""`.anm` export.

The evaluated pose is sampled at the baked trajectory rate (including physics subframes) and
inverted through the import formula:

    local_league = P^-1 @ (PRE^-1 @ basis @ POST^-1) @ P

Written as compressed `r3d2canm` by default (lossy within fixed tolerances), or uncompressed
`r3d2anmd` v4 with `compressed=False`. A clip too big for the compressed container falls back to
uncompressed. Physics trajectories retain dense samples and use the uncompressed container.
"""

import os
import math

import numpy as np

import bpy
from mathutils import Matrix

from . import convert
from . import donor as donor_mod
from . import promote
from . import registry as reg


class AnimExportReport:
    def __init__(self):
        self.path = ""
        self.track_count = 0
        self.frame_count = 0
        self.fps = 0.0
        self.skipped_helpers = []
        self.donor = None
        self.bytes_written = 0
        self.compressed = False
        self.max_shear = 0.0
        self.worst_shear_bone = None
        self.warnings = []

    def summary(self):
        lines = ["wrote %d tracks x %d frames at %.6g fps (%d bytes, %s)" % (
            self.track_count, self.frame_count, self.fps, self.bytes_written,
            "compressed" if self.compressed else "uncompressed")]
        note = donor_mod.describe(self.donor, used=False)
        if note:
            lines.append(note)
        if self.skipped_helpers:
            lines.append("helpers left out: %s" % ", ".join(self.skipped_helpers[:8]))
        if self.max_shear > 1e-5:
            lines.append(
                "the pose asks for shear a .anm key cannot hold (worst %.4g at %r); it is dropped"
                % (self.max_shear, self.worst_shear_bone))
        lines.extend(self.warnings)
        return lines


def scene_fps(scene):
    """The rate Blender is actually playing at, as a float."""
    return scene.render.fps / scene.render.fps_base


def frame_range(scene, armature, use_scene_range=True):
    """(start, end) inclusive. From the action when there is one, otherwise the scene."""
    if not use_scene_range and armature.animation_data and armature.animation_data.action:
        start, end = armature.animation_data.action.frame_range
        return int(round(start)), int(round(end))
    return scene.frame_start, scene.frame_end


def _channel_matrices(armature, exportable):
    """Per exportable bone: (hash, name, PRE^-1, POST^-1, parent name, rest-delta^-1).

    Precomputed once, since the rest pose does not change while sampling.
    """
    identity = Matrix.Identity(4)
    out = []
    for name in exportable:
        bone = armature.data.bones[name]
        rest = bone.matrix_local.copy()

        def bind_of(b):
            stored = b.get(reg.REST_GLOBAL) if b is not None else None
            if stored is not None and len(stored) == 16:
                return convert.riot_to_blender(
                    convert.flat_to_matrix([float(v) for v in stored]))
            return b.matrix_local.copy() if b is not None else identity

        parent = reg.nearest_native_ancestor(bone, exportable)
        pre = rest.inverted() @ (bind_of(parent) if parent is not None else identity)
        post = bind_of(bone).inverted() @ rest

        # the Blender parent, which the pose is relative to (not the .skl parent)
        pose_parent = bone.parent
        if pose_parent is not None:
            rest_delta_inverse = (pose_parent.matrix_local.inverted() @ rest).inverted()
        else:
            rest_delta_inverse = rest.inverted()

        out.append((int(reg.bone_hash(bone) or 0), name, pre.inverted(), post.inverted(),
                    pose_parent.name if pose_parent is not None else None, rest_delta_inverse))
    return out


def _unchanged_source_channels(armature, scene):
    """Reuse native TRS for untouched imported channels, avoiding a second shear loss.

    Compare every original frame against a fresh import through the current rest rig.
    A matching name or source path alone is deliberately insufficient.
    """
    from . import anm_in, convert
    from .. import native
    action=armature.animation_data.action if armature.animation_data else None
    path=action.get("lol_anm_filepath", "") if action else ""
    if not path or not os.path.isfile(path):return None, set()
    data=armature.animation_data
    if data.drivers or any(not track.mute for track in data.nla_tracks):return None,set()
    try:source=native.rs().Animation.from_path(path)
    except (ValueError,OSError):return None, set()
    if source.fps<=0:return None,set()
    if tuple(round(v) for v in action.frame_range)!=(0,source.frame_count-1):return None,set()
    inputs=anm_in._bone_inputs(armature)
    density=max(1,min(16,int(action.get("lol_source_sample_substeps",1))))
    baked=anm_in.bake_channels(source,inputs,sample_fps=source.fps*density);count=baked.frame_count
    buffers=[np.frombuffer(baked.locations,dtype=np.float32),
             np.frombuffer(baked.quaternions,dtype=np.float32),
             np.frombuffer(baked.scales,dtype=np.float32)]
    curves={(c.data_path,c.array_index):c for c in action.fcurves}
    hashes=set(source.track_hashes);keep=set()
    for i,bone in enumerate(armature.pose.bones):
        joint_hash=reg.bone_hash(bone.bone)
        if joint_hash not in hashes or bone.rotation_mode!='QUATERNION':continue
        if any(not c.mute and c.influence for c in bone.constraints):continue
        matches=True
        for (channel,width),buffer in zip((('location',3),('rotation_quaternion',4),('scale',3)),buffers):
            path=bone.path_from_id(channel)
            for axis in range(width):
                curve=curves.get((path,axis))
                if curve is None or curve.mute or curve.modifiers:matches=False;break
                if any(k.interpolation!='LINEAR' for k in curve.keyframe_points):matches=False;break
                expected=buffer[(i*width+axis)*count:(i*width+axis+1)*count]
                if any(abs(curve.evaluate(f/density)-float(value))>2e-6 for f,value in enumerate(expected)):
                    matches=False;break
                # A fractional key edit must not be silently replaced by the source.
                for key in curve.keyframe_points:
                    f=float(key.co.x)*density
                    if f<0 or f>count-1:matches=False;break
                    lo=int(f);hi=min(count-1,lo+1);t=f-lo
                    wanted=float(expected[lo])*(1-t)+float(expected[hi])*t
                    if abs(float(key.co.y)-wanted)>2e-6:matches=False;break
                if not matches:break
            if not matches:break
        if matches:keep.add(bone.name)
    return source,keep


def build(armature, scene, start, end, report=None):
    """Sample the rig over [start, end] and return (fps, tracks, report)."""
    report = report or AnimExportReport()

    promotion = promote.promote(armature)
    if promotion.refused:
        raise ValueError("Cannot export skeleton identities: " + "; ".join(
            "%s: %s" % item for item in promotion.refused))
    report.skipped_helpers = list(promotion.helpers)
    for name, reason in promotion.refused:
        report.warnings.append("%s: %s" % (name, reason))

    exportable = sorted(reg.exportable_names(armature))
    channels = _channel_matrices(armature, exportable)
    source, preserved = _unchanged_source_channels(armature, scene)
    action = armature.animation_data.action if armature.animation_data else None
    subdivisions = max(1, min(16, int(action.get("lol_physics_substeps", 1)))) if action else 1
    frames = [start+i/subdivisions for i in range(int(end-start)*subdivisions+1)]
    fps = scene_fps(scene)

    times = np.array([(f - start) / fps for f in frames], dtype=np.float32)
    translations = {c[1]: np.zeros((len(frames), 3), dtype=np.float32) for c in channels}
    rotations = {c[1]: np.zeros((len(frames), 4), dtype=np.float32) for c in channels}
    scales = {c[1]: np.ones((len(frames), 3), dtype=np.float32) for c in channels}

    original_frame = scene.frame_current
    original_subframe = scene.frame_subframe
    try:
        for row, frame in enumerate(frames):
            scene.frame_set(math.floor(frame), subframe=frame-math.floor(frame))
            for _hash, name, pre_inv, post_inv, parent_name, rest_delta_inv in channels:
                pose_bone = armature.pose.bones[name]

                # evaluated pose, not matrix_basis, so constraints and physics are included
                if parent_name is not None:
                    basis = (rest_delta_inv
                             @ armature.pose.bones[parent_name].matrix.inverted()
                             @ pose_bone.matrix)
                else:
                    basis = rest_delta_inv @ pose_bone.matrix

                local_blender = pre_inv @ basis @ post_inv
                local_league = convert.blender_to_riot(local_blender)
                use_source = name in preserved
                if use_source and subdivisions > 1 and action.get("lol_source_sample_substeps",1)==1:
                    # Physics saw Blender's component-wise interpolation. Native
                    # SLERP at fractional frames can move a collider by whole units.
                    # Reuse the original only when TRS reconstruction would lose shear;
                    # otherwise retain exactly the dense pose used by the simulation.
                    loc,quat,scl=local_league.decompose()
                    reconstructed=Matrix.LocRotScale(loc,quat,scl)
                    use_source=convert.max_element_deviation(local_league,reconstructed)>1e-5
                if use_source:
                    original=source.sample_local(_hash,frame/source.fps)
                    if original is not None:
                        if subdivisions > 1 and action.get("lol_source_sample_substeps",1)==1:
                            from .fit_local import fit_local
                            local_league=fit_local(basis,pre_inv,post_inv,convert.flat_to_matrix(original))
                        else:
                            local_league=convert.flat_to_matrix(original)
                from .anm_in import decompose_channels
                translation, rotation, scale = decompose_channels(local_league)
                if not all(math.isfinite(float(value)) for row_values in local_league for value in row_values):
                    raise ValueError("Non-finite animated transform for %s at frame %s" % (name, frame))
                if not all(math.isfinite(float(value)) for values in (translation, rotation, scale) for value in values):
                    raise ValueError("Non-finite animation channels for %s at frame %s" % (name, frame))

                rebuilt = (Matrix.Translation(translation)
                           @ rotation.to_matrix().to_4x4()
                           @ Matrix.Diagonal(scale).to_4x4())
                shear = convert.max_element_deviation(local_league, rebuilt)
                if shear > report.max_shear:
                    report.max_shear = shear
                    report.worst_shear_bone = name

                translations[name][row] = (translation.x, translation.y, translation.z)
                rotations[name][row] = (rotation.x, rotation.y, rotation.z, rotation.w)
                scales[name][row] = (scale.x, scale.y, scale.z)
    finally:
        scene.frame_set(original_frame, subframe=original_subframe)

    if subdivisions > 1:
        # Eliminate float evaluation noise before building the v4 palettes.
        # This bounds the perturbation to microunits while sharing identical
        # static transforms across the dense samples.
        for name in translations:
            translations[name] = np.round(translations[name], 5)
            scales[name] = np.round(scales[name], 5)
            rotations[name] = np.round(rotations[name], 6)
            rotations[name] /= np.maximum(np.linalg.norm(rotations[name],axis=1,keepdims=True),1e-12)
            for values in (translations[name],scales[name],rotations[name]):
                values[values == 0] = 0.0  # canonical positive zero for palette keys
    tracks = []
    for joint_hash, name, _pre, _post, _parent, _delta in channels:
        tracks.append((
            joint_hash,
            times.tolist(),
            translations[name].reshape(-1).tolist(),
            rotations[name].reshape(-1).tolist(),
            scales[name].reshape(-1).tolist(),
        ))

    report.track_count = len(tracks)
    report.frame_count = len(frames)
    fps *= subdivisions
    report.fps = fps
    return fps, tracks, report


def _write_animation_atomic(filepath, *args):
    """Replace a completed ANM only after the native writer succeeds."""
    import os
    import tempfile
    from .. import native
    directory = os.path.dirname(os.path.abspath(filepath))
    descriptor, temporary = tempfile.mkstemp(prefix='.lol-anm-', suffix='.anm', dir=directory)
    os.close(descriptor)
    try:
        result = native.rs().write_animation(temporary, *args)
        os.replace(temporary, filepath)
        return result
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def export_anm(armature, filepath, scene=None, start=None, end=None, report=None, compressed=True):
    """Write the armature's animation as a `.anm`. Returns an AnimExportReport.

    Every export goes through here (export operator and physics auto-export).
    """
    from .. import native

    scene = scene or bpy.context.scene
    if start is None or end is None:
        auto_start, auto_end = frame_range(scene, armature)
        start = auto_start if start is None else start
        end = auto_end if end is None else end

    action = armature.animation_data.action if armature.animation_data else None
    if action is not None and action.get("lol_physics_substeps", 1) > 1:
        # Lossy quaternion reduction can put a touching volume back inside the
        # collider. Preserve these collision-constrained trajectories verbatim.
        compressed = False
    fps, tracks, report = build(armature, scene, start, end, report)
    report.path = filepath
    report.donor = donor_mod.find(filepath, ".anm")
    try:
        report.bytes_written, report.compressed = _write_animation_atomic(
            filepath, fps, tracks, compressed)
    except ValueError as exc:
        if compressed or "anm palette exceeds 65535 entries" not in str(exc):
            raise
        # Dense long clips can exhaust v4's 16-bit palette. Use explicitly
        # bounded key reduction, never the ordinary export tolerances.
        from ..ui import physics_props
        protected=set()
        for _,physics_set in physics_props.sets_of(armature):
            if not physics_set.enabled:continue
            for item in list(physics_set.bones)+list(physics_set.colliders):
                bone=armature.data.bones.get(item.name)
                if bone is not None:protected.update([bone.name]+[p.name for p in bone.parent_recursive])
        hashes=[int(reg.bone_hash(armature.data.bones[n])) for n in protected
                if reg.bone_hash(armature.data.bones[n]) is not None]
        report.bytes_written, report.compressed = _write_animation_atomic(
            filepath, fps, tracks, True, True, (0.000025,0.00025,0.000025), hashes)
        report.warnings.append("dense palette full; precise compression on physics bones, colliders and their parents")
    if compressed and not report.compressed:
        report.warnings.append(
            "too many keys for the compressed container; written uncompressed instead")
    # a full clip written to its own source path is no longer unexported
    import os
    action = armature.animation_data.action if armature.animation_data else None
    if (action is not None and action.get("lol_physics_unexported", False)
            and not armature.lol_physics.live
            and int(start) == int(round(action.frame_range[0]))
            and int(end) == int(round(action.frame_range[1]))
            and os.path.normcase(os.path.abspath(filepath)) == os.path.normcase(os.path.abspath(action.get("lol_anm_filepath", "")))):
        action["lol_physics_unexported"] = False
    return report
