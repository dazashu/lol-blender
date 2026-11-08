# LoL Blender 2.3.0 — Daza Tools

League of Legends asset import/export, skin weights and bone physics.
Tested on Blender 4.2.0, Windows x64.

## 2.3.0

- Restores the physics of release 2.2.0: solver, controls, live preview, collisions and bake behavior.
- Keeps current mesh/skeleton/animation fixes, custom-bone identity checks, animation precision and zero-scale visibility.
- Supports SKN meshes above 65535 total vertices using submesh-relative indices, with a limit of 65535 vertices per exported submesh after UV/normal splitting.
- Keeps safe export replacement, bake failure recovery, shared-action safety and timeline restoration.

Already-baked keys are not automatically changed. Restore the original action or reimport the animation before baking again.

## Build and install

Requires Windows x64, Python 3, Rust stable and Visual Studio C++ build tools.

```powershell
.\build.ps1
python tools/package_addon.py
```

Install `dist/LoL_Blender_2.3.0.zip` in Blender Preferences > Add-ons. Restart Blender after replacing an older version.
The repository contains sources; native binaries and game assets are not committed.
Python and Rust both require native version 0.25.0.

## Tests

```powershell
& 'C:\Program Files\Blender Foundation\Blender 4.2\blender.exe' -b --factory-startup --python-exit-code 1 --python docs-and-tests/tests/run.py
```

Tests requiring game assets look in `docs-and-tests/samples` and report skips when unavailable.
Set `LOL_TESTS` to comma-separated filename fragments to select tests.
Set `LOL_SKN_FIXTURE` to a local SKN with its matching SKL and run `docs-and-tests/tests/check_large_skn.py` for a large-mesh round trip.

## Layout

- `plugin/LoL_Blender`: addon, operators and UI.
- `plugin/rsblender`: Rust source, including the original 2.2.0 solver.
- `external/Ritoshark-Crates`: required format crates with their licenses.
- `docs-and-tests/tests`: regressions; no proprietary assets.
- `tools/package_addon.py`: addon ZIP packaging.

## License and credits

GPL-3.0-or-later; see [LICENSE](LICENSE).
Physics is based on [Wiggle 2 by Steve Miller](https://github.com/shteeve3d/blender-wiggle-2).
[RitoShark-Crates](https://github.com/RitoShark/RitoShark-Crates) is MIT/Apache-2.0.
Derived Maya translator code credits lol_maya in the source. Additional notices are retained in the addon licenses directory and Rust modules.
