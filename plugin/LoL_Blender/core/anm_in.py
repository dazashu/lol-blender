"""Apply a `.anm` to an imported armature as a Blender Action.

- Every frame is baked with LINEAR keys, since Blender does not slerp quaternion curves.
- The rate uses `fps_base`, so non-integer rates survive.
- Untracked bones get the skeleton's local transform, like the engine.
- Rest transforms come from the live armature, so edited rigs still animate correctly.
"""

import numpy as np

import bpy
from mathutils import Matrix, Vector, Quaternion

from . import armature as arm_util
from . import convert
from . import legacy
from . import registry as reg

#: Above this the clip asks for shear, which pose channels cannot hold.
SHEAR_REPORT_THRESHOLD = 1e-5


class AnimReport:
    def __init__(self):
        self.name = ""
        self.fps = 0.0
        self.frame_count = 0
        self.track_count = 0
        self.bone_count = 0
        self.matched = 0
        self.unmatched_tracks = []
        self.static_bones = 0
        self.curves = 0
        self.max_decompose_error = 0.0
        self.worst_decompose_bone = ""
        self.byte_exact = False
        self.fps_exact = True
        self.action = None

    def summary(self):
        lines = [
            "%s: %.6g fps, %d frames, %d tracks -> %d of %d bones matched, %d F-curves" % (
                self.name, self.fps, self.frame_count, self.track_count,
                self.matched, self.bone_count, self.curves),
        ]
        if self.static_bones:
            lines.append(
                "%d bones hold the rest pose with explicit constant keys"
                % self.static_bones)
        if self.unmatched_tracks:
            lines.append(
                "%d tracks name joints this armature does not have: %s"
                % (len(self.unmatched_tracks),
                   ", ".join("0x%08X" % h for h in self.unmatched_tracks[:8])))
        if not self.fps_exact:
            lines.append(
                "the clip's frame rate could not be expressed exactly as Blender's fps/fps_base "
                "pair; playback will drift slightly")
        if self.max_decompose_error > SHEAR_REPORT_THRESHOLD:
            lines.append(
                "SHEAR DROPPED: the clip asks for a transform a pose channel cannot hold, worst "
                "%.6g at %r. Blender cannot represent this and it has been discarded, not "
                "approximated" % (self.max_decompose_error, self.worst_decompose_bone))
        return lines


def set_scene_fps(scene, fps):
    """Set the scene rate to `fps` as `(round(fps), round(fps) / fps)`.

    Returns True when the stored rate reads back equal to `fps`.
    """
    if fps <= 0.0:
        return False

    numerator = max(1, int(round(fps)))
    scene.render.fps = numerator
    scene.render.fps_base = numerator / float(fps)

    achieved = scene.render.fps / scene.render.fps_base
    return abs(achieved - fps) <= abs(fps) * 1e-6


def _bind_global_blender(bone):
    """The joint's bind in Blender space, or the bone's rest for a user-added bone."""
    stored = bone.get(reg.REST_GLOBAL)
    if stored is not None and len(stored) == 16:
        return convert.riot_to_blender(convert.flat_to_matrix([float(v) for v in stored]))
    return bone.matrix_local.copy()


def _bone_inputs(armature_object):
    """Per pose bone: (joint_hash, PRE, POST, fallback_local_league).

    Matching Blender's skinning to the engine's gives

        basis(j) = PRE(j) @ (P @ local_league @ P^-1) @ POST(j)
        PRE(j)   = rest(j)^-1 @ bind_global(blender parent)
        POST(j)  = bind_global(j)^-1 @ rest(j)

    POST carries the bind scale a rest bone cannot hold. The fallback is the joint's local
    transform, used when the clip has no track for it.
    """
    identity = Matrix.Identity(4)
    bones = []
    for pose_bone in armature_object.pose.bones:
        bone = pose_bone.bone
        joint_hash = reg.bone_hash(bone)
        if joint_hash is None:
            # user-added bone: keyed at rest so a previous clip's pose does not linger
            joint_hash = 0xFFFFFFFF

        fallback = bone.get(reg.REST_LOCAL)
        if fallback is None or len(fallback) != 16:
            # no stamped transform: hold the Blender rest (identity channel)
            local = arm_util.rest_local(bone)
            fallback = convert.matrix_to_flat(convert.blender_to_riot(local))

        rest = bone.matrix_local.copy()
        # the Blender parent, which Blender's pose formula uses
        parent_bind = _bind_global_blender(bone.parent) if bone.parent else identity

        pre = rest.inverted() @ parent_bind
        post = _bind_global_blender(bone).inverted() @ rest

        bones.append((
            int(joint_hash) & 0xFFFFFFFF,
            convert.matrix_to_flat(pre),
            convert.matrix_to_flat(post),
            [float(v) for v in fallback],
        ))
    return bones


