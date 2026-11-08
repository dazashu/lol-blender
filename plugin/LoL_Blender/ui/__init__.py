"""Sidebar UI. Props register before the panels that draw them."""

from . import anim_library
from . import panel
from . import physics_panel
from . import physics_props
from . import props
from . import toolbar
from . import weight_panel
from . import weight_props

_modules = (props, physics_props, weight_props, anim_library, panel, physics_panel,
            weight_panel, toolbar)


def register():
    for module in _modules:
        module.register()


def unregister():
    for module in reversed(_modules):
        module.unregister()
