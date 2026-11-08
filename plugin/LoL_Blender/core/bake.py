"""Physics bake: write the simulation as keyframes, then clean them up.

Ported from the old standalone addon. Pass order:

    settle -> record -> key -> spikes -> linear -> frame 0 -> seam -> pre-seam -> sign

The sign fix runs last: any earlier pass can put `q` next to `-q`, which Blender interpolates the
long way round.
"""

import math

from mathutils import Quaternion

#: Body bones used to detect a looping clip (capes, hair and buffbones may differ at the ends).
LOOP_CHECK = (
    'spine', 'pelvis', 'l_hip', 'r_hip', 'l_clavicle', 'r_clavicle',
    'l_shoulder', 'r_shoulder', 'l_elbow', 'r_elbow', 'l_hand', 'r_hand',
    'head', 'neck',
)

CHANNELS = (('rotation_quaternion', 4), ('location', 3), ('scale', 3))


def bone_of(data_path):
    try:
        return data_path.split('pose.bones["')[1].split('"]')[0]
    except (IndexError, AttributeError):
        return ''


def _curves(action, names):
    wanted = {n for n in names if n}
    for curve in action.fcurves:
        if 'pose.bones["' in curve.data_path and bone_of(curve.data_path) in wanted:
            yield curve


def _quaternion_groups(action, names):
    """`{data_path: {index: curve}}` for the rotation channels of the listed bones."""
    groups = {}
    for curve in _curves(action, names):
        if 'rotation_quaternion' in curve.data_path:
            groups.setdefault(curve.data_path, {})[curve.array_index] = curve
    return {path: channels for path, channels in groups.items() if len(channels) == 4}


def detects_loop(action, frame_start, frame_end):
    """True when the clip's first and last frame hold the same body pose."""
    starts = {}
    ends = {}
    found = False
    for curve in action.fcurves:
        path = curve.data_path
        if 'pose.bones["' not in path:
            continue
        name = bone_of(path).lower()
        if 'buffbone' in name or not any(k in name for k in LOOP_CHECK):
            continue
        if curve.array_index >= 4:
            continue
        starts.setdefault(path, [0.0] * 4)[curve.array_index] = curve.evaluate(frame_start)
        ends.setdefault(path, [0.0] * 4)[curve.array_index] = curve.evaluate(frame_end)
        found = True
    if not found:
        return False

    worst = 0.0
    for path, start in starts.items():
        end = ends[path]
        if 'rotation_quaternion' in path:
            ms = math.sqrt(sum(v * v for v in start))
            me = math.sqrt(sum(v * v for v in end))
            if ms > 1e-3 and me > 1e-3:
                dot = sum((start[i] / ms) * (end[i] / me) for i in range(4))
                difference = 1.0 - abs(dot)
            else:
                difference = max(abs(start[i] - end[i]) for i in range(4))
        else:
            difference = max(abs(start[i] - end[i]) for i in range(3))
        worst = max(worst, difference)
    return worst < 0.05


def strip(action, names):
    """Remove the physics channels of the listed bones, so old keys do not fight the bake."""
    wanted = {n for n in names if n}
    removed = 0
    for curve in list(action.fcurves):
        path = curve.data_path
        if 'pose.bones["' not in path or bone_of(path) not in wanted:
            continue
        if not any(path.endswith('.' + prop) for prop, _n in CHANNELS):
            continue
        action.fcurves.remove(curve)
        removed += 1
    return removed


def write(action, samples, names, frame_start, frame_end):
    """Key `samples` = `{(bone, frame): (quaternion, location, scale)}`.

    A missing (non-finite) frame is skipped, so it interpolates between its neighbours.
    """
    for name in names:
        if not name:
            continue
        for prop, count in CHANNELS:
            path = 'pose.bones["%s"].%s' % (name, prop)
            for index in range(count):
                curve = action.fcurves.find(path, index=index)
                if curve is None:
                    curve = action.fcurves.new(path, index=index, action_group=name)
                for frame in range(frame_start, frame_end + 1):
                    values = samples.get((name, frame))
                    if values is None:
                        continue
                    if prop == 'rotation_quaternion':
                        value = values[0][index]
                    elif prop == 'location':
                        value = values[1][index]
                    else:
                        value = values[2][index]
                    curve.keyframe_points.insert(frame, value, options={'FAST', 'REPLACE'})
                curve.update()


