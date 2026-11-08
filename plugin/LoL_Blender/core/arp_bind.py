"""The ARP 3.75.43 bind sequence (Heat Maps, Voxelized, VHDS) for an existing skeleton.

Blender objects and operators are handled here; bone heat, VHDS and weight processing run in Rust.
See docs/architecture.md.
"""
from contextlib import contextmanager
from types import SimpleNamespace
import time
import bpy
from mathutils import Vector
from .. import native
from . import vhds
from .weight_write import replace_weight, raise_weights, DeformEdit

DEFAULTS = dict(engine='VHDS', split_parts=True, selected_bones_only=True,
    selected_vertices_only=True, apply_shape_keys=False, preserve_volume=False,
    scale_fix=False, optimize_highres=False, highres_threshold=70000,
    voxel_type='1', voxel_resolution=7, vhds_resolution=128, vhds_loops=5,
    vhds_samples=64, vhds_influence=8, vhds_falloff=.2, vhds_solidify=False,
    vhds_half_cpu=False, parent_objects=True, remove_armature_modifiers=True,
    remove_vertex_groups=True)


def options(settings=None, **overrides):
    values = {k: getattr(settings, k, v) for k, v in DEFAULTS.items()}
    unknown = overrides.keys() - values.keys()
    if unknown:
        raise ValueError('Unknown binding settings: ' + ', '.join(sorted(unknown)))
    values.update(overrides)
    return SimpleNamespace(**values)


def select(objects, active=None):
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='DESELECT')
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = active or (objects[-1] if objects else None)


def replace_geometry(obj, coords, faces, edges=()):
    mesh = bpy.data.meshes.new(obj.data.name + ' Remesh')
    try:
        mesh.from_pydata([coords[i:i+3] for i in range(0, len(coords), 3)], edges, faces)
        mesh.update()
    except Exception:
        bpy.data.meshes.remove(mesh)
        raise
    previous = obj.data
    obj.data = mesh
    if previous.users == 0:
        bpy.data.meshes.remove(previous)
    bpy.context.view_layer.update()


def used_groups(obj):
    used = set()
    for mod in obj.modifiers:
        name = getattr(mod, 'vertex_group', '')
        if name:
            used.add(name)
        if mod.type == 'PARTICLE_SYSTEM':
            for system in obj.particle_systems:
                for attr in system.bl_rna.properties:
                    if attr.identifier.startswith('vertex_group'):
                        used.add(getattr(system, attr.identifier))
    for group in bpy.data.node_groups:
        for node in group.nodes:
            if node.type == 'INPUT_ATTRIBUTE':
                for socket in node.inputs:
                    value = getattr(socket, 'default_value', None)
                    if isinstance(value, str):
                        used.add(value)
    return used


def unbind_mesh(obj, settings, full=False):
    for mod in list(obj.modifiers):
        if mod.type == 'ARMATURE' and (full or settings.remove_armature_modifiers):
            obj.modifiers.remove(mod)
    if full or (settings.remove_vertex_groups and not settings.selected_bones_only and not settings.selected_vertices_only):
        used = used_groups(obj)
        for group in list(obj.vertex_groups):
            if not group.lock_weight and group.name not in used:
                obj.vertex_groups.remove(group)
    world = obj.matrix_world.copy()
    obj.parent = None
    obj.matrix_world = world


def armature_modifier(obj, armature, settings):
    select([obj])
    mod = obj.modifiers.get('rig')
    if mod is not None and mod.type != 'ARMATURE':
        raise ValueError(f'{obj.name}: a non-armature modifier is named rig. Rename it and bind again')
    if mod is None:
        mod = obj.modifiers.new('rig', 'ARMATURE')
    mod.object = armature
    mod.use_deform_preserve_volume = settings.preserve_volume
    mod.show_in_editmode = True
    mod.show_on_cage = True
    for name in [mod.name] + [m.name for m in obj.modifiers if m.type == 'MIRROR']:
        for _ in range(50):
            if obj.modifiers[0].name == name:
                break
            try:
                bpy.ops.object.modifier_move_up(modifier=name)
            except RuntimeError:
                break


