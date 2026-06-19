import os
import bpy
import bpy.utils.previews

# Global dictionary to hold preview collections
preview_collections = {}

def register():
    pcoll = bpy.utils.previews.new()

    # Path to the local icons folder
    addon_dir = os.path.dirname(__file__)
    icons_dir = os.path.join(addon_dir, "icons")

    pcoll.load("icon_52", os.path.join(icons_dir, "52.png"), 'IMAGE')

    preview_collections["main"] = pcoll

def unregister():
    for pcoll in preview_collections.values():
        bpy.utils.previews.remove(pcoll)
    preview_collections.clear()

def get_icon(name):
    pcoll = preview_collections.get("main")
    if not pcoll:
        return 0
    
    icon = pcoll.get(name)
    if not icon:
        return 0
        
    return icon.icon_id
