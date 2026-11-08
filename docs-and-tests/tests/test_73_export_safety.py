"""Export failure paths preserve the scene and reject ambiguous joint identities."""
import bpy
from test_16_physics import _chain
from LoL_Blender.core import promote, registry as reg, skn_out, skl_out, anm_out

def test_rename_collision_is_transactional_and_exports_refuse():
    arm,names=_chain(2)
    reg.write_registry(arm,{})
    promote.promote(arm)
    before=reg.read_registry(arm).copy()
    identity=[(b.get(reg.HASH),b.get(reg.NAME)) for b in arm.data.bones]
    arm.data.bones[names[0]].name=names[1].upper()
    for dry_run in (True,False):
        report=promote.promote(arm,dry_run=dry_run)
        assert report.refused, report.summary()
        assert reg.read_registry(arm)==before
        assert [(b.get(reg.HASH),b.get(reg.NAME)) for b in arm.data.bones]==identity
    mesh=bpy.data.objects.new("test_mesh",bpy.data.meshes.new("test_mesh"))
    for exporter in (lambda:skl_out.build(arm,'not_written.skl'),
                     lambda:anm_out.build(arm,bpy.context.scene,0,1),
                     lambda:skn_out.build(arm,[mesh],'not_written.skn')):
        try: exporter()
        except ValueError as error: assert 'hash' in str(error)
        else: raise AssertionError('Invalid identities exported')
    arm.data.bones[names[1].upper()].name='SafeCustomName'
    assert not promote.promote(arm).refused
    assert len(reg.read_registry(arm))==2

def test_export_failure_restores_modifier_even_before_mesh_created():
    from types import SimpleNamespace
    for failing_at in ('update','evaluate','mesh','cleanup'):
        modifier=SimpleNamespace(type='ARMATURE',show_viewport=True)
        class Graph:
            calls=0
            def update(self):
                self.calls+=1
                if failing_at=='update' and self.calls==1:raise RuntimeError('update')
        class Object:
            modifiers=[modifier]
            def evaluated_get(self,graph):
                if failing_at=='evaluate':raise RuntimeError('evaluate')
                return self
            def to_mesh(self): raise RuntimeError('mesh')
            def to_mesh_clear(self):
                if failing_at=='cleanup':raise RuntimeError('cleanup')
        try:skn_out._corner_arrays(Object(),Graph())
        except RuntimeError:pass
        else:raise AssertionError('failure was swallowed')
        assert modifier.show_viewport, failing_at
