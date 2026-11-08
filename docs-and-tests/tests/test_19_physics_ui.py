"""Phase 6b: the physics workflow.

The solver is `test_16`. This is everything around it, which is where the workflow lives and where
the reported bugs were:

  * three sets that keep their own bones, branches and settings when you switch between them;
  * a bone list you edit by typing or by selecting, and every edit reaching the solver;
  * colliders from the same name tables the standalone addon used, never including a set's own
    bones;
  * a live handler that moves the rig on a frame change without touching the scene;
  * a bake that keys what you watched, closes a loop, and can be undone - including putting the
    POSE back, which is the bug that left breast bones sitting high after Clear Baked Keys.
"""

import math
import os

import bpy
from mathutils import Matrix, Quaternion, Vector

from LoL_Blender import native
from LoL_Blender.core import bake, detect, wiggle
from LoL_Blender.ui import physics_props
from run import Skip, corpus, riven_anim


def _fresh_addon():
    import importlib
    import sys
    for name in [n for n in sys.modules if n.split(".")[0] == "LoL_Blender"]:
        del sys.modules[name]
    return importlib.import_module("LoL_Blender")


def _load(clip=None):
    path = corpus("riven_skin23.skn")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon = _fresh_addon()
    addon.register()
    bpy.ops.lolblender.import_skn(filepath=path, report_details=False,
                                  existing='KEEP')
    armature = [o for o in bpy.data.objects if o.type == 'ARMATURE'][0]
    if clip is not None:
        from LoL_Blender.core import anm_in
        anm_in.import_anm(clip, armature, replace_existing=True)
    bpy.context.view_layer.objects.active = armature
    return addon, armature


def _setup(armature):
    bpy.ops.lolblender.physics_auto_setup()


# --------------------------------------------------------------------------- the three sets

def test_auto_setup_fills_three_sets_with_branches():
    addon, armature = _load()
    try:
        _setup(armature)
        seen = {}
        summary = []
        for identifier, physics_set in physics_props.sets_of(armature):
            names = [item.name for item in physics_set.bones if item.name]
            branches = {item.branch for item in physics_set.bones}
            summary.append("%s %d bones in %d branches, %d colliders"
                           % (identifier, len(names), len(branches), len(physics_set.colliders)))
            for name in names:
                assert name not in seen, ("%s is in both %s and %s"
                                          % (name, seen[name], identifier))
                seen[name] = identifier
        print("      " + "; ".join(summary))
        assert len(seen) > 20, len(seen)

        # A branch is the whole strand, not just the bone whose name matched: naming only the root
        # gives a hinge, not hair.
        hair = physics_props.get_set(armature, 'HAIR')
        longest = max(len([i for i in hair.bones if i.branch == b])
                      for b in {i.branch for i in hair.bones})
        assert longest >= 3, longest
    finally:
        addon.unregister()


def test_switching_set_keeps_everything_the_other_sets_hold():
    """The explicit ask: Hair to Breast and back must not cost you the bones you typed in."""
    addon, armature = _load()
    try:
        _setup(armature)
        props = armature.lol_physics
        before = {}
        for identifier, physics_set in physics_props.sets_of(armature):
            before[identifier] = (
                [item.name for item in physics_set.bones],
                [item.branch for item in physics_set.bones],
                physics_set.num_branches,
                physics_set.intensity,
                physics_set.max_angle,
                physics_set.collider_text,
            )

        for identifier in ('BREAST', 'CLOTH', 'HAIR', 'BREAST', 'HAIR'):
            props.active = identifier

        for identifier, physics_set in physics_props.sets_of(armature):
            after = (
                [item.name for item in physics_set.bones],
                [item.branch for item in physics_set.bones],
                physics_set.num_branches,
                physics_set.intensity,
                physics_set.max_angle,
                physics_set.collider_text,
            )
            assert after == before[identifier], "%s changed when the tab did" % identifier
        print("      five tab switches, all three sets identical afterwards")
    finally:
        addon.unregister()


def test_each_set_starts_on_its_own_defaults():
    """Breast is not hair. Its intensity, and the movement limit that keeps it off the chest, come
    from the standalone addon's own numbers."""
    addon, armature = _load()
    try:
        for identifier, _attribute, _label in physics_props.SETS:
            bpy.ops.lolblender.physics_add_bone(set_id=identifier, branch=0)
        hair = physics_props.get_set(armature, 'HAIR')
        breast = physics_props.get_set(armature, 'BREAST')
        assert hair.intensity == 8 and breast.intensity == 10, (hair.intensity, breast.intensity)
        assert not hair.limit_movement, "hair should not start capped"
        assert breast.limit_movement and abs(breast.max_angle - 45.0) < 1e-6, (
            breast.limit_movement, breast.max_angle)
        print("      hair 8 uncapped, breast 10 capped at 45 - the source's numbers")
    finally:
        addon.unregister()


# --------------------------------------------------------------------------- editing the list

def test_the_bone_rows_add_remove_and_delete_a_branch():
    addon, armature = _load()
    try:
        hair = physics_props.get_set(armature, 'HAIR')
        hair.num_branches = 2
        for branch in (0, 1):
            for _ in range(3):
                bpy.ops.lolblender.physics_add_bone(set_id='HAIR', branch=branch)
        assert len(hair.bones) == 6

        hair.bones[0].name = "hair_jt"
        hair.bones[1].name = "Cape1"
        assert [i.name for i in hair.bones][:2] == ["hair_jt", "Cape1"]

        # Remove Last takes the last row of THAT branch, not the last row overall.
        bpy.ops.lolblender.physics_remove_bone(set_id='HAIR', index=-1, branch=0, target='BONES')
        assert len([i for i in hair.bones if i.branch == 0]) == 2
        assert len([i for i in hair.bones if i.branch == 1]) == 3

        # The X on a row removes that row.
        bpy.ops.lolblender.physics_remove_bone(set_id='HAIR', index=0, target='BONES')
        assert [i.name for i in hair.bones if i.branch == 0] == ["Cape1"]

        bpy.ops.lolblender.physics_delete_branch(set_id='HAIR', branch=0)
        assert len(hair.bones) == 3, "deleting branch 1 should take its bones with it"
        assert all(item.branch == 0 for item in hair.bones), "the gap was not closed"
        assert hair.num_branches == 1
        print("      added, typed, removed, and deleted a branch")
    finally:
        addon.unregister()


def test_use_selected_bones_adds_them_parents_first():
    addon, armature = _load()
    try:
        bpy.ops.object.mode_set(mode='POSE')
        chain = [b for b in armature.pose.bones if b.name.lower().startswith("cape")]
        if len(chain) < 3:
            raise Skip("this rig has no cape chain")
        for bone in armature.data.bones:
            bone.select = False
        # Select them in the WRONG order on purpose.
        for pose_bone in reversed(chain[:4]):
            pose_bone.bone.select = True
        bpy.ops.lolblender.physics_bones_from_selection(set_id='CLOTH', branch=0, target='BONES')
        bpy.ops.object.mode_set(mode='OBJECT')

        cloth = physics_props.get_set(armature, 'CLOTH')
        names = [item.name for item in cloth.bones]
        depth = {}
        for name in names:
            pose_bone = armature.pose.bones[name]
            level = 0
            node = pose_bone.parent
            while node is not None:
                level += 1
                node = node.parent
            depth[name] = level
        assert names == sorted(names, key=lambda n: (depth[n], n)), names
        print("      %s -> %s" % ([b.name for b in reversed(chain[:4])], names))
    finally:
        addon.unregister()


def test_the_collider_list_is_editable_as_text():
    """Comma-separated, the way the standalone addon had it, so a list can be pasted whole."""
    addon, armature = _load()
    try:
        hair = physics_props.get_set(armature, 'HAIR')
        hair.collider_text = "Spine1, Spine2 ,Head"
        assert [i.name for i in hair.colliders] == ["Spine1", "Spine2", "Head"]
        assert hair.collider_text == "Spine1, Spine2, Head"

        hair.collider_text = "Head,,Head, Spine1"
        assert [i.name for i in hair.colliders] == ["Head", "Spine1"]

        hair.collider_text = ""
        assert len(hair.colliders) == 0
        print("      pasted, de-duplicated and cleared")
    finally:
        addon.unregister()


def test_a_set_never_collides_with_its_own_bones():
    """Pushing a strand off the bones it is made of makes it explode."""
    addon, armature = _load()
    try:
        _setup(armature)
        for identifier, physics_set in physics_props.sets_of(armature):
            own = {item.name for item in physics_set.bones}
            overlap = own & {item.name for item in physics_set.colliders}
            assert not overlap, "%s collides with itself: %s" % (identifier, sorted(overlap)[:4])

        # Auto-Detect obeys the same rule, and gives breasts the arms rather than the spine: a
        # breast bone hangs off the chest, so a spine collider would shove it off the body.
        bpy.ops.lolblender.physics_auto_colliders(set_id='BREAST')
        breast = physics_props.get_set(armature, 'BREAST')
        names = [item.name.lower() for item in breast.colliders]
        assert names, "no colliders at all"
        assert not any("spine" in n for n in names), names
        assert any(k in n for n in names for k in ("clavicle", "arm", "shoulder")), names

        bpy.ops.lolblender.physics_auto_colliders(set_id='HAIR')
        hair = [i.name.lower() for i in physics_props.get_set(armature, 'HAIR').colliders]
        assert any("spine" in n or "head" in n for n in hair), hair
        print("      breasts collide with %d bones, hair with %d" % (len(names), len(hair)))
    finally:
        addon.unregister()


# --------------------------------------------------------------------------- live

