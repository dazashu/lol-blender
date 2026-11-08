"""The "a League model is already loaded" dialog shown before an import.

The importer parks the rest of its work as `run(mode, report)` and returns; the dialog calls it on
OK. `run` must not hold the importer operator, which Blender frees when it returns. Cancel imports
nothing.
"""


import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator

from ..core import replace

#: token -> (run, source_path, label), removed when the dialog answers.
_PENDING = {}
_NEXT = [0]


def survey(context, source_path):
    """What is already in the scene: (rigs, meshes, objects from this same file)."""
    scene = getattr(context, "scene", None)
    existing = replace.imported_objects(scene)
    rigs = [o for o in existing if o.type == 'ARMATURE']
    meshes = [o for o in existing if o.type == 'MESH']
    same = replace.matching(source_path, scene) if source_path else []
    return rigs, meshes, same


def needed(context, source_path):
    """True when there is something to ask about. Never in background mode (no dialog)."""
    if bpy.app.background:
        return False
    rigs, meshes, _same = survey(context, source_path)
    return bool(rigs or meshes)


def update_target(context, source_path):
    """For a .skl: the skeleton in the scene it would update (lol_maya), or None."""
    if not source_path.lower().endswith(".skl"):
        return None
    try:
        from .. import native
        from ..core import skl_update
        return skl_update.find_target(context, native.rs().Skeleton.from_path(source_path))
    except Exception:                                   # noqa: BLE001 - only a suggestion
        return None


def default_choice(context, source_path):
    """UPDATE for a .skl matching a skeleton, FILE when something came from this file, else ALL."""
    if update_target(context, source_path) is not None:
        return 'UPDATE'
    _rigs, _meshes, same = survey(context, source_path)
    return 'FILE' if same else 'ALL'


def park(run, source_path, label):
    """Hold `run` until the dialog answers. Returns the token to hand the dialog."""
    _NEXT[0] += 1
    token = "lolblender_ask_%d" % _NEXT[0]
    _PENDING[token] = (run, source_path, label)
    return token


def open_dialog(context, run, source_path, label):
    """Ask, then run. Returns what the importer should return."""
    token = park(run, source_path, label)
    bpy.ops.lolblender.ask_existing('INVOKE_DEFAULT', token=token)
    return {'FINISHED'}


class LOLBLENDER_OT_ask_existing(Operator):
    bl_idname = "lolblender.ask_existing"
    bl_label = "A League model is already loaded"
    bl_description = "Choose what happens to the model already in the scene"
    bl_options = {'REGISTER', 'UNDO'}

    token: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    choice: EnumProperty(
        name="",
        items=(
            ('UPDATE', "Update the skeleton",
             "Like lol_maya: put the file's joints onto the skeleton already in the scene. The "
             "pose goes back to the file's bind, missing joints are added, weights are kept"),
            ('FILE', "Replace it",
             "Remove what was imported from this same file, then import. Anything else in the "
             "scene is left exactly as it is"),
            ('ALL', "Replace every League model",
             "Remove every rig and mesh this plugin imported, then import"),
            ('KEEP', "Keep both",
             "Import alongside what is already there"),
        ),
        default='FILE',
        options={'SKIP_SAVE'},
    )

    def invoke(self, context, _event):
        entry = _PENDING.get(self.token)
        self.choice = default_choice(context, entry[1] if entry else "")
        return context.window_manager.invoke_props_dialog(self, width=440)

    def draw(self, context):
        layout = self.layout
        entry = _PENDING.get(self.token)
        source = entry[1] if entry else ""
        label = entry[2] if entry else ""
        rigs, meshes, same = survey(context, source)

        box = layout.box()
        box.label(text="Already in the scene", icon='INFO')
        row = box.row()
        row.scale_y = 0.8
        row.label(text="%d rig%s, %d mesh object%s"
                       % (len(rigs), "" if len(rigs) == 1 else "s",
                          len(meshes), "" if len(meshes) == 1 else "s"))
        for obj in rigs[:4]:
            line = box.row()
            line.scale_y = 0.7
            line.label(text=obj.name, icon='ARMATURE_DATA')
        if len(rigs) > 4:
            line = box.row()
            line.scale_y = 0.7
            line.label(text="and %d more" % (len(rigs) - 4))

        if label:
            head = layout.row()
            head.scale_y = 0.85
            head.label(text="Importing %s" % label, icon='IMPORT')

        note = layout.row()
        note.scale_y = 0.8
        if same:
            note.label(text="%d object%s came from this same file"
                            % (len(same), "" if len(same) == 1 else "s"), icon='CHECKMARK')
        else:
            note.label(text="Nothing in the scene came from this file", icon='QUESTION')

        layout.separator()
        # one row per option: with expand=True Blender blanks labels that do not fit
        choices = layout.column(align=True)
        choices.scale_y = 1.2
        options = [('FILE', 'FILE_REFRESH'), ('ALL', 'TRASH'), ('KEEP', 'DUPLICATE')]
        target = update_target(context, source)
        if target is not None:
            options.insert(0, ('UPDATE', 'ARMATURE_DATA'))
            hint = layout.row()
            hint.scale_y = 0.8
            hint.label(text="%s shares its joints with this file" % target.name,
                       icon='BONE_DATA')
        for identifier, icon in options:
            row = choices.row(align=True)
            row.prop_enum(self, "choice", identifier, icon=icon)

        foot = layout.column(align=True)
        foot.scale_y = 0.7
        foot.label(text="Only objects this plugin imported can be removed.")
        foot.label(text="Anything you built yourself is never touched.")

    def execute(self, context):
        entry = _PENDING.pop(self.token, None)
        if entry is None:
            self.report({'ERROR'}, "that import is no longer pending")
            return {'CANCELLED'}
        run = entry[0]
        # this operator's report: the importer is gone by now
        return run(self.choice, self.report)

    def cancel(self, _context):
        _PENDING.pop(self.token, None)


_classes = (LOLBLENDER_OT_ask_existing,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    _PENDING.clear()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
