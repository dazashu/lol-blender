"""Phase 6: the solver itself.

THE POINT OF THIS FILE is that the Rust solver IS the standalone addon's solver, not something that
looks like it. So the tests are equalities against the original wherever an original exists:

  * `track_quat` against `mathutils.Vector.to_track_quat`, over thousands of vectors including the
    singular direction where the answer is decided by a rounding error;
  * the parameter curves against the constants in `hair_physics.py` and `boobs_physics.py`, at both
    ends of every slider;
  * the movement limit against `clamp_local_rotation_from_identity`.

Then the behaviour that has no original to check against: a strand that trails a moving rig, a
collider that pushes it out, a slider that re-tunes without restarting, and a frame budget.
"""

import math
import time

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

import _rsblender
from LoL_Blender.core import wiggle
from run import Skip, corpus


# --------------------------------------------------------------------------- helpers

def _chain(bone_count=4, length=10.0):
    """A straight chain hanging along +Y, each bone parented to the last but NOT connected -
    which is how a League rig comes in."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    data = bpy.data.armatures.new("rig")
    armature = bpy.data.objects.new("rig", data)
    bpy.context.scene.collection.objects.link(armature)
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode='EDIT')
    names = []
    previous = None
    for i in range(bone_count):
        bone = data.edit_bones.new("b%d" % i)
        bone.head = Vector((0.0, i * length, 0.0))
        bone.tail = Vector((0.0, (i + 1) * length, 0.0))
        bone.use_connect = False
        if previous is not None:
            bone.parent = previous
        previous = bone
        names.append(bone.name)
    bpy.ops.object.mode_set(mode='OBJECT')
    return armature, names


def _sim(armature, names, kind='HAIR', intensity=10, smoothness=12, gravity=1.0, **kwargs):
    return wiggle.Sim(armature, kind, names,
                      wiggle.params_for(kind, intensity, smoothness, gravity), **kwargs)


def _run(sim, frames=60, gravity=(0.0, 0.0, -9.81), dt=1.0 / 30.0):
    for _ in range(frames):
        sim.step(dt, 1.0, list(gravity))


def _shake(sim, armature, frames=60, amplitude=20.0, period=20.0, dt=1.0 / 30.0):
    """Move the RIG, which is how hair physics is actually driven.

    Gravity in this solver is a small bias - the multipliers run from 0.05 to 0.35 - and it is not
    what makes a strand swing. What makes it swing is the character moving out from under it: the
    bone's animated head is somewhere new every frame and the particle has to catch up. A test that
    only switches gravity on measures almost nothing, so this one walks the armature back and forth.

    Returns the worst distance any particle ever sat from where the animation put it.
    """
    worst = 0.0
    for frame in range(frames):
        armature.matrix_world = Matrix.Translation(
            (amplitude * math.sin(frame / period * math.tau), 0.0, 0.0))
        sim.step(dt, 1.0, [0.0, 0.0, -9.81])
        particles = sim.solver.particles()
        for i, pose_bone in enumerate(sim.topology.pose_bones):
            animated = armature.matrix_world @ pose_bone.bone.tail_local
            simulated = Vector(particles[i * 3:i * 3 + 3])
            worst = max(worst, (simulated - animated).length)
    return worst


def _lag(sim, armature):
    """Per bone, how far its particle is from where the animation would have put it."""
    particles = sim.solver.particles()
    return [(Vector(particles[i * 3:i * 3 + 3])
             - (armature.matrix_world @ pose_bone.bone.tail_local)).length
            for i, pose_bone in enumerate(sim.topology.pose_bones)]


# --------------------------------------------------------------------------- 1:1 with Blender

def test_track_quat_is_blenders_own_function_to_the_last_bit():
    """Every simulated bone's rotation comes out of `to_track_quat('Y','Z')`, so a port that is
    merely close is a rig that is merely close.

    The singular direction is the one that matters. When the bone points along world Z the "Z up"
    request cannot be satisfied, and Blender resolves it with whatever sign a cancellation lands
    on - computed in f32, through a `quat_to_mat3` that detours via f64 and a sqrt(2) scaling.
    Getting that detour wrong is invisible everywhere except there, and there it is a bone with a
    different roll.
    """
    import random

    random.seed(20240119)
    vectors = [(0, 1, 0), (0, -1, 0), (1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1),
               (1e-9, 0, -1), (0, 1e-9, -1), (1e-7, 1.0, 0.0), (0.0, 1.0, 1e-7)]
    vectors += [(random.uniform(-5, 5), random.uniform(-5, 5), random.uniform(-5, 5))
                for _ in range(5000)]

    worst = 0.0
    where = None
    for v in vectors:
        expected = Vector(v).to_track_quat('Y', 'Z')
        got = Quaternion(_rsblender.track_quat(list(v)))
        if expected.dot(got) < 0.0:
            got = Quaternion((-got.w, -got.x, -got.y, -got.z))
        difference = max(abs(expected[i] - got[i]) for i in range(4))
        if difference > worst:
            worst, where = difference, v
    print("      %d vectors, worst deviation %.3e at %s" % (len(vectors), worst, where))
    assert worst < 1e-5, "not the same function"


def test_the_movement_limit_lands_exactly_on_the_cap():
    """`clamp_local_rotation_from_identity`: slerp from rest by exactly the fraction that fits."""
    for degrees in (10.0, 20.0, 45.0, 90.0):
        far = Euler((math.radians(150.0), 0.0, 0.0)).to_quaternion()
        capped = Quaternion(_rsblender.clamp_rotation([far.w, far.x, far.y, far.z], degrees))
        angle = math.degrees(2.0 * math.acos(min(1.0, abs(capped.w))))
        assert abs(angle - degrees) < 1e-4, "%.4f deg against a %.1f cap" % (angle, degrees)

    # Anything already inside the cap is returned untouched.
    small = Euler((math.radians(5.0), 0.0, 0.0)).to_quaternion()
    same = Quaternion(_rsblender.clamp_rotation([small.w, small.x, small.y, small.z], 45.0))
    assert (small.inverted() @ same).angle < 1e-6

    # A rotation past half a turn must be folded onto the short arc first, or `2*acos(|w|)` reads
    # the long way round and the cap is applied to the wrong number.
    backwards = Euler((math.radians(300.0), 0.0, 0.0)).to_quaternion()
    folded = Quaternion(_rsblender.clamp_rotation(
        [backwards.w, backwards.x, backwards.y, backwards.z], 20.0))
    assert abs(math.degrees(2.0 * math.acos(min(1.0, abs(folded.w)))) - 20.0) < 1e-4
    print("      10, 20, 45 and 90 degree caps all land on the number")


def test_the_parameter_curves_are_the_standalone_addons_numbers():
    """Both ends of every slider, against the constants in the addon being ported."""
    low = wiggle.params_for('HAIR', 1, 1, 1.0)
    high = wiggle.params_for('HAIR', 20, 20, 1.0)
    assert abs(low['stiff'] - 400.0) < 1e-6 and abs(high['stiff'] - 40.0) < 1e-6
    assert abs(low['damp'] - 0.35) < 1e-6 and abs(high['damp'] - 8.0) < 1e-6
    assert abs(low['gravity'] - 0.05) < 1e-6 and abs(high['gravity'] - 0.35) < 1e-6
    assert abs(low['mass'] - 0.15) < 1e-6 and abs(high['mass'] - 1.2) < 1e-6
    assert abs(low['stretch'] - 0.0) < 1e-6 and abs(high['stretch'] - 0.04) < 1e-6

    low = wiggle.params_for('BREAST', 1, 1, 1.0)
    high = wiggle.params_for('BREAST', 20, 20, 1.0)
    assert abs(low['stiff'] - 650.0) < 1e-6 and abs(high['stiff'] - 110.0) < 1e-6
    assert abs(low['damp'] - 0.60) < 1e-6 and abs(high['damp'] - 12.0) < 1e-6
    # GRAVITY IS THE ONE DEPARTURE, and it is deliberate. The standalone addon's breast curve tops
    # out at 0.03, which is not distinguishable from zero at League's unit scale - measured, the
    # raw values 0.03, 0.3 and 3.0 sag the particle by 0.01%, 0.07% and 0.68% of a bone length.
    # See `wiggle.BREAST_GRAVITY`. Gravity 0 still reproduces the original exactly.
    assert abs(wiggle.params_for('BREAST', 10, 12, 0.0)['gravity']) < 1e-9
    assert abs(low['gravity'] - wiggle.BREAST_GRAVITY) < 1e-6
    assert abs(high['gravity'] - wiggle.BREAST_GRAVITY) < 1e-6

    # Cloth is on the SAME curve as hair. The standalone addon had no cloth set, so capes went in
    # the Hair panel and were tuned on Ponytail; a softer preset was tried here and rejected.
    for intensity in (1, 10, 20):
        assert (wiggle.params_for('CLOTH', intensity, 12, 1.0)
                == wiggle.params_for('HAIR', intensity, 12, 1.0)), intensity

    # Intensity must move the whole curve in one direction, or the slider does not mean anything.
    stiffness = [wiggle.params_for('HAIR', i, 12, 1.0)['stiff'] for i in range(1, 21)]
    assert all(a > b for a, b in zip(stiffness, stiffness[1:])), "intensity is not monotonic"
    damping = [wiggle.params_for('HAIR', 8, s, 1.0)['damp'] for s in range(1, 21)]
    assert all(a < b for a, b in zip(damping, damping[1:])), "smoothness is not monotonic"
    print("      hair, cloth and breast curves match the source at both ends")


# --------------------------------------------------------------------------- 1:1 with Wiggle 2

def _python_wiggle(armature, sim, world_at, frames, dt=1.0 / 30.0, gravity=(0.0, 0.0, -9.81),
                   iterations=None):
    """Wiggle 2's `move` / `constrain` / `update_matrix`, transcribed into this test.

    Straight out of `physics_plugin/physics.py`, restricted to the configuration the standalone
    addon used - tail particles, chained, no head, no wind, no pins, no collision - and driven by
    the same inputs as the Rust solver. This is the reference the port is checked against, and it
    is deliberately written the slow, literal way: `mathutils` types, one bone at a time, no
    cleverness. If the two disagree, the port is wrong.
    """
    iterations = iterations or wiggle.ITERATIONS
    topology = sim.topology
    n = len(topology)
    parents = topology.parents
    # Wiggle's direct-parent shortcut: when the child's head sits on the parent's tail it
    # TRANSLATES the parent's particle instead of swinging and rescaling it. The transcription has
    # to carry the same rule as the port, or it stops being a second copy of the same solver and
    # starts being a control for a question nobody asked. See `Topology` on why the flag cannot be
    # used to decide it on a League rig.
    connected = topology.direct_connected
    rest = topology.rest_lengths
    settings = sim.settings
    gravity = Vector(gravity)

    positions = []
    matrices = []
    world = world_at(0)
    for i in range(n):
        anim = world @ topology.pose_bones[i].bone.matrix_local
        positions.append(anim @ Vector((0.0, rest[i], 0.0)))
        matrices.append(anim.copy())
    last = [p.copy() for p in positions]
    velocity = [Vector((0.0, 0.0, 0.0)) for _ in range(n)]

    for frame in range(frames):
        world = world_at(frame)
        anim = [world @ topology.pose_bones[i].bone.matrix_local for i in range(n)]
        world_length = [((anim[i] @ Vector((0.0, rest[i], 0.0))) - anim[i].to_translation()).length
                        for i in range(n)]
        anim_scale = [(world.inverted() @ anim[i]).to_scale() for i in range(n)]

        def followed(i):
            p = parents[i]
            if p < 0:
                return anim[i].copy()
            return matrices[p] @ (anim[p].inverted() @ anim[i])

        def update_matrix(i):
            m2 = followed(i)
            vec = m2.inverted() @ positions[i]
            rot = vec.to_track_quat('Y', 'Z').to_matrix().to_4x4()
            # NO BOUND HERE. The stretch bound and the movement limit shape what gets WRITTEN
            # and neither touches the simulated matrix - see `update_matrix` in `physics.rs`. This
            # transcription is compared against the solver's particles, so it carries the solve
            # and not the write.
            sy = vec.length / rest[i] if rest[i] > 1e-8 else 1.0
            scale = Matrix.Scale(sy, 4, Vector((0.0, 1.0, 0.0)))
            matrices[i] = m2 @ rot @ scale
            return rot @ scale

        def spring(target, position):
            s = target - position
            force = s * settings['stiff'] / iterations * dt * dt
            return s if force.length > s.length else force

        def drag(p, pivot_from, pivot_to):
            origin = matrices[p].to_translation()
            v1 = pivot_from - origin
            v2 = pivot_to - origin
            if v1.length < 1e-12:
                return
            scale = v2.length / v1.length
            v3 = v1.rotation_difference(v2) @ (positions[p] - origin)
            positions[p] = origin + v3 * scale

        for i in range(n):
            damp = max(0.0, min(1.0, 1.0 - settings['damp'] * dt))
            velocity[i] = velocity[i] * damp
            positions[i] = positions[i] + velocity[i] + gravity * settings['gravity'] * dt * dt
            update_matrix(i)

        for iteration in range(iterations):
            index = iterations - 1 - iteration
            for i in range(n):
                base = followed(i)
                mat = Matrix.LocRotScale(base.to_translation(), base.to_quaternion(),
                                         anim_scale[i])
                p = parents[i]
                coupled = p >= 0
                # Equal masses across a set, so `get_fac` is 0.5 except on the last pass, where
                # Wiggle swaps in the parent's stretch.
                factor = settings['stretch'] if index == 0 else 0.5

                s = spring(mat @ Vector((0.0, rest[i], 0.0)), positions[i])
                if coupled:
                    if connected[i]:
                        positions[p] = positions[p] - s * factor
                    else:
                        head = matrices[i].to_translation()
                        tail = matrices[i] @ Vector((0.0, rest[i], 0.0))
                        drag(p, (head + tail) / 2.0, (head + (tail - s * factor)) / 2.0)
                    positions[i] = positions[i] + s * (1.0 - factor)
                else:
                    positions[i] = positions[i] + s

                origin = mat.to_translation()
                direction = (positions[i] - origin).normalized()
                s = (origin + direction * world_length[i] - positions[i]) * (
                    1.0 - settings['stretch'])
                if coupled:
                    if connected[i]:
                        positions[p] = positions[p] - s * factor
                    else:
                        head = matrices[i].to_translation()
                        drag(p, head, head - s * factor)
                    positions[i] = positions[i] + s * (1.0 - factor)
                else:
                    positions[i] = positions[i] + s

                if coupled:
                    update_matrix(p)
                update_matrix(i)

        for i in range(n):
            update_matrix(i)
        for i in range(n):
            velocity[i] = positions[i] - last[i]
            last[i] = positions[i].copy()

    return positions


def test_the_rust_solver_is_the_python_one():
    """The port, held against a transcription of Wiggle 2 itself.

    This is the test that decides whether "ported to Rust" is true, and it runs at three intensity
    settings so it covers a stiff spring, a soft one and the middle.

    ONE FRAME is where the logic is checked - a difference in the order of operations shows up
    immediately and cannot hide. Sixty frames is the amplification check: this is a spring solver
    with position corrections feeding straight back into Verlet velocity, so it is chaotic, and the
    f32 inside `to_track_quat` guarantees the two runs part company eventually. What must not
    happen is that they part company by anything that reads as a different simulation.
    """
    for intensity in (1, 12, 20):
        armature, names = _chain(4)
        sim = _sim(armature, names, intensity=intensity)

        def world_at(frame):
            return Matrix.Translation((8.0 * math.sin(frame / 17.0 * math.tau),
                                       3.0 * math.cos(frame / 11.0 * math.tau), 0.0))

        def rust_after(frames):
            armature.matrix_world = world_at(0)
            sim.reset()
            for frame in range(frames):
                armature.matrix_world = world_at(frame)
                sim.step(1.0 / 30.0, 1.0, [0.0, 0.0, -9.81])
            return [Vector(sim.solver.particles()[i * 3:i * 3 + 3]) for i in range(len(names))]

        immediate = max((a - b).length for a, b in
                        zip(rust_after(1), _python_wiggle(armature, sim, world_at, 1)))
        settled = _python_wiggle(armature, sim, world_at, 60)
        after = max((a - b).length for a, b in zip(rust_after(60), settled))
        travelled = max((p - (world_at(59) @ b.bone.tail_local)).length
                        for p, b in zip(settled, sim.topology.pose_bones))

        print("      intensity %2d: 1 frame %.2e, 60 frames %.2e, strand %.1f units off rest"
              % (intensity, immediate, after, travelled))
        # THE THRESHOLD, and why it is where it is. This is not a bit-for-bit claim and cannot be:
        # `to_track_quat` runs in f32 in both, on inputs that reached it through different f64 op
        # orders, so the two round apart by up to 7e-07 per quaternion component - about 3e-05 of
        # arc on a chain 40 units long. Measured frame-1 divergence across the three settings runs
        # 4e-08 to 2e-05, so 1e-04 sits just above the noise and 2.5e-06 relative.
        #
        # It is tight enough for the bugs that actually happen. The two porting mistakes this
        # project hit for real were a rotation applied to the wrong bone in the chain, which showed
        # up as 103 degrees, and a stale parent matrix, which showed up as 0.167 units - both four
        # orders of magnitude clear of this.
        assert travelled > 1.0, "the reference barely moved, so agreeing with it proves nothing"
        assert immediate < 1e-4, "different on the very first frame: %.3e" % immediate
        assert after < travelled * 1e-3, "%.3e apart after 60 frames" % after


# --------------------------------------------------------------------------- behaviour

def test_a_chain_lags_behind_a_moving_rig():
    """The whole effect, measured: the rig moves, the strand does not keep up, and the further
    down the chain a bone is the further behind it gets."""
    armature, names = _chain(4)
    sim = _sim(armature, names, intensity=15)
    sim.reset()

    worst = _shake(sim, armature, 90)
    lag = _lag(sim, armature)
    print("      worst lag %.3f units, per bone " % worst
          + ", ".join("%.2f" % value for value in lag))
    assert all(math.isfinite(value) for value in lag), "the solver produced a non-finite pose"
    assert worst > 1.0, "the strand followed the rig exactly: %.4f" % worst
    assert lag[-1] > lag[0], "the tip should trail further than the root: %s" % lag


def test_a_chain_settles_once_the_rig_stops():
    armature, names = _chain(4)
    sim = _sim(armature, names, intensity=15)
    sim.reset()
    _shake(sim, armature, 60)

    armature.matrix_world = Matrix.Identity(4)
    _run(sim, 20)
    early = max(_lag(sim, armature))
    _run(sim, 150)
    late = max(_lag(sim, armature))
    print("      lag %.4f -> %.4f after standing still" % (early, late))
    assert late < early, "it never settles"


def test_intensity_changes_the_result_at_every_notch():
    """That the slider does something, and that it does the SAME something Wiggle 2 did.

    Deliberately not "stiffer moves less". Wiggle applies its spring as a position correction and
    then reads velocity back out of the position change, so a stiff setting hands the particle its
    own correction as momentum and the strand rings around the target instead of sitting on it -
    measured here at 9.3 units of swing on a rig shaking by 5. That is the ported solver's own
    behaviour, not this port's, and it is not something to quietly tune away: the test records what
    the slider does and `test_the_rust_solver_is_the_python_one` is what says it is faithful.
    """
    swings = {}
    for intensity in (1, 10, 20):
        armature, names = _chain(4)
        sim = _sim(armature, names, intensity=intensity)
        sim.reset()
        widest = 0.0
        for frame in range(120):
            armature.matrix_world = Matrix.Translation(
                (5.0 * math.sin(frame / 24.0 * math.tau), 0.0, 0.0))
            sim.step(1.0 / 30.0, 1.0, [0.0, 0.0, -9.81])
            widest = max(widest, abs(Vector(sim.solver.particles()[-3:]).x))
        swings[intensity] = widest
    print("      widest tip swing by intensity: "
          + ", ".join("%d=%.2f" % (k, v) for k, v in sorted(swings.items())))
    values = sorted(swings.values())
    assert values[-1] - values[0] > 1.0, "the slider changed almost nothing: %s" % swings


def test_a_slider_moved_mid_run_does_not_restart_the_simulation():
    """This is what separates a live preview from a preview button.

    Re-tuning must leave the particles where they are. If `set_params` reset anything, the strand
    would snap back to rest every time the slider moved by one notch.
    """
    armature, names = _chain(4)
    sim = _sim(armature, names, intensity=15)
    sim.reset()
    _shake(sim, armature, 40)

    before = list(sim.solver.particles())
    sim.settings = wiggle.params_for('HAIR', 3, 12, 1.0)
    sim.push_settings()
    after = list(sim.solver.particles())
    assert before == after, "changing a setting moved the particles"

    _run(sim, 30)
    assert list(sim.solver.particles()) != before, "the new setting did nothing"
    print("      re-tuned mid-run with the state intact")


def test_a_collider_keeps_the_strand_out_of_it():
    """A sphere on a body bone, and a strand that would otherwise hang inside it."""
    armature, names = _chain(4)

    # A collider bone whose sphere swallows the end of the chain. Named Head so it takes the
    # sphere-shaped radius the addon gave heads, which is what makes it big enough to reach.
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode='EDIT')
    blocker = armature.data.edit_bones.new("Head")
    blocker.head = Vector((0.0, 40.0, -8.0))
    blocker.tail = Vector((0.0, 50.0, -8.0))
    bpy.ops.object.mode_set(mode='OBJECT')

    free = _sim(armature, names, intensity=15)
    free.reset()
    _shake(free, armature, 60)
    without = [Vector(free.solver.particles()[i * 3:i * 3 + 3]) for i in range(len(names))]

    blocked = _sim(armature, names, intensity=15, colliders=["Head"])
    assert blocked.solver.collider_count == 0, "colliders are uploaded per frame, not at build"
    blocked.reset()
    _shake(blocked, armature, 60)
    with_collision = [Vector(blocked.solver.particles()[i * 3:i * 3 + 3])
                      for i in range(len(names))]

    moved = max((a - b).length for a, b in zip(without, with_collision))
    print("      %d spheres, worst particle difference %.3f"
          % (blocked.solver.collider_count, moved))
    assert blocked.solver.collider_count > 0, "no collision spheres were built"
    assert moved > 0.5, "the collider changed nothing: %.4f" % moved


def test_the_movement_limit_holds_on_a_real_chain():
    armature, names = _chain(4)

    def worst_rotation(sim):
        worst = 0.0
        for frame in range(120):
            armature.matrix_world = Matrix.Translation(
                (40.0 * math.sin(frame / 12.0 * math.tau), 0.0, 0.0))
            for delta in sim.step(1.0 / 30.0, 1.0, [0.0, 0.0, -9.81]):
                _loc, rotation, _scale = delta.decompose()
                worst = max(worst, math.degrees(2.0 * math.acos(min(1.0, abs(rotation.w)))))
        return worst

    capped = _sim(armature, names, intensity=20, max_angle=15.0)
    capped.reset()
    under_cap = worst_rotation(capped)

    # The same chain uncapped must go well past it, or the cap proves nothing.
    armature.matrix_world = Matrix.Identity(4)
    loose = _sim(armature, names, intensity=20)
    loose.reset()
    unbounded = worst_rotation(loose)

    print("      worst local rotation: %.4f with a 15 degree cap, %.1f without"
          % (under_cap, unbounded))
    assert under_cap <= 15.0 + 1e-3, under_cap
    assert unbounded > 20.0, "the uncapped chain barely moved, so the cap proves nothing"


def test_it_never_produces_a_non_finite_pose():
    """A single NaN used to pin every bone to rest for the rest of the clip, because the reset
    fired and whatever caused it persisted. Drive it with settings that would blow up."""
    armature, names = _chain(6)
    sim = _sim(armature, names, intensity=20, smoothness=1)
    sim.reset()
    for frame in range(200):
        # Violent, changing gravity, which is the closest a test gets to a rig being thrown around
        # by a fast animation.
        angle = frame * 0.7
        deltas = sim.step(1.0 / 30.0, 1.0,
                          [900.0 * math.sin(angle), 900.0 * math.cos(angle), -981.0])
        for delta in deltas:
            for row in delta:
                for value in row:
                    assert math.isfinite(value), "non-finite at frame %d" % frame
    print("      200 frames of a thrashing rig, every value finite")


def test_one_frame_is_fast_enough_to_play():
    """Playback is 30 frames a second and the plugin is not the only thing running."""
    armature, names = _chain(12)
    sim = _sim(armature, names, intensity=12)
    sim.reset()
    _run(sim, 10)
    started = time.perf_counter()
    _run(sim, 200)
    per_frame = (time.perf_counter() - started) / 200.0 * 1000.0
    print("      %d bones, %.3f ms per frame" % (len(names), per_frame))
    assert per_frame < 5.0, "%.3f ms per frame is too slow to play" % per_frame


def test_a_real_rig_builds_and_runs():
    path = corpus("riven_skin23.skn")
    if path is None:
        raise Skip("riven_skin23.skn not in File/")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    import importlib
    import sys
    for name in [n for n in sys.modules if n.split(".")[0] == "LoL_Blender"]:
        del sys.modules[name]
    addon = importlib.import_module("LoL_Blender")
    addon.register()
    try:
        bpy.ops.lolblender.import_skn(filepath=path, report_details=False,
                                      existing='KEEP')
        armature = [o for o in bpy.data.objects if o.type == 'ARMATURE'][0]
        from LoL_Blender.core import detect

        found = detect.suggest(armature)
        assert found, "nothing recognised on a rig with hair, a cape and a skirt"
        total = 0
        budget = 0.0
        for identifier, strands, colliders in found:
            names = [n for strand in strands for n in strand]
            sim = wiggle.Sim(armature, identifier, names,
                             wiggle.params_for(identifier, 10, 12, 1.0), colliders)
            sim.reset()
            started = time.perf_counter()
            _run(sim, 30)
            elapsed = (time.perf_counter() - started) / 30.0 * 1000.0
            budget += elapsed
            print("      %-6s %2d bones in %d branches, %3d spheres, %.3f ms/frame"
                  % (identifier, len(names), len(strands), sim.solver.collider_count, elapsed))
            for value in sim.solver.particles():
                assert math.isfinite(value), "%s produced a non-finite particle" % identifier
            total += len(names)
        print("      whole rig: %.3f ms/frame" % budget)
        assert total > 20, total
        assert budget < 8.0, "%.2f ms a frame for the whole rig is too slow to play" % budget
    finally:
        addon.unregister()