def test_the_live_handler_moves_the_bones_on_a_frame_change():
    """No operator, no mode switch, no `view_layer.update()` - just the handler and a frame."""
    clip = riven_anim("riven_skin23_run_ult.anm")
    addon, armature = _load(clip)
    try:
        _setup(armature)
        scene = bpy.context.scene
        armature.lol_physics.live = True

        names = sorted(physics_props.listed_bones(armature))
        scene.frame_set(1)
        before = {n: armature.pose.bones[n].matrix_basis.copy() for n in names}
        for frame in range(2, 30):
            scene.frame_set(frame)
        after = {n: armature.pose.bones[n].matrix_basis.copy() for n in names}

        moved = max(wiggle.angle_between(before[n], after[n]) for n in names)
        print("      %d bones, worst local rotation after 30 frames %.2f degrees"
              % (len(names), moved))
        assert moved > 0.5, "nothing moved: the handler is not running"
        for name in names:
            for row in after[name]:
                for value in row:
                    assert math.isfinite(value), name

        # And turning it off leaves the rig alone.
        armature.lol_physics.live = False
        scene.frame_set(40)
        held = {n: armature.pose.bones[n].matrix_basis.copy() for n in names}
        scene.frame_set(60)
        assert all(held[n] == armature.pose.bones[n].matrix_basis for n in names), (
            "the handler ran with live preview off")
    finally:
        addon.unregister()


def test_a_slider_retunes_the_running_solver_without_rebuilding_it():
    clip = riven_anim("riven_skin23_run_ult.anm")
    addon, armature = _load(clip)
    try:
        _setup(armature)
        from LoL_Blender.ops import physics as ops_physics

        scene = bpy.context.scene
        for frame in range(1, 20):
            scene.frame_set(frame)
        rig = ops_physics.rig_for(armature)
        particles = [list(sim.solver.particles()) for sim in rig.sims]

        physics_props.get_set(armature, 'HAIR').intensity = 3
        again = ops_physics.rig_for(armature)
        assert again is rig, "moving a slider rebuilt the solver"
        assert [list(sim.solver.particles()) for sim in again.sims] == particles, (
            "moving a slider moved the particles")

        # A bone list change MUST rebuild, though.
        # Use a bone not already owned by Cloth; duplicate ownership is now rejected.
        physics_props.get_set(armature, 'HAIR').bones[0].name = "Head"
        assert ops_physics.rig_for(armature) is not rig, "editing the bone list did not rebuild"
        print("      slider re-tunes in place, bone list rebuilds")
    finally:
        addon.unregister()


def test_the_movement_limit_reaches_the_solver_from_the_checkbox():
    addon, armature = _load()
    try:
        _setup(armature)
        from LoL_Blender.ops import physics as ops_physics

        breast = physics_props.get_set(armature, 'BREAST')
        breast.limit_movement = True
        breast.max_angle = 30.0
        rig = ops_physics.rig_for(armature, rebuild=True)
        capped = [sim for sim in rig.sims if sim.kind == 'BREAST']
        assert capped and abs(capped[0].max_angle - 30.0) < 1e-6, "the cap never reached the sim"

        breast.limit_movement = False
        rig.refresh_settings()
        assert capped[0].max_angle == 0.0, "unticking the box left the cap on"
        print("      the checkbox is what turns it on, the slider is what it is set to")
    finally:
        addon.unregister()


# --------------------------------------------------------------------------- bake

def test_a_bake_keys_the_bones_and_clearing_it_puts_the_clip_back():
    """THE REPORTED BUG, and the gap found auditing the fix.

    Two things have to happen when a bake is undone. The POSE has to go back: removing the curves
    leaves `matrix_basis` holding the last simulated frame, and with no channel left to overwrite
    it the bone simply stays there, which is why clearing a breast bake left the bones sitting
    high. And the CLIP has to go back: a League `.anm` usually animates the hair bones itself, the
    bake has to strip those channels to simulate from rest, and deleting someone's animation is not
    something they can undo by hand. The standalone addon kept a copy behind Undo Preview; this
    keeps the same copy behind Clear Baked Keys.
    """
    clip = riven_anim("riven_skin23_idle1.anm")
    addon, armature = _load(clip)
    try:
        _setup(armature)
        names = sorted(physics_props.listed_bones(armature))

        def channels():
            action = armature.animation_data.action
            return {(c.data_path, c.array_index): [round(k.co[1], 6)
                                                   for k in c.keyframe_points]
                    for c in action.fcurves if bake.bone_of(c.data_path) in set(names)}

        # The pose these bones are in when ONLY the clip drives them. That is what "put it back"
        # has to mean here: this clip animates the hair itself, so the answer is not the rest pose.
        armature.lol_physics.live = False
        bpy.context.scene.frame_set(1)
        untouched = {n: armature.pose.bones[n].matrix_basis.copy() for n in names}
        before = channels()
        assert before, "this clip does not animate any of the simulated bones, so it proves nothing"
        assert max(wiggle.angle_between(untouched[n], Matrix.Identity(4)) for n in names) > 1.0, (
            "the clip leaves these bones at rest, so restoring it would prove nothing")

        armature.lol_physics.live = True
        bpy.ops.lolblender.physics_bake()
        action = armature.animation_data.action
        keyed = {bake.bone_of(c.data_path) for c in action.fcurves
                 if 'rotation_quaternion' in c.data_path}
        assert set(names) <= keyed, "these were not keyed: %s" % sorted(set(names) - keyed)
        assert channels() != before, "the bake changed nothing"

        # Live preview OFF first: with it on, the handler simulates again the instant the frame is
        # touched, and what you would be measuring is the next frame of physics rather than
        # whether the clear put things back.
        armature.lol_physics.live = False
        bpy.ops.lolblender.physics_clear_bake()

        assert channels() == before, "the clip did not come back as it was imported"
        bpy.context.scene.frame_set(1)
        # Compared ELEMENT BY ELEMENT, not as an angle. `2*acos(|dot|)` is ill-conditioned next to
        # identity - these bones sit within a thousandth of rest, and there the f32 a keyframe is
        # stored in reads as four hundredths of a degree while the matrix itself agrees to 1e-07.
        worst = max(abs(a - b) for n in names
                    for row_a, row_b in zip(armature.pose.bones[n].matrix_basis, untouched[n])
                    for a, b in zip(row_a, row_b))
        print("      %d bones baked and undone, clip restored, pose back to within %.2e"
              % (len(names), worst))
        assert worst < 1e-5, "the pose was left where the simulation stopped: %.3e" % worst

        # And with live preview back ON, the simulation drives them again rather than leaving them
        # frozen where the bake stopped.
        armature.lol_physics.live = True
        for frame in range(1, 12):
            bpy.context.scene.frame_set(frame)
        bpy.context.scene.frame_set(1)
        driven = max(wiggle.angle_between(armature.pose.bones[n].matrix_basis, untouched[n])
                     for n in names)
        assert driven > 0.1, "the simulation did not take over again: %.4f deg" % driven
    finally:
        addon.unregister()


def test_a_looping_clip_closes_after_the_bake():
    clip = riven_anim("riven_skin23_run_ult.anm")
    addon, armature = _load(clip)
    try:
        _setup(armature)
        action = armature.animation_data.action
        # The clip's OWN range. This importer writes from frame 0, so assuming the animation
        # starts at 1 skips a real frame - and it was that assumption, in the bake, that was
        # overwriting the first frame of every loop with the rest pose.
        start = int(round(action.frame_range[0]))
        end = int(round(action.frame_range[1]))
        looping = bake.detects_loop(action, start, end)
        if not looping:
            raise Skip("riven_skin23_run does not read as a loop on this rig")

        bpy.ops.lolblender.physics_bake()
        names = physics_props.listed_bones(armature)

        # COMPARED AS ROTATIONS, not as raw channel values. The sign pass runs last and is allowed
        # to leave the seam holding -q where the first frame holds q: the same rotation, four
        # channels that differ by up to 2.0, and a curve that reads as broken if you subtract them.
        groups = {}
        others = []
        for curve in action.fcurves:
            if bake.bone_of(curve.data_path) not in names:
                continue
            if 'rotation_quaternion' in curve.data_path:
                groups.setdefault(curve.data_path, {})[curve.array_index] = curve
            else:
                others.append(curve)

        worst = 0.0
        for channels in groups.values():
            if len(channels) < 4:
                continue
            first = Quaternion(tuple(channels[c].evaluate(start) for c in range(4)))
            last = Quaternion(tuple(channels[c].evaluate(end) for c in range(4)))
            if first.magnitude < 1e-8 or last.magnitude < 1e-8:
                continue
            first.normalize()
            last.normalize()
            worst = max(worst, math.degrees(2.0 * math.acos(min(1.0, abs(first.dot(last))))))
        for curve in others:
            worst = max(worst, abs(curve.evaluate(start) - curve.evaluate(end)))

        print("      loop seam: frame %d and frame %d differ by %.2e" % (start, end, worst))
        assert worst < 1e-3, "the loop does not close: %.6f" % worst
    finally:
        addon.unregister()