@contextmanager
def muted(objects, all_modifiers=False):
    changed = []
    for obj in objects:
        for mod in obj.modifiers:
            if mod.show_viewport and (all_modifiers or mod.type == 'MASK'):
                changed.append(mod)
                mod.show_viewport = False
    try:
        yield
    finally:
        for mod in changed:
            mod.show_viewport = True


def transfer(source, target, mapping, hide_all=False):
    select([target, source], source)
    with muted([source, target], hide_all):
        names = [g.name for g in source.vertex_groups]
        if not names:
            return
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = source.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
        try:
            mesh.calc_loop_triangles()
            transform = source.matrix_world.inverted_safe() @ target.matrix_world
            co = [0.0] * (len(mesh.vertices) * 3)
            mesh.vertices.foreach_get('co', co)
            tri_flat = [0] * (len(mesh.loop_triangles) * 3)
            mesh.loop_triangles.foreach_get('vertices', tri_flat)
            tri_owner = [0] * len(mesh.loop_triangles)
            mesh.loop_triangles.foreach_get('polygon_index', tri_owner)
            moved = world_points(target.data, transform)
            links = native.rs().transfer_map(
                [co[i:i + 3] for i in range(0, len(co), 3)],
                polygon_lists(mesh),
                [tri_flat[i:i + 3] for i in range(0, len(tri_flat), 3)],
                tri_owner,
                [moved[i:i + 3] for i in range(0, len(moved), 3)], mapping)
            source_weights = [[(g.group, g.weight) for g in v.groups] for v in mesh.vertices]
            source_indices = {name: i for i, name in enumerate(names)}
            target_indices = {g.index: source_indices[g.name] for g in target.vertex_groups
                              if g.name in source_indices}
            old = [[(target_indices[g.group], g.weight) for g in v.groups
                    if g.group in target_indices] for v in target.data.vertices]
            weights = native.rs().transfer_values(source_weights, links, old)
        finally:
            evaluated.to_mesh_clear()
        groups = [target.vertex_groups.get(name) or target.vertex_groups.new(name=name) for name in names]
        index = [g.index for g in groups]
        # Targets are always the bind's temporary copies.
        edit = DeformEdit(target)
        for vertex, entries in enumerate(weights):
            for group, weight in entries:
                edit.set(vertex, index[group], weight)
        edit.close()


def clean_weights(obj):
    select([obj])
    records = [[(g.group, g.weight) for g in v.groups] for v in obj.data.vertices]
    for group, vertices in native.rs().clean_weight_members(records, .01):
        obj.vertex_groups[group].remove(vertices)


def world_points(mesh, matrix):
    """`[x for v in mesh.vertices for x in matrix @ v.co]`, bit for bit, read in bulk."""
    co = [0.0] * (len(mesh.vertices) * 3)
    mesh.vertices.foreach_get('co', co)
    return native.rs().mathutils_transform(co, [x for row in matrix for x in row])


def polygon_lists(mesh):
    """Every polygon's vertex indices, read in bulk (same values as `list(p.vertices)`)."""
    loops = [0] * len(mesh.loops)
    mesh.loops.foreach_get('vertex_index', loops)
    starts = [0] * len(mesh.polygons)
    totals = [0] * len(mesh.polygons)
    mesh.polygons.foreach_get('loop_start', starts)
    mesh.polygons.foreach_get('loop_total', totals)
    return [loops[s:s + t] for s, t in zip(starts, totals)]


def loose_parts(obj):
    """`mesh.separate(type='LOOSE')` order, computed in Rust: [(vertices, faces)], part 0 first."""
    mesh = obj.data
    edges = [0] * (len(mesh.edges) * 2)
    mesh.edges.foreach_get('vertices', edges)
    return native.rs().separate_loose(len(mesh.vertices), edges, polygon_lists(mesh))