def smooth_spikes(action, names, max_degrees=20.0):
    """Replace an isolated frame that jumps away from the midpoint of its neighbours."""
    limit = math.radians(max_degrees)
    fixed = 0
    for channels in _quaternion_groups(action, names).values():
        points = {c: {int(k.co[0]): k for k in channels[c].keyframe_points} for c in range(4)}
        frames = sorted(points[0])
        if len(frames) < 3:
            continue

        values = {}
        for frame in frames:
            if any(frame not in points[c] for c in range(4)):
                continue
            q = Quaternion(tuple(points[c][frame].co[1] for c in range(4)))
            if q.magnitude > 1e-8:
                q.normalize()
            values[frame] = q

        for i in range(1, len(frames) - 1):
            frame, before, after = frames[i], frames[i - 1], frames[i + 1]
            if frame not in values or before not in values or after not in values:
                continue
            following = values[after].copy()
            if values[before].dot(following) < 0.0:
                following.negate()
            middle = values[before].slerp(following, 0.5)
            if middle.magnitude > 1e-8:
                middle.normalize()
            if 2.0 * math.acos(min(1.0, abs(values[frame].dot(middle)))) <= limit:
                continue
            for c, value in enumerate((middle.w, middle.x, middle.y, middle.z)):
                points[c][frame].co[1] = value
            values[frame] = middle
            fixed += 1

        for curve in channels.values():
            curve.update()
    return fixed


def linear(action, names):
    """LINEAR keys and CONSTANT extrapolation (Bezier would overshoot between frames)."""
    for curve in _curves(action, names):
        curve.extrapolation = 'CONSTANT'
        for point in curve.keyframe_points:
            point.interpolation = 'LINEAR'
        curve.update()


def rest_frame(action, names, frame, interpolation='CONSTANT'):
    """Key the rest pose on one frame."""
    for curve in _curves(action, names):
        prop = curve.data_path.rsplit('].', 1)[-1]
        if 'location' in prop or 'rotation_euler' in prop:
            value = 0.0
        elif 'rotation_quaternion' in prop:
            value = 1.0 if curve.array_index == 0 else 0.0
        elif 'scale' in prop:
            value = 1.0
        else:
            continue
        point = curve.keyframe_points.insert(frame, value, options={'FAST', 'REPLACE'})
        if point is not None:
            point.interpolation = interpolation
        curve.update()


def snap_seam(action, frame_start, frame_end, names):
    """Copy the first frame onto the last so the clip loops (the last one has drifted).

    Quaternion signs follow `frame_end - 1` so the last segment does not flip.
    """
    groups = _quaternion_groups(action, names)
    quaternion_paths = set(groups)
    for curve in _curves(action, names):
        if curve.data_path in quaternion_paths:
            continue
        points = {int(k.co[0]): k for k in curve.keyframe_points}
        first = points.get(frame_start)
        if first is None:
            continue
        last = points.get(frame_end)
        if last is None:
            last = curve.keyframe_points.insert(
                frame_end, first.co[1], options={'FAST', 'REPLACE'})
        last.co[1] = first.co[1]
        curve.update()

    for channels in groups.values():
        points = {c: {int(k.co[0]): k for k in channels[c].keyframe_points} for c in range(4)}
        if any(frame_start not in points[c] for c in range(4)):
            continue
        first = [points[c][frame_start].co[1] for c in range(4)]
        previous = [points[c][frame_end - 1].co[1] if frame_end - 1 in points[c]
                    else channels[c].evaluate(frame_end - 1) for c in range(4)]
        dot = sum(first[c] * previous[c] for c in range(4))
        target = first if dot >= 0 else [-v for v in first]
        for c in range(4):
            last = points[c].get(frame_end)
            if last is None:
                last = channels[c].keyframe_points.insert(
                    frame_end, target[c], options={'FAST', 'REPLACE'})
            last.co[1] = target[c]
        for curve in channels.values():
            curve.update()


