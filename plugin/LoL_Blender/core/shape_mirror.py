"""Live body-shape mirroring to the opposite bone. The mirror math is in Rust."""
import re
import bpy
from bpy.app.handlers import persistent
from mathutils import Matrix, Vector
from . import shapes
from .. import native, compat_handlers

_states = {}
_busy = False


def opposite_name(armature, name):
    candidates = set()
    for left, right in [('Left','Right'),('left','right'),('LEFT','RIGHT'),('L','R'),('l','r')]:
        for old,new in ((left,right),(right,left)):
            pattern = r'(?<![A-Za-z0-9])' + old + r'(?![A-Za-z0-9])'
            for match in re.finditer(pattern,name):
                candidates.add(name[:match.start()]+new+name[match.end():])
            if len(old)>1 and name.startswith(old):
                candidates.add(new+name[len(old):])
            if len(old)>1 and name.endswith(old):
                candidates.add(name[:-len(old)]+new)
    found = [n for n in candidates if n != name and n in armature.data.bones]
    return found[0] if len(found)==1 else None


def _state(obj):
    return (tuple(v for row in obj.matrix_basis for v in row), obj.get(shapes.KIND),
            tuple(obj.get(shapes.HALF,())),float(obj.get(shapes.ROUND,0)),obj.get(shapes.BONE))


def _objects(scene):
    collection=bpy.data.collections.get(shapes.COLLECTION)
    return [o for o in collection.objects if o.name in scene.objects and shapes.owner(o)] if collection else []


def _remember(scene):
    for obj in _objects(scene):
        _states[obj.as_pointer()]=_state(obj)


def copy_to_opposite(source):
    armature=shapes.owner(source)
    if armature is None or source[shapes.BONE] not in armature.data.bones:return None
    props=armature.lol_physics
    name=opposite_name(armature,source[shapes.BONE])
    if not name:
        props.shape_mirror_status='No unambiguous opposite bone for '+source[shapes.BONE]
        return None
    target=shapes.find(armature,name)
    if target is None:
        # only for a listed bone: an unlisted shape would never collide
        wanted=shapes.wanted(armature).get(name)
        if wanted is None:
            props.shape_mirror_status='Add '+name+' to the body/collision bones first'
            return None
        kind,role,set_id=wanted
        target=shapes.create(armature,name,kind,role,set_id=set_id,
                             fitted=(Vector(source[shapes.HALF]),Vector((0,0,0))))
    if target is None:return None
    shapes.heal(target, armature)
    flatten=lambda m:[v for row in m for v in row]
    result=native.rs().mirror_body_shape(
        flatten(armature.data.bones[source[shapes.BONE]].matrix_local),
        flatten(armature.data.bones[name].matrix_local),flatten(source.matrix_basis),
        'XYZ'.index(props.shape_mirror_axis))
    kind=source[shapes.KIND];half=tuple(source[shapes.HALF]);rounding=float(source[shapes.ROUND])
    if (target.get(shapes.KIND),tuple(target.get(shapes.HALF,())),float(target.get(shapes.ROUND,0))) != (kind,half,rounding):
        old=target.data
        target.data=shapes._mesh_for(target.name,kind,Vector(half),rounding)
        if old.users==0:bpy.data.meshes.remove(old)
        target[shapes.KIND]=kind;target[shapes.HALF]=list(half);target[shapes.ROUND]=rounding
    # clear deltas, or they compound with matrix_basis
    target.delta_location=(0,0,0);target.delta_rotation_euler=(0,0,0)
    target.delta_rotation_quaternion=(1,0,0,0);target.delta_scale=(1,1,1)
    target.matrix_basis=Matrix([result[i:i+4] for i in range(0,16,4)])
    target.update_tag(refresh={'OBJECT'})
    _states[target.as_pointer()]=_state(target)
    props.shape_mirror_status=source[shapes.BONE]+' -> '+name
    return target


def settings_changed(props,context):
    global _busy
    if _busy:return
    _busy=True
    try:
        _remember(context.scene)
        source=context.view_layer.objects.active
        if props.shape_mirror_enabled and shapes.owner(source)==props.id_data:
            copy_to_opposite(source)
            _remember(context.scene)
    except (ValueError,RuntimeError,ReferenceError,KeyError) as error:
        props.shape_mirror_status=str(error)
    finally:_busy=False


@persistent
def sync(scene,_depsgraph=None):
    global _busy
    if _busy:return
    _busy=True
    try:
        objects=_objects(scene)
        current={o.as_pointer():_state(o) for o in objects}
        changed=[o for o in objects if o.as_pointer() in _states and current[o.as_pointer()]!=_states[o.as_pointer()]
                 and hasattr(shapes.owner(o), "lol_physics")
                 and shapes.owner(o).lol_physics.shape_mirror_enabled]
        active=bpy.context.view_layer.objects.active
        changed.sort(key=lambda o:(o!=active,o.name))
        handled=set()
        for source in changed:
            if source.as_pointer() in handled:continue
            try:
                target=copy_to_opposite(source)
                handled.add(source.as_pointer())
                if target:handled.add(target.as_pointer())
            except (ValueError,RuntimeError,ReferenceError,KeyError) as error:
                armature=shapes.owner(source)
                if armature:armature.lol_physics.shape_mirror_status=str(error)
        _states.clear()
        _remember(scene)
    finally:_busy=False


@persistent
def reset(_unused=None):
    _states.clear()
    # no scene during registration; the first depsgraph update seeds it instead
    scene = getattr(bpy.context, "scene", None)
    if scene is not None:
        _remember(scene)


def register():
    compat_handlers.install(bpy.app.handlers.depsgraph_update_post,sync)
    for pool in (bpy.app.handlers.load_post,bpy.app.handlers.undo_post,bpy.app.handlers.redo_post):
        compat_handlers.install(pool,reset)
    reset()


def unregister():
    compat_handlers.remove(bpy.app.handlers.depsgraph_update_post,sync)
    for pool in (bpy.app.handlers.load_post,bpy.app.handlers.undo_post,bpy.app.handlers.redo_post):
        compat_handlers.remove(pool,reset)
    _states.clear()
