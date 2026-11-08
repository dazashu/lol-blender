"""The lol_maya shelf tools: normals, shared vertices, UVs, bind pose, weights.

No namespace tools: Blender scopes bone names per armature, so they are not needed.
"""

import bpy
from contextlib import contextmanager
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty
from bpy.types import Operator

from ..core import registry as reg

#: Influences per `.skn` vertex.
MAX_INFLUENCES = 4


@contextmanager
def _only_mesh(context, obj):
    """Make `obj` the only selected object, so multi-object Edit Mode does not act twice."""
    active = context.view_layer.objects.active
    mode = active.mode if active else 'OBJECT'
    selected = list(context.selected_objects)
    hidden = obj.hide_get()
    try:
        if active and active.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for item in context.view_layer.objects:
            item.select_set(False)
        obj.hide_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        yield
    finally:
        if context.object and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for item in context.view_layer.objects:
            item.select_set(item in selected)
        obj.hide_set(hidden)
        context.view_layer.objects.active = active
        if active and mode != 'OBJECT':
            bpy.ops.object.mode_set(mode=mode)


def _mesh_objects(context, selected_only=True):
    pool = context.selected_objects if selected_only else context.view_layer.objects
    return [o for o in pool if o.type == 'MESH']


def _has_mesh(context):
    return any(o.type == 'MESH' for o in context.selected_objects)


# --------------------------------------------------------------------------------------- normals

class LOLBLENDER_OT_show_face_normals(Operator):
    bl_idname = "lolblender.show_face_normals"
    bl_label = "Face normals"
    bl_description = "Toggle the face-normal overlay. Edit Mode only, like Blender's own overlay"
    bl_options = {'REGISTER'}

    def execute(self, context):
        space = context.space_data
        if space is None or space.type != 'VIEW_3D':
            self.report({'ERROR'}, "run this from a 3D viewport")
            return {'CANCELLED'}
        overlay = space.overlay
        overlay.show_face_normals = not overlay.show_face_normals
        if overlay.show_face_normals and context.mode != 'EDIT_MESH':
            self.report({'INFO'}, "face normals are drawn in Edit Mode - press Tab")
        return {'FINISHED'}


class LOLBLENDER_OT_unify_normals(Operator):
    bl_idname = "lolblender.unify_normals"
    bl_label = "Unify normals"
    bl_description = (
        "Make every face on the selected meshes point the same way (outwards). lol_maya's "
        "'conform/unify normals'")
    bl_options = {'REGISTER', 'UNDO'}

    inside: BoolProperty(name="Point inwards", default=False)

    @classmethod
    def poll(cls, context):
        return _has_mesh(context)

    def execute(self, context):
        meshes = _mesh_objects(context)
        if not meshes:
            self.report({'ERROR'}, "select a mesh")
            return {'CANCELLED'}

        previous = context.mode
        done = 0
        for obj in meshes:
            with _only_mesh(context, obj):
                bpy.ops.object.mode_set(mode='EDIT')
                bpy.ops.mesh.select_all(action='SELECT')
                bpy.ops.mesh.normals_make_consistent(inside=self.inside)
            done += 1
        if previous == 'EDIT_MESH':
            bpy.ops.object.mode_set(mode='EDIT')
        self.report({'INFO'}, "unified normals on %d mesh%s" % (done, "" if done == 1 else "es"))
        return {'FINISHED'}


class LOLBLENDER_OT_flip_normals(Operator):
    bl_idname = "lolblender.flip_normals"
    bl_label = "Reverse selected faces"
    bl_description = "Flip the normal of the selected faces. Edit Mode"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def execute(self, context):
        bpy.ops.mesh.flip_normals()
        return {'FINISHED'}


# ------------------------------------------------------------------------------ shared vertices

