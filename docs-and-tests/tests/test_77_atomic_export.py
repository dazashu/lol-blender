"""A failed native write must leave an existing exported animation intact."""
from pathlib import Path
from unittest.mock import patch
from LoL_Blender.core import anm_out
from LoL_Blender import native
from run import scratch


def test_failed_native_write_preserves_target_and_cleans_temporary():
    target=Path(scratch('atomic-failure.anm'))
    target.write_bytes(b'previous valid animation')
    temporary=[]
    def fail(path,*args):
        temporary.append(Path(path));Path(path).write_bytes(b'partial bytes')
        raise ValueError('injected writer failure')
    with patch.object(native.rs(),'write_animation',side_effect=fail):
        try:anm_out._write_animation_atomic(str(target),30.,[],False)
        except ValueError as exc:assert 'injected' in str(exc)
        else:assert False,'failed writer accepted'
    assert target.read_bytes()==b'previous valid animation'
    assert temporary and all(not p.exists() for p in temporary)


def test_failed_replace_preserves_target_and_cleans_temporary():
    target=Path(scratch('atomic-replace-failure.anm'))
    target.write_bytes(b'previous animation')
    temporary=[]
    def write(path,*args):
        temporary.append(Path(path));Path(path).write_bytes(b'new animation');return 13,False
    with patch.object(native.rs(),'write_animation',side_effect=write),patch('os.replace',side_effect=PermissionError('locked destination')):
        try:anm_out._write_animation_atomic(str(target),30.,[],False)
        except PermissionError:pass
        else:assert False,'failed replacement accepted'
    assert target.read_bytes()==b'previous animation'
    assert temporary and all(not p.exists() for p in temporary)


def test_atomic_native_animation_remains_readable():
    target=Path(scratch('atomic-native-success.anm'))
    target.write_bytes(b'old')
    result=anm_out._write_animation_atomic(str(target),2.,[(123,[0.,.5],[0.,0.,0.]*2,[0.,0.,0.,1.]*2,[1.,1.,1.]*2)],False)
    assert result[0]==target.stat().st_size
    assert native.rs().Animation.from_path(str(target)).sample_local(123,0.) is not None


def test_apply_all_failure_restores_exact_timeline():
    import bpy
    from test_38_physics_controls import setup
    addon,arm,group,bone=setup(animated=True)
    try:
        from LoL_Blender.core import anm_in
        scene=bpy.context.scene
        scene.lolblender.animations.clear()
        clip=scene.lolblender.animations.add();clip.name='broken';clip.filepath=scratch('broken-batch-source.anm')
        arm.lol_physics.export_folder=''
        scene.frame_set(4,subframe=.375)
        expected=(scene.frame_current,scene.frame_subframe,scene.frame_start,scene.frame_end,scene.render.fps,scene.render.fps_base)
        original=arm.animation_data.action
        undo=bpy.context.preferences.edit.use_global_undo
        def fail(*args,**kwargs):
            scene.frame_set(19,subframe=.75);scene.frame_end=100;scene.render.fps=12
            raise ValueError('injected clip import failure')
        with patch.object(anm_in,'import_anm',side_effect=fail):
            assert bpy.ops.lolblender.physics_apply_all()=={'FINISHED'}
        actual=(scene.frame_current,scene.frame_subframe,scene.frame_start,scene.frame_end,scene.render.fps,scene.render.fps_base)
        assert actual==expected,(actual,expected)
        assert arm.animation_data.action==original
        assert bpy.context.preferences.edit.use_global_undo==undo
    finally:addon.unregister()