def join_parts(obj, parts):
    """Give `obj` the vertex order ARP's copy has after separate LOOSE then join.

    The weight transfers that follow break ties between coincident vertices by this order.
    """
    if len(parts) <= 1:
        return
    mesh = obj.data
    world = [x for row in obj.matrix_world for x in row]
    owner = [0] * len(mesh.vertices)
    local = [0] * len(mesh.vertices)
    for index, (verts, _faces) in enumerate(parts):
        for i, v in enumerate(verts):
            owner[v] = index
            local[v] = i
    part_edges = [[] for _ in parts]
    for e in mesh.edges:
        a, b = e.vertices
        part_edges[owner[a]].append([local[a], local[b]])
    inputs = []
    for (verts, faces), edges in zip(parts, part_edges):
        inputs.append(([x for v in verts for x in mesh.vertices[v].co], edges,
                       [[local[v] for v in mesh.polygons[f].vertices] for f in faces], world))
    order = [v for verts, _ in parts for v in verts]
    read = DeformEdit(obj)
    group_names = [g.name for g in obj.vertex_groups]
    records = [[(group_names[g], w) for g, w in read.entries(v)] for v in order]
    read.bm.free()
    names = [(g.name, g.lock_weight) for g in obj.vertex_groups]
    coords, new_edges, new_faces = native.rs().join_geometry(inputs)
    replace_geometry(obj, coords, new_faces, new_edges)
    # Group names live on the mesh since Blender 3.0: recreate them, same order and locks.
    if not obj.vertex_groups:
        for name, locked in names:
            obj.vertex_groups.new(name=name).lock_weight = locked
    position = {g.name: g.index for g in obj.vertex_groups}
    edit = DeformEdit(obj)
    for index, entries in enumerate(records):
        for name, weight in entries:
            edit.set(index, position[name], weight)
    edit.close()


def heat(obj, armature, split_parts=False, loose_out=None):
    """Bone heat on `obj`, with the same inputs as Blender's armature_skinning.cc.

    With `split_parts`, each loose part is solved separately, like ARP. Returns the vertices of
    the parts that failed; those keep their old weights.
    """
    bones = [b for b in armature.data.bones if b.use_deform]
    obj.data.calc_loop_triangles()
    if any(m.type == 'SUBSURF' for m in obj.modifiers):
        crease = obj.data.attributes.get('crease_edge')
        local = native.rs().subsurf_limit(
            [x for v in obj.data.vertices for x in v.co],
            [tuple(e.vertices) for e in obj.data.edges],
            [list(f.vertices) for f in obj.data.polygons],
            [c.value for c in crease.data] if crease else [0.] * len(obj.data.edges))
        coords = native.rs().mathutils_transform(local, [x for row in obj.matrix_world for x in row])
    else:
        coords = world_points(obj.data, obj.matrix_world)
    tris = [0] * (len(obj.data.loop_triangles) * 3)
    obj.data.loop_triangles.foreach_get('vertices', tris)
    heads, tails, group_ids = [], [], []
    evaluated = armature.evaluated_get(bpy.context.evaluated_depsgraph_get())
    for index, bone in enumerate(bones):
        if bone.bbone_segments == 1:
            heads.extend(armature.matrix_world @ bone.head_local)
            tails.extend(armature.matrix_world @ bone.tail_local)
            group_ids.append(index)
        else:
            pose = evaluated.pose.bones[bone.name]
            for segment in reversed(range(bone.bbone_segments)):
                head = bone.matrix_local @ pose.bbone_segment_matrix(segment, rest=True).translation
                tail = (bone.tail_local if segment+1 == bone.bbone_segments else
                        bone.matrix_local @ pose.bbone_segment_matrix(segment+1, rest=True).translation)
                heads.extend(armature.matrix_world @ head)
                tails.extend(armature.matrix_world @ tail)
                group_ids.append(index)
    count = len(obj.data.vertices)
    if split_parts:
        per_polygon = [[] for _ in obj.data.polygons]
        owners = [0] * len(obj.data.loop_triangles)
        obj.data.loop_triangles.foreach_get('polygon_index', owners)
        for index, polygon in enumerate(owners):
            per_polygon[polygon].append(index)
        loose = loose_parts(obj)
        parts = [(verts, [k for f in faces for k in per_polygon[f]]) for verts, faces in loose]
        if loose_out is not None:
            loose_out.extend(loose)
    else:
        parts = [(list(range(count)), list(range(len(obj.data.loop_triangles))))]
    weights, failed = native.rs().bone_heat_parts(coords, tris, heads, tails, group_ids, parts)
    untouched = {v for index in failed for v in parts[index][0]}
    solved = [v for v in range(count) if v not in untouched]
    groups = []
    for bone in bones:
        group = obj.vertex_groups.get(bone.name) or obj.vertex_groups.new(name=bone.name)
        groups.append(group)
    for group in groups:
        if not group.lock_weight and solved:
            group.remove(solved)
    # bulk edit: obj is a temporary copy
    edit = DeformEdit(obj)
    for bone, (members, values) in enumerate(native.rs().weight_lists(weights)):
        if not groups[bone].lock_weight:
            gi = groups[bone].index
            for v, weight in zip(members, values):
                edit.set(v, gi, weight)
    edit.close()
    select([obj, armature], armature)
    bpy.ops.object.parent_set(type='ARMATURE')
    # ARP renames the new modifier to `rig`
    created = [m for m in obj.modifiers if m.type == 'ARMATURE' and m.object == armature]
    if created and not obj.modifiers.get('rig'):
        created[-1].name = 'rig'
        created[-1].show_in_editmode = True
        created[-1].show_on_cage = True
    return sorted(untouched)


