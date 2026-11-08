"""Import/export options remembered in the addon preferences.

Operators load them in `invoke()` and save them in `execute()`, so they survive a new file and
a restart. Each mapping is {operator property: preference property}.
"""

import bpy

SKN = {
    "load_skeleton": "skn_load_skeleton",
    "separate_submeshes": "skn_separate_submeshes",
    "existing": "import_existing",
    "report_details": "import_report_details",
}

EXPORT_SKIN = {
    "write_skeleton": "export_write_skeleton",
    "use_blender_normals": "export_use_blender_normals",
    "report_details": "export_report_details",
}

EXPORT_SKN = {
    "use_blender_normals": "export_use_blender_normals",
    "report_details": "export_report_details",
}

EXPORT_SKL = {
    "report_details": "export_report_details",
}

EXPORT_ANM = {
    "range_source": "export_range_source",
    "report_details": "export_report_details",
}

IMPORT_STATIC = {
    "report_details": "import_report_details",
}

EXPORT_STATIC = {
    "report_details": "export_report_details",
}

SKL = {
    "existing": "skl_existing",
    "keep_meshes": "skl_keep_meshes",
    "select_after": "skl_select_after",
    "report_details": "import_report_details",
}

ANM = {
    "replace_existing": "anm_replace_existing",
    "frame_offset": "anm_frame_offset",
    "set_scene_range": "anm_set_scene_range",
    "report_details": "import_report_details",
}


def settings(context=None):
    """The addon preferences holding the remembered options, or None."""
    context = context or bpy.context
    package = __package__.rsplit(".", 1)[0]
    addons = context.preferences.addons
    addon = addons.get(package)
    if addon is None:
        # no entry when registered directly instead of through addon_enable (scripts, tests)
        try:
            addon = addons.new()
            addon.module = package
        except Exception:                           # noqa: BLE001 - read-only preferences
            return None
    try:
        return addon.preferences
    except AttributeError:
        return None


def load(operator, context, mapping):
    """Seed the operator from the preferences. Only from `invoke`, so the file browser wins."""
    values = settings(context)
    if values is None:
        return
    for operator_prop, scene_prop in mapping.items():
        if hasattr(values, scene_prop) and hasattr(operator, operator_prop):
            setattr(operator, operator_prop, getattr(values, scene_prop))


def save(operator, context, mapping):
    """Store the values used. Called at the top of `execute`."""
    values = settings(context)
    if values is None:
        return
    for operator_prop, scene_prop in mapping.items():
        if hasattr(values, scene_prop) and hasattr(operator, operator_prop):
            setattr(values, scene_prop, getattr(operator, operator_prop))
