"""The `riot_` donor lookup, same order as lol_maya.

On export, `riot_<basename>.<ext>` then `riot.<ext>` next to the target is the original file to
take what the scene cannot carry. The `.skl` donor is named after the `.skn` target, as in lol_maya.
"""

import os


def find(target_path, extension=None):
    """The donor for `target_path`, or None."""
    directory = os.path.dirname(os.path.abspath(target_path))
    base = os.path.basename(target_path)
    stem, own_ext = os.path.splitext(base)
    ext = extension if extension is not None else own_ext

    for candidate in ("riot_%s%s" % (stem, ext), "riot%s" % ext):
        path = os.path.join(directory, candidate)
        if os.path.isfile(path):
            return path
    return None


def skeleton_donor(skn_target_path):
    """The `.skl` donor, derived from the `.skn` target name."""
    return find(skn_target_path, ".skl")


def describe(path, used=True):
    """A report line naming the donor, or None.

    `.mapgeo` and `.skl` read their donor (the `.skl` takes its joint order and name fields, as
    lol_maya does). The other formats pass `used=False`.
    """
    if not path:
        return None
    if used:
        return "using donor %s" % os.path.basename(path)
    return ("%s is beside the target but was not used - the joint order and the container fields "
            "come from the rig itself" % os.path.basename(path))
