"""Blender side of the VHDS (voxel heat diffuse) bind. The solver runs in Rust.

An explicit `vhd.exe` path runs the vendor binary instead, for comparison only.
"""
import os
import json
import platform
import tempfile
import time
import bpy
from .. import native
from .weight_write import replace_weight


def local_path():
    """`vhds_path` from the optional `local_settings.json` next to the addon, or ''."""
    filename = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'local_settings.json')
    try:
        with open(filename, encoding='utf-8') as stream:
            value = json.load(stream).get('vhds_path', '')
        return value if isinstance(value, str) else ''
    except (OSError, ValueError, AttributeError):
        return ''


def executable(configured=''):
    """Path to the vendor `vhd.exe`, found without importing its addon."""
    candidates = []
    if configured:
        candidates.append(bpy.path.abspath(configured))
    elif local_path():
        candidates.append(local_path())
    import addon_utils
    for module in addon_utils.modules(refresh=False):
        if getattr(module, 'bl_info', {}).get('name') == 'Voxel Heat Diffuse Skinning':
            candidates.append(os.path.dirname(module.__file__))
    for path in candidates:
        if os.path.isfile(path):
            return os.path.abspath(path)
        system = platform.system()
        arch = 'arm64' if platform.machine().lower() in ('arm64', 'aarch64') else 'x64'
        suffix = os.path.join('bin', system, 'vhd') if system == 'Darwin' else os.path.join('bin', system, arch, 'vhd.exe' if system == 'Windows' else 'vhd')
        for folder in (path, os.path.join(path, 'voxel_skinning')):
            candidate = os.path.join(folder, suffix)
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)
    raise ValueError('VHDS executable not found. Set the purchased VHDS folder or vhd.exe in LoL Blender preferences')


class Session:
    """One bind job and its temp files. Callers poll it from a Blender timer."""
    def __init__(self, armature, meshes, bones, settings, path):
        self.meshes = list(meshes)  # sorted by original name by the caller
        self.directory = tempfile.TemporaryDirectory(prefix='daza-vhds-')
        self.job = None
        self.protected = []
        try:
            payload = []
            for obj in self.meshes:
                co =[0.0] * (len(obj.data.vertices) * 3)
                obj.data.vertices.foreach_get('co', co)
                coords = native.rs().mathutils_transform(co, [x for row in obj.matrix_world for x in row])
                faces = [list(p.vertices) for p in obj.data.polygons]
                payload.append((coords, faces))
                self.protected.append({v.index: [(obj.vertex_groups[g.group].name, g.weight) for g in v.groups]
                                       for v in obj.data.vertices if settings.selected_vertices_only and not v.select})
            heads, tails, names = [], [], []
            wanted = set(bones)
            for bone in armature.data.bones:
                if bone.use_deform and bone.name in wanted:
                    names.append(bone.name)
                    heads.extend(armature.matrix_world @ bone.head_local)
                    tails.extend(armature.matrix_world @ bone.tail_local)
            self.names = names
            if path:
                # vendor binary, for comparison; the UI never sets this
                mesh_text, bone_text = native.rs().vhds_encode(payload, names, heads, tails)
                self.job = native.rs().VhdsJob(path, self.directory.name, mesh_text, bone_text,
                    [len(o.data.vertices) for o in self.meshes], settings.vhds_resolution,
                    settings.vhds_loops, settings.vhds_samples, settings.vhds_influence,
                    settings.vhds_falloff, settings.vhds_solidify, settings.vhds_half_cpu)
            else:
                self.job = native.rs().VhdsNativeJob(payload, names, heads, tails,
                    settings.vhds_resolution, settings.vhds_loops, settings.vhds_samples,
                    settings.vhds_influence, settings.vhds_falloff, settings.vhds_solidify,
                    settings.vhds_half_cpu)
        except Exception:
            self.close()
            raise

    def poll(self):
        return self.job.poll()

    def apply(self):
        names, entries = self.job.result()
        if set(names) != set(self.names):
            raise ValueError('VHDS returned unexpected bones. Check the executable and bind again')
        # recreate the groups in result order, locked ones included, like the vendor addon
        for obj in self.meshes:
            for name in names:
                group = obj.vertex_groups.get(name)
                if group:
                    obj.vertex_groups.remove(group)
                obj.vertex_groups.new(name=name)
        for mesh, vertex, bone, weight in entries:
            if vertex not in self.protected[mesh]:
                replace_weight(self.meshes[mesh].vertex_groups[names[bone]], [vertex], weight)
        for obj, protected in zip(self.meshes, self.protected):
            for vertex, groups in protected.items():
                for name, weight in groups:
                    replace_weight(obj.vertex_groups[name], [vertex], weight)

    def wait(self):
        """Block until done. For background runs, which have no timer."""
        while self.poll() is None:
            time.sleep(0.02)
        self.apply()

    def close(self):
        if self.job is not None:
            self.job.cancel()
            self.job = None
        self.directory.cleanup()