class LOLBLENDER_OT_split_shared_vertices(Operator):
    bl_idname = "lolblender.split_shared_vertices"
    bl_label = "Fix shared vertices"
    bl_description = (
        "Split vertices that carry more than one UV or more than one normal, on every mesh in the "
        "scene. A .skn stores ONE uv and ONE normal per vertex, so a vertex shared across a UV "
        "seam cannot be written and the export fails on it")
    bl_options = {'REGISTER', 'UNDO'}

    scene_wide: BoolProperty(
        name="All meshes in the scene",
        description="lol_maya's button works scene-wide; untick to do only the selection",
        default=True,
    )

    def execute(self, context):
        meshes = _mesh_objects(context, selected_only=not self.scene_wide)
        if not meshes:
            self.report({'ERROR'}, "no mesh to work on")
            return {'CANCELLED'}

        previous_active = context.view_layer.objects.active
        total_before = total_after = 0
        for obj in meshes:
            total_before += len(obj.data.vertices)
            with _only_mesh(context, obj):
                import bmesh
                mesh = obj.data
                uv = mesh.uv_layers.active
                normals = mesh.corner_normals
                corners = {}
                for polygon in mesh.polygons:
                    loops = list(polygon.loop_indices)
                    for i, index in enumerate(loops):
                        other = loops[(i + 1) % len(loops)]
                        edge = mesh.loops[index].edge_index
                        for corner in (index, other):
                            vertex = mesh.loops[corner].vertex_index
                            value = tuple(normals[corner].vector) + (tuple(uv.data[corner].uv) if uv else ())
                            corners.setdefault((edge, vertex), set()).add(value)
                split = {edge for (edge, _), values in corners.items() if len(values) > 1}
                if split:
                    bpy.ops.object.mode_set(mode='EDIT')
                    bpy.ops.mesh.select_all(action='DESELECT')
                    bm = bmesh.from_edit_mesh(mesh)
                    bm.edges.ensure_lookup_table()
                    for index in split:
                        bm.edges[index].select_set(True)
                    bmesh.update_edit_mesh(mesh)
                    bpy.ops.mesh.edge_split(type='EDGE')
            total_after += len(obj.data.vertices)

        if previous_active is not None:
            context.view_layer.objects.active = previous_active
        added = total_after - total_before
        self.report(
            {'INFO'},
            "%d mesh%s: %d -> %d vertices (%d split)" % (
                len(meshes), "" if len(meshes) == 1 else "es", total_before, total_after, added))
        return {'FINISHED'}


class LOLBLENDER_OT_separate_by_material(Operator):
    bl_idname = "lolblender.separate_by_material"
    bl_label = "Separate by material"
    bl_description = (
        "Split the selected meshes into one object per material slot. The same thing the .skn "
        "importer's 'Separate submeshes' does, for a mesh that was imported combined or built by "
        "hand")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _has_mesh(context)

    def execute(self, context):
        meshes = _mesh_objects(context)
        before = set(bpy.data.objects)

        for obj in meshes:
            if len(obj.data.materials) < 2:
                continue
            with _only_mesh(context, obj):
                bpy.ops.object.mode_set(mode='EDIT')
                bpy.ops.mesh.select_all(action='SELECT')
                bpy.ops.mesh.separate(type='MATERIAL')

        created = [o for o in bpy.data.objects if o not in before]

        # copy the submesh identity onto each piece (the original is a piece too), reading the
        # material from the faces: every piece keeps the full slot list
        for obj in set(created) | set(meshes):
            if obj.type != 'MESH' or not obj.data.polygons or not obj.data.materials:
                continue
            slot = obj.data.polygons[0].material_index
            if slot >= len(obj.data.materials):
                continue
            material = obj.data.materials[slot]
            if material is None:
                continue
            if "riot_submesh_index" in material:
                obj["riot_submesh_index"] = material["riot_submesh_index"]
            if "riot_submesh_name" in material:
                obj["riot_submesh_name"] = material["riot_submesh_name"]
                obj.name = material["riot_submesh_name"]

        self.report({'INFO'}, "separated into %d extra object%s" % (
            len(created), "" if len(created) == 1 else "s"))
        return {'FINISHED'}


# -------------------------------------------------------------------------------------- submesh

class LOLBLENDER_OT_lasso_select(Operator):
    bl_idname = "lolblender.lasso_select"
    bl_label = "Lasso select"
    bl_description = (
        "Edit Mode on the selected mesh, vertex selection, with the Lasso tool active. Draw "
        "around the vertices to take, then press New submesh")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        obj = context.view_layer.objects.active
        return (obj is not None and obj.type == 'MESH') or _has_mesh(context)

    def execute(self, context):
        obj = context.view_layer.objects.active
        if obj is None or obj.type != 'MESH':
            obj = next(iter(_mesh_objects(context)), None)
            if obj is None:
                self.report({'ERROR'}, "select a mesh")
                return {'CANCELLED'}
        if context.mode != 'EDIT_MESH':
            if context.object is not None and context.object.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            context.view_layer.objects.active = obj
            obj.select_set(True)
            bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_mode(type='VERT')
        try:
            bpy.ops.wm.tool_set_by_id(name="builtin.select_lasso", space_type='VIEW_3D')
        except (RuntimeError, TypeError) as error:
            self.report({'WARNING'}, "Edit Mode is on, but the Lasso tool could not be set: %s"
                        % error)
            return {'FINISHED'}
        self.report({'INFO'}, "lasso: drag around vertices (Shift adds, Ctrl removes)")
        return {'FINISHED'}


