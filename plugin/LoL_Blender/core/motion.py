"""Motion: calm baked strands on frames where a reference bone jerks.

Post-bake pass, off unless a reference bone is set. Uses jerk (third derivative), not speed, so
a smooth jump keeps its motion while a run's impacts get damped.
"""

import math

from mathutils import Quaternion

from .bake import bone_of


def factors(context, armature, reference, frame_start, frame_end, strength):
    """`{frame: factor}` in `[strength, 1.0]` - low where the rig jerks, 1.0 where it is smooth."""
    if frame_end - frame_start < 3:
        return {}
    scene = context.scene
    saved = scene.frame_current
    positions = {}
    try:
        for frame in range(frame_start, frame_end + 1):
            scene.frame_set(frame)
            pose_bone = armature.pose.bones.get(reference)
            if pose_bone is None:
                return {}
            positions[frame] = (armature.matrix_world @ pose_bone.head).copy()
    finally:
        scene.frame_set(saved)

    velocity = {f: positions[f] - positions[f - 1] for f in range(frame_start + 1, frame_end + 1)}
    acceleration = {f: velocity[f] - velocity[f - 1] for f in range(frame_start + 2, frame_end + 1)}
    jerk = {f: (acceleration[f] - acceleration[f - 1]).length
            for f in range(frame_start + 3, frame_end + 1)}
    if not jerk:
        return {}
    for frame in range(frame_start, frame_start + 3):
        jerk[frame] = jerk[frame_start + 3]

    smoothed = {}
    for frame in range(frame_start, frame_end + 1):
        window = [jerk[f] for f in (frame - 1, frame, frame + 1) if f in jerk]
        smoothed[frame] = sum(window) / len(window) if window else 0.0
    peak = max(smoothed.values())
    if peak < 1e-9:
        return {}
    return {f: 1.0 - (1.0 - strength) * (smoothed[f] / peak)
            for f in range(frame_start, frame_end + 1)}


def scale_toward_rest(action, names, amount):
    """Slerp baked rotations toward rest by `amount` (a float or `{frame: factor}`).

    Keeps the shape of the curve, only the amplitude shrinks.
    """
    wanted = {n for n in names if n}
    per_frame = isinstance(amount, dict)
    if not per_frame and amount >= 0.999:
        return 0

    groups = {}
    for curve in action.fcurves:
        path = curve.data_path
        if 'pose.bones["' not in path or 'rotation_quaternion' not in path:
            continue
        if bone_of(path) not in wanted:
            continue
        groups.setdefault(path, {})[curve.array_index] = curve

    identity = Quaternion((1.0, 0.0, 0.0, 0.0))
    scaled = 0
    for channels in groups.values():
        if len(channels) < 4:
            continue
        points = {c: {int(k.co[0]): k for k in channels[c].keyframe_points} for c in range(4)}
        frames = sorted(set(points[0]) & set(points[1]) & set(points[2]) & set(points[3]))
        for frame in frames:
            factor = amount.get(frame, 1.0) if per_frame else amount
            if factor >= 0.999:
                continue
            q = Quaternion(tuple(points[c][frame].co[1] for c in range(4)))
            if q.magnitude < 1e-8:
                continue
            q.normalize()
            if q.w < 0.0:
                q = Quaternion((-q.w, -q.x, -q.y, -q.z))
            reduced = identity.slerp(q, factor)
            for c, value in enumerate((reduced.w, reduced.x, reduced.y, reduced.z)):
                points[c][frame].co[1] = value
            scaled += 1
        for curve in channels.values():
            curve.update()
    return scaled


def calm(context, armature, action, names, frame_start, frame_end, reference, strength):
    """Measure the reference bone, then damp the strands where it jerks."""
    if strength >= 0.999 or not names:
        return 0
    measured = factors(context, armature, reference, frame_start, frame_end, strength)
    if not measured:
        return 0
    return scale_toward_rest(action, names, measured)


def degrees_between(a, b):
    return math.degrees(2.0 * math.acos(min(1.0, abs(a.dot(b)))))
