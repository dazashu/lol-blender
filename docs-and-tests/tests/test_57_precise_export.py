"""Export keeps source TRS only when the user has not edited those channels."""
import math
import bpy
from run import graves,graves_anim,scratch
from test_15_export import _import


def test_untouched_source_preservation_rejects_fractional_key_edits():
    addon,arm=_import(graves('graves.skn'))
    try:
        from LoL_Blender.core import anm_in,anm_out
        anm_in.import_anm(graves_anim('graves_idle1.anm'),arm)
        source,names=anm_out._unchanged_source_channels(arm,bpy.context.scene)
        assert source is not None and names
        name=sorted(names)[0];bone=arm.pose.bones[name]
        curve=arm.animation_data.action.fcurves.find(bone.path_from_id('location'),index=0)
        original=curve.evaluate(.5)
        key=curve.keyframe_points.insert(.5,original+.25);key.interpolation='LINEAR';curve.update()
        _,after=anm_out._unchanged_source_channels(arm,bpy.context.scene)
        assert name not in after,'fractional key edit would be replaced by the source'
    finally:addon.unregister()


def test_protected_compression_keeps_small_physics_motion():
    import _rsblender
    from mathutils import Quaternion
    q=Quaternion((1,0,0),.002)
    tracks=[(123,[0.,.5,1.],[0.,0.,0.]*3,
             [0.,0.,0.,1.,q.x,q.y,q.z,q.w,0.,0.,0.,1.],[1.,1.,1.]*3)]
    ordinary=scratch('precision_default.anm');protected=scratch('precision_protected.anm')
    _rsblender.write_animation(ordinary,2.,tracks,True)
    _rsblender.write_animation(protected,2.,tracks,True,True,(.000025,.00025,.000025),[123])
    reference=q.to_matrix().to_4x4()
    def error(path):
        values=_rsblender.Animation.from_path(path).sample_local(123,.5)
        return max(abs(values[r*4+c]-reference[r][c]) for r in range(4) for c in range(4))
    assert error(ordinary)>.001
    assert error(protected)<.0001
