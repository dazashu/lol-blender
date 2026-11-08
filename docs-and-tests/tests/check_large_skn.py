import sys, os, time, json
from pathlib import Path
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, os.environ.get('LOL_ADDON_ROOT', str(ROOT.parents[1] / 'plugin')))
import bpy
import numpy as np
import LoL_Blender
from LoL_Blender import native
from LoL_Blender.core import skn_in, skn_out
from LoL_Blender.ops.export import _write_skin_pair

fixture = Path(os.environ.get('LOL_SKN_FIXTURE', ''))
if not os.environ.get('LOL_SKN_FIXTURE'):
    raise RuntimeError('Set LOL_SKN_FIXTURE to a local SKN with its matching SKL')
output = Path(os.environ.get('LOL_SKN_OUTPUT', str(ROOT / 'results'))) / fixture.stem
output.mkdir(parents=True, exist_ok=True)
LoL_Blender.register()
source = native.rs().Mesh.from_path(str(fixture))
print('NATIVE', native.rs().version(), 'VERTICES', source.vertex_count, 'FLAGS', source.flags, flush=True)
results = []
# Boundary and rejection tests exercise the exporter before its lossy uint16 cast.
def chunk(count, base=0, indices=None):
    return dict(name='boundary', positions=np.zeros((count, 3), dtype=np.float32),
                base=base, indices=np.array(indices if indices is not None else [0, 1, count-1]))
indices, relative = skn_out._pack_submesh_indices([chunk(65535)])
assert not relative and indices[-1] == 65534
indices, relative = skn_out._pack_submesh_indices([chunk(65535), chunk(3, 65535)])
assert relative and indices[-1] == 2
indices, relative = skn_out._pack_submesh_indices([chunk(3), chunk(3, 3)])
assert not relative and list(indices) == [0, 1, 2, 3, 4, 5]
assert skn_out._pack_submesh_indices([chunk(3)], True)[1]
for invalid in (chunk(65536), chunk(3, indices=[-1, 1, 2]),
                chunk(3, indices=[0, 1, 65536]), chunk(3, indices=[0, 1])):
    try:
        skn_out._pack_submesh_indices([invalid])
    except ValueError:
        pass
    else:
        raise AssertionError('Invalid submesh accepted')
print('BOUNDARY AND INVALID INDEX CHECKS PASSED', flush=True)
for separate in (True, False):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    start = time.perf_counter()
    arm, meshes, report, skeleton = skn_in.import_skn(str(fixture), separate_submeshes=separate)
    imported = time.perf_counter() - start
    # Force automatic format selection: do not rely on an imported flag-2 hint.
    for mesh in meshes:
        mesh[skn_in.SKN_FLAGS] = 0
    target = output / ('separated.skn' if separate else 'combined.skn')
    start = time.perf_counter()
    _write_skin_pair(arm, str(target))
    exported = time.perf_counter() - start
    result = native.rs().Mesh.from_path(str(target))
    assert bool(result.flags & 2) == (source.vertex_count > 65535)
    assert result.vertex_count == source.vertex_count, (result.vertex_count, source.vertex_count)
    assert result.index_count == source.index_count
    assert [s.name for s in result.submeshes] == [s.name for s in source.submeshes]
    # Compare triangle corner attributes, independent of exporter vertex reordering.
    a, b = skn_in._arrays(source), skn_in._arrays(result)
    for key in ('positions', 'normals', 'uvs', 'colors', 'blend_indices', 'blend_weights'):
        if a[key] is None:
            assert b[key] is None, key
            continue
        left, right = a[key][a['indices']], b[key][b['indices']]
        assert np.allclose(left, right, atol=2e-5, rtol=1e-5), key
    # Reimport both exported SKN and SKL into an empty actual Blender scene.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    arm2, meshes2, report2, _ = skn_in.import_skn(str(target), separate_submeshes=separate)
    assert arm2 is not None
    assert report2.vertex_count == source.vertex_count
    results.append(dict(separated=separate, vertices=result.vertex_count,
                        submeshes=len(result.submeshes), import_seconds=imported,
                        export_seconds=exported, reimport=True))
    print('PASS', results[-1], flush=True)
(output / 'report.json').write_text(json.dumps(results, indent=2))
print('ALL SKN CHECKS PASSED', flush=True)
