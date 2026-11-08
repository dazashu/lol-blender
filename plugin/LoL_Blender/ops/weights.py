"""The Daza Weight buttons: check, clean, bind, rebind, unbind.

Every failure is reported: one line in the status bar, the full list in the panel.
"""

import bpy
from bpy.props import BoolProperty
from bpy.types import Operator

from ..core import weights


def _gather(context):
    armature = weights.armature_in(context)
    bones = weights.selected_bones(armature, context) if armature else []
    if armature and not context.scene.lol_weight.selected_bones_only:
        bones = [b.name for b in armature.data.bones if b.use_deform]
    meshes = weights.selected_meshes(context)
    return armature, bones, meshes


def _store(context, problems, headline=""):
    """Store the problems for the panel."""
    props = context.scene.lol_weight
    props.report.clear()
    for problem in problems:
        item = props.report.add()
        item.target = problem.target
        item.summary = problem.summary
        item.fix = problem.fix
        item.blocking = problem.blocking
    props.status = headline or ("%d problem%s found" % (len(problems),
                                                        "" if len(problems) == 1 else "s")
                                if problems else "Ready")


class LOLWEIGHT_OT_check(Operator):
    bl_idname = "lolblender.weight_check"
    bl_label = "Check Meshes"
    bl_description = ("Look at what is selected and report anything that would make the bind wrong "
                      "or ugly, without binding")

    def execute(self, context):
        armature, bones, meshes = _gather(context)
        problems = weights.diagnose(armature, meshes, bones)
        blocking = weights.blockers(problems)
        if not problems:
            _store(context, problems, "Nothing wrong - %d bones, %d meshes"
                   % (len(bones), len(meshes)))
            self.report({'INFO'}, "Ready to bind: %d bones, %d meshes" % (len(bones), len(meshes)))
            return {'FINISHED'}
        _store(context, problems)
        first = problems[0]
        if blocking:
            self.report({'ERROR'}, "%d must be fixed first. %s" % (len(blocking), first.full()))
        else:
            self.report({'WARNING'}, "%d warning%s, none blocking. %s"
                        % (len(problems), "" if len(problems) == 1 else "s", first.line()))
        return {'FINISHED'}


class LOLWEIGHT_OT_clean(Operator):
    bl_idname = "lolblender.weight_clean"
    bl_label = "Clean Meshes"
    bl_description = ("Run the whole cleanup on the selected meshes: apply rotation and scale, "
                      "merge doubled vertices, dissolve faces with no area, recalculate normals "
                      "outside")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        _armature, _bones, meshes = _gather(context)
        if not meshes:
            self.report({'ERROR'}, "Select the meshes to clean")
            return {'CANCELLED'}
        props = context.scene.lol_weight
        done = weights.clean(meshes,
                             merge=props.clean_merge,
                             normals=props.clean_normals,
                             degenerate=props.clean_degenerate,
                             apply_transforms=props.clean_transforms,
                             context=context)
        armature, bones, meshes = _gather(context)
        problems = weights.diagnose(armature, meshes, bones)
        changed = sum(len(v) for v in done.values())
        _store(context, problems, "Cleaned %d mesh%s" % (len(done), "" if len(done) == 1 else "es"))
        if not changed:
            self.report({'INFO'}, "Nothing needed cleaning")
        else:
            first = next(iter(done.items()))
            self.report({'INFO'}, "%s: %s%s" % (first[0], ", ".join(first[1]),
                                                "" if len(done) == 1 else
                                                " (and %d more)" % (len(done) - 1)))
        return {'FINISHED'}


_active_binds = []


@bpy.app.handlers.persistent
def _cancel_weight_jobs(_unused):
    for operator in list(_active_binds):
        operator._cleanup(bpy.context)


