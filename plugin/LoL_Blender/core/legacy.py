"""Property names the old standalone `physics_plugin` reads, written in one place.

Rigs imported here stay readable by it. A PoseBone has its own ID property store, so the
`native_*` values go on both the Bone and the PoseBone. Our own `riot_*` properties stay the
source of truth.
"""

from . import convert


def stamp_bone(bone, pose_bone, index, parent_name):
    """Write the four `native_*` properties on both stores. Unedited at import, so the rest
    matrix is `Bone.matrix_local`."""
    flat = convert.matrix_to_flat(bone.matrix_local)
    for holder in (bone, pose_bone):
        holder["native_bone_index"] = index
        holder["native_parent"] = parent_name
        holder["native_matrix_local"] = flat
        holder["native_global_rest_mat"] = flat


def stamp_skeleton_source(armature_object, path):
    """Record the `.skl` path. The clip list looks in `dirname(path)/animations`."""
    armature_object["lol_skl_filepath"] = path


def stamp_mesh_source(mesh_object, armature_object, path):
    """Record the `.skn` path, the fallback when no `.skl` path is set."""
    mesh_object["lol_skn_filepath"] = path
    if armature_object is not None and "lol_skl_filepath" not in armature_object:
        armature_object["lol_skn_filepath"] = path


def stamp_action_source(action, path):
    """Record which clip an Action came from."""
    import os

    action["lol_anm_filepath"] = path
    action["lol_anm_filename"] = os.path.basename(path)