def _submesh_name(material, obj):
    """The submesh a material stands for: its stored name, else the material name."""
    if material is not None:
        name = material.get("riot_submesh_name")
        return str(name) if name else material.name
    return str(obj.get("riot_submesh_name") or obj.name)


def next_submesh_name(base):
    """`base_1`, then `base_2`... the first one no material, object or submesh uses."""
    taken = set(bpy.data.materials.keys()) | set(bpy.data.objects.keys())
    for material in bpy.data.materials:
        if material.get("riot_submesh_name"):
            taken.add(str(material["riot_submesh_name"]))
    index = 1
    while "%s_%d" % (base, index) in taken:
        index += 1
    return "%s_%d" % (base, index)


class LOLBLENDER_OT_new_submesh(Operator):
    bl_idname = "lolblender.new_submesh"
    bl_label = "New submesh"
    bl_description = (
        "Make the selected faces a submesh of their own, with a new material named after the "
        "original plus _1 (_2, ... if taken). Weights, UVs and the rig are kept. Edit Mode")
    bl_options = {'REGISTER', 'UNDO'}

    target: EnumProperty(
        name="Put it in",
        items=(
            ('AUTO', "Auto",
             "A new object when the mesh holds one submesh (separated import), a new material "
             "slot when it holds several (combined import)"),
            ('OBJECT', "New object", "Separate the faces into a new object"),
            ('SLOT', "Same object", "Keep the faces here, on a new material slot"),
        ),
        default='AUTO',
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def execute(self, context):
        import bmesh

        objects = [o for o in (context.objects_in_mode or [context.edit_object])
                   if o is not None and o.type == 'MESH']
        # what each object had selected, read before anything is changed
        plans = []
        for obj in objects:
            bm = bmesh.from_edit_mesh(obj.data)
            faces = [f.index for f in bm.faces if f.select]
            if faces:
                plans.append((obj, faces, len(bm.faces)))

        made = []
        for obj, faces, total in plans:
            bm = bmesh.from_edit_mesh(obj.data)
            bm.faces.ensure_lookup_table()
            selected = [bm.faces[i] for i in faces]
            # the source submesh: the most used slot among the selected faces
            counts = {}
            for face in selected:
                counts[face.material_index] = counts.get(face.material_index, 0) + 1
            slot = max(counts, key=counts.get)
            source = obj.data.materials[slot] if slot < len(obj.data.materials) else None
            name = next_submesh_name(_submesh_name(source, obj))

            target = self.target
            if target == 'AUTO':
                used = {f.material_index for f in bm.faces}
                target = 'SLOT' if len(used) > 1 else 'OBJECT'
            if target == 'OBJECT' and len(faces) == total:
                self.report({'WARNING'}, "%s: every face is selected, nothing to split off"
                            % obj.name)
                continue

            material = source.copy() if source is not None else bpy.data.materials.new(name)
            material.name = name
            material["riot_submesh_name"] = name
            if "riot_submesh_index" in material:
                del material["riot_submesh_index"]

            if target == 'SLOT':
                obj.data.materials.append(material)
                index = len(obj.data.materials) - 1
                for face in selected:
                    face.material_index = index
                bmesh.update_edit_mesh(obj.data)
                made.append(name)
                continue

            # separate only this object's faces: an earlier piece may have deselected them, and
            # the other objects are deselected for the call
            for face in selected:
                face.select_set(True)
            bmesh.update_edit_mesh(obj.data)
            for other, _other_faces, _total in plans:
                if other is obj:
                    continue
                other_bm = bmesh.from_edit_mesh(other.data)
                for face in other_bm.faces:
                    face.select_set(False)
                other_bm.select_flush_mode()
                bmesh.update_edit_mesh(other.data)
            before = set(bpy.data.objects)
            bpy.ops.mesh.separate(type='SELECTED')
            created = [o for o in bpy.data.objects if o not in before and o.type == 'MESH']
            if not created:
                bpy.data.materials.remove(material)
                continue
            piece = created[0]
            piece.data.materials.clear()
            piece.data.materials.append(material)
            piece.data.polygons.foreach_set("material_index", [0] * len(piece.data.polygons))
            piece.data.update()
            piece.name = name
            piece.data.name = name
            piece["riot_submesh_name"] = name
            if "riot_submesh_index" in piece:
                del piece["riot_submesh_index"]
            made.append(name)

        if not made:
            self.report({'ERROR'}, "select whole faces first (Lasso select, then this)")
            return {'CANCELLED'}
        self.report({'INFO'}, "new submesh%s: %s" % ("" if len(made) == 1 else "es",
                                                    ", ".join(made)))
        return {'FINISHED'}


# ------------------------------------------------------------------------------------------- UV

class LOLBLENDER_OT_open_uv_editor(Operator):
    bl_idname = "lolblender.open_uv_editor"
    bl_label = "UV Editor"
    bl_description = "Switch to the UV Editing workspace, in Edit Mode with everything selected"
    bl_options = {'REGISTER'}

    def execute(self, context):
        workspace = bpy.data.workspaces.get("UV Editing")
        if workspace is None:
            self.report({'ERROR'}, "this file has no 'UV Editing' workspace")
            return {'CANCELLED'}
        context.window.workspace = workspace

        obj = context.view_layer.objects.active
        if obj is not None and obj.type == 'MESH':
            if context.mode != 'EDIT_MESH':
                bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_all(action='SELECT')
        return {'FINISHED'}


class LOLBLENDER_OT_uv_to_corner(Operator):
    bl_idname = "lolblender.uv_to_corner"
    bl_label = "UVs to corner"
    bl_description = (
        "Move the selected UVs onto one corner of the tile. For the flat helper meshes that only "
        "need a single texel - lol_maya has one button per corner")
    bl_options = {'REGISTER', 'UNDO'}

    corner: EnumProperty(
        name="Corner",
        items=(
            ('BL', "Bottom left", "(0, 0)"),
            ('BR', "Bottom right", "(1, 0)"),
            ('TL', "Top left", "(0, 1)"),
            ('TR', "Top right", "(1, 1)"),
        ),
        default='BL',
    )

    @classmethod
    def poll(cls, context):
        obj = context.view_layer.objects.active
        return obj is not None and obj.type == 'MESH' and context.mode == 'EDIT_MESH'

    def execute(self, context):
        import bmesh

        target = {'BL': (0.0, 0.0), 'BR': (1.0, 0.0), 'TL': (0.0, 1.0), 'TR': (1.0, 1.0)}[self.corner]
        moved = 0
        objects = _mesh_objects(context) or [context.view_layer.objects.active]
        for obj in objects:
            if obj is None or obj.type != 'MESH':
                continue
            bm = bmesh.from_edit_mesh(obj.data)
            layer = bm.loops.layers.uv.active
            if layer is None:
                continue
            # every loop of the selected faces
            for face in bm.faces:
                if not face.select:
                    continue
                for loop in face.loops:
                    loop[layer].uv = target
                    moved += 1
            bmesh.update_edit_mesh(obj.data)

        self.report({'INFO'}, "moved %d uv%s to %s" % (moved, "" if moved == 1 else "s", target))
        return {'FINISHED'}


# ------------------------------------------------------------------------------- pose and weights

class LOLBLENDER_OT_go_to_bind_pose(Operator):
    bl_idname = "lolblender.go_to_bind_pose"
    bl_label = "Go to bind pose"
    bl_description = (
        "Clear every pose channel, putting the rig back at the .skl bind pose. Does not touch the "
        "action - stepping a frame plays it again")
    bl_options = {'REGISTER', 'UNDO'}

    unassign_action: BoolProperty(
        name="Unassign the action",
        description="Also detach the current action, so playback does not immediately re-pose it",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        from ..ui import anim_library
        return anim_library.find_armature(context) is not None

    def execute(self, context):
        from ..ui import anim_library

        armature = anim_library.find_armature(context)
        # drop the cached simulated pose; Live stays on and restarts on the next frame
        from . import physics
        physics.forget(armature)
        if self.unassign_action and armature.animation_data is not None:
            armature.animation_data.action = None

        for pose_bone in armature.pose.bones:
            pose_bone.matrix_basis.identity()

        context.view_layer.update()
        self.report({'INFO'}, "%r is at its bind pose" % armature.name)
        return {'FINISHED'}


class LOLBLENDER_OT_set_bind_pose(Operator):
    """lol_maya's 'set current pose as bind pose': new rest, updated bind, actions reloaded
    against the new rest so they still play the same."""

    bl_idname = "lolblender.set_bind_pose"
    bl_label = "Set current pose as bind pose"
    bl_description = (
        "Make the pose you are looking at the new rest pose, and rewrite the stored bind so an "
        "export matches it. Existing animations are reloaded from their source files so they keep "
        "playing exactly as before")
    bl_options = {'REGISTER', 'UNDO'}

    keep_animation: BoolProperty(
        name="Keep animations playing the same",
        description=(
            "Re-import every action from the .anm it came from, against the new rest. Without "
            "this the actions keep their old channel values, which now mean something else"),
        default=True,
    )

    @classmethod
    def poll(cls, context):
        from ..ui import anim_library
        return anim_library.find_armature(context) is not None

    keep_meshes: BoolProperty(
        name="Bound meshes keep their shape",
        description=(
            "Meshes bound to the rig keep the shape they show now, like Maya's skin cluster. "
            "Untick to let them go back to their modeled shape"),
        default=True,
    )

    def execute(self, context):
        from ..core import rest_pose
        from ..ui import anim_library
        from . import physics

        armature = anim_library.find_armature(context)
        if self.keep_animation:
            try:
                rest_pose.check_actions(armature)
            except Exception as error:
                self.report({'ERROR'}, "cannot rebuild animation: %s" % error)
                return {'CANCELLED'}
        physics.forget(armature)
        result = rest_pose.pose_to_rest(context, armature, keep_meshes=self.keep_meshes,
                                        keep_animation=self.keep_animation)

        message = "%r rebound at the current pose" % armature.name
        if result.reloaded:
            message += ", %d action%s reloaded" % (result.reloaded,
                                                   "" if result.reloaded == 1 else "s")
        orphaned = result.orphaned
        if orphaned:
            self.report(
                {'WARNING'},
                message + " - %d action%s have no source .anm and now mean something else: %s"
                % (len(orphaned), "" if len(orphaned) == 1 else "s", ", ".join(orphaned[:3])))
        else:
            self.report({'INFO'}, message)
        return {'FINISHED'}


class LOLBLENDER_OT_prune_influences(Operator):
    bl_idname = "lolblender.prune_influences"
    bl_label = "Prune to 4 influences"
    bl_description = (
        "Keep only the 4 heaviest weights per vertex and renormalise them to sum to 1. A .skn "
        "stores exactly four, so anything beyond the fourth is silently lost on export - this "
        "makes the loss visible and controlled instead")
    bl_options = {'REGISTER', 'UNDO'}

    limit: IntProperty(
        name="Max influences", default=MAX_INFLUENCES, min=1, max=8,
        description="The format's limit is 4. Above that the export cannot represent the result")
    threshold: FloatProperty(
        name="Drop below", default=0.001, min=0.0, max=1.0, precision=4,
        description="Also drop any weight under this. lol_maya prunes at 0.001 (lol_maya.py:2141)")
    renormalise: BoolProperty(
        name="Renormalise", default=True,
        description="Rescale what is left so the weights sum to 1, which is what the engine assumes")

    @classmethod
    def poll(cls, context):
        return any(o.type == 'MESH' and o.vertex_groups for o in context.selected_objects)

    def execute(self, context):
        meshes = [o for o in _mesh_objects(context) if o.vertex_groups]
        if not meshes:
            self.report({'ERROR'}, "select a skinned mesh")
            return {'CANCELLED'}

        touched = 0
        worst_lost = 0.0
        for obj in meshes:
            with _only_mesh(context, obj):
                groups = obj.vertex_groups
                # bone groups only (masks are not influences)
                armature = next((m.object for m in obj.modifiers
                                 if m.type == 'ARMATURE' and m.object is not None), None)
                bone_names = set(armature.data.bones.keys()) if armature is not None else None

                for vertex in obj.data.vertices:
                    entries = []
                    for element in vertex.groups:
                        group = groups[element.group]
                        if bone_names is not None and group.name not in bone_names:
                            continue
                        entries.append((element.weight, group))

                    keep = sorted(entries, key=lambda e: -e[0])[:self.limit]
                    keep = [(w, g) for w, g in keep if w > self.threshold]
                    dropped = [(w, g) for w, g in entries if (w, g) not in keep]
                    if not dropped and not (self.renormalise and keep and sum(w for w, _ in keep) != 1.0):
                        continue

                    lost = sum(w for w, _ in dropped)
                    worst_lost = max(worst_lost, lost)
                    for _, group in dropped:
                        group.remove([vertex.index])

                    if self.renormalise and keep:
                        total = sum(w for w, _ in keep)
                        if total > 0.0:
                            for weight, group in keep:
                                group.add([vertex.index], weight / total, 'REPLACE')
                    touched += 1
        self.report(
            {'INFO'},
            "pruned %d vertices; the largest weight thrown away was %.4f" % (touched, worst_lost))
        return {'FINISHED'}


class LOLBLENDER_OT_weight_paint(Operator):
    bl_idname = "lolblender.weight_paint"
    bl_label = "Weight paint"
    bl_description = "Jump into Weight Paint on the selected mesh with its rig in Pose Mode"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return _has_mesh(context)

    def execute(self, context):
        mesh = next((o for o in _mesh_objects(context)), None)
        if mesh is None:
            self.report({'ERROR'}, "select a mesh")
            return {'CANCELLED'}

        armature = next((m.object for m in mesh.modifiers
                         if m.type == 'ARMATURE' and m.object is not None), None)
        context.view_layer.objects.active = mesh
        bpy.ops.object.mode_set(mode='WEIGHT_PAINT')
        if armature is not None:
            armature.select_set(True)
            # rig selected too, so bones can be picked while painting
            context.object.data.use_paint_mask_vertex = False
        return {'FINISHED'}


class LOLBLENDER_OT_weight_report(Operator):
    bl_idname = "lolblender.weight_report"
    bl_label = "Check weights"
    bl_description = (
        "Report what would break on export: vertices over 4 influences, vertices with no weight "
        "at all, and weights that do not sum to 1")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return any(o.type == 'MESH' and o.vertex_groups for o in context.selected_objects)

    def execute(self, context):
        problems = []
        for obj in [o for o in _mesh_objects(context) if o.vertex_groups]:
            over = unweighted = 0
            worst_sum = 0.0
            # bone groups only, like the exporter
            groups = obj.vertex_groups
            armature = next((m.object for m in obj.modifiers
                             if m.type == 'ARMATURE' and m.object is not None), None)
            bone_names = set(armature.data.bones.keys()) if armature is not None else None
            for vertex in obj.data.vertices:
                weights = [e.weight for e in vertex.groups
                           if e.weight > 0.0
                           and (bone_names is None
                                or groups[e.group].name in bone_names)]
                if not weights:
                    unweighted += 1
                    continue
                if len(weights) > MAX_INFLUENCES:
                    over += 1
                worst_sum = max(worst_sum, abs(sum(weights) - 1.0))
            line = "%s: %d over %d influences, %d unweighted, worst weight-sum error %.4g" % (
                obj.name, over, MAX_INFLUENCES, unweighted, worst_sum)
            print("[LoL Blender] %s" % line)
            if over or unweighted or worst_sum > 1e-3:
                problems.append(line)

        if problems:
            self.report({'WARNING'}, problems[0] if len(problems) == 1
                        else "%d meshes have weight problems - see the console" % len(problems))
        else:
            self.report({'INFO'}, "weights are export-ready")
        return {'FINISHED'}


_classes = (
    LOLBLENDER_OT_show_face_normals,
    LOLBLENDER_OT_unify_normals,
    LOLBLENDER_OT_flip_normals,
    LOLBLENDER_OT_split_shared_vertices,
    LOLBLENDER_OT_separate_by_material,
    LOLBLENDER_OT_lasso_select,
    LOLBLENDER_OT_new_submesh,
    LOLBLENDER_OT_open_uv_editor,
    LOLBLENDER_OT_uv_to_corner,
    LOLBLENDER_OT_go_to_bind_pose,
    LOLBLENDER_OT_set_bind_pose,
    LOLBLENDER_OT_prune_influences,
    LOLBLENDER_OT_weight_paint,
    LOLBLENDER_OT_weight_report,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
