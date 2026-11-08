"""Regression coverage for audited physics controls and lifecycle bugs."""
import bpy


def setup(animated=False):
    from test_16_physics import _chain
    from test_19_physics_ui import _fresh_addon
    arm, names = _chain(1)
    addon = _fresh_addon()
    addon.register()
    arm.lol_physics.live = False
    group = arm.lol_physics.breast
    group.bones.add().name = names[0]
    group.intensity = 20
    group.limit_movement = False
    bone = arm.pose.bones[names[0]]
    if animated:
        bone.rotation_mode = 'XYZ'
        for frame, angle in ((1, .1), (10, .8)):
            bone.rotation_euler.z = angle
            bone.keyframe_insert('rotation_euler', frame=frame)
    return addon, arm, group, bone


def delta(a, b):
    return max(abs(a[i][j]-b[i][j]) for i in range(4) for j in range(4))


def test_live_off_restores_unkeyed_and_current_animated_pose():
    for animated in (False, 'KEYS', 'NLA', 'DRIVER'):
        addon, arm, group, bone = setup(animated)
        try:
            if animated == 'NLA':
                action = arm.animation_data.action
                arm.animation_data.nla_tracks.new().strips.new('Test NLA', 1, action)
                arm.animation_data.action = None
            if animated == 'DRIVER':
                bone.driver_add('rotation_euler', 0).driver.expression = 'frame * 0.05'
            scene = bpy.context.scene
            scene.frame_set(8)
            expected = bone.matrix_basis.copy()
            scene.frame_set(1)
            arm.lol_physics.live = True
            for frame in range(2, 9):
                scene.frame_set(frame)
            assert delta(bone.matrix_basis, expected) > 1e-5
            arm.lol_physics.live = False
            assert delta(bone.matrix_basis, expected) < 1e-6, (animated, delta(bone.matrix_basis, expected))
            assert scene.frame_current == 8
        finally:
            addon.unregister()


def test_disabling_set_releases_its_pose():
    addon, arm, group, bone = setup()
    try:
        expected = bone.matrix_basis.copy()
        arm.lol_physics.live = True
        for frame in range(1, 31):
            bpy.context.scene.frame_set(frame)
        assert delta(bone.matrix_basis, expected) > 1e-5
        group.enabled = False
        assert delta(bone.matrix_basis, expected) < 1e-6
    finally:
        addon.unregister()


def test_removing_bone_releases_pose_and_restart_has_no_stale_state():
    addon, arm, group, bone = setup()
    try:
        from LoL_Blender.ops import physics
        expected = bone.matrix_basis.copy()
        arm.lol_physics.live = True
        for frame in range(1, 31):
            bpy.context.scene.frame_set(frame)
        group.bones.clear()
        physics.topology_changed(arm)
        assert delta(bone.matrix_basis, expected) < 1e-6
        assert arm.name not in physics._rigs
        group.bones.add().name = bone.name
        group.gravity = 0
        bpy.context.scene.frame_set(31)
        assert delta(bone.matrix_basis, expected) < 1e-6
    finally:
        addon.unregister()


def test_disabling_addon_releases_unkeyed_pose():
    addon, arm, group, bone = setup()
    expected = bone.matrix_basis.copy()
    try:
        arm.lol_physics.live = True
        for frame in range(1, 31):
            bpy.context.scene.frame_set(frame)
        assert delta(bone.matrix_basis, expected) > 1e-5
    finally:
        addon.unregister()
    assert delta(bone.matrix_basis, expected) < 1e-6


def test_global_gravity_switch_and_custom_twenty():
    addon, arm, group, bone = setup()
    try:
        from LoL_Blender.core import wiggle
        scene = bpy.context.scene
        outputs = []
        for enabled in (False, True):
            scene.use_gravity = enabled
            wiggle.clear_pose(arm, [bone.name])
            rig = wiggle.Rig(arm)
            rig.reset()
            for _ in range(60):
                rig.step(scene, elapsed=1)
            outputs.append(rig.sims[0].solver.particles())
        assert max(abs(a-b) for a,b in zip(*outputs)) > .01
        arm.lol_physics.active = 'CUSTOM'
        arm.lol_physics.custom_gravity = 20
        assert wiggle.settings_for(group)['gravity'] == 20
    finally:
        addon.unregister()


