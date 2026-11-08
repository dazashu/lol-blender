"""Rebind: bind each mesh again to the bones it is already weighted to."""
import time
import bpy
from . import arp_bind

RIG_KEY = '_daza_rebind_armature'


def rig_for(obj):
    rigs = {m.object for m in obj.modifiers if m.type == 'ARMATURE' and m.object}
    if not rigs and obj.parent and obj.parent.type == 'ARMATURE':
        rigs.add(obj.parent)
    if not rigs:
        saved = obj.get(RIG_KEY)
        if isinstance(saved, bpy.types.Object) and saved.type == 'ARMATURE':
            rigs.add(saved)
    if len(rigs) != 1:
        raise ValueError(f'{obj.name}: expected one bound armature. Bind the mesh first or resolve its multiple armature modifiers')
    return next(iter(rigs))


def targets(meshes):
    if not meshes:
        raise ValueError('Select at least one mesh to rebind')
    result = []
    for obj in meshes:
        obj.update_from_editmode()
        rig = rig_for(obj)
        used = {g.group for v in obj.data.vertices for g in v.groups if g.weight > 0}
        names = {g.name for g in obj.vertex_groups if g.index in used}
        bones = [b.name for b in rig.data.bones if b.use_deform and b.name in names and b.name != 'Picker']
        if not bones:
            raise ValueError(f'{obj.name}: no existing positive bone weights. Use Bind first')
        if obj.library or obj.data.library or obj.data.users > 1 or rig.library or rig.data.library:
            raise ValueError(f'{obj.name}: make mesh and armature data local and single-user before rebinding')
        if not obj.visible_get() or not obj.data.polygons:
            raise ValueError(f'{obj.name}: show the mesh and ensure it has faces before rebinding')
        result.append((obj, rig, bones))
    return result


class Snapshot:
    """A mesh's data, groups, parent and armature modifiers, restorable on failure."""
    def __init__(self, obj):
        self.obj = obj
        self.data = obj.data.copy()
        self.groups = [(g.name, g.lock_weight) for g in obj.vertex_groups]
        self.parent = (obj.parent, obj.parent_type, obj.parent_bone)
        self.inverse = obj.matrix_parent_inverse.copy()
        self.world = obj.matrix_world.copy()
        self.order = [m.name for m in obj.modifiers]
        self.mods = []
        for mod in obj.modifiers:
            if mod.type != 'ARMATURE':
                continue
            attrs = {}
            for p in mod.bl_rna.properties:
                if not p.is_readonly and p.type in {'BOOLEAN', 'ENUM', 'INT', 'FLOAT', 'STRING', 'POINTER'}:
                    value = getattr(mod, p.identifier)
                    attrs[p.identifier] = tuple(value) if getattr(p, 'is_array', False) else value
            self.mods.append((mod.name, attrs))

    def restore(self):
        obj = self.obj
        obj.vertex_groups.clear()
        previous = obj.data
        obj.data, self.data = self.data, None
        for name, locked in self.groups:
            obj.vertex_groups.new(name=name).lock_weight = locked
        if previous.users == 0:
            bpy.data.meshes.remove(previous)
        for mod in list(obj.modifiers):
            if mod.type == 'ARMATURE':
                obj.modifiers.remove(mod)
        for name, attrs in self.mods:
            mod = obj.modifiers.new(name, 'ARMATURE')
            for key, value in attrs.items():
                setattr(mod, key, value)
        arp_bind.select([obj])
        for index, name in enumerate(self.order):
            bpy.ops.object.modifier_move_to_index(modifier=name, index=index)
        obj.parent, obj.parent_type, obj.parent_bone = self.parent
        obj.matrix_parent_inverse = self.inverse
        obj.matrix_world = self.world

    def discard(self):
        if self.data is not None:
            bpy.data.meshes.remove(self.data)
            self.data = None


class Rebind:
    """Rebinds meshes one after another. Restores them all on error or cancel."""
    def __init__(self, meshes, settings):
        self.plans = targets(meshes)
        self.settings = arp_bind.options(settings, selected_bones_only=True,
                                         selected_vertices_only=False, remove_vertex_groups=False)
        self.original_selection = list(bpy.context.selected_objects)
        self.original_active = bpy.context.view_layer.objects.active
        self.original_mode = self.original_active.mode if self.original_active else 'OBJECT'
        self.snapshots = []
        self.current = None
        self.index = 0
        self.committed = False
        self.closed = False
        self.notes = []
        self.started = time.perf_counter()
        try:
            for obj, _, _ in self.plans:
                self.snapshots.append(Snapshot(obj))
            self.start_next()
        except Exception:
            self.close()
            raise

    @property
    def session(self):
        return self.current.session if self.current else None

    def start_next(self):
        obj, rig, bones = self.plans[self.index]
        self.current = arp_bind.Bind(rig, [obj], bones, self.settings)

    def advance(self):
        self.notes.extend(self.current.finish().notes)
        self.current.close()
        self.current = None
        self.index += 1
        if self.index < len(self.plans):
            self.start_next()
            return True
        return False

    def compute(self):
        while self.current:
            self.current.compute()
            self.advance()

    def finish(self):
        if self.current is not None:
            raise RuntimeError('Rebind computation is not finished')
        self.committed = True
        return arp_bind.Result(len(self.plans), sum(len(b) for _, _, b in self.plans),
                               sum(len(o.data.vertices) for o, _, _ in self.plans),
                               time.perf_counter() - self.started, self.notes)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.current:
            self.current.close()
        try:
            if not self.committed:
                for snapshot in self.snapshots:
                    snapshot.restore()
        finally:
            for snapshot in self.snapshots:
                snapshot.discard()
            arp_bind.select(self.original_selection, self.original_active)
            if self.original_active and self.original_mode != 'OBJECT':
                bpy.ops.object.mode_set(mode=self.original_mode)
