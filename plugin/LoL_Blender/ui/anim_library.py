"""The animation library: a clip list, click a clip to load it.

The folder is found from the rig's source file: `.../skins/base/animations/` next to the `.skn`.
"""

import os

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty
from bpy.types import Operator, UIList

from ..core import anm_in
from ..core import registry as reg
from ..core import skn_in

#: Clip folder next to the mesh, in Riot's layout.
ANIMATIONS_SUBDIR = "animations"


def find_armature(context):
    """The League armature to act on: from the active object, then the selection, then the only
    one in the scene."""
    obj = context.view_layer.objects.active
    def related(candidate):
        if candidate is None:
            return None
        # a mesh may be bound by modifier only, or sit under a group Empty
        if candidate.type == 'MESH':
            rigs = list(dict.fromkeys(m.object for m in candidate.modifiers
                                     if m.type == 'ARMATURE' and reg.is_lol_armature(m.object)))
            if len(rigs) == 1:
                return rigs[0]
            if len(rigs) > 1:
                return None
        while candidate is not None:
            if reg.is_lol_armature(candidate):
                return candidate
            candidate = candidate.parent
        return None

    rig = related(obj)
    if rig is not None:
        return rig

    for candidate in context.selected_objects:
        rig = related(candidate)
        if rig is not None:
            return rig

    found = [o for o in context.view_layer.objects if reg.is_lol_armature(o)]
    return found[0] if len(found) == 1 else None


def derive_folder(armature):
    """`animations/` beside the file the rig came from, or None."""
    if armature is None:
        return None

    candidates = [armature.get(reg.SOURCE_PATH), armature.get("lol_skl_filepath")]
    for child in getattr(armature, "children", ()):
        candidates.append(child.get(skn_in.SKN_PATH))
        candidates.append(child.get("lol_skn_filepath"))
    candidates.append(armature.get("lol_skn_filepath"))

    for source in candidates:
        if not source:
            continue
        folder = os.path.join(os.path.dirname(source), ANIMATIONS_SUBDIR)
        if os.path.isdir(folder):
            return folder
        # or clips right next to the mesh
        beside = os.path.dirname(source)
        if os.path.isdir(beside) and any(
                n.lower().endswith(".anm") for n in os.listdir(beside)):
            return beside
    return None


class LOLBLENDER_UL_animations(UIList):
    """The clip list. Clicking a name loads it."""

    def draw_item(self, context, layout, _data, item, _icon, _active_data, _active_prop, index):
        props = context.scene.lolblender
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            playing = item.name == props.current_clip
            row.label(text="", icon='PLAY' if playing else 'ACTION')
            op = row.operator("lolblender.load_clip", text=item.name, emboss=False)
            op.filepath = item.filepath
            op.clip_name = item.name
            op.index = index
        elif self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            layout.label(text=item.name, icon='ACTION')

    def filter_items(self, context, data, propname):
        props = context.scene.lolblender
        items = getattr(data, propname)
        flags = [self.bitflag_filter_item] * len(items)
        needle = props.search.lower()
        if needle:
            for i, item in enumerate(items):
                if needle not in item.name.lower():
                    flags[i] = 0
        return flags, []


class LOLBLENDER_OT_refresh_clips(Operator):
    bl_idname = "lolblender.refresh_clips"
    bl_label = "Refresh"
    bl_description = "Rescan the folder and update only the animation list; leave loaded animations unchanged"
    bl_options = {'REGISTER'}

    discard_local_bakes: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        props = context.scene.lolblender
        folder = props.custom_folder or derive_folder(find_armature(context))
        if not folder or not os.path.isdir(folder):
            props.folder = ""
            self.report(
                {'WARNING'},
                "no animations folder found - import a .skn first, or pick one with the folder "
                "button")
            return {'CANCELLED'}

        props.animations.clear()
        props.folder = folder
        names = sorted(n for n in os.listdir(folder) if n.lower().endswith(".anm"))
        for filename in names:
            item = props.animations.add()
            item.name = os.path.splitext(filename)[0]
            item.filepath = os.path.join(folder, filename)

        if not self.discard_local_bakes:
            self.report({'INFO'}, "%d animations listed; loaded actions unchanged" % len(names))
            return {'FINISHED'}

        # only the Reload disk button asks for this
        armature = find_armature(context)
        reloaded = 0
        preserved = 0
        failures = []
        if armature is not None:
            original = armature.animation_data.action if armature.animation_data else None
            live = armature.lol_physics.live
            armature.lol_physics.live = False
            try:
                for item in props.animations:
                    source = os.path.normcase(os.path.abspath(item.filepath))
                    old_actions = [a for a in bpy.data.actions
                                   if a.get("riot_animation_rig") == armature
                                   and not a.name.startswith("__lolphys_")
                                   and os.path.normcase(os.path.abspath(a.get("lol_anm_filepath", ""))) == source]
                    if not old_actions:
                        continue
                    if not self.discard_local_bakes and any(a.get("lol_physics_unexported", False) for a in old_actions):
                        preserved += 1
                        continue
                    try:
                        new = anm_in.import_anm(item.filepath, armature,
                                                set_fps=False, set_frame_range=False).action
                        for old in old_actions:
                            if old == original:
                                original = new
                            old.user_remap(new)
                            bpy.data.actions.remove(old)
                        reloaded += 1
                    except Exception as error:
                        failures.append(item.name + ": " + str(error))
            finally:
                if armature.animation_data:
                    armature.animation_data.action = original
                anm_in.focus_clip(context.scene, original)
                armature.lol_physics.live = live
        if failures:
            self.report({'WARNING'}, "%d reloaded; %d failed (see console)" % (reloaded, len(failures)))
            print("[LoL Blender] Refresh: " + "\n".join(failures))
        elif names:
            self.report({'INFO'}, "%d animations; %d reloaded; %d unexported bakes kept" % (len(names), reloaded, preserved))
        else:
            self.report({'WARNING'}, "no .anm in %s" % folder)
        return {'FINISHED'}


