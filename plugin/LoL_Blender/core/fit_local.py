"""Invert import TRS decomposition for untouched channels with inherited shear.

The imported animation stores normalized rotations and three scales. Directly
decomposing the inverse matrix loses shear a second time. Start from the source
TRS and fit the pose that Blender actually evaluated, including subframes.
"""
import numpy as np
from mathutils import Matrix,Quaternion,Vector
from . import convert

def fit_local(basis,pre_inv,post_inv,seed):
    pre=pre_inv.inverted();post=post_inv.inverted()
    target_loc,target_q,target_scale=basis.decompose()
    loc,q,scale=seed.decompose()
    def evaluate(loc,q,scale):
        result=pre@convert.riot_to_blender(Matrix.LocRotScale(loc,q,scale))@post
        l,r,s=result.decompose()
        delta=target_q.rotation_difference(r)
        if delta.w<0:delta.negate()
        return np.array([*(l-target_loc),*(Vector((delta.x,delta.y,delta.z))*2),*(s-target_scale)],dtype=float)
    def change(loc,q,scale,step):
        loc=loc+Vector(step[:3]);v=Vector(step[3:6]);angle=v.length
        if angle>1e-10:q=q@Quaternion(v/angle,angle)
        scale=scale+Vector(step[6:])
        return loc,q,scale
    residual=evaluate(loc,q,scale)
    for iteration in range(12):
        if np.max(np.abs(residual))<2e-6:break
        jac=np.empty((9,9))
        for axis in range(9):
            step=np.zeros(9);step[axis]=.001
            jac[:,axis]=(evaluate(*change(loc,q,scale,step))-residual)/.001
        update=np.linalg.lstsq(jac,-residual,rcond=1e-6)[0]
        norm=np.linalg.norm(update[3:6])
        if norm>.3:update*=.3/norm
        best=float(residual@residual);accepted=False
        for gain in (1.,.5,.25,.125):
            candidate=change(loc,q,scale,update*gain);error=evaluate(*candidate)
            if error@error<best:
                loc,q,scale=candidate;residual=error;accepted=True;break
        if not accepted:break
    if not np.all(np.isfinite(residual)) or np.max(np.abs(residual))>1e-4:
        raise ValueError('Cannot preserve the imported transform during animation export')
    return Matrix.LocRotScale(loc,q,scale)
