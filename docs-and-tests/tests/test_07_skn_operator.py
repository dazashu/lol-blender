"""The SKN import operator, driven the way the File menu drives it."""

import bpy

from LoL_Blender.core import registry as reg
from run import graves


def _fresh_addon():
    import importlib
    import sys
    for name in [n for n in sys.modules if n.split(".")[0] == "LoL_Blender"]:
        del sys.modules[name]
    return importlib.import_module("LoL_Blender")


def _clean():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon = _fresh_addon()
    addon.register()
    return addon


def test_both_importers_appear_in_the_menu():
    addon = _clean()
    try:
        assert hasattr(bpy.ops.lolblender, "import_skn")
        assert hasattr(bpy.ops.lolblender, "import_skl")
        entries = bpy.types.TOPBAR_MT_file_import._dyn_ui_initialize()
        # Both modules append a function called `_menu_entry`, so count rather than name.
        appended = [f for f in entries if getattr(f, "__name__", "") == "_menu_entry"]
        assert len(appended) >= 2, "expected an entry per importer, found %d" % len(appended)
    finally:
        addon.unregister()


def test_operator_imports_mesh_and_skeleton_together():
    addon = _clean()
    try:
        result = bpy.ops.lolblender.import_skn(
            filepath=graves("graves.skn"), report_details=False)
        assert result == {'FINISHED'}, result

        meshes = [o for o in bpy.data.objects if o.type == 'MESH']
        armatures = [o for o in bpy.data.objects if o.type == 'ARMATURE']
        assert len(meshes) == 1, [o.name for o in meshes]
        assert len(armatures) == 1, "the .skl beside the .skn should have loaded too"
        assert reg.is_lol_armature(armatures[0])

        obj = meshes[0]
        assert obj.parent is armatures[0]
        assert any(m.type == 'ARMATURE' for m in obj.modifiers)
        assert len(obj.vertex_groups) > 0, "no weights were applied"
        assert bpy.context.mode == 'OBJECT'
    finally:
        addon.unregister()


def test_load_skeleton_off_leaves_the_mesh_unbound():
    addon = _clean()
    try:
        bpy.ops.lolblender.import_skn(
            filepath=graves("graves.skn"), load_skeleton=False, report_details=False)

        assert not [o for o in bpy.data.objects if o.type == 'ARMATURE']
        obj = [o for o in bpy.data.objects if o.type == 'MESH'][0]
        assert not obj.vertex_groups, "an unbound mesh must not get vertex groups"
        assert not [m for m in obj.modifiers if m.type == 'ARMATURE']
        # The geometry still has to be complete - unbound is not a partial import.
        assert len(obj.data.vertices) == 7088
    finally:
        addon.unregister()


def test_separate_submeshes_flag_is_honoured():
    """graves.skn has one submesh, so both settings must produce exactly one mesh object and no
    group empty. The flag switching itself off is lol_maya.py:73-74."""
    for separate in (True, False):
        addon = _clean()
        try:
            bpy.ops.lolblender.import_skn(
                filepath=graves("graves.skn"), separate_submeshes=separate,
                report_details=False)
            meshes = [o for o in bpy.data.objects if o.type == 'MESH']
            empties = [o for o in bpy.data.objects if o.type == 'EMPTY']
            assert len(meshes) == 1, "separate=%s gave %r" % (separate, [o.name for o in meshes])
            assert not empties, "separate=%s created a group for a single submesh" % separate
        finally:
            addon.unregister()


def test_operator_refuses_a_skl_as_a_skn():
    addon = _clean()
    try:
        message = None
        try:
            bpy.ops.lolblender.import_skn(
                filepath=graves("graves.skl"), report_details=False)
        except RuntimeError as e:
            message = str(e)
        assert message and "graves.skl" in message, message
        assert not [o for o in bpy.data.objects if o.type == 'MESH'], \
            "a failed import must leave nothing behind"
    finally:
        addon.unregister()
