"""Load `lol_maya.py`'s format readers with no Maya installed, as an independent oracle.

WHY THIS EXISTS. The plugin's readers are the Ritoshark crates, and those are the only
implementation that ships. But "byte-exact round-trip" proves a reader agrees with *itself* - it
does not prove it agrees with the format. The Maya plugin is a second, unrelated implementation
of the same six formats written by someone else years earlier, so where the two disagree, one of
them has a bug. That is the cheapest bug detector available here and it costs nothing to run.

HOW. `lol_maya.py` opens with three `from maya... import *` star imports, and its translator
classes subclass `MPxFileTranslator` at class-creation time, so the names have to resolve before
the module body finishes. Rather than hand-maintain a stub listing every Maya symbol it touches -
which rots the moment the Maya plugin is updated - the module is executed against a globals dict
that manufactures a dummy class for any name it cannot find. The format classes themselves
(`BinaryStream`, `SKL`, `SKN`, `ANM`, `SO`, `MAPGEO`) only use `struct` and `math`, so they come
out fully functional; only the `load`/`dump` halves, which drive the Maya scene, are hollow, and
those are never called from here.

The Maya plugin is GPL-3.0. It is READ at test time, never vendored or redistributed.
"""

import builtins
import os
import sys
import types

from run import EXTERNAL, Skip
LOL_MAYA_PY = os.path.join(EXTERNAL, "lol maya fix", "plug-ins", "lol_maya.py")


class _Dummy:
    """Stands in for any Maya symbol. Constructible, callable and subclassable, so it works
    whether the name is used as a base class, a constructor or a bare constant."""

    def __init__(self, *_a, **_k):
        pass

    def __call__(self, *_a, **_k):
        return _Dummy()

    def __getattr__(self, _name):
        return _Dummy()


class _MayaGlobals(dict):
    """Globals that manufacture a `_Dummy` for any unresolved name.

    Module-level code and class bodies look names up with LOAD_NAME, and function bodies with
    LOAD_GLOBAL; on a dict *subclass* CPython routes both through `__getitem__`, so `__missing__`
    covers the whole module. Names that genuinely exist - `struct`, `math`, everything the module
    defines itself - are found normally and never reach here.

    BUILTINS MUST FALL THROUGH. Globals are searched before builtins, so a `__missing__` that
    answers everything also answers `open`, `ord` and `range`, and the reader then tries to
    iterate a dummy. Raising KeyError for those is not an error path: it is how the interpreter
    is told to carry on to the builtins namespace, which is exactly what should happen.
    """

    def __missing__(self, name):
        if name.startswith("__") or hasattr(builtins, name):
            raise KeyError(name)
        value = type(name, (_Dummy,), {})
        self[name] = value
        return value


_module = None


def load():
    """Import lol_maya's format classes. Cached; safe to call repeatedly."""
    global _module
    if _module is not None:
        return _module

    if not os.path.isfile(LOL_MAYA_PY):
        raise Skip(
            "the oracle needs the Maya plugin at %s" % LOL_MAYA_PY)

    with open(LOL_MAYA_PY, "r", encoding="utf-8") as fh:
        source = fh.read()

    # Drop the Maya star-imports. Everything else - struct, math, random - is real and stays.
    lines = [
        "" if line.startswith("from maya.") else line
        for line in source.splitlines()
    ]
    source = "\n".join(lines)

    mod = types.ModuleType("lol_maya_oracle")
    g = _MayaGlobals()
    g["__name__"] = mod.__name__
    g["__file__"] = LOL_MAYA_PY
    g["__builtins__"] = __builtins__

    exec(compile(source, LOL_MAYA_PY, "exec"), g)

    for key, value in g.items():
        setattr(mod, key, value)
    sys.modules[mod.__name__] = mod
    _module = mod
    return mod


def read_skl(path):
    """Parse a `.skl` with the Maya plugin's reader. Returns its `SKL` object.

    NOTE: no `flip()` is applied. `flip` is the Maya-space conversion (mirror X); the oracle must
    hand back the values exactly as they sit in the file, because that is what the Rust reader
    returns and what the comparison is about.
    """
    m = load()
    skl = m.SKL()
    skl.read(path)
    return skl


def read_skn(path):
    m = load()
    skn = m.SKN()
    skn.read(path)
    return skn


def read_anm(path):
    m = load()
    anm = m.ANM()
    anm.read(path)
    return anm
