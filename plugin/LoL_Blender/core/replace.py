"""Replace previously imported objects on re-import, and roll back a failed import.

Only objects this plugin imported are replaceable, found by their `riot_*` marks, never by name.
Datablocks are left to Blender's zero-user cleanup.
"""

import bpy
from functools import wraps

from . import registry as reg
from . import skn_in


def transactional_import(function):
    """Keep the old model until the import succeeds; remove partial data on failure."""
    @wraps(function)
    def run(context, paths, mode, options, report):
        if not paths:
            report({'ERROR'}, "no file selected")
            return {'CANCELLED'}
        if mode not in {'KEEP', 'FILE', 'ALL'}:
            raise ValueError("unknown replace mode %r" % mode)
        targets = (imported_objects(context.scene) if mode == 'ALL' else
                   list(dict.fromkeys(o for path in paths for o in matching(path, context.scene)))
                   if mode == 'FILE' else [])
        collections = [bpy.data.objects, bpy.data.meshes, bpy.data.armatures,
                       bpy.data.materials, bpy.data.actions, bpy.data.collections]
        before = [set(pool) for pool in collections]
        selected = list(context.selected_objects)
        active = context.view_layer.objects.active
        previous_mode = active.mode if active else 'OBJECT'
        result = {'CANCELLED'}
        try:
            if active and active.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            result = function(context, paths, 'KEEP', options, report)
            if result == {'FINISHED'}:
                remove(targets)
            return result
        finally:
            if result != {'FINISHED'}:
                obj = context.view_layer.objects.active
                if obj and obj.mode != 'OBJECT':
                    bpy.ops.object.mode_set(mode='OBJECT')
                for pool, old in zip(collections, before):
                    for item in set(pool) - old:
                        pool.remove(item, do_unlink=True)
                context.view_layer.update()
                for obj in context.view_layer.objects:
                    if obj is not None:
                        obj.select_set(obj in selected)
                context.view_layer.objects.active = active
                if active and previous_mode != 'OBJECT':
                    bpy.ops.object.mode_set(mode=previous_mode)
    return run


def atomic_scene_import(function):
    """The same rollback for import operators that do not offer replacement modes."""
    @wraps(function)
    def execute(operator, context):
        @transactional_import
        def invoke(context, _paths, _mode, _options, _report):
            return function(operator, context)
        return invoke(context, ['<import>'], 'KEEP', {}, operator.report)
    return execute


def imported_objects(scene=None):
    """Every object in the scene that this plugin created."""
    view_layer_objects = (scene or bpy.context.scene).objects
    found = []
    for obj in view_layer_objects:
        if reg.is_lol_armature(obj):
            found.append(obj)
        elif skn_in.SKN_PATH in obj:
            found.append(obj)
        elif obj.type == 'EMPTY' and skn_in.SUBMESH_ORDER in obj:
            found.append(obj)
    return found


def matching(source_path, scene=None):
    """Objects that came from this file, plus everything parented to them."""
    import os

    wanted = os.path.normcase(os.path.abspath(source_path))
    stems = {os.path.splitext(wanted)[0]}

    found = []
    for obj in imported_objects(scene):
        for key in (skn_in.SKN_PATH, reg.SOURCE_PATH):
            path = obj.get(key)
            if not path:
                continue
            if os.path.splitext(os.path.normcase(os.path.abspath(path)))[0] in stems:
                found.append(obj)
                break
    matched = set(found)
    for obj in imported_objects(scene):
        parent = obj.parent
        while parent is not None:
            if parent in matched:
                found.append(obj)
                break
            parent = parent.parent
    return list(dict.fromkeys(found))


def remove(objects):
    """Delete these objects. Returns how many went."""
    gone = 0
    for obj in list(objects):
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
            gone += 1
        except ReferenceError:
            pass  # already removed with its parent
    return gone


def apply(mode, source_path, scene=None):
    """Apply a replace mode before an import. Returns a report line or None.

    KEEP adds alongside, FILE removes what came from this file, ALL removes every League object.
    """
    if mode == 'KEEP':
        return None
    if mode == 'FILE':
        targets = matching(source_path, scene)
        label = "from this file"
    elif mode == 'ALL':
        targets = imported_objects(scene)
        label = "imported earlier"
    else:
        raise ValueError("unknown replace mode %r" % mode)

    if not targets:
        return None
    gone = remove(targets)
    return "replaced %d object%s %s" % (gone, "" if gone == 1 else "s", label)