def test_the_bake_leaves_no_bezier_and_no_sign_flip():
    """Two passes that exist because of two specific artefacts: a Bezier handle overshooting
    between physics frames, and `q` sitting next to `-q` so LINEAR goes the long way round."""
    clip = riven_anim("riven_skin23_idle1.anm")
    addon, armature = _load(clip)
    try:
        _setup(armature)
        bpy.ops.lolblender.physics_bake()
        action = armature.animation_data.action
        names = physics_props.listed_bones(armature)

        groups = {}
        for curve in action.fcurves:
            if bake.bone_of(curve.data_path) not in names:
                continue
            for point in curve.keyframe_points:
                assert point.interpolation in {'LINEAR', 'CONSTANT'}, (
                    "%s has a %s key" % (curve.data_path, point.interpolation))
            if 'rotation_quaternion' in curve.data_path:
                groups.setdefault(curve.data_path, {})[curve.array_index] = curve

        flips = 0
        for channels in groups.values():
            if len(channels) < 4:
                continue
            points = {c: {int(k.co[0]): k.co[1] for k in channels[c].keyframe_points}
                      for c in range(4)}
            frames = sorted(set(points[0]) & set(points[1]) & set(points[2]) & set(points[3]))
            for a, b in zip(frames, frames[1:]):
                dot = sum(points[c][a] * points[c][b] for c in range(4))
                if dot < 0.0:
                    flips += 1
        print("      %d quaternion channels, %d sign flips" % (len(groups), flips))
        assert flips == 0, "%d adjacent keys are in opposite hemispheres" % flips
    finally:
        addon.unregister()


def test_motion_does_nothing_without_a_reference_bone():
    """It shipped off, and it stays off until you name the bone it watches - guessing produces a
    clip that is calm in the wrong places."""
    from LoL_Blender.core import motion

    clip = riven_anim("riven_skin23_run_ult.anm")
    addon, armature = _load(clip)
    try:
        _setup(armature)
        hair = physics_props.get_set(armature, 'HAIR')
        assert abs(hair.motion - 1.0) < 1e-9 and hair.motion_bone == ""

        bpy.ops.lolblender.physics_bake()
        action = armature.animation_data.action
        names = [item.name for item in hair.bones if item.name]
        before = {(c.data_path, c.array_index): [k.co[1] for k in c.keyframe_points]
                  for c in action.fcurves if bake.bone_of(c.data_path) in set(names)}

        hair.motion = 0.5
        changed = motion.calm(bpy.context, armature, action, names,
                              1, int(action.frame_range[1]), "", 0.5)
        assert changed == 0, "Motion ran with no reference bone"
        after = {(c.data_path, c.array_index): [k.co[1] for k in c.keyframe_points]
                 for c in action.fcurves if bake.bone_of(c.data_path) in set(names)}
        assert before == after

        # With a bone, it does reduce the swing.
        reference = "Spine1" if "Spine1" in armature.pose.bones else armature.pose.bones[0].name
        changed = motion.calm(bpy.context, armature, action, names,
                              1, int(action.frame_range[1]), reference, 0.5)
        print("      off without a ref bone, %d keys reduced with one" % changed)
        assert changed > 0, "Motion did nothing even with a reference bone"
    finally:
        addon.unregister()


# --------------------------------------------------------------------------- the loop

def test_the_bake_does_not_overwrite_the_first_frame_of_the_clip():
    """The bug behind "the loop is not perfect any more".

    The standalone addon's importer put a clip on frames 1..N and left frame 0 as the bind pose, so
    every bake forced frame 0 back to rest. THIS importer writes the clip from frame 0. Carrying
    that assumption over meant the bake started at frame 1 and then overwrote frame 0 with the rest
    pose - which for a looping clip is the exact frame the loop closes onto.
    """
    clip = riven_anim("riven_skin23_run_ult.anm")
    addon, armature = _load(clip)
    try:
        _setup(armature)
        action = armature.animation_data.action
        assert int(round(action.frame_range[0])) == 0, (
            "this importer is expected to write from frame 0; the test is checking the wrong thing")

        names = sorted(physics_props.listed_bones(armature))
        bpy.ops.lolblender.physics_bake()

        at_rest = 0
        keyed = 0
        for name in names:
            path = 'pose.bones["%s"].rotation_quaternion' % name
            channels = [action.fcurves.find(path, index=i) for i in range(4)]
            if any(c is None for c in channels):
                continue
            keyed += 1
            first = Quaternion(tuple(c.evaluate(0) for c in channels))
            if first.magnitude > 1e-8:
                first.normalize()
            if wiggle.angle_between(Matrix.Identity(4), first.to_matrix().to_4x4()) < 1e-4:
                at_rest += 1
        print("      %d bones keyed, %d of them left at rest on frame 0" % (keyed, at_rest))
        assert keyed > 5, keyed
        assert at_rest < keyed, "every simulated bone was forced to rest on frame 0"
    finally:
        addon.unregister()


def test_the_loop_switch_overrides_what_the_detector_thinks():
    """Automatic gets it right on nearly everything once it is reading the clip's real range: on
    this corpus every true loop comes out at 0.0000 exactly, because the clips duplicate frame 0 at
    the end. `idle2` and `idle3` are the exceptions - long fidget idles that drift - and no
    threshold separates those from a spell without also calling `death` a loop. So the reading is
    shown in the panel and can be overridden."""
    from LoL_Blender.ops import physics as ops_physics

    clip = riven_anim("riven_skin23_idle2.anm")
    addon, armature = _load(clip)
    try:
        _setup(armature)
        action = armature.animation_data.action
        start = int(round(action.frame_range[0]))
        end = int(round(action.frame_range[1]))
        assert not ops_physics.detected_loop(action), (
            "idle2 is expected to read as NOT a loop; this test is checking the wrong clip")

        armature.lol_physics.loop_mode = 'LOOP'
        bpy.ops.lolblender.physics_bake()

        names = physics_props.listed_bones(armature)
        groups = {}
        for curve in action.fcurves:
            if bake.bone_of(curve.data_path) in names and 'rotation_quaternion' in curve.data_path:
                groups.setdefault(curve.data_path, {})[curve.array_index] = curve
        worst = 0.0
        for channels in groups.values():
            if len(channels) < 4:
                continue
            first = Quaternion(tuple(channels[c].evaluate(start) for c in range(4)))
            last = Quaternion(tuple(channels[c].evaluate(end) for c in range(4)))
            if first.magnitude < 1e-8 or last.magnitude < 1e-8:
                continue
            first.normalize()
            last.normalize()
            worst = max(worst, math.degrees(2.0 * math.acos(min(1.0, abs(first.dot(last))))))
        print("      forced to Loop: seam closes to %.2e degrees on a clip the detector "
              "calls one-shot" % worst)
        assert worst < 1e-3, worst
    finally:
        addon.unregister()


# --------------------------------------------------------------------------- the Custom button

def test_custom_is_one_switch_for_the_whole_rig():
    """The four buttons are one radio, and Custom is a MODE, not a fourth set.

    Custom values are used when, and only when, Custom is the button that is lit. There is one copy
    of them for the whole rig - no per-set copy, no tick box, nothing left switched on behind you.
    Press Hair, Cloth or Breast and every set is back on its own preset immediately.
    """
    addon, armature = _load()
    try:
        _setup(armature)
        props = armature.lol_physics
        hair = physics_props.get_set(armature, 'HAIR')
        cloth = physics_props.get_set(armature, 'CLOTH')
        breast = physics_props.get_set(armature, 'BREAST')

        presets = {
            'HAIR': wiggle.params_for('HAIR', hair.intensity, hair.smoothness, hair.gravity),
            'CLOTH': wiggle.params_for('CLOTH', cloth.intensity, cloth.smoothness, cloth.gravity),
            'BREAST': wiggle.params_for('BREAST', breast.intensity, breast.smoothness,
                                        breast.gravity),
        }
        sets = {'HAIR': hair, 'CLOTH': cloth, 'BREAST': breast}

        def on_presets(where):
            for identifier, physics_set in sets.items():
                got = wiggle.settings_for(physics_set, identifier)
                for key in ('stiff', 'damp', 'gravity', 'stretch'):
                    assert abs(got[key] - presets[identifier][key]) < 1e-3, (
                        "%s: %s is %.4f, not the %s preset's %.4f"
                        % (where, key, got[key], identifier, presets[identifier][key]))

        # --- every preset button leaves every set on its own preset
        for button in ('HAIR', 'CLOTH', 'BREAST'):
            props.active = button
            on_presets("after pressing %s" % button)

        # --- Custom, and it applies to the whole rig
        props.active = 'CUSTOM'
        props.custom_stiff = 42.0
        props.custom_damp = 0.8
        props.custom_gravity = 0.25
        props.custom_stretch = 0.03
        for identifier, physics_set in sets.items():
            got = wiggle.settings_for(physics_set, identifier)
            assert abs(got['stiff'] - 42.0) < 1e-4, (identifier, got['stiff'])
            assert abs(got['damp'] - 0.8) < 1e-4, (identifier, got['damp'])
            assert abs(got['gravity'] - 0.25) < 1e-4, (identifier, got['gravity'])
            assert abs(got['stretch'] - 0.03) < 1e-4, (identifier, got['stretch'])

        # --- and every way back out lands on the presets again, with nothing left behind
        for button in ('HAIR', 'CLOTH', 'BREAST'):
            props.active = 'CUSTOM'
            props.active = button
            on_presets("after Custom then %s" % button)

        # The typed values survive for the next time Custom is pressed. That is storage, not state:
        # nothing is using them while another button is lit.
        assert abs(props.custom_stiff - 42.0) < 1e-4
        print("      3 presets x 3 exits verified; Custom drives all 3 sets at stiff 42.0")
    finally:
        addon.unregister()


