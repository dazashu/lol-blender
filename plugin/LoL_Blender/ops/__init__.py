"""Blender operators and menu entries."""

from . import ask
from . import import_skl
from . import import_skn
from . import import_anm
from . import lock
from . import mapgeo
from . import bones
from . import drop
from . import export
from . import physics
from . import static
from . import tools
from . import weights

# File > Import lists entries in registration order.
_modules = (ask, import_skl, import_skn, import_anm, export, static, mapgeo, bones,
            tools, lock, physics, weights, drop)


def register():
    for module in _modules:
        module.register()


def unregister():
    for module in reversed(_modules):
        module.unregister()