def decompose_channels(matrix):
    """Complete the rotation frame of collapsed visibility transforms."""
    linear = matrix.to_3x3()
    if all(value == 0.0 for row in linear for value in row):
        location = matrix.to_translation()
        if not all(np.isfinite(value) for value in location):
            raise ValueError("Non-finite translation in collapsed animation transform")
        return location, Quaternion((1, 0, 0, 0)), Vector((0, 0, 0))
    volume = linear.col[0].length * linear.col[1].length * linear.col[2].length
    if volume > 0 and abs(linear.determinant()) > volume * 1e-10:
        return matrix.decompose()
    values = np.array([list(row) for row in linear], dtype=np.float64)
    left, _singular, right = np.linalg.svd(values)
    orthogonal = left @ right
    if np.linalg.det(orthogonal) < 0:
        left[:, -1] *= -1
        orthogonal = left @ right
    rotation = Matrix(orthogonal.tolist()).to_quaternion().normalized()
    scale = Vector(tuple(float(np.dot(orthogonal[:, axis], values[:, axis])) for axis in range(3)))
    return matrix.to_translation(), rotation, scale


def bake_channels(animation, bones, sample_fps=None):
    """Keep zero-scale visibility keys finite when native TRS is singular.

    A collapsed bone has no observable orientation. Reconstruct only failed
    decompositions by completing their orthogonal rotation frame, leaving all
    ordinary native samples byte-for-byte unchanged.
    """
    from types import SimpleNamespace
    baked = animation.bake(bones, **({"sample_fps": sample_fps} if sample_fps is not None else {}))
    arrays = [np.frombuffer(getattr(baked, field), dtype=np.float32).reshape(
        baked.bone_count, width, baked.frame_count)
        for field, width in (("locations", 3), ("quaternions", 4), ("scales", 3))]
    bad = np.zeros((baked.bone_count, baked.frame_count), dtype=bool)
    for values in arrays:
        bad |= ~np.isfinite(values).all(axis=1)
    # A missing scale axis can also yield a finite but wrong native rotation.
    # Complete the orthogonal frame for every rank-deficient sample.
    bad |= (np.abs(arrays[2]) < 1e-12).any(axis=1)
    if not bad.any():
        return baked
    arrays = [values.copy() for values in arrays]
    fps = sample_fps if sample_fps is not None else animation.fps
    fps = fps if fps > 0 else 30.0
    static = list(baked.is_static)
    max_error = baked.max_decompose_error
    worst = baked.worst_decompose_bone
    for i, frame in zip(*np.where(bad)):
        joint_hash, pre, post, fallback = bones[i]
        local = animation.sample_local(joint_hash, float(frame) / fps)
        local = fallback if local is None else local
        basis = (convert.flat_to_matrix(pre)
                 @ convert.riot_to_blender(convert.flat_to_matrix(local))
                 @ convert.flat_to_matrix(post))
        if not all(np.isfinite(float(v)) for row in basis for v in row):
            raise ValueError("Animation contains a non-finite transform at bone %d, sample %d" % (i, frame))
        location, rotation, scale = decompose_channels(basis)
        if not all(np.isfinite(float(v)) for channel in (location, rotation, scale) for v in channel):
            raise ValueError("Cannot decompose animation bone %d, sample %d" % (i, frame))
        for target, value in zip(arrays, (location, rotation, scale)):
            target[i, :, frame] = value
        error = convert.max_element_deviation(basis, Matrix.LocRotScale(location, rotation, scale))
        if error > max_error:
            max_error, worst = error, int(i)
        static[i] = False
    for i in np.where(bad.any(axis=1))[0]:
        # Invisible frames have no observable orientation. Continue the nearest
        # visible sample so a visibility ramp does not invent a spin from identity.
        hidden = (np.abs(arrays[2][i]) < 1e-12).all(axis=0)
        visible = np.flatnonzero(~hidden)
        if len(visible):
            for frame in np.flatnonzero(hidden):
                nearest = visible[np.argmin(np.abs(visible - frame))]
                arrays[1][i, :, frame] = arrays[1][i, :, nearest]
        for frame in range(1, baked.frame_count):
            if np.dot(arrays[1][i, :, frame-1], arrays[1][i, :, frame]) < 0:
                arrays[1][i, :, frame] *= -1
    return SimpleNamespace(bone_count=baked.bone_count, frame_count=baked.frame_count,
        locations=arrays[0].tobytes(), quaternions=arrays[1].tobytes(), scales=arrays[2].tobytes(),
        tracked=baked.tracked, is_static=static, max_decompose_error=max_error,
        worst_decompose_bone=worst)