def test_there_is_exactly_one_copy_of_the_custom_values():
    """No per-set custom anything. If a set ever grows its own copy, this fails."""
    addon, armature = _load()
    try:
        _setup(armature)
        leaked = []
        for identifier, physics_set in physics_props.sets_of(armature):
            for name in physics_set.bl_rna.properties.keys():
                if "custom" in name.lower():
                    leaked.append("%s.%s" % (identifier, name))
        assert not leaked, "a set grew its own custom values: %s" % leaked
        assert hasattr(armature.lol_physics, "custom_stiff")
        print("      the four values live once, on the object")
    finally:
        addon.unregister()


def test_the_other_panels_keep_showing_the_set_you_were_on_while_custom_is_up():
    """Pressing Custom must not look like it emptied your bone list."""
    addon, armature = _load()
    try:
        _setup(armature)
        props = armature.lol_physics
        props.active = 'BREAST'
        breast = physics_props.get_set(armature)
        props.active = 'CUSTOM'
        still = physics_props.get_set(armature)
        assert still == breast, "Custom changed which set the other panels are editing"
        assert physics_props.edited_set_id(props) == 'BREAST'
        print("      Custom is up and the panels still edit Breast (%d bones)"
              % len([i for i in still.bones if i.name]))
    finally:
        addon.unregister()


def test_loading_another_clip_keeps_the_custom_values():
    """The exact case reported as stupid and annoying: type custom values, load another animation,
    and find the rig quietly back on the Hair preset.

    It cannot happen, because a clip change REBUILDS the solver and the rebuild reads the button
    live through `wiggle.settings_for` - but "cannot happen" is not a measurement, so here it is.
    """
    addon, armature = _load(riven_anim("riven_skin23_run_ult.anm"))
    try:
        _setup(armature)
        from LoL_Blender.ops import physics as ops_physics
        from LoL_Blender.core import anm_in

        props = armature.lol_physics
        props.active = 'CUSTOM'
        props.custom_stiff = 21.5
        props.custom_damp = 0.75

        scene = bpy.context.scene
        for frame in range(0, 8):
            scene.frame_set(frame)
        rig = ops_physics.rig_for(armature)
        sim = [s for s in rig.sims if s.kind == 'HAIR'][0]
        assert abs(sim.settings['stiff'] - 21.5) < 1e-4, sim.settings['stiff']

        anm_in.import_anm(riven_anim("riven_skin23_idle1.anm"), armature, replace_existing=True)
        for frame in range(0, 8):
            scene.frame_set(frame)

        rebuilt = ops_physics.rig_for(armature)
        assert rebuilt is not rig, "the clip change should have rebuilt the solver"
        again = [s for s in rebuilt.sims if s.kind == 'HAIR'][0]
        assert props.active == 'CUSTOM', "loading a clip moved the button"
        assert abs(again.settings['stiff'] - 21.5) < 1e-4, again.settings['stiff']
        assert abs(again.settings['damp'] - 0.75) < 1e-4, again.settings['damp']
        print("      clip swapped, solver rebuilt, still on stiff 21.5 and damp 0.75")
    finally:
        addon.unregister()


def test_the_preset_sliders_cannot_reach_the_rig_while_custom_is_lit():
    """And the moment a preset button is pressed, they can again - at their CURRENT positions."""
    addon, armature = _load(riven_anim("riven_skin23_run_ult.anm"))
    try:
        _setup(armature)
        from LoL_Blender.ops import physics as ops_physics

        props = armature.lol_physics
        hair = physics_props.get_set(armature, 'HAIR')
        props.active = 'CUSTOM'
        props.custom_stiff = 60.0

        rig = ops_physics.rig_for(armature)
        sim = [s for s in rig.sims if s.kind == 'HAIR'][0]

        hair.intensity = 1
        assert abs(sim.settings['stiff'] - 60.0) < 1e-4, (
            "Intensity reached the rig while Custom was lit: %.3f" % sim.settings['stiff'])
        hair.smoothness = 20
        hair.gravity = 0.0
        assert abs(sim.settings['stiff'] - 60.0) < 1e-4

        props.active = 'HAIR'
        expected = wiggle.params_for('HAIR', 1, 20, 0.0)
        assert abs(sim.settings['stiff'] - expected['stiff']) < 1e-3, sim.settings['stiff']
        assert abs(sim.settings['damp'] - expected['damp']) < 1e-3, sim.settings['damp']
        print("      custom held at 60.0 through three slider moves, then the preset took over "
              "at %.1f" % sim.settings['stiff'])
    finally:
        addon.unregister()


def test_a_custom_value_reaches_the_running_solver():
    """Typing a number has to move the hair, without rebuilding the simulation."""
    addon, armature = _load(riven_anim("riven_skin23_run_ult.anm"))
    try:
        _setup(armature)
        # AFTER `_load`, never before: it reloads the package, and the module imported earlier
        # would be a different object with its own solver cache.
        from LoL_Blender.ops import physics as ops_physics

        props = armature.lol_physics
        rig = ops_physics.rig_for(armature)
        sim = [s for s in rig.sims if s.kind == 'HAIR'][0]
        stiff_before = sim.settings['stiff']

        props.active = 'CUSTOM'
        props.custom_stiff = 12.5
        props.custom_damp = 0.9

        assert ops_physics.rig_for(armature) is rig, "a value edit must NOT rebuild the solver"
        assert abs(sim.settings['stiff'] - 12.5) < 1e-4, sim.settings['stiff']
        assert abs(sim.settings['damp'] - 0.9) < 1e-4, sim.settings['damp']

        # And it has to be the SIMULATION that changed, not just the dictionary.
        scene = bpy.context.scene
        scene.frame_set(0)
        rig.reset()
        for frame in range(0, 12):
            scene.frame_set(frame)
        soft = list(sim.solver.particles())

        props.active = 'HAIR'
        scene.frame_set(0)
        rig.reset()
        for frame in range(0, 12):
            scene.frame_set(frame)
        preset = list(sim.solver.particles())

        worst = max(abs(a - b) for a, b in zip(soft, preset))
        print("      stiff %.1f -> 12.5 moves the strand %.3f units after 12 frames"
              % (stiff_before, worst))
        assert worst > 1e-3, worst
    finally:
        addon.unregister()


def test_go_back_to_default_values_restores_the_ponytail_numbers():
    """The button exists for the person who typed 2000 into stiffness and lost the thread."""
    addon, armature = _load()
    try:
        _setup(armature)
        props = armature.lol_physics
        props.active = 'CUSTOM'
        defaults = (props.custom_stiff, props.custom_damp, props.custom_gravity,
                    props.custom_stretch)
        # The defaults ARE the Ponytail curve at 8 / 12, so the panel opens on something that works.
        expected = wiggle.params_for('HAIR', 8, 12, 1.0)
        assert abs(defaults[0] - expected['stiff']) < 1e-2, (defaults[0], expected['stiff'])

        props.custom_stiff = 1999.0
        props.custom_damp = 44.0
        props.custom_gravity = 4.0
        props.custom_stretch = 0.9
        bpy.ops.lolblender.physics_reset_custom()

        got = (props.custom_stiff, props.custom_damp, props.custom_gravity, props.custom_stretch)
        assert all(abs(a - b) < 1e-4 for a, b in zip(got, defaults)), (got, defaults)
        # The button stays on Custom: Reset is "back to a known-good starting place", not "give up".
        assert props.active == 'CUSTOM'
        print("      reset restored stiff %.4f from 1999.0" % got[0])
    finally:
        addon.unregister()


def test_a_bake_records_the_custom_values_and_not_the_preset():
    """The preview and the bake must not disagree about which numbers are in force."""
    clip = riven_anim("riven_skin23_run_ult.anm")
    addon, armature = _load(clip)
    try:
        _setup(armature)
        for identifier, physics_set in physics_props.sets_of(armature):
            physics_set.enabled = (identifier == 'HAIR')
        props = armature.lol_physics
        names = physics_props.listed_bones(armature)

        def baked():
            bpy.ops.lolblender.physics_bake()
            action = armature.animation_data.action
            out = []
            for curve in sorted(action.fcurves, key=lambda c: (c.data_path, c.array_index)):
                if bake.bone_of(curve.data_path) in names and 'rotation' in curve.data_path:
                    out.extend(curve.evaluate(f) for f in range(0, 12))
            bpy.ops.lolblender.physics_clear_bake()
            return out

        preset = baked()
        props.active = 'CUSTOM'
        props.custom_stiff = 15.0
        props.custom_damp = 0.6
        custom = baked()

        assert len(preset) == len(custom) and preset, (len(preset), len(custom))
        worst = max(abs(a - b) for a, b in zip(preset, custom))
        print("      bake follows the custom values: %.4f apart on the quaternions" % worst)
        assert worst > 1e-3, worst
    finally:
        addon.unregister()


def test_a_one_shot_bake_starts_from_rest():
    """A clip that plays once has a beginning, so the bake must not warm the strand up first.

    The standalone addon reset the solver at the start frame and recorded straight through - no
    warm-up lap, no settling frames, only the loop branch got those. An earlier build here ran one
    full pass over the clip plus eight settling frames before recording a NON-looping one, which
    handed frame one the velocity from the end of the animation. This is that regression, pinned:
    force Plays once and the first frame has to come out at the animated pose.
    """
    clip = riven_anim("riven_skin23_spell1a.anm")
    addon, armature = _load(clip)
    try:
        _setup(armature)
        action = armature.animation_data.action
        start = int(round(action.frame_range[0]))
        armature.lol_physics.loop_mode = 'ONCE'
        bpy.ops.lolblender.physics_bake()

        names = physics_props.listed_bones(armature)
        worst = 0.0
        for curve in action.fcurves:
            if bake.bone_of(curve.data_path) not in names:
                continue
            if 'rotation_quaternion' not in curve.data_path:
                continue
            rest = 1.0 if curve.array_index == 0 else 0.0
            worst = max(worst, abs(curve.evaluate(start) - rest))
        print("      Plays once: frame %d is %.2e off the animated pose" % (start, worst))
        assert worst < 1e-4, worst
    finally:
        addon.unregister()