class LOLBLENDER_OT_reload_clips(Operator):
    bl_idname = "lolblender.reload_clips_from_disk"
    bl_label = "Discard local bakes and reload from disk"
    bl_description = "Replace loaded animations with disk files, including unexported physics bakes"
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        return bpy.ops.lolblender.refresh_clips(discard_local_bakes=True)


class LOLBLENDER_OT_pick_folder(Operator):
    bl_idname = "lolblender.pick_folder"
    bl_label = "Choose Animations Folder"
    bl_description = "Pick the folder to scan for .anm files"
    bl_options = {'REGISTER'}

    directory: StringProperty(subtype='DIR_PATH')
    filter_glob: StringProperty(default="*.anm", options={'HIDDEN'})

    def invoke(self, context, _event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if self.directory:
            context.scene.lolblender.custom_folder = self.directory.rstrip("/\\")
            bpy.ops.lolblender.refresh_clips()
        return {'FINISHED'}


class LOLBLENDER_OT_clear_folder(Operator):
    bl_idname = "lolblender.clear_folder"
    bl_label = "Use the rig's own folder"
    bl_description = "Forget the hand-picked folder and go back to the one beside the imported rig"
    bl_options = {'REGISTER'}

    def execute(self, context):
        context.scene.lolblender.custom_folder = ""
        bpy.ops.lolblender.refresh_clips()
        return {'FINISHED'}


class LOLBLENDER_OT_load_clip(Operator):
    bl_idname = "lolblender.load_clip"
    bl_label = "Load Animation"
    bl_description = "Load this clip onto the rig"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: StringProperty()
    clip_name: StringProperty()
    index: IntProperty(default=-1)

    def execute(self, context):
        from .. import native

        props = context.scene.lolblender
        armature = find_armature(context)
        if armature is None:
            self.report({'ERROR'}, "no League armature in the scene")
            return {'CANCELLED'}
        if not self.filepath or not os.path.isfile(self.filepath):
            self.report({'ERROR'}, "not on disk: %s" % self.filepath)
            return {'CANCELLED'}
        try:
            native.rs()
        except native.NativeMissing as e:
            self.report({'ERROR'}, str(e).splitlines()[0])
            return {'CANCELLED'}

        # a click always reloads from disk, even after a bake
        try:
            previous = [a for a in bpy.data.actions
                        if a.get("riot_animation_rig") == armature
                        and not a.name.startswith("__lolphys_")
                        and os.path.normcase(os.path.abspath(a.get("lol_anm_filepath", "")))
                        == os.path.normcase(os.path.abspath(self.filepath))]
            loaded = anm_in.import_anm(
                self.filepath, armature,
                replace_existing=(props.load_mode == 'REPLACE')).action
            for old in previous:
                old.user_remap(loaded)
                bpy.data.actions.remove(old)
        except Exception as e:                      # noqa: BLE001 - surfaced to the user
            self.report({'ERROR'}, "%s: %s" % (os.path.basename(self.filepath), e))
            return {'CANCELLED'}

        current = armature.animation_data.action if armature.animation_data else None
        anm_in.focus_clip(context.scene, current)

        props.current_clip = self.clip_name
        if self.index >= 0:
            props.active_index = self.index
        self.report({'INFO'}, "loaded %s" % self.clip_name)
        return {'FINISHED'}


class LOLBLENDER_OT_load_all_clips(Operator):
    bl_idname = "lolblender.load_all_clips"
    bl_label = "Load all"
    bl_description = (
        "Import every clip in the list as its own Action, so the rig carries the whole library. "
        "Only the last one stays assigned")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from .. import native

        props = context.scene.lolblender
        armature = find_armature(context)
        if armature is None:
            self.report({'ERROR'}, "no League armature in the scene")
            return {'CANCELLED'}
        try:
            native.rs()
        except native.NativeMissing as e:
            self.report({'ERROR'}, str(e).splitlines()[0])
            return {'CANCELLED'}

        loaded = skipped = 0
        assigned = ""
        for item in props.animations:
            if any(a.get("riot_animation_rig") == armature
                   and not a.name.startswith("__lolphys_")
                   and os.path.normcase(os.path.abspath(a.get("lol_anm_filepath", "")))
                   == os.path.normcase(os.path.abspath(item.filepath)) for a in bpy.data.actions):
                skipped += 1
                continue
            if not os.path.isfile(item.filepath):
                continue
            try:
                anm_in.import_anm(item.filepath, armature, replace_existing=False)
            except Exception as e:                      # noqa: BLE001 - surfaced to the user
                self.report({'ERROR'}, "%s: %s" % (item.name, e))
                return {'CANCELLED'}
            loaded += 1
            assigned = item.name

        # mark the last clip imported, which is the one assigned
        if assigned:
            props.current_clip = assigned
        self.report(
            {'INFO'},
            "loaded %d action%s%s" % (
                loaded, "" if loaded == 1 else "s",
                "" if not skipped else ", %d already present" % skipped))
        return {'FINISHED'}


_classes = (
    LOLBLENDER_UL_animations,
    LOLBLENDER_OT_refresh_clips,
    LOLBLENDER_OT_reload_clips,
    LOLBLENDER_OT_pick_folder,
    LOLBLENDER_OT_clear_folder,
    LOLBLENDER_OT_load_clip,
    LOLBLENDER_OT_load_all_clips,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