class _BindWorkflow:
    bl_idname = "lolblender.weight_bind"
    bl_label = "Bind"
    bl_description = "Bind the selected meshes using the selected ARP engine"
    bl_options = {'REGISTER', 'UNDO'}
    _operation = None
    _timer = None
    rebind = False

    @classmethod
    def poll(cls, context):
        return not context.scene.lol_weight.running

    def execute(self, context):
        from ..core import arp_bind
        props = context.scene.lol_weight
        armature, bones, meshes = _gather(context)
        if armature is None and not self.rebind:
            self.report({'ERROR'}, "Select one armature and the meshes to bind")
            return {'CANCELLED'}
        previous = context.object.mode if context.object else 'OBJECT'
        active = context.view_layer.objects.active
        if active and previous != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        if not self.rebind and not props.selected_bones_only:
            bones = [b.name for b in armature.data.bones if b.use_deform]
        try:
            # bind where the skeleton is seen, not at its old rest (see core/rest_pose.py)
            from ..core import rest_pose
            from . import physics
            if self.rebind:
                from ..core.rebind import rig_for
                rigs = []
                for obj in weights.bound_meshes(meshes) or meshes:
                    try:
                        rigs.append(rig_for(obj))
                    except ValueError:
                        pass
            else:
                rigs = [armature]
            for rig in dict.fromkeys(rigs):
                if rest_pose.is_posed(rig):
                    physics.forget(rig)
                    rest_pose.settle_for_bind(context, rig, props.bind_current_pose)
            if self.rebind:
                from ..core.rebind import Rebind
                # a mixed selection rebinds only the bound meshes
                self._operation = Rebind(weights.bound_meshes(meshes) or meshes, props)
            else:
                self._operation = arp_bind.Bind(armature, meshes, bones, arp_bind.options(props))
            self._operation.original_mode = previous
            self._operation.original_active = active
            props.running = True
            props.status = "Rebinding existing influences..." if self.rebind else "Binding..."
            props.report.clear()
            _active_binds.append(self)
            if self._operation.session and not bpy.app.background:
                self._timer = context.window_manager.event_timer_add(.1, window=context.window)
                context.window_manager.modal_handler_add(self)
                return {'RUNNING_MODAL'}
            self._operation.compute()
            return self._finish(context)
        except Exception as error:
            self._error(context, error)
            self._cleanup(context)
            if active and previous != 'OBJECT' and active.mode == 'OBJECT':
                context.view_layer.objects.active = active
                bpy.ops.object.mode_set(mode=previous)
            return {'CANCELLED'}

    def modal(self, context, event):
        if event.type == 'ESC':
            self._cleanup(context)
            context.scene.lol_weight.status = "Bind cancelled; existing weights preserved"
            return {'CANCELLED'}
        if event.type == 'TIMER':
            try:
                if self._operation.session.poll() is not None:
                    self._operation.session.apply()
                    if self.rebind and self._operation.advance():
                        return {'RUNNING_MODAL'}
                    return self._finish(context)
            except Exception as error:
                self._error(context, error)
                self._cleanup(context)
                return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def _finish(self, context):
        try:
            result = self._operation.finish()
            props = context.scene.lol_weight
            summary = result.summary()
            if self.rebind:
                summary = summary.replace('Bound ', 'Rebound ', 1)
            notes = getattr(result, 'notes', [])
            problems = [weights.Problem(target, what, fix, blocking)
                        for target, what, fix, blocking in notes]
            failed = [p for p in problems if p.blocking]
            # ARP 5512: a Bone Heat failure turns Scale Fix on and asks to bind again.
            if (props.engine == 'HEAT_MAP' and not props.scale_fix
                    and any('Bone Heat' in p.summary for p in failed)):
                props.scale_fix = True
            if failed:
                headline = "%d mesh%s not fully bound" % (len(failed), "" if len(failed) == 1 else "es")
                _store(context, problems, headline)
                self.report({'WARNING'}, "%s. %s" % (headline, failed[0].full()))
            else:
                _store(context, problems, summary)
                self.report({'INFO'}, summary)
            return {'FINISHED'}
        finally:
            self._cleanup(context)

    def _error(self, context, error):
        props = context.scene.lol_weight
        if 'Bone Heat Weighting' in str(error) and not props.scale_fix:
            props.scale_fix = True
        text = str(error)
        target, _, rest = text.partition(': ')
        summary, _, fix = rest.partition('. ')
        _store(context, [weights.Problem(target if rest else "Bind", summary or text,
                                         fix or "See the message above", True)],
               "Failed: " + text)
        self.report({'ERROR'}, "Failed: " + text)

    def _cleanup(self, context):
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        if self._operation:
            self._operation.close()
            self._operation = None
        if self in _active_binds:
            _active_binds.remove(self)
        context.scene.lol_weight.running = False