def test_loading_another_clip_restarts_the_simulation():
    """Reported as "play on a loop does not take the change of animation into account".

    The solver caches per rig. If the clip is not part of what that cache is keyed on, switching
    animations carries the old momentum into the new one and the strand arrives already swinging.
    """
    from LoL_Blender.ops import physics as ops_physics

    first = riven_anim("riven_skin23_run_ult.anm")
    second = riven_anim("riven_skin23_idle1.anm")
    addon, armature = _load(first)
    try:
        _setup(armature)
        scene = bpy.context.scene
        for frame in range(scene.frame_start, scene.frame_start + 25):
            scene.frame_set(frame)
        rig = ops_physics.rig_for(armature)
        moving = max(abs(v) for sim in rig.sims for v in sim.solver.particles())
        assert moving > 0.0

        from LoL_Blender.core import anm_in
        anm_in.import_anm(second, armature, replace_existing=True)
        again = ops_physics.rig_for(armature)
        assert again is not rig, "the solver kept running the previous clip's simulation"

        # And the scene range follows the new clip, so playback loops over IT.
        action = armature.animation_data.action
        clip_first = int(round(action.frame_range[0]))
        clip_last = int(round(action.frame_range[1]))
        assert scene.frame_start == clip_first
        assert scene.frame_end == anm_in.playback_end(action, clip_first, clip_last)
        print("      clip swap rebuilt the solver and moved the range to %d..%d"
              % (scene.frame_start, scene.frame_end))
    finally:
        addon.unregister()


def test_playback_does_not_reset_when_it_wraps():
    """What makes a live loop look seamless: crossing the seam is one step forward, not a restart.

    The reset now happens BEFORE the clip's first frame rather than at frame 0, so a clip that
    starts at 0 is never interrupted by it.
    """
    from LoL_Blender.ops import physics as ops_physics

    clip = riven_anim("riven_skin23_run_ult.anm")
    addon, armature = _load(clip)
    try:
        _setup(armature)
        scene = bpy.context.scene
        start, end = scene.frame_start, scene.frame_end
        assert start == 0, "this clip is expected to start at frame 0"

        names = sorted(physics_props.listed_bones(armature))
        for lap in range(3):
            for frame in range(start, end + 1):
                scene.frame_set(frame)

        # What one ordinary frame of this clip does to the pose, as the yardstick. Some of these
        # bones are one-unit tip markers that swing through 170 degrees every frame whatever
        # happens, so an absolute limit would measure them and nothing else.
        scene.frame_set(start + 5)
        a = {n: armature.pose.bones[n].matrix_basis.copy() for n in names}
        scene.frame_set(start + 6)
        b = {n: armature.pose.bones[n].matrix_basis.copy() for n in names}
        ordinary = max(wiggle.angle_between(a[n], b[n]) for n in names)

        for frame in range(start, end + 1):
            scene.frame_set(frame)
        before_wrap = {n: armature.pose.bones[n].matrix_basis.copy() for n in names}
        scene.frame_set(start)          # the wrap playback does
        after_wrap = {n: armature.pose.bones[n].matrix_basis.copy() for n in names}
        moved = max(wiggle.angle_between(before_wrap[n], after_wrap[n]) for n in names)

        rig = ops_physics.rig_for(armature)
        settled = max(abs(v) for sim in rig.sims for v in sim.solver.particles())
        print("      the wrap moves the pose %.1f degrees against %.1f for an ordinary frame, "
              "and the particles are still out at %.1f" % (moved, ordinary, settled))
        assert settled > 1.0, "the simulation was reset by the wrap"
        assert moved < ordinary * 2.0, (
            "the wrap is a jump, not a step: %.1f against %.1f" % (moved, ordinary))
    finally:
        addon.unregister()


def test_baking_twice_still_restores_the_original_clip():
    """The copy is taken once, not on every bake.

    By the time a second bake runs, the action already carries the first bake's curves. Copying it
    again would leave Clear Baked Keys restoring a bake instead of the animation, and the hand
    animation the clip shipped with would be gone with nothing left pointing at it.
    """
    clip = riven_anim("riven_skin23_idle1.anm")
    addon, armature = _load(clip)
    try:
        _setup(armature)
        names = sorted(physics_props.listed_bones(armature))

        def channels():
            action = armature.animation_data.action
            return {(c.data_path, c.array_index): [round(k.co[1], 6) for k in c.keyframe_points]
                    for c in action.fcurves if bake.bone_of(c.data_path) in set(names)}

        armature.lol_physics.live = False
        bpy.context.scene.frame_set(0)
        original = channels()
        assert original, "this clip does not animate the simulated bones"

        bpy.ops.lolblender.physics_bake()
        first = channels()
        assert first != original

        physics_props.get_set(armature, 'HAIR').intensity = 16
        bpy.ops.lolblender.physics_bake()
        assert channels() != first, "the second bake changed nothing"

        armature.lol_physics.live = False
        bpy.ops.lolblender.physics_clear_bake()
        print("      baked twice, cleared once, %d channels back to the imported values"
              % len(original))
        assert channels() == original, "the clear restored a bake instead of the clip"
    finally:
        addon.unregister()


# --------------------------------------------------------------------------- swept collision

def _collider_distance(point, matrices, halves, radii, index):
    """Signed distance from a world point to one collider, mirroring `Collider::distance`.

    The tests used to read a sphere's radius out of the collider matrix's scale. The matrix is
    rigid now and the shape travels beside it, so the tests do the same arithmetic the solver does
    rather than a sphere approximation of it - otherwise "is the particle inside" would be asking
    about a different shape than the one it hit.

    THE HALF-EXTENTS ARE SHRUNK BY THE ROUNDING, exactly as `set_colliders` does on the way in.
    Leaving that out measures a shape `rounding` bigger than the one the solver holds, in every
    direction. It was invisible while the rounding was a third of the smallest half-extent and it
    stopped being invisible the moment that went up: two frames of `death` read as inside a
    collider that the solver had never put them in.
    """
    m = Matrix([matrices[index * 16 + r * 4:index * 16 + r * 4 + 4] for r in range(4)])
    local = m.inverted() @ Vector(point)
    full = Vector(halves[index * 3:index * 3 + 3])
    half = Vector((max(0.0, full.x - radii[index]), max(0.0, full.y - radii[index]),
                   max(0.0, full.z - radii[index])))
    q = Vector((abs(local.x) - half.x, abs(local.y) - half.y, abs(local.z) - half.z))
    outside = Vector((max(q.x, 0.0), max(q.y, 0.0), max(q.z, 0.0))).length
    inside = min(max(q.x, q.y, q.z), 0.0)
    return outside + inside - radii[index]


def _sphere(centre, radius):
    """One collider sphere as the solver takes them now: a RIGID matrix, zero half-extents and the
    radius beside it. A sphere is a rounded box that has been rounded all the way."""
    flat = []
    for row in Matrix.Translation(Vector(centre)):
        flat.extend(row)
    return flat, [0.0, 0.0, 0.0], [radius]


def test_a_particle_thrown_across_a_sphere_does_not_come_out_the_far_side():
    """The reported failure: at high speed the physics goes through the collider.

    Wiggle's collision only ever asks where the particle IS. A strand that moves further in one
    frame than a sphere is wide is outside it at both ends of the step, so nothing fires and the
    hair is simply on the other side of the shoulder. The swept test asks where the particle WENT.

    Driven at the solver, with no rig in the way: one bone, one sphere sitting squarely in the path,
    and an animated pose that jumps the particle from one side to the other in a single frame.
    """
    solver = native.rs().Wiggle([-1], [False], [1.0])
    solver.set_params([200.0], [2.0], [0.0], [1.0], [0.0], [True],
                      [0.05], [0.0], [0.0], [0.0])

    def anim_at(x):
        m = Matrix.Translation(Vector((x, 0.0, 0.0)))
        flat = []
        for row in m:
            flat.extend(row)
        return flat

    # The sphere sits at the origin. The bone starts well to the left and the animation puts it
    # well to the right one frame later - a 20 unit step across a sphere 6 units wide.
    solver.set_colliders(*_sphere((0.0, 0.0, 0.0), 3.0))
    solver.reset(anim_at(-10.0))
    start = Vector(solver.particles()[0:3])
    assert start.x < -3.0, start

    solver.set_colliders(*_sphere((0.0, 0.0, 0.0), 3.0))
    solver.step(anim_at(10.0), [1.0, 1.0, 1.0], 1.0 / 30.0, 1.0, [0.0, 0.0, 0.0], 20)
    after = Vector(solver.particles()[0:3])

    print("      one 20 unit step across a sphere of radius 3: x %.3f -> %.3f, %.3f from the centre"
          % (start.x, after.x, after.length))
    assert after.x < 0.0, (
        "the particle came out the far side at x=%.3f - it tunnelled straight through" % after.x)
    assert after.length >= 3.0 - 1e-6, (
        "the particle finished INSIDE the sphere, %.3f from its centre" % after.length)