class Result:
    def __init__(self, meshes, bones, vertices, seconds, notes=()):
        self.meshes, self.bones, self.vertices, self.seconds = meshes, bones, vertices, seconds
        #: (mesh name, what went wrong, how to fix it, blocking) for each mesh not fully bound.
        self.notes = list(notes)

    def summary(self):
        return f'Bound {self.meshes} meshes, {self.vertices} vertices to {self.bones} bones in {self.seconds:.2f}s'


class Bind:
    """Transactional bind. Solver failure never destroys the existing weights."""
    def __init__(self, armature, meshes, bones, settings, vhds_path='', heat_solver=None):
        self.armature = armature
        self.meshes = list(meshes)
        self.mesh_world = {obj: obj.matrix_world.copy() for obj in self.meshes}
        self.bones = list(bones)
        self.settings = settings
        self.heat_solver = heat_solver or heat
        self.native_heat = heat_solver is None
        self.started = time.perf_counter()
        self.temp = []
        self.session = None
        self.closed = False
        self.original_selection = list(bpy.context.selected_objects)
        self.original_active = bpy.context.view_layer.objects.active
        self.original_mode = self.original_active.mode if self.original_active else 'OBJECT'
        self.rig_transform = armature.matrix_world.copy()
        self.rig_location = armature.location.copy()
        self.rig_scale = armature.scale.copy()
        self.mirror = armature.data.use_mirror_x
        self.deform = {b.name: b.use_deform for b in armature.data.bones}
        self.pose = armature.data.pose_position
        render = bpy.context.scene.render
        self.simplify = (render.use_simplify, render.simplify_subdivision)
        self.undo = bpy.context.preferences.edit.use_global_undo
        self.targets = []
        self.protected = {}
        self.heat_failed = {}
        self.voxel_failed = False
        self.whole_mesh = set()
        self.vertex_selection = {}
        try:
            self.preflight()
            path = vhds.executable(vhds_path) if settings.engine == 'VHDS' and vhds_path else ''
            select([armature])
            armature.data.use_mirror_x = False
            armature.data.pose_position = 'REST'
            for b in armature.data.bones:
                if b.name == 'Picker' or (settings.selected_bones_only and b.name not in self.bones):
                    b.use_deform = False
            render.use_simplify, render.simplify_subdivision = True, 0
            bpy.context.preferences.edit.use_global_undo = False
            for obj in self.meshes:
                work = self.duplicate(obj)
                work.name = obj.name + '_daza_bind'
                unbind_mesh(work, settings)
                work.data.use_mirror_topology = False
                self.vertex_selection[work.name] = [v.select for v in obj.data.vertices]
                if obj.name in self.whole_mesh:
                    # nothing selected: bind the whole mesh
                    for v in work.data.vertices:
                        v.select = True
                self.targets.append((obj, work))
                self.protected[work.name] = {v.index: [(work.vertex_groups[g.group].name, g.weight) for g in v.groups]
                    for v in work.data.vertices if settings.selected_vertices_only and not v.select}
            if settings.engine != 'VHDS' and settings.scale_fix:
                for obj in [armature] + [w for _, w in self.targets]:
                    obj.location *= 20
                    obj.scale *= 20
            bpy.context.view_layer.update()
            if settings.engine == 'VHDS':
                # mesh order by original name, not the duplicates' names
                ordered = [work for _, work in sorted(self.targets, key=lambda pair: pair[0].name)]
                self.session = vhds.Session(armature, ordered, self.bones, settings, path)
        except Exception:
            self.close()
            raise

    def preflight(self):
        if self.settings.engine not in ('HEAT_MAP', 'PSEUDO_VOXELS', 'VHDS'):
            raise ValueError('Select a supported binding engine')
        if not self.meshes:
            raise ValueError('Select at least one mesh to bind')
        active = [b for b in self.armature.data.bones if b.use_deform and b.name != 'Picker'
                  and (not self.settings.selected_bones_only or b.name in self.bones)]
        if not active:
            if self.settings.selected_bones_only and not self.bones:
                raise ValueError(f'{self.armature.name}: no bones are selected. Select the bones to bind '
                                 '(Outliner, Pose Mode or the eye button next to Selected Bones Only), '
                                 'or turn off Selected Bones Only')
            raise ValueError(f'{self.armature.name}: none of the selected bones deforms. Enable Deform '
                             'on them (Bone Properties > Deform) or select other bones')
        self.bones = [b.name for b in active]
        for obj in self.meshes:
            conflict = obj.modifiers.get('rig')
            if conflict and conflict.type != 'ARMATURE':
                raise ValueError(f'{obj.name}: rename the non-armature modifier named rig before binding')
            if not obj.visible_get() or not obj.data.polygons:
                raise ValueError(f'{obj.name}: mesh is hidden or has no faces. Show the mesh and add faces before binding')
            if self.settings.selected_vertices_only and not any(v.select for v in obj.data.vertices):
                self.whole_mesh.add(obj.name)
            if obj.library or obj.data.library:
                raise ValueError(f'{obj.name}: linked data is read-only. Make the mesh local before binding')
            if obj.data.users > 1:
                raise ValueError(f'{obj.name}: mesh data is shared. Make the mesh single-user before binding')

    def duplicate(self, obj):
        select([obj])
        bpy.ops.object.duplicate()
        result = bpy.context.object
        self.temp.append(result)
        return result

    def heat_maps(self, obj):
        settings = self.settings
        work = obj
        applied = None
        if settings.apply_shape_keys:
            work = self.duplicate(obj)
            work.vertex_groups.clear()
            work.modifiers.clear()
            bpy.ops.object.convert(target='MESH')
            applied = work
        reduced = None
        failed = []
        particle = any(m.type == 'PARTICLE_SYSTEM' for m in work.modifiers)
        highres = settings.optimize_highres and len(work.data.polygons) > settings.highres_threshold
        if particle or highres:
            work = self.duplicate(work)
            work.vertex_groups.clear()
            if highres:
                bpy.ops.object.convert(target='MESH')
                coords, edges, faces = native.rs().decimate_mesh(
                    [x for vertex in work.data.vertices for x in vertex.co],
                    [list(edge.vertices) for edge in work.data.edges],
                    [list(face.vertices) for face in work.data.polygons], .2)
                replace_geometry(work, coords, faces, edges)
            reduced = work
        if settings.split_parts:
            source = work if reduced else self.duplicate(work)
            if self.native_heat:
                loose = []
                failed = heat(source, self.armature, split_parts=True, loose_out=loose)
                join_parts(source, loose)
                armature_modifier(source, self.armature, settings)
            else:
                # an injected solver gets separate objects
                select([source])
                bpy.ops.mesh.separate(type='LOOSE')
                parts = [o for o in bpy.context.selected_objects if o.type == 'MESH']
                for part in parts:
                    if part not in self.temp:
                        self.temp.append(part)
                for part in parts:
                    self.heat_solver(part, self.armature)
                    armature_modifier(part, self.armature, settings)
                select(parts, source)
                if len(parts) > 1:
                    bpy.ops.object.join()
            if not reduced:
                if source.data.shape_keys:
                    source.active_shape_key_index = 0
                    source.show_only_shape_key = True
                transfer(source, work, 'NEAREST')
        else:
            failed = self.heat_solver(work, self.armature)
        if reduced:
            target = applied or obj
            transfer(reduced, target, 'POLYINTERP_NEAREST')
            clean_weights(target)
        if applied:
            transfer(applied, obj, 'TOPOLOGY')
            clean_weights(obj)
        return len(failed or ())

    def voxelized(self):
        settings = self.settings
        copies = [self.duplicate(work) for _, work in self.targets]
        if settings.apply_shape_keys:
            for work in copies:
                work.vertex_groups.clear()
                work.modifiers.clear()
        select(copies)
        bpy.ops.object.convert(target='MESH')
        work = bpy.context.object
        if len(copies)>1:
            ordered = [work] + [obj for obj in bpy.context.selected_editable_objects if obj != work]
            coords, edges, faces = native.rs().join_geometry([
                ([x for vertex in obj.data.vertices for x in vertex.co],
                 [list(edge.vertices) for edge in obj.data.edges],
                 [list(face.vertices) for face in obj.data.polygons],
                 [x for row in obj.matrix_world for x in row]) for obj in ordered])
            replace_geometry(work, coords, faces, edges)
            for obj in ordered[1:]:
                data = obj.data
                bpy.data.objects.remove(obj, do_unlink=True)
                if data.users == 0:
                    bpy.data.meshes.remove(data)
            select([work])
        work.vertex_groups.clear()
        dimensions = list(work.dimensions)
        largest = max(dimensions)
        scale = max(abs(s) for s in work.scale)
        if scale == 0 or min(dimensions) == 0:
            raise ValueError('Voxelized mesh has a zero dimension. Give the mesh thickness or use Heat Maps')
        if settings.voxel_type == '1':
            work.data.calc_loop_triangles()
            vertices = [x for vertex in work.data.vertices for x in vertex.co]
            triangles = [i for triangle in work.data.loop_triangles for i in triangle.vertices]
            remesh_scale, remove_disconnected = .95, True
            def rebuild():
                coords, faces = native.rs().remesh_sharp(vertices, triangles,
                    settings.voxel_resolution, remesh_scale, remove_disconnected, .82, 1.)
                replace_geometry(work, coords, faces)
            rebuild()
            valid, attempt = [False]*3, 0
            while valid != [True]*3 and attempt<3:
                for axis, dim in enumerate(dimensions):
                    if abs(work.dimensions[axis]/dim-1) > .04:
                        if attempt == 0:
                            remesh_scale = .9
                        elif attempt == 1:
                            remove_disconnected = False
                        rebuild()
                        attempt += 1
                        valid[axis] = False
                        break
                    valid[axis] = True
        else:
            resolution = {7:6, 9:10}.get(settings.voxel_resolution, settings.voxel_resolution)
            voxel_size = ((largest/scale)*.003)/(resolution/8)
            work.data.calc_loop_triangles()
            coords, faces = native.rs().remesh_voxel(
                [x for vertex in work.data.vertices for x in vertex.co],
                [i for triangle in work.data.loop_triangles for i in triangle.vertices], voxel_size)
            replace_geometry(work, coords, faces)
        threshold = (largest/scale)/1000
        for _ in range(40):
            previous = len(work.data.polygons)
            if previous <= 70000:
                break
            coords, faces = native.rs().merge_mesh(
                [x for vertex in work.data.vertices for x in vertex.co],
                [list(face.vertices) for face in work.data.polygons], threshold)
            replace_geometry(work, coords, faces)
            count = len(work.data.polygons)
            removed = previous-count
            factor = 1.3
            if count<120000 and removed>30000: factor=1.15
            if count<100000 and removed>30000: factor=1.06
            if count>200000 and removed<500: factor=4
            threshold *= factor
        self.voxel_failed = bool(self.heat_solver(work, self.armature))
        for _, target in self.targets:
            transfer(work, target, 'POLYINTERP_NEAREST', hide_all=True)
            clean_weights(target)
            edges = [i for e in target.data.edges for i in e.vertices]
            groups = [[(g.group, g.weight) for g in v.groups] for v in target.data.vertices]
            for vertex, entries in native.rs().fill_unweighted(groups, edges):
                for group, weight in entries:
                    replace_weight(target.vertex_groups[group], [vertex], weight)

    def compute(self):
        if self.session:
            self.session.wait()
        elif self.settings.engine == 'PSEUDO_VOXELS':
            self.voxelized()
        else:
            for original, work in self.targets:
                try:
                    failed = self.heat_maps(work)
                except Exception as error:
                    raise ValueError(f'{original.name}: {error}. Enable Scale Fix and press Bind again; if it still fails, Clean Meshes or use Voxelized') from error
                if failed:
                    self.heat_failed[original.name] = failed

    def finish(self):
        backups = []
        for obj in self.meshes:
            mods = []
            for mod in obj.modifiers:
                if mod.type == 'ARMATURE':
                    attrs = {}
                    for prop in mod.bl_rna.properties:
                        if not prop.is_readonly and prop.type in {'BOOLEAN', 'ENUM', 'INT', 'FLOAT', 'STRING', 'POINTER'}:
                            value = getattr(mod, prop.identifier)
                            attrs[prop.identifier] = tuple(value) if getattr(prop, 'is_array', False) else value
                    mods.append((mod.name, attrs))
            backups.append((obj, obj.data.copy(), obj.parent, obj.parent_type, obj.parent_bone,
                            obj.matrix_parent_inverse.copy(), [m.name for m in obj.modifiers], mods))
        try:
            result = self._commit()
        except Exception:
            self.armature.location = self.rig_location
            self.armature.scale = self.rig_scale
            bpy.context.view_layer.update()
            for obj, mesh, parent, parent_type, parent_bone, inverse, order, modifiers in backups:
                changed = obj.data
                obj.data = mesh
                if changed.users == 0:
                    bpy.data.meshes.remove(changed)
                for mod in list(obj.modifiers):
                    if mod.type == 'ARMATURE':
                        obj.modifiers.remove(mod)
                for name, attrs in modifiers:
                    mod = obj.modifiers.new(name, 'ARMATURE')
                    for attr, value in attrs.items():
                        setattr(mod, attr, value)
                select([obj])
                for index, name in enumerate(order):
                    bpy.ops.object.modifier_move_to_index(modifier=name, index=index)
                obj.parent, obj.parent_type, obj.parent_bone = parent, parent_type, parent_bone
                obj.matrix_parent_inverse = inverse
                obj.matrix_world = self.mesh_world[obj]
            raise
        else:
            for _, mesh, *_rest in backups:
                bpy.data.meshes.remove(mesh)
            return result

    def _commit(self):
        settings = self.settings
        for original, work in self.targets:
            if len(original.data.vertices) != len(work.data.vertices):
                raise ValueError(f'{original.name}: vertex count changed during binding. Restore the original mesh and bind again')
        # everything ran on copies; only now are the originals changed
        for original, work in self.targets:
            if settings.engine != 'VHDS' and settings.selected_vertices_only:
                protected = self.protected[work.name]
                indices = list(protected)
                for group in work.vertex_groups:
                    if indices:
                        group.remove(indices)
                for vertex, entries in protected.items():
                    for name, weight in entries:
                        group = work.vertex_groups.get(name) or work.vertex_groups.new(name=name)
                        replace_weight(group, [vertex], weight)
                clean_weights(work)
            # copy the weights back, keeping the original mesh data
            original.vertex_groups.clear()
            for group in work.vertex_groups:
                new = original.vertex_groups.new(name=group.name)
                new.lock_weight = group.lock_weight
            targets = list(original.vertex_groups)
            over = []
            for vertex in work.data.vertices:
                vi = [vertex.index]
                for entry in vertex.groups:
                    weight = entry.weight
                    targets[entry.group].add(vi, weight, 'REPLACE')
                    if weight > 1.0:
                        over.append((vertex.index, entry.group, weight))
            raise_weights(original, over)
            if settings.remove_armature_modifiers or settings.engine == 'VHDS':
                for mod in list(original.modifiers):
                    if mod.type == 'ARMATURE':
                        original.modifiers.remove(mod)
            armature_modifier(original, self.armature, settings)
            world = original.matrix_world.copy()
            original.parent = None
            original.matrix_world = world
            if settings.engine != 'VHDS':
                # like ARP: deselect all, or restore the selection with Selected Vertices Only
                kept = self.vertex_selection[work.name] if settings.selected_vertices_only else None
                for vertex in original.data.vertices:
                    vertex.select = kept is not None and kept[vertex.index]
        # restore the rig scale (Scale Fix) before parenting
        self.armature.location = self.rig_location
        self.armature.scale = self.rig_scale
        bpy.context.view_layer.update()
        for obj in self.meshes:
            obj.matrix_world = self.mesh_world[obj]
        if settings.parent_objects:
            select(self.meshes+[self.armature], self.armature)
            bpy.ops.object.parent_set(type='OBJECT', keep_transform=True)
        return Result(len(self.meshes), len(self.bones), sum(len(m.data.vertices) for m in self.meshes),
                      time.perf_counter()-self.started, self.check())

    def check(self):
        """Every mesh that did not come out fully weighted, with the reason and the fix."""
        settings = self.settings
        bound = set(self.bones)
        notes = []
        for original, work in self.targets:
            name = original.name
            if name in self.whole_mesh:
                notes.append((name, 'no vertex was selected, so the whole mesh was bound',
                              'Nothing to do. To bind only part of it, select those vertices in '
                              'Edit Mode first', False))
            ids = {g.index for g in original.vertex_groups if g.name in bound}
            scope = range(len(original.data.vertices))
            if settings.selected_vertices_only and name not in self.whole_mesh:
                selection = self.vertex_selection[work.name]
                scope = [i for i in scope if selection[i]]
            vertices = original.data.vertices
            empty = sum(1 for i in scope
                        if not any(g.group in ids and g.weight > 0 for g in vertices[i].groups))
            total = len(scope)
            if settings.engine == 'HEAT_MAP' and name in self.heat_failed:
                fix = ('Scale Fix is now on: press Bind again. If it still fails, use Voxelized or '
                       'VHDS, or run Clean Meshes' if not settings.scale_fix else
                       'Use Voxelized or VHDS, or run Clean Meshes (doubled vertices and flipped '
                       'faces are the usual cause)')
                notes.append((name, 'Bone Heat found no solution for %d of %d vertices (as in '
                              'Blender and ARP)' % (self.heat_failed[name], len(vertices)), fix, True))
            elif empty == total and total:
                notes.append((name, 'nothing was bound: no vertex got a weight', {
                    'HEAT_MAP': 'Turn on Scale Fix and bind again, or use Voxelized or VHDS',
                    'PSEUDO_VOXELS': ('Bone Heat failed on the voxelized volume. ' if self.voxel_failed else
                                      'The voxelized volume missed this mesh. ') +
                                     'Raise Voxel Resolution, switch Type 1/Type 2, or use VHDS',
                    'VHDS': 'The selected bones are outside this mesh or too far from it. Select '
                            'bones that sit inside it, or raise Voxel Resolution',
                }[settings.engine], True))
            elif empty:
                notes.append((name, '%d of %d vertices got no weight' % (empty, total), {
                    'HEAT_MAP': 'Those parts see no bone. Turn on Scale Fix and bind again, or use '
                                'Voxelized or VHDS',
                    'PSEUDO_VOXELS': 'Raise Voxel Resolution or switch Type 1/Type 2 so thin parts '
                                     'are kept, or use VHDS',
                    'VHDS': 'Raise Voxel Resolution so thin parts get voxels, or select bones '
                            'closer to those parts',
                }[settings.engine], True))
        return notes

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.session:
            self.session.close()
        if bpy.context.object and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for obj in reversed(self.temp):
            try:
                data = obj.data
                bpy.data.objects.remove(obj, do_unlink=True)
                if data.users == 0:
                    bpy.data.meshes.remove(data)
            except ReferenceError:
                pass
        self.armature.location = self.rig_location
        self.armature.scale = self.rig_scale
        self.armature.data.use_mirror_x = self.mirror
        self.armature.data.pose_position = self.pose
        for name, value in self.deform.items():
            self.armature.data.bones[name].use_deform = value
        render = bpy.context.scene.render
        render.use_simplify, render.simplify_subdivision = self.simplify
        bpy.context.preferences.edit.use_global_undo = self.undo
        select(self.original_selection, self.original_active)
        if self.original_active and self.original_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode=self.original_mode)


def bind(armature, meshes, bones, settings=None, context=None, vhds_path='', **overrides):
    operation = Bind(armature, meshes, bones, options(settings, **overrides), vhds_path)
    try:
        operation.compute()
        return operation.finish()
    finally:
        operation.close()