class LOLWEIGHT_OT_bind(_BindWorkflow, Operator):
    bl_idname = "lolblender.weight_bind"
    bl_label = "Bind"
    bl_description = "Bind the selected meshes using the selected ARP engine"
    bl_options = {'REGISTER', 'UNDO'}


class LOLWEIGHT_OT_rebind(_BindWorkflow, Operator):
    bl_idname = "lolblender.weight_rebind"
    bl_label = "Rebind"
    bl_description = ("Recalculate every vertex of each selected mesh using its own armature and "
                      "bones with existing positive weights. No bone or vertex selection required")
    bl_options = {'REGISTER', 'UNDO'}
    rebind = True


class LOLWEIGHT_OT_unbind(Operator):
    bl_idname = "lolblender.weight_unbind"
    bl_label = "Unbind"
    bl_description = ("Remove the armature modifier from the selected meshes. The vertex groups "
                      "are kept unless you ask for them to go")
    bl_options = {'REGISTER', 'UNDO'}

    remove_groups: BoolProperty(
        name="Remove unused, unlocked groups",
        description="ARP cleanup: keep locked groups and groups used by modifiers or attributes",
        default=False)

    @classmethod
    def poll(cls, context):
        return not context.scene.lol_weight.running

    def execute(self, context):
        meshes = weights.selected_meshes(context)
        if not meshes:
            self.report({'ERROR'}, "Select the meshes to unbind")
            return {'CANCELLED'}
        try:
            touched = weights.unbind(None, meshes, self.remove_groups)
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        context.scene.lol_weight.status = "Unbound %d mesh%s" % (touched,
                                                                 "" if touched == 1 else "es")
        self.report({'INFO'}, "Unbound %d mesh%s" % (touched, "" if touched == 1 else "es"))
        return {'FINISHED'}


class LOLWEIGHT_OT_select_deform(Operator):
    bl_idname = "lolblender.weight_select_deform"
    bl_label = "Select Deforming Bones"
    bl_description = ("Select every bone on the rig that the mesh files actually use, and "
                      "deselect the rest. That is the set a League model is weighted to")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        armature = weights.armature_in(context)
        if armature is None:
            self.report({'ERROR'}, "No armature is active or selected")
            return {'CANCELLED'}
        from ..core import registry

        # .skl joints for a League rig, else the use_deform flag
        wanted = set()
        try:
            if registry.is_lol_armature(armature):
                wanted = set(registry.exportable_names(armature))
        except Exception:                               # noqa: BLE001 - not a League rig
            wanted = set()
        if not wanted:
            wanted = {bone.name for bone in armature.data.bones if bone.use_deform}

        # minus buffbones, locators and roll helpers, which steal vertices (see is_deforming)
        wanted = {name for name in wanted if weights.is_deforming(name)}
        if not wanted:
            self.report({'WARNING'}, "No deforming bones found - select them by hand")
            return {'CANCELLED'}

        count = 0
        for bone in armature.data.bones:
            hit = bone.name in wanted
            bone.select = hit
            bone.select_head = hit
            bone.select_tail = hit
            count += 1 if hit else 0
        self.report({'INFO'}, "Selected %d deforming bones, skipped %d"
                    % (count, len(armature.data.bones) - count))
        return {'FINISHED'}


_classes = (
    LOLWEIGHT_OT_check,
    LOLWEIGHT_OT_clean,
    LOLWEIGHT_OT_bind,
    LOLWEIGHT_OT_rebind,
    LOLWEIGHT_OT_unbind,
    LOLWEIGHT_OT_select_deform,
)


def register():
    from ..compat_handlers import install
    install(bpy.app.handlers.load_pre, _cancel_weight_jobs)
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    _cancel_weight_jobs(None)
    from ..compat_handlers import remove
    remove(bpy.app.handlers.load_pre, _cancel_weight_jobs)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
