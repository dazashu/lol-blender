"""Daza Weight settings and its problem report. Stored on the scene, not the rig."""

import bpy
from bpy.props import (
    BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty,
    StringProperty)


class LOLWEIGHT_ProblemItem(bpy.types.PropertyGroup):
    """One line of the report."""

    target: StringProperty()
    summary: StringProperty()
    fix: StringProperty()
    blocking: BoolProperty(default=False)


class LOLWEIGHT_Settings(bpy.types.PropertyGroup):
    # names, items and ranges copied from ARP 3.75.43 and the VHDS addon
    engine: EnumProperty(name="Bind Engine", description="Binding method", items=(
        ('HEAT_MAP', "Heat Maps", "Surface skinning.\nAccurate but may be inconsistent with "
                                  "multiple layers of clothes, props"),
        ('PSEUDO_VOXELS', "Voxelized", "Approximated volume skinning. \nMore consistent than Heat "
                                       "Maps, but less accurate with small details (fingers, facial...)"),
        ('VHDS', "Voxel Heat Diffuse Skinning", "True volume skinning.\nRust port, no addon or "
                                                "executable required")), default='VHDS')
    split_parts: BoolProperty(
        name="Split Parts", default=True,
        description="Improve skinning by separating the loose parts (e.g: hats, buttons, belt...) "
                    "before binding.\nWarning: meshes with a lot of separate pieces can take "
                    "several minutes to bind")
    selected_bones_only: BoolProperty(name="Selected Bones Only", default=True,
                                      description="Only bind to selected deforming bones")
    bind_current_pose: BoolProperty(
        name="Bind at Current Pose", default=True,
        description=("Bind the skeleton where you see it, like Maya: the pose becomes the rest "
                     "first. Skipped while an animation drives the pose, unless the skeleton is "
                     "locked"))
    selected_vertices_only: BoolProperty(name="Selected Vertices Only", default=True,
                                         description="Only bind selected vertices")
    apply_shape_keys: BoolProperty(
        name="Apply Shape Keys", default=False,
        description="Shape Keys will be evaluated in their current state when binding")
    preserve_volume: BoolProperty(
        name="Preserve Volume", default=False,
        description="Enables the Preserve Volume option of the armature modifier (dual quaternions "
                    "skinning).\nShould be disabled when exporting to game engines")
    scale_fix: BoolProperty(
        name="Scale Fix", default=False,
        description="Check this if binding did not work.\nInternally operate on a scaled version "
                    "of the meshes to fix defective skinning")
    optimize_highres: BoolProperty(
        name="Optimize High Resolution Meshes", default=False,
        description="Speed up binding time of high resolution meshes")
    highres_threshold: IntProperty(
        name="High Res Threshold", default=70000, min=1,
        description="Meshes with polycount higher than this will be considered as high resolution "
                    "meshes to optimize binding performances")
    voxel_type: EnumProperty(name="Pseudo-Voxels Type", description="Voxelization engine",
                             items=(('1', "Type 1", "Type 1"), ('2', "Type 2", "Type2")),
                             default='1')
    voxel_resolution: IntProperty(
        name="Pseudo-Voxels Resolution", default=7, min=3, max=12,
        description="Voxel precision, higher values is more accurate but longer to compute.\nLow "
                    "values may sometimes work better than high values depending on the mesh "
                    "complexity")
    vhds_resolution: IntProperty(name="Voxel Resolution", default=128, min=32, max=1024,
                                 description="Maximum voxel grid size")
    vhds_loops: IntProperty(name="Diffuse Loops", default=5, min=1, max=9,
                            description="Heat diffuse pass = Voxel Resolution * Diffuse Loops")
    vhds_samples: IntProperty(name="Sample Rays", default=64, min=32, max=128,
                              description="Ray samples count")
    vhds_influence: IntProperty(
        name="Influence Bones", default=8, min=1, max=128,
        description="Max influence bones per vertex, please decrease the value (such as 4) for "
                    "mobile devices")
    vhds_falloff: FloatProperty(name="Diffuse Falloff", default=0.2, min=0.01, max=0.99,
                                description="Heat diffuse falloff")
    vhds_solidify: BoolProperty(
        name="Detect Solidify", default=False,
        description="Detect solidified clothes, if you enable this option, make sure that all "
                    "bones are in the charecter's volume, otherwise, the result may be wrong")
    vhds_half_cpu: BoolProperty(
        name="Use Half CPU Cores", default=False,
        description="Use only half of the CPU cores, if your computer get stuck, please enable "
                    "this option to keep your computer running smoothly")

    # ARP-rig-only refinements: drawn greyed out like ARP, never used on a League skeleton
    bind_chin: BoolProperty(
        name="Refine Head Weights", default=True,
        description="Improve head skinning based on the chin position, defined by Smart or "
                    "approximated.\nOnly when facial is disabled, and biped type")
    bind_improve_twists: BoolProperty(
        name="Smooth Twist Weights", default=True,
        description="Refine twist weights by applying a gradient decay along the bone")
    bind_improve_hips: BoolProperty(name="Improve Hips Weights", default=True,
                                    description="Improve hips weights")
    bind_improve_heels: BoolProperty(name="Improve Heels Weights", default=True,
                                     description="Improve foot, heel weights")

    # ARP addon preferences, kept on the scene here
    parent_objects: BoolProperty(
        name="Parent Objects when Binding", default=True,
        description="Parent the objects to the armature when binding.\nTurning it off *is not "
                    "recommended* as it can lead to issues, but it may be useful for some custom "
                    "workflows")
    remove_armature_modifiers: BoolProperty(
        name="Remove Existing Armature Modifiers when Binding", default=True,
        description="Remove existing armature modifiers when binding")
    remove_vertex_groups: BoolProperty(
        name="Remove Existing Vertex Groups when Binding", default=True,
        description="Remove existing vertex groups when binding")
    running: BoolProperty(default=False, options={'SKIP_SAVE'})

    # -- cleaning ------------------------------------------------------------------------
    clean_transforms: BoolProperty(
        name="Apply Rotation and Scale", default=True,
        description="Ctrl+A on each mesh. The most important one: a mesh carrying a transform is a "
                    "different shape in the armature's space than the one on screen")
    clean_merge: BoolProperty(
        name="Merge by Distance", default=True,
        description="Weld vertices sitting on top of one another")
    clean_degenerate: BoolProperty(
        name="Dissolve Degenerate", default=True,
        description="Remove faces and edges with no area or length")
    clean_normals: BoolProperty(
        name="Recalculate Normals", default=True,
        description="Shift+N, so every face points outwards")

    status: StringProperty(default="Ready")
    report: CollectionProperty(type=LOLWEIGHT_ProblemItem)
    show_settings: BoolProperty(default=False, name="Advanced")


_classes = (LOLWEIGHT_ProblemItem, LOLWEIGHT_Settings)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.lol_weight = PointerProperty(type=LOLWEIGHT_Settings)


def unregister():
    del bpy.types.Scene.lol_weight
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
