import numpy as np
from mathutils import Matrix,Quaternion,Vector


def test_fitted_shear_roundtrips_through_native_import():
    from LoL_Blender.core.fit_local import fit_local
    from LoL_Blender.core import convert
    from LoL_Blender import native
    from run import scratch
    pre=Matrix.Translation((2,3,4))@Matrix.Rotation(.4,4,'X')
    post=Matrix.Rotation(.8,4,'Z')
    seed=Matrix.LocRotScale(Vector((.1,.2,.3)),Quaternion((1,0,0),.6),Vector((1,2,.7)))
    other=Matrix.LocRotScale(Vector((.11,.2,.3)),Quaternion((1,0,0),.62),Vector((1,2.02,.7)))
    original=pre@convert.riot_to_blender(other)@post
    loc,q,scale=original.decompose()

    target=Matrix.LocRotScale(loc,q,scale)
    fitted=fit_local(target,pre.inverted(),post.inverted(),seed)
    l,r,s=fitted.decompose();path=scratch('shear_fit.anm')
    native.rs().write_animation(path,30,[(1,[0.],list(l),[r.x,r.y,r.z,r.w],list(s))],False)
    flat=lambda m:[v for row in m for v in row]
    result=native.rs().Animation.from_path(path).bake([(1,flat(pre),flat(post),flat(Matrix.Identity(4)))])
    got_q=np.frombuffer(result.quaternions,dtype=np.float32).astype(float);got_q/=np.linalg.norm(got_q)
    want=np.array(q);got_q=got_q if got_q@want>=0 else -got_q
    assert max(abs(got_q-want))<3e-5
    assert max(abs(np.frombuffer(result.locations,dtype=np.float32)-np.array(loc)))<3e-5
    assert max(abs(np.frombuffer(result.scales,dtype=np.float32)-np.array(scale)))<3e-5