def test_the_swept_test_leaves_a_slow_approach_alone():
    """It must fire only on the case the static test provably cannot see: both ends of the step
    outside the sphere. A particle that simply comes to rest against a collider has to behave
    exactly as it did before the sweep existed, which is what this pins.
    """
    solver = native.rs().Wiggle([-1], [False], [1.0])
    solver.set_params([200.0], [2.0], [0.0], [1.0], [0.0], [True],
                      [0.05], [0.0], [0.0], [0.0])

    def anim_at(x):
        m = Matrix.Translation(Vector((x, 0.0, 0.0)))
        flat = []
        for row in m:
            flat.extend(row)
        return flat

    solver.set_colliders(*_sphere((0.0, 0.0, 0.0), 3.0))
    solver.reset(anim_at(-10.0))
    positions = []
    for step in range(1, 25):
        x = -10.0 + step * 0.4                      # 0.4 a frame: far shorter than the sphere
        solver.set_colliders(*_sphere((0.0, 0.0, 0.0), 3.0))
        solver.step(anim_at(x), [1.0, 1.0, 1.0], 1.0 / 30.0, 1.0, [0.0, 0.0, 0.0], 20)
        positions.append(Vector(solver.particles()[0:3]))

    closest = min(p.length for p in positions)
    ended = positions[-1]
    print("      creeping in at 0.4 a frame: closest approach %.4f, ends %.4f from the centre"
          % (closest, ended.length))
    # It stops on the surface plus its own skin offset, and never gets pushed off early.
    assert closest >= 3.0 - 1e-6, closest
    assert closest < 3.0 + 0.5, closest


def test_the_swept_test_only_changes_the_clips_that_were_tunnelling():
    """The whole point of a swept test over a substep count: it costs nothing when nothing tunnels.

    Riven skin23, BackHair4 and its children, colliding with the spine, shoulders, neck and head -
    the setup the report named. Before the sweep, `run_ult` crossed a sphere twice with both ends
    of the step outside it, worst 0.205 units deep on a 6.48 unit step, and steps reached 22.6
    units elsewhere. This asserts nothing crosses deeply any more.
    """
    from LoL_Blender.core import anm_in

    colliders = ["Spine1", "Spine2", "Spine3", "L_Shoulder", "R_Shoulder", "Neck", "Head"]
    addon, armature = _load()
    try:
        names = []
        stack = [armature.pose.bones["BackHair4"]]
        while stack:
            pose_bone = stack.pop(0)
            names.append(pose_bone.name)
            stack.extend(pose_bone.children)
        missing = [c for c in colliders if c not in armature.pose.bones]
        assert not missing, missing

        scene = bpy.context.scene
        params = wiggle.params_for('HAIR', 8, 12, 1.0)
        worst_overall = 0.0
        report = []
        for clip in ("riven_skin23_run_ult.anm", "riven_skin23_spell1a.anm",
                     "riven_skin23_spell1b.anm"):
            anm_in.import_anm(riven_anim(clip), armature, replace_existing=True)
            action = armature.animation_data.action
            first = int(round(action.frame_range[0]))
            last = int(round(action.frame_range[1]))

            sim = wiggle.Sim(armature, 'HAIR', names, params, colliders)
            scene.frame_set(first)
            sim.reset()

            previous = None
            worst = 0.0
            longest = 0.0
            for frame in range(first, last + 1):
                scene.frame_set(frame)
                sim.step(1.0 / 30.0, 1, list(scene.gravity), write=False)
                matrices, halves, radii = sim.colliders()
                points = list(sim.solver.particles())
                if previous is not None:
                    for i in range(len(sim.topology.names)):
                        a = Vector(previous[i * 3:i * 3 + 3])
                        b = Vector(points[i * 3:i * 3 + 3])
                        step = b - a
                        longest = max(longest, step.length)
                        if step.length < 1e-9:
                            continue
                        for index in range(len(radii)):
                            if (_collider_distance(a, matrices, halves, radii, index) <= 0.0
                                    or _collider_distance(b, matrices, halves, radii, index) <= 0.0):
                                continue    # an ordinary contact; the static push-out owns it
                            # Walk the step and take the deepest the straight line ever gets.
                            deepest = 0.0
                            for k in range(1, 16):
                                mid = a + step * (k / 16.0)
                                deepest = max(deepest, -_collider_distance(
                                    mid, matrices, halves, radii, index))
                            worst = max(worst, deepest)
                previous = points
            worst_overall = max(worst_overall, worst)
            report.append("%s worst crossing %.3f on steps up to %.1f"
                          % (clip.replace("riven_skin23_", "").replace(".anm", ""), worst, longest))
        print("      " + "; ".join(report))
        # Was 0.205 before the swept test, on a rig whose bones are 12 to 22 units long.
        assert worst_overall < 0.10, worst_overall
    finally:
        addon.unregister()


def test_the_pose_survives_an_evaluation_that_is_not_a_frame_change():
    """The pause bug: stop playback and the strand jumps back onto the .anm pose.

    Every simulated bone is ALSO keyed by the clip - measured here, 40 of 40 - so re-evaluating
    the object's animation overwrites `matrix_basis` with the file's value. While the clip plays
    that never shows, because a frame change is immediately followed by the handler writing again.
    Paused, the next redraw or click reverts the rig. Measured before the fix: 7.13 on
    `C_Rear_Skirt5`.
    """
    addon, armature = _load(riven_anim("riven_skin23_run_ult.anm"))
    try:
        _setup(armature)
        scene = bpy.context.scene
        names = sorted(physics_props.listed_bones(armature))

        keyed = {curve.data_path.split('"')[1] for curve in armature.animation_data.action.fcurves
                 if 'pose.bones["' in curve.data_path}
        overlap = [n for n in names if n in keyed]
        assert overlap, "the clip does not key the simulated bones, so this cannot reproduce"

        for frame in range(0, 18):
            scene.frame_set(frame)
        before = {n: armature.pose.bones[n].matrix_basis.copy() for n in names}
        moved = max(max(abs(before[n][r][c] - (1.0 if r == c else 0.0))
                        for r in range(4) for c in range(4)) for n in names)
        assert moved > 1e-3, "the physics never moved anything, so this proves nothing"

        # Exactly what pausing does: no frame change, but the animation is evaluated again.
        armature.update_tag(refresh={'TIME'})
        bpy.context.view_layer.update()

        worst, who = 0.0, ""
        for n in names:
            a = before[n]
            b = armature.pose.bones[n].matrix_basis
            d = max(abs(a[r][c] - b[r][c]) for r in range(4) for c in range(4))
            if d > worst:
                worst, who = d, n
        print("      %d of %d simulated bones are keyed by the clip; after a re-evaluation with no "
              "frame change the pose moved %.4f (%s)" % (len(overlap), len(names), worst, who))
        assert worst < 1e-4, (worst, who)
    finally:
        addon.unregister()


def test_restoring_settles_instead_of_tagging_the_depsgraph_for_ever():
    """The restore runs from a depsgraph handler, so it must write only when it has to.

    Writing unconditionally would tag the depsgraph on every pass and the handler would never
    settle - a frozen viewport rather than a wrong one.
    """
    addon, armature = _load(riven_anim("riven_skin23_run_ult.anm"))
    try:
        _setup(armature)
        from LoL_Blender.ops import physics as ops_physics

        scene = bpy.context.scene
        for frame in range(0, 14):
            scene.frame_set(frame)
        rig = ops_physics.rig_for(armature)

        assert not rig.restore(), "nothing changed, so restore must not write"
        armature.update_tag(refresh={'TIME'})
        bpy.context.view_layer.update()
        # The handler already put it back, so a second call has nothing left to do.
        assert not rig.restore(), "restore never settles - it would loop in the handler"
        print("      restore writes once and then reports nothing to do")
    finally:
        addon.unregister()


def test_motion_finds_the_bone_that_carries_the_character():
    """Motion needed a reference bone typed by hand and said so and nothing else, which is not a
    usable instruction. The dropper fills it with the bone that actually travels."""
    addon, armature = _load()
    try:
        _setup(armature)
        found = detect.motion_reference(armature)
        assert found in armature.pose.bones, found

        physics_set = physics_props.get_set(armature, 'HAIR')
        physics_set.motion_bone = ""
        assert bpy.ops.lolblender.physics_motion_bone(set_id='HAIR') == {'FINISHED'}
        assert physics_set.motion_bone == found, (physics_set.motion_bone, found)

        # It has to be a bone that TRAVELS, never one that swings, or Motion would calm the hair
        # from the hair's own movement.
        simulated = physics_props.listed_bones(armature)
        assert found not in simulated, (
            "Motion would be watching a simulated bone: %r" % found)
        print("      Motion watches %r, which is not one of the %d simulated bones"
              % (found, len(simulated)))
    finally:
        addon.unregister()


# --------------------------------------------------------------------------- the timeline

def test_loading_a_clip_puts_the_timeline_on_it_and_rewinds():
    """Reported as: the timeline is not set to the animation's frames, so it just carries on doing
    nothing, and the live preview looks broken while a bake of the same clip is fine.

    Blender does not move the current frame when a range changes. A playhead left at 120 by the
    previous clip is simply outside a 0..38 one: the action holds its last pose, nothing moves, and
    the physics has nothing to follow. A bake is unaffected because it drives the frames itself,
    which is exactly the asymmetry that was reported.
    """
    addon, armature = _load()
    try:
        from LoL_Blender.core import anm_in

        scene = bpy.context.scene
        scene.frame_start = 1
        scene.frame_end = 250
        scene.frame_set(120)

        anm_in.import_anm(riven_anim("riven_skin23_run_ult.anm"), armature,
                          replace_existing=True)
        action = armature.animation_data.action
        first = int(round(action.frame_range[0]))
        last = int(round(action.frame_range[1]))

        print("      clip %s is %d..%d; timeline is now %d..%d at frame %d"
              % (action.name, first, last, scene.frame_start, scene.frame_end,
                 scene.frame_current))
        assert scene.frame_start == first, (scene.frame_start, first)
        # `run_ult` loops, so its last baked frame is the wrap - the same pose as frame 0 - and
        # playing it would freeze the character for a frame every lap. See
        # `test_08_anm_import.test_a_looping_clip_does_not_play_its_wrap_frame`.
        assert scene.frame_end == anm_in.playback_end(action, first, last), (
            scene.frame_end, last)
        assert scene.frame_end == last - 1, (
            "`run_ult` is expected to be detected as looping, so this test no longer covers the "
            "wrap-frame rule")
        assert scene.frame_current == first, (
            "the playhead was left at %d, outside the clip" % scene.frame_current)
        assert not scene.use_preview_range, "a leftover preview range would override the new one"
    finally:
        addon.unregister()