def test_duplicate_sets_block_preview_and_bake_without_touching_action():
    addon, arm, group, bone = setup(True)
    try:
        from LoL_Blender.core import wiggle
        from LoL_Blender.ops import physics
        arm.lol_physics.hair.bones.add().name = bone.name
        action = arm.animation_data.action
        before = [(c.data_path, c.array_index, [tuple(k.co) for k in c.keyframe_points]) for c in action.fcurves]
        try:
            wiggle.Rig(arm)
        except ValueError as error:
            assert bone.name in str(error) and 'HAIR' in str(error) and 'BREAST' in str(error)
        else:
            raise AssertionError('Duplicate ownership accepted')
        ok, message = physics.run_bake(bpy.context, arm)
        assert not ok and bone.name in message
        assert action == arm.animation_data.action
        assert before == [(c.data_path, c.array_index, [tuple(k.co) for k in c.keyframe_points]) for c in action.fcurves]
        arm.lol_physics.live = True
        bpy.context.scene.frame_set(3)
        assert bone.name in arm.lol_physics.status
        arm.lol_physics.hair.enabled = False
        bpy.context.scene.frame_set(4)
        assert arm.lol_physics.status == 'Physics active'
    finally:
        addon.unregister()


def test_bake_uses_animated_scene_gravity_on_each_frame():
    addon, arm, group, bone = setup(True)
    try:
        from LoL_Blender.core import wiggle
        from LoL_Blender.ops import physics
        scene = bpy.context.scene
        arm.lol_physics.loop_mode = 'ONCE'
        for frame, enabled in ((1, True), (5, False), (10, True)):
            scene.use_gravity = enabled
            scene.gravity.z = -float(frame)
            scene.keyframe_insert('use_gravity', frame=frame)
            scene.keyframe_insert('gravity', frame=frame)
        received = []
        original = wiggle.Sim.step
        def capture(self, dt, elapsed, gravity, write=True):
            received.append((scene.frame_current, list(gravity), wiggle.scene_gravity(scene)))
            return original(self, dt, elapsed, gravity, write)
        wiggle.Sim.step = capture
        try:
            assert physics.run_bake(bpy.context, arm)[0]
        finally:
            wiggle.Sim.step = original
        assert len(received) == 10
        assert all(actual == expected for _, actual, expected in received)
        assert received[0][1][2] == -1 and received[4][1] == [0, 0, 0] and received[-1][1][2] == -10
    finally:
        addon.unregister()


def test_animated_physics_sliders_reach_preview_and_bake():
    addon, arm, group, bone = setup(True)
    try:
        from LoL_Blender.core import wiggle
        from LoL_Blender.ops import physics
        scene = bpy.context.scene
        for frame, gravity, intensity in ((1, 0., 1), (10, 1., 20)):
            group.gravity = gravity
            group.intensity = intensity
            group.keyframe_insert('gravity', frame=frame)
            group.keyframe_insert('intensity', frame=frame)
        arm.lol_physics.live = True
        previous = None
        for frame in range(1, 11):
            scene.frame_set(frame)
            rig = physics.rig_for(arm)
            assert previous is None or previous is rig, 'Animated sliders restarted the solver'
            assert rig.sims[0].settings == wiggle.settings_for(group, 'BREAST')
            previous = rig
        assert rig.sims[0].settings['gravity'] == 20
        arm.lol_physics.live = False
        arm.lol_physics.loop_mode = 'ONCE'
        received = []
        original = wiggle.Sim.step
        def capture(self, dt, elapsed, gravity, write=True):
            received.append((self.settings.copy(), wiggle.settings_for(group, 'BREAST')))
            return original(self, dt, elapsed, gravity, write)
        wiggle.Sim.step = capture
        try:
            assert physics.run_bake(bpy.context, arm)[0]
        finally:
            wiggle.Sim.step = original
        assert len(received) == 10 and all(a == b for a,b in received)
        assert received[0][0]['gravity'] == 0 and received[-1][0]['gravity'] == 20
    finally:
        addon.unregister()