def _write_curves(action, pose_bones, baked, frame_offset, report, subdivisions=1, only_names=None):
    """Build the F-curves from the baked buffers, with bulk `foreach_set` calls."""
    frames = baked.frame_count
    locations = np.frombuffer(baked.locations, dtype=np.float32)
    quaternions = np.frombuffer(baked.quaternions, dtype=np.float32)
    scales = np.frombuffer(baked.scales, dtype=np.float32)

    frame_numbers = np.arange(frames, dtype=np.float32)/subdivisions + float(frame_offset)
    curves = 0

    channels = (
        ("location", 3, locations),
        ("rotation_quaternion", 4, quaternions),
        ("scale", 3, scales),
    )

    for bone_index, pose_bone in enumerate(pose_bones):
        if only_names is not None and pose_bone.name not in only_names:continue
        if baked.is_static[bone_index]:
            report.static_bones += 1
        # static bones still get keys, or switching clips keeps the old pose
        samples = np.array([0] if frames == 1 else [0, frames - 1]) if baked.is_static[bone_index] else np.arange(frames)

        pose_bone.rotation_mode = 'QUATERNION'
        path_prefix = 'pose.bones["%s"].' % bpy.utils.escape_identifier(pose_bone.name)

        for channel, count, buffer in channels:
            base = bone_index * count * frames
            for axis in range(count):
                values = buffer[base + axis * frames: base + (axis + 1) * frames]

                curve = action.fcurves.new(path_prefix + channel, index=axis)
                curve.keyframe_points.add(len(samples))

                # keyframe_points 'co' is (frame, value) interleaved.
                co = np.empty(len(samples) * 2, dtype=np.float32)
                co[0::2] = frame_numbers[samples]
                co[1::2] = values[samples]
                curve.keyframe_points.foreach_set("co", co)

                # LINEAR: values are already per frame
                curve.keyframe_points.foreach_set(
                    "interpolation", np.full(len(samples), 1, dtype=np.int32))
                curve.update()
                curves += 1

    report.curves = curves


def apply_animation(armature_object, animation, name="Action", frame_offset=0,
                    replace_existing=True, set_fps=True, set_frame_range=True, source_path=None):
    """Bake `animation` onto `armature_object` as a new Action. Returns an AnimReport."""
    report = AnimReport()
    report.name = name
    report.fps = animation.fps
    report.track_count = animation.track_count
    report.byte_exact = animation.byte_exact

    scene = bpy.context.scene
    if set_fps:
        report.fps_exact = set_scene_fps(scene, animation.fps)

    pose_bones = list(armature_object.pose.bones)
    report.bone_count = len(pose_bones)

    bones = _bone_inputs(armature_object)
    subdivisions = 1
    baked = bake_channels(animation, bones)
    report.frame_count = animation.frame_count
    report.matched = sum(1 for t in baked.tracked if t)
    report.max_decompose_error = baked.max_decompose_error
    if baked.bone_count:
        report.worst_decompose_bone = pose_bones[baked.worst_decompose_bone].name

    present = set(int(h) for h in animation.track_hashes)
    claimed = set()
    for pose_bone in pose_bones:
        h = reg.bone_hash(pose_bone.bone)
        if h is not None:
            claimed.add(int(h))
    report.unmatched_tracks = sorted(present - claimed)

    if armature_object.animation_data is None:
        armature_object.animation_data_create()
    if replace_existing and armature_object.animation_data.action is not None:
        # lol_maya's delchannel: do not stack clips
        armature_object.animation_data.action = None

    action = bpy.data.actions.new(name=name)
    action["riot_animation_rig"] = armature_object
    action.use_fake_user = True
    if source_path:
        legacy.stamp_action_source(action, source_path)
    _write_curves(action, pose_bones, baked, frame_offset, report, subdivisions)
    action["lol_source_sample_substeps"]=subdivisions
    armature_object.animation_data.action = action
    report.action = action

    if set_frame_range and frame_offset == 0:
        focus_clip(scene, action)

    return report


def playback_end(action, first, last):
    """The last frame to play: `last - 1` for a looping clip, else `last`.

    In a loop the last frame repeats the first, so playing it would freeze one frame per lap.
    It stays in the action for the bake and the export.
    """
    from . import bake

    if last <= first:
        return last
    return last - 1 if bake.detects_loop(action, first, last) else last


def focus_clip(scene, action):
    """Set the scene range to this clip (see `playback_end`) and jump to its first frame."""
    if action is None:
        return
    first = int(round(action.frame_range[0]))
    last = int(round(action.frame_range[1]))
    scene.use_preview_range = False
    scene.frame_start = first
    scene.frame_end = max(first, playback_end(action, first, last))
    scene.frame_set(first)


def import_anm(filepath, armature_object, name=None, frame_offset=0, replace_existing=True,
               set_fps=True, set_frame_range=True):
    """Read a `.anm` and apply it. Returns an AnimReport."""
    import os

    from .. import native

    animation = native.rs().Animation.from_path(filepath)
    if name is None:
        name = os.path.splitext(os.path.basename(filepath))[0]
    return apply_animation(
        armature_object, animation, name=name, frame_offset=frame_offset,
        replace_existing=replace_existing, set_fps=set_fps, set_frame_range=set_frame_range,
        source_path=filepath)