def test_clicking_a_clip_already_in_the_file_still_rewinds():
    """The branch that reuses an action instead of re-importing it was the one that did nothing at
    all to the timeline."""
    addon, armature = _load()
    try:
        scene = bpy.context.scene
        path = riven_anim("riven_skin23_run_ult.anm")
        name = os.path.splitext(os.path.basename(path))[0]

        bpy.ops.lolblender.load_clip(filepath=path, clip_name=name)
        first = int(round(armature.animation_data.action.frame_range[0]))

        # Wander off, then click the SAME clip again: it is in `bpy.data.actions` now, so the
        # importer is skipped entirely.
        scene.frame_set(scene.frame_end)
        assert scene.frame_current != first
        actions_before = len(bpy.data.actions)

        bpy.ops.lolblender.load_clip(filepath=path, clip_name=name)
        assert len(bpy.data.actions) == actions_before, "the action was duplicated"
        assert scene.frame_current == first, (
            "clicking a clip already in the file left the playhead at %d" % scene.frame_current)
        print("      re-clicking a loaded clip rewinds to %d without duplicating the action"
              % first)
    finally:
        addon.unregister()


def test_the_live_preview_moves_when_the_playhead_is_on_the_clip():
    """The end to end version of the report: load, play, and the hair has to move.

    Driven exactly as playback does it - a frame change per frame, nothing else - and it checks the
    two failure modes separately: nothing moving at all, and moving once and then freezing.
    """
    addon, armature = _load()
    try:
        from LoL_Blender.core import anm_in
        from LoL_Blender.ops import physics as ops_physics

        _setup(armature)
        scene = bpy.context.scene
        scene.frame_set(120)                    # deliberately off the clip, as after another one
        anm_in.import_anm(riven_anim("riven_skin23_run_ult.anm"), armature,
                          replace_existing=True)

        names = sorted(physics_props.listed_bones(armature))
        moved_on = []
        previous = None
        for frame in range(scene.frame_start, scene.frame_end + 1):
            scene.frame_set(frame)
            pose = [armature.pose.bones[n].matrix_basis.copy() for n in names]
            if previous is not None:
                delta = max(abs(a[r][c] - b[r][c])
                            for a, b in zip(pose, previous)
                            for r in range(4) for c in range(4))
                if delta > 1e-4:
                    moved_on.append(frame)
            previous = pose

        span = scene.frame_end - scene.frame_start
        print("      %d of %d frames moved the rig" % (len(moved_on), span))
        assert moved_on, "the live preview never moved anything"
        assert len(moved_on) > span * 0.6, (
            "the preview moved on only %d of %d frames - it froze partway"
            % (len(moved_on), span))
    finally:
        addon.unregister()


# --------------------------------------------------------------------------- breasts on a death

def test_the_movement_limit_is_what_keeps_breasts_sane_on_a_fall():
    """Reported as: with body collision on the arms, a fall makes the breasts do anything, and
    intensity 20 bugs in every direction.

    The collider is not the cause. Measured on `death` with the clavicles and hands as colliders:
    ZERO frames put a particle inside a sphere, and the nearest sphere surface is 4.2 units away at
    rest. What breaks is the bone turning past 140 degrees, which is it pointing backwards - and
    that is exactly what the 45 degree movement limit exists to stop. It is on by default for
    Breast; with it off, this is what happens.
    """
    clip = riven_anim("riven_skin23_death.anm")
    addon, armature = _load(clip)
    try:
        _setup(armature)
        physics_set = physics_props.get_set(armature, 'BREAST')
        names = [item.name for item in physics_set.bones if item.name]
        assert names, "auto setup found no breast bones"
        # WHAT AUTO-DETECT ACTUALLY OFFERS, which is the configuration a user gets. The clavicles
        # ARE in it, as they were in the standalone addon - see `detect.BODY_COLLIDERS`. They were
        # taken out while a collider was a box fitted to the mesh, which reached the breast bone at
        # rest; a clavicle sphere is 0.22 of a short bone and reaches nothing it should not, and
        # measured on `death` it makes the swing calmer rather than worse - 92.8 degrees without
        # them, 83.6 with.
        colliders = [item.name for item in physics_set.colliders]
        assert colliders, "auto setup gave the breast set no colliders"
        assert [n for n in colliders if "clavicle" in n.lower()], colliders
        for identifier, other in physics_props.sets_of(armature):
            other.enabled = (identifier == 'BREAST')

        scene = bpy.context.scene
        action = armature.animation_data.action
        first = int(round(action.frame_range[0]))
        last = int(round(action.frame_range[1]))

        def run(intensity, limit):
            params = wiggle.params_for('BREAST', intensity, 20, 1.0)
            sim = wiggle.Sim(armature, 'BREAST', names, params, colliders,
                             45.0 if limit else 0.0)
            scene.frame_set(first)
            sim.reset()
            peak = 0.0
            inside = 0
            for frame in range(first, last + 1):
                scene.frame_set(frame)
                deltas = sim.step(1.0 / 30.0, 1, list(scene.gravity), write=False)
                for matrix in deltas:
                    quaternion = matrix.to_quaternion()
                    peak = max(peak, math.degrees(
                        2.0 * math.acos(min(1.0, abs(quaternion.w)))))
                matrices, halves, radii = sim.colliders()
                points = sim.solver.particles()
                for i in range(len(sim.topology.names)):
                    position = Vector(points[i * 3:i * 3 + 3])
                    for index in range(len(radii)):
                        if _collider_distance(position, matrices, halves, radii, index) < 0.0:
                            inside += 1
            return peak, inside

        loose_peak, loose_inside = run(20, False)
        capped_peak, capped_inside = run(20, True)
        print("      intensity 20 on a death: %.1f deg with the limit off, %.1f with it on; "
              "%d and %d frames inside a collider"
              % (loose_peak, capped_peak, loose_inside, capped_inside))

        # The collider is exonerated: nothing ever ends a frame inside one, either way.
        assert loose_inside == 0 and capped_inside == 0, (loose_inside, capped_inside)
        # And the limit is what makes the difference.
        assert loose_peak > 120.0, (
            "this clip no longer drives the bone past 120 degrees, so the test proves nothing")
        assert capped_peak <= 45.0 + 1e-3, capped_peak
    finally:
        addon.unregister()


def test_the_gravity_slider_moves_a_breast_set():
    """Reported as "I get the impression gravity changes nothing". It did not, and now it does.

    The standalone addon's curve tops out at 0.03, and 0.03 is not distinguishable from zero:
    measured on `idle1`, the raw values 0.03, 0.3 and 3.0 sag the particle by 0.01%, 0.07% and
    0.68% of a bone length. It only becomes visible around 30. The cause is the unit scale -
    Blender's gravity is 9.81 in BLENDER units and a champion is 176 units tall - and hair and
    cloth only get away with it because gravity accumulates down a chain, which two bones on a
    chest do not have.

    So the breast curve tops out at `BREAST_GRAVITY` instead. Gravity 0 still reproduces the old
    behaviour exactly, because the old value was never distinguishable from none.
    """
    addon, armature = _load(riven_anim("riven_skin23_idle1.anm"))
    try:
        _setup(armature)
        scene = bpy.context.scene
        action = armature.animation_data.action
        first = int(round(action.frame_range[0]))
        last = int(round(action.frame_range[1]))

        physics_set = physics_props.get_set(armature, 'BREAST')
        names = [item.name for item in physics_set.bones if item.name]
        assert names
        for identifier, other in physics_props.sets_of(armature):
            other.enabled = (identifier == 'BREAST')

        def settle(multiplier):
            params = wiggle.params_for('BREAST', 20, 12, multiplier)
            sim = wiggle.Sim(armature, 'BREAST', names, params, (), 0.0)
            scene.frame_set(first)
            sim.reset()
            for frame in range(first, last + 1):
                scene.frame_set(frame)
                sim.step(1.0 / 30.0, 1, list(scene.gravity), write=False)
            return list(sim.solver.particles()), max(sim.topology.rest_lengths)

        none, length = settle(0.0)
        half, _ = settle(0.5)
        full, _ = settle(1.0)
        sag_half = max(abs(a - b) for a, b in zip(half, none)) / length
        sag_full = max(abs(a - b) for a, b in zip(full, none)) / length

        print("      gravity 0.5 sags %.1f%% of a bone, 1.0 sags %.1f%%"
              % (sag_half * 100.0, sag_full * 100.0))
        assert sag_full > 0.05, (
            "the gravity slider still does nothing on a breast set: %.4f%%" % (sag_full * 100.0))
        assert sag_full > sag_half > 0.0, (sag_half, sag_full)
    finally:
        addon.unregister()


