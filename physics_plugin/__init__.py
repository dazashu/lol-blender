bl_info = {
    "name": "Daza Physics",
    "author": "dazashu",
    "version": (1, 0, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > Daza Physics",
    "description": "Independent physics tools for League of Legends armature animations, combining Wiggle 2, boobs jiggle, and hair chain simulation.",
    "category": "Animation",
}

import bpy

from . import icons
from . import physics
from . import boobs_physics
from . import hair_physics

def register():
    icons.register()
    physics.register()
    boobs_physics.register()
    hair_physics.register()

def unregister():
    hair_physics.unregister()
    boobs_physics.unregister()
    physics.unregister()
    icons.unregister()