def smooth_pre_seam(action, frame_start, frame_end, names):
    """Rewrite `frame_end - 1` as the midpoint of its neighbours, after the seam snap."""
    a, b, c = frame_end - 2, frame_end - 1, frame_end
    if a < frame_start:
        return
    groups = _quaternion_groups(action, names)
    quaternion_paths = set(groups)
    for curve in _curves(action, names):
        if curve.data_path in quaternion_paths:
            continue
        points = {int(k.co[0]): k for k in curve.keyframe_points}
        if a in points and b in points and c in points:
            points[b].co[1] = (points[a].co[1] + points[c].co[1]) * 0.5
            curve.update()

    for channels in groups.values():
        points = {i: {int(k.co[0]): k for k in channels[i].keyframe_points} for i in range(4)}
        if not all(f in points[i] for i in range(4) for f in (a, b, c)):
            continue
        qa = Quaternion(tuple(points[i][a].co[1] for i in range(4)))
        qc = Quaternion(tuple(points[i][c].co[1] for i in range(4)))
        qa.normalize()
        qc.normalize()
        qb = qa.slerp(qc, 0.5)
        for i, value in enumerate((qb.w, qb.x, qb.y, qb.z)):
            points[i][b].co[1] = value
        for curve in channels.values():
            curve.update()


def ease_in(action, frame_start, names, span=3):
    """Blend the first frames from rest, for a non-looping clip."""
    for curve in _curves(action, names):
        points = {int(k.co[0]): k for k in curve.keyframe_points}
        anchor = points.get(frame_start)
        if anchor is None:
            continue
        value = anchor.co[1]
        for i in range(1, span + 1):
            point = points.get(frame_start + i)
            if point is not None:
                t = i / (span + 1)
                point.co[1] = value * (1.0 - t) + point.co[1] * t
        curve.update()


def fix_signs(action, names):
    """Put adjacent quaternion keys in the same hemisphere. ALWAYS LAST."""
    for channels in _quaternion_groups(action, names).values():
        points = {c: {int(k.co[0]): k for k in channels[c].keyframe_points} for c in range(4)}
        frames = sorted(set(points[0]) & set(points[1]) & set(points[2]) & set(points[3]))
        if len(frames) < 2:
            continue
        values = {}
        for frame in frames:
            v = [points[c][frame].co[1] for c in range(4)]
            magnitude = math.sqrt(sum(x * x for x in v))
            if magnitude > 1e-8:
                v = [x / magnitude for x in v]
            values[frame] = v
        previous = values[frames[0]]
        for frame in frames[1:]:
            current = values[frame]
            if sum(previous[c] * current[c] for c in range(4)) < 0.0:
                current = [-x for x in current]
                values[frame] = current
            previous = current
        for frame in frames:
            for c in range(4):
                points[c][frame].co[1] = values[frame][c]
        for curve in channels.values():
            curve.update()


def finish(action, names, frame_start, frame_end, looping, motion=None):
    """Every post-bake pass, in order. `motion` (optional) runs first, before the smoothing."""
    if motion is not None:
        motion()
    smooth_spikes(action, names)
    linear(action, names)
    if frame_start > 0:
        # frame 0 is a rest frame only when the clip starts later
        rest_frame(action, names, 0)
    if looping:
        # only smooth the pre-seam frame where the seam actually changed
        needs_repair = {bone_of(curve.data_path) for curve in _curves(action, names)
                        if curve.evaluate(frame_start) != curve.evaluate(frame_end)}
        snap_seam(action, frame_start, frame_end, names)
        if needs_repair:
            smooth_pre_seam(action, frame_start, frame_end, needs_repair)
    else:
        rest_frame(action, names, frame_start, interpolation='LINEAR')
        ease_in(action, frame_start, names)
    fix_signs(action, names)
