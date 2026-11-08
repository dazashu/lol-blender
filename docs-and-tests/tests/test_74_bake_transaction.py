import bpy
from unittest.mock import patch
from test_38_physics_controls import setup

def keys(action):
    return [(c.data_path,c.array_index,[(tuple(p.co),p.interpolation) for p in c.keyframe_points]) for c in action.fcurves]

def test_failed_bake_restores_shared_action_and_subframe():
    addon,arm,group,bone=setup(animated=True)
    try:
        from LoL_Blender.ops import physics
        from LoL_Blender.core import bake
        source=arm.animation_data.action;source['transaction_marker']='retain'
        expected=keys(source);name=source.name
        other=bpy.data.objects.new('SharedActionUser',bpy.data.armatures.new('SharedActionUser'))
        bpy.context.collection.objects.link(other);other.animation_data_create();other.animation_data.action=source
        bpy.context.scene.frame_set(4,subframe=.375)
        def fail(*args,**kwargs):raise RuntimeError('injected write failure')
        with patch.object(bake,'write',side_effect=fail):ok,message=physics.run_bake(bpy.context,arm)
        assert not ok and 'previous animation restored' in message,message
        restored=arm.animation_data.action
        assert restored==other.animation_data.action
        assert restored.name==name and restored['transaction_marker']=='retain'
        assert keys(restored)==expected
        assert bpy.context.scene.frame_current==4 and abs(bpy.context.scene.frame_subframe-.375)<1e-6
    finally:addon.unregister()

def test_clear_bake_keeps_action_used_by_another_rig():
    addon,arm,group,bone=setup(animated=True)
    try:
        from LoL_Blender.ops import physics
        source=arm.animation_data.action
        physics._take_backup(arm,source)
        other=bpy.data.objects.new('KeepBakedUser',bpy.data.armatures.new('KeepBakedUser'))
        bpy.context.collection.objects.link(other);other.animation_data_create();other.animation_data.action=source
        assert physics._restore_backup(arm)
        assert other.animation_data.action==source and source.name in bpy.data.actions
        assert arm.animation_data.action!=source
    finally:addon.unregister()
