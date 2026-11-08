"""Headless test runner.

    blender -b --factory-startup --python tests/run.py

Runs every `test_*.py` in this directory, in name order, and exits non-zero on the first
failure so a build script can gate on it. No pytest: Blender ships its own Python without it,
and adding a dependency to run the suite is a dependency the plugin does not otherwise need.

Tests that need game files call `corpus()` and raise `Skip` when the file is absent - the same
convention the Ritoshark crates use, so a clean checkout stays green.
"""

import importlib.util
import os
import sys
import traceback

# The project layout, in one place. Every test and tool finds a folder through these names.
#
#   plugin/          LoL_Blender (the addon), rsblender (Rust), build and install scripts
#   external/        Ritoshark-Crates, Rubyre, lol maya fix, ARP, physics_plugin, reference
#   docs-and-tests/  docs, tests (this folder), samples (League files the tests read)
#   releases/        addon zips
TESTS = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(os.path.dirname(TESTS))
PLUGIN = os.path.join(PROJECT, "plugin")
EXTERNAL = os.path.join(PROJECT, "external")
SAMPLES = os.path.join(PROJECT, "docs-and-tests", "samples")
DOCS = os.path.join(PROJECT, "docs-and-tests", "docs")
#: The folder holding `LoL_Blender`, which is what has to be on sys.path to import it.
ROOT = PLUGIN
ADDON_BIN = os.path.join(PLUGIN, "LoL_Blender", "bin")

for p in (ROOT, TESTS, ADDON_BIN):
    if p not in sys.path:
        sys.path.insert(0, p)

# ONE COPY OF THIS MODULE, NOT TWO. Blender runs this file as `__main__`, and every test does
# `from run import Skip, corpus`, which imports it a SECOND time under the name `run`. That gives
# two `Skip` classes: the tests raise `run.Skip` and `main()` below catches `__main__.Skip`, so a
# skip was counted as a failure instead. Measured the day a corpus file was re-exported and three
# data-dependent controls went red rather than reporting themselves blind.
sys.modules.setdefault("run", sys.modules[__name__])


class Skip(Exception):
    """Raised by a test whose input files are not present."""


_corpus_index = None


def _index_corpus():
    """Map every file under `samples/` by its lowercase basename.

    WHY A SEARCH AND NOT A FIXED PATH. The corpus started as `File/assets/...` and became
    `File/graves/assets/...` (the folder is `samples/` now) the moment a second skin was dropped in beside it. Hard-coded paths
    turned every asset test into a silent Skip, and the suite still reported green - which is the
    worst possible failure mode for a corpus. Searching by name is immune to the layout, and
    `require_corpus()` below turns a missing file into a loud skip count rather than nothing.
    """
    global _corpus_index
    if _corpus_index is not None:
        return _corpus_index

    _corpus_index = {}
    root = SAMPLES
    for folder, _dirs, files in os.walk(root):
        for name in files:
            _corpus_index.setdefault(name.lower(), []).append(os.path.join(folder, name))
    return _corpus_index


def corpus(*parts):
    """A corpus file. Accepts a path relative to `samples/`, or just a basename to search for."""
    direct = os.path.join(SAMPLES, *parts)
    if os.path.exists(direct):
        return direct

    matches = _index_corpus().get(parts[-1].lower(), [])
    if len(parts) > 1:
        # Disambiguate with the parent folders that were asked for, most specific first.
        for depth in range(len(parts) - 1, 0, -1):
            tail = os.path.join(*parts[-depth - 1:]).lower()
            narrowed = [m for m in matches if m.lower().endswith(tail)]
            if narrowed:
                matches = narrowed
                break
    if not matches:
        raise Skip("missing corpus file: %s" % os.path.join(*parts))
    return sorted(matches)[0]


def graves(name):
    """A file from the Graves base skin, the reference asset for every phase."""
    return corpus("characters", "graves", "skins", "base", name)


def graves_anim(name):
    return corpus("graves", "skins", "base", "animations", name)


def riven(name):
    """A file from the custom Riven skin: 4 submeshes, 165 joints, made in Maya with lol_maya.

    The multi-submesh case. Graves has ONE submesh, so it never exercised the separated build path,
    the group Empty, or the influence table at any real size - and a crash lived there unnoticed."""
    return corpus("riven", "skins", "skin23", name)


def scratch(name, directory=False):
    """A path under `tests/_scratch/`, for anything a test writes.

    Never the corpus and never the addon: an export test that wrote next to its input would leave
    files that the next run's donor lookup would then find, and the test would start passing for
    the wrong reason.
    """
    root = os.path.join(TESTS, "_scratch")
    path = os.path.join(root, name)
    os.makedirs(path if directory else root, exist_ok=True)
    return path


def riven_anim(name):
    return corpus("riven", "skins", "skin23", "animations", name)


def _load(path):
    name = os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    files = sorted(
        os.path.join(TESTS, f)
        for f in os.listdir(TESTS)
        if f.startswith("test_") and f.endswith(".py")
    )
    # `LOL_TESTS=16,19` runs only those files. For iterating on one area without paying for the
    # whole suite; the build script never sets it, so a build always runs everything.
    only = [p.strip() for p in os.environ.get("LOL_TESTS", "").split(",") if p.strip()]
    if only:
        files = [f for f in files if any(p in os.path.basename(f) for p in only)]

    passed = failed = skipped = 0
    failures = []

    for path in files:
        mod = _load(path)
        for attr in sorted(dir(mod)):
            if not attr.startswith("test_"):
                continue
            fn = getattr(mod, attr)
            if not callable(fn):
                continue
            label = "%s.%s" % (os.path.basename(path)[:-3], attr)
            try:
                fn()
            except Skip as e:
                skipped += 1
                print("SKIP %s  (%s)" % (label, e))
            except Exception:
                failed += 1
                failures.append(label)
                print("FAIL %s" % label)
                traceback.print_exc()
            else:
                passed += 1
                print("ok   %s" % label)

    print("\n%d passed, %d failed, %d skipped" % (passed, failed, skipped))
    if failures:
        print("failed: " + ", ".join(failures))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
