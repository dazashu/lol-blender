"""Collapsed visibility bones retain finite channels and observable matrices."""
import bpy,numpy as np
from mathutils import Matrix,Quaternion
from LoL_Blender.core import anm_in,convert
from LoL_Blender import native
from run import scratch

def test_zero_scale_visibility_import_is_finite_and_preserves_matrices():
    path=scratch('zero_scale_visibility.anm')
    q=Quaternion((0,0,1),.8)
    native.rs().write_animation(path,2.,[(123,[0.,.5,1.],[0.,0.,0.]*3,
        [q.x,q.y,q.z,q.w]*3,[0.,0.,0.,1.,1.,1.,0.,1.,1.])],False)
    animation=native.rs().Animation.from_path(path)
    identity=convert.matrix_to_flat(Matrix.Identity(4));bones=[(123,identity,identity,identity)]
    raw=animation.bake(bones)
    assert not np.isfinite(np.frombuffer(raw.quaternions,np.float32)).all(), 'fixture no longer exercises native singularity'
    data=anm_in.bake_channels(animation,bones,sample_fps=8.)
    values=[np.frombuffer(getattr(data,key),np.float32).reshape(1,w,data.frame_count)
            for key,w in [('locations',3),('quaternions',4),('scales',3)]]
    assert all(np.isfinite(v).all() for v in values)
    assert abs(float(np.dot(values[1][0,:,0],values[1][0,:,1])))>1-1e-6, "Visibility ramp invented rotation"
    for f in range(data.frame_count):
        original=convert.riot_to_blender(convert.flat_to_matrix(animation.sample_local(123,f/8.)))
        rebuilt=Matrix.LocRotScale(values[0][0,:,f],Quaternion(values[1][0,:,f]),values[2][0,:,f])
        assert convert.max_element_deviation(original,rebuilt)<1e-5,(f,original,rebuilt)

def test_regular_animation_buffers_are_unchanged():
    path=scratch('ordinary_safe_bake.anm')
    native.rs().write_animation(path,2.,[(123,[0.,.5,1.],[0.,0.,0.]*3,
        [0.,0.,0.,1.]*3,[1.,1.,1.]*3)],False)
    animation=native.rs().Animation.from_path(path)
    identity=convert.matrix_to_flat(Matrix.Identity(4));bones=[(123,identity,identity,identity)]
    before=animation.bake(bones);after=anm_in.bake_channels(animation,bones)
    for field in ('locations','quaternions','scales'):
        assert getattr(before,field)==getattr(after,field)


def test_imported_visibility_curves_and_exported_matrices_remain_valid():
    from test_16_physics import _chain
    from LoL_Blender.core import registry as reg, anm_out
    arm,names=_chain(1)
    reg.write_registry(arm,{123:names[0]})
    arm.data.bones[names[0]][reg.HASH]=123
    arm.data.bones[names[0]][reg.NAME]=names[0]
    source=scratch('visibility_roundtrip_source.anm')
    target=scratch('visibility_roundtrip_export.anm')
    q=Quaternion((0,0,1),.8)
    native.rs().write_animation(source,2.,[(123,[0.,.5,1.],[0.,0.,0.]*3,
        [q.x,q.y,q.z,q.w]*3,[0.,0.,0.,1.,1.,1.,0.,1.,1.])],False)
    anm_in.import_anm(source,arm)
    for curve in arm.animation_data.action.fcurves:
        assert all(np.isfinite(point.co.y) for point in curve.keyframe_points)
    anm_out.export_anm(arm,target,bpy.context.scene,0,2,compressed=False)
    original=native.rs().Animation.from_path(source)
    exported=native.rs().Animation.from_path(target)
    for frame in range(3):
        a=original.sample_local(123,frame/2.)
        b=exported.sample_local(123,frame/2.)
        assert np.isfinite(b).all()
        assert max(abs(x-y) for x,y in zip(a,b))<1e-5,(frame,a,b)

def test_fully_hidden_fast_path_preserves_translations_and_rejects_nan():
    import random
    from mathutils import Vector
    rng=random.Random(2212)
    for _ in range(300):
        matrix=Matrix.Diagonal((0.,0.,0.,1.))
        matrix.translation=Vector([rng.uniform(-1e5,1e5) for _ in range(3)])
        rebuilt=Matrix.LocRotScale(*anm_in.decompose_channels(matrix))
        assert convert.max_element_deviation(matrix,rebuilt)==0
    matrix.translation.x=float('nan')
    try:anm_in.decompose_channels(matrix)
    except ValueError:pass
    else:raise AssertionError('Non-finite translation was hidden')