def test_gravity_still_works_on_a_long_chain():
    """Cloth was never the problem and must not become one: gravity accumulates down a chain, and
    25 bones have plenty of chain."""
    addon, armature = _load(riven_anim("riven_skin23_idle1.anm"))
    try:
        _setup(armature)
        scene = bpy.context.scene
        action = armature.animation_data.action
        first = int(round(action.frame_range[0]))
        last = int(round(action.frame_range[1]))
        physics_set = physics_props.get_set(armature, 'CLOTH')
        names = [item.name for item in physics_set.bones if item.name]
        assert names
        for identifier, other in physics_props.sets_of(armature):
            other.enabled = (identifier == 'CLOTH')

        moved = []
        for multiplier in (0.0, 0.5):
            params = wiggle.params_for('CLOTH', 8, 12, multiplier)
            sim = wiggle.Sim(armature, 'CLOTH', names, params, (), 0.0)
            scene.frame_set(first)
            sim.reset()
            for frame in range(first, last + 1):
                scene.frame_set(frame)
                sim.step(1.0 / 30.0, 1, list(scene.gravity), write=False)
            moved.append((list(sim.solver.particles()), max(sim.topology.rest_lengths)))

        sag = max(abs(a - b) for a, b in zip(moved[1][0], moved[0][0])) / moved[0][1]
        print("      cloth: gravity 0 to 0.5 sags %.1f%% of a bone" % (sag * 100.0))
        assert sag > 0.01, sag
    finally:
        addon.unregister()


def test_turning_collision_on_must_not_make_the_motion_worse():
    """Reported as: the breasts only break when I turn collisions on, and the old plugin was fine
    with the same list of bones. Both halves were true.

    It was increasing it - 80.0 degrees to 127.4 on `death` at intensity 7 - without a single frame
    ever putting a particle inside a sphere, which is what said the contact was not real.

    The swept test was measuring the segment in WORLD space. On a death animation the whole
    character travels, so every particle covers a long world-space path each frame while barely
    moving relative to the body, and any arm sphere sitting along that path read as a tunnel that
    never happened. Measuring it in the collider's own frame, with the start point through the
    collider's PREVIOUS matrix, makes a body and an arm falling together produce almost no relative
    motion and the test stays silent. That is what this guards.

    "A COLLIDER MAY NOT ADD A DEGREE" IS NOT A PROPERTY OF THE OLD PLUGIN, and this test spent a
    long time pretending it was. The collision path is now verified against the standalone addon
    end to end - `test_21.test_the_collision_is_the_old_plugins_on_a_breast_set_too` runs both for
    60 frames with a live collider and they agree to 0.0005 units - so whatever this clip produces
    IS what the old plugin produces, and a bar it fails is a wrong bar.

    What moved it last was the collider ORDER. `collide()` walks the colliders in the order it is
    handed them and each hit overwrites the last one's push, so the order is part of the result;
    putting the clavicles first, where `COLLISION_BONE_CANDIDATES` has them, takes `death` from
    72.5 degrees to 83.6. Both are the old plugin, and only one of them is the old plugin's list.

    Measured on `death` at intensity 7:

        no colliders                              77.8
        colliders, particle len * 0.12            98.1   <- a third too small, thrown out
        colliders, clavicles last                 72.5
        colliders, the addon's list and order     83.6   <- what ships

    So the bar is the swept bug's own signature, which is what this was written to catch: that was
    +59% with no frame ever inside a sphere. A collider working is +7%. And what a user sees is
    45.0 either way, because the movement limit is on by default for breasts and the standalone
    addon applied it to every breast bake with no way to turn it off.
    """
    clip = riven_anim("riven_skin23_death.anm")
    addon, armature = _load(clip)
    try:
        _setup(armature)
        physics_set = physics_props.get_set(armature, 'BREAST')
        names = [item.name for item in physics_set.bones if item.name]
        # THE DEFAULT LIST, because a partial one is not what anybody runs and it behaves worse.
        colliders = [item.name for item in physics_set.colliders if item.name]
        assert names and colliders, (names, colliders)
        assert not [n for n in colliders if n in names], "a set must never collide with itself"
        for identifier, other in physics_props.sets_of(armature):
            other.enabled = (identifier == 'BREAST')

        scene = bpy.context.scene
        action = armature.animation_data.action
        first = int(round(action.frame_range[0]))
        last = int(round(action.frame_range[1]))

        def peak(with_colliders, limit=0.0):
            params = wiggle.params_for('BREAST', 7, 20, 1.0)
            sim = wiggle.Sim(armature, 'BREAST', names, params,
                             colliders if with_colliders else (), limit)
            scene.frame_set(first)
            sim.reset()
            worst = 0.0
            for frame in range(first, last + 1):
                scene.frame_set(frame)
                for matrix in sim.step(1.0 / 30.0, 1, list(scene.gravity), write=False):
                    quaternion = matrix.to_quaternion()
                    worst = max(worst, math.degrees(
                        2.0 * math.acos(min(1.0, abs(quaternion.w)))))
            return worst

        free = peak(False)
        collided = peak(True)
        capped = peak(True, 45.0)
        print("      death, intensity 7: %.1f deg free, %.1f with %d colliders, %.1f capped"
              % (free, collided, len(colliders), capped))
        assert collided <= free * 1.15, (
            "collision made the swing far worse, %.1f against %.1f - the contacts are not real"
            % (collided, free))
        # And what a user actually sees is the capped number, because the limit is on by default
        # for breasts and the standalone addon applied it to every breast bake with no way off.
        assert abs(capped - 45.0) < 0.1, capped
    finally:
        addon.unregister()


# --------------------------------------------------------------------------- collider shapes
#
# The two tests that lived here measured BOXES fitted to the mesh. That system is gone - see
# `findings.md`, "Back to the spheres" - and the sphere placement it was replaced by is held
# against the standalone addon's own function in `test_21`, at 0.00e+00.

def test_a_sphere_is_a_box_with_no_extent():
    """One shape in the solver, not two. The sphere option is the same struct with zero half-
    extents, which is why choosing it costs no second code path anywhere."""
    solver = native.rs().Wiggle([-1], [False], [1.0])
    solver.set_params([200.0], [2.0], [0.0], [1.0], [0.0], [True],
                      [0.05], [0.0], [0.0], [0.0])

    flat = []
    for row in Matrix.Translation(Vector((0.0, 0.0, 0.0))):
        flat.extend(row)

    def settle(halves, radii):
        solver.set_colliders(flat, halves, radii)
        anim = []
        for row in Matrix.Translation(Vector((-10.0, 0.0, 0.0))):
            anim.extend(row)
        solver.reset(anim)
        for step in range(1, 30):
            moving = []
            for row in Matrix.Translation(Vector((-10.0 + step * 0.4, 0.0, 0.0))):
                moving.extend(row)
            solver.set_colliders(flat, halves, radii)
            solver.step(moving, [1.0, 1.0, 1.0], 1.0 / 30.0, 1.0, [0.0, 0.0, 0.0], 20)
        return Vector(solver.particles()[0:3])

    sphere = settle([0.0, 0.0, 0.0], [3.0])
    # A box of half-extent 3 in every direction, rounded by 3, IS that sphere.
    box = settle([3.0, 3.0, 3.0], [3.0])
    print("      sphere r=3 stops at %.4f from the centre; the equivalent box at %.4f"
          % (sphere.length, box.length))
    assert sphere.length >= 3.0 - 1e-6, sphere.length
    # The box's half-extents are shrunk by the rounding, so half 3 rounded 3 is exactly the sphere.
    assert abs(sphere.length - box.length) < 1e-6, (sphere.length, box.length)


def test_each_set_starts_on_its_own_numbers_with_nothing_touched():
    """Reported as "why is Limit Movement on Breast always unticked, and why is it 20".

    Because all three sets pointed at ONE PropertyGroup, so all three started on whatever that
    class declared - hair's. `apply_defaults` did put breast's numbers in, but only when it ran:
    adding the FIRST bone to an empty set, Auto Setup, or Reset to Defaults. Open the panel and
    click Breast and you were reading hair's settings, and nothing put them right, because Blender
    stores a property in the file only once you change it.

    45 is not a number chosen here. `physics_plugin/boobs_physics.py:486` runs
    `clamp_local_rotation_from_identity(action, bone_names, max_deg=45.0)` at the end of every
    breast bake, unconditionally, with no way to turn it off. Hair has no such call at all.

    This reads the sets on a rig that nothing has touched, which is the state that was wrong.
    """
    addon, armature = _load()
    try:
        for identifier, physics_set in physics_props.sets_of(armature):
            expected = physics_props.DEFAULTS[identifier]
            got = {name: getattr(physics_set, name) for name in expected}
            print("      %-6s %s" % (identifier, ", ".join(
                "%s=%s" % (k, round(v, 4) if isinstance(v, float) else v)
                for k, v in sorted(got.items()))))
            for name, value in expected.items():
                actual = got[name]
                if isinstance(value, float):
                    assert abs(actual - value) < 1e-6, (
                        "%s.%s starts at %r, not %r" % (identifier, name, actual, value))
                else:
                    assert actual == value, (
                        "%s.%s starts at %r, not %r" % (identifier, name, actual, value))

        breast = physics_props.get_set(armature, 'BREAST')
        assert breast.limit_movement, "the movement limit is off on a fresh Breast set"
        assert abs(breast.max_angle - 45.0) < 1e-6, breast.max_angle
        # And the three really are separate stores, not one shared block.
        physics_props.get_set(armature, 'HAIR').max_angle = 33.0
        assert abs(breast.max_angle - 45.0) < 1e-6, (
            "changing Hair moved Breast, so the sets share one store")
    finally:
        addon.unregister()
