"""League <-> Blender coordinate conversion. The only place axis handling lives."""

import math

from mathutils import Matrix, Quaternion, Vector

#: League is Y-up left-handed, Blender Z-up right-handed: (x, y, z) -> (-x, -z, y).
#: Mirror X then Rx(+90), the same as lol_maya and physics_plugin. det = -1 (handedness flip).
P = Matrix((
    (-1.0, 0.0, 0.0, 0.0),
    (0.0, 0.0, -1.0, 0.0),
    (0.0, 1.0, 0.0, 0.0),
    (0.0, 0.0, 0.0, 1.0),
))

#: P is orthogonal, so its inverse is its transpose.
P_INV = P.transposed()


def riot_to_blender(matrix):
    """A League-space transform, in Blender space."""
    return P @ matrix @ P_INV


def blender_to_riot(matrix):
    """The inverse of `riot_to_blender`."""
    return P_INV @ matrix @ P


def riot_point_to_blender(x, y, z):
    """A position. Points transform with P directly, not by conjugation."""
    return Vector((-x, -z, y))


def blender_point_to_riot(vec):
    return (-vec.x, vec.z, -vec.y)


def trs_to_matrix(translation, rotation_xyzw, scale):
    """Compose League (T, R, S) into a 4x4. Rotation is (x, y, z, w), as in the file."""
    x, y, z, w = rotation_xyzw
    return (
        Matrix.Translation(translation)
        @ Quaternion((w, x, y, z)).to_matrix().to_4x4()
        @ Matrix.Diagonal(Vector(scale).to_4d())
    )


def matrix_to_trs(matrix):
    """Decompose a 4x4 into League (T, (x, y, z, w), S)."""
    t, q, s = matrix.decompose()
    return (t.x, t.y, t.z), (q.x, q.y, q.z, q.w), (s.x, s.y, s.z)


def orthonormalized(matrix):
    """Drop scale, keep rotation and translation. Rest bones cannot hold scale."""
    t, q, _ = matrix.decompose()
    return Matrix.Translation(t) @ q.to_matrix().to_4x4()


def scale_residue(matrix):
    """How far the scale is from 1. 0.0 when there is none."""
    _, _, s = matrix.decompose()
    return max(abs(s.x - 1.0), abs(s.y - 1.0), abs(s.z - 1.0))


def is_uniform_scale(matrix, tolerance=1e-5):
    """True when the three scale factors agree within a RELATIVE tolerance.

    Relative, because float32 cannot resolve large scales (78.4 and up) to an absolute 1e-5.
    """
    _, _, s = matrix.decompose()
    spread = max(abs(s.x - s.y), abs(s.y - s.z), abs(s.x - s.z))
    magnitude = max(abs(s.x), abs(s.y), abs(s.z))
    if magnitude <= 0.0:
        return True
    return spread / magnitude <= tolerance


def shear_residue(matrix):
    """How much of `matrix` translation + rotation + scale cannot express (the shear)."""
    translation, rotation, scale = matrix.decompose()
    rebuilt = (Matrix.Translation(translation)
               @ rotation.to_matrix().to_4x4()
               @ Matrix.Diagonal(scale).to_4x4())
    return max_element_deviation(matrix, rebuilt)


def max_element_deviation(a, b):
    """Worst absolute difference between two 4x4s."""
    return max(abs(a[r][c] - b[r][c]) for r in range(4) for c in range(4))


def matrix_to_flat(matrix):
    """Row-major 16 floats, for a custom property."""
    return [matrix[r][c] for r in range(4) for c in range(4)]


def flat_to_matrix(flat):
    if flat is None or len(flat) != 16:
        return None
    return Matrix((flat[0:4], flat[4:8], flat[8:12], flat[12:16]))


def _selftest():
    """Checks that hold by construction, so a typo in P fails fast."""
    assert abs(P.determinant() + 1.0) < 1e-9, "P must be a handedness flip (det = -1)"
    assert max_element_deviation(P @ P_INV, Matrix.Identity(4)) < 1e-9
    # League +Y (up) lands on Blender +Z (up)
    assert (riot_point_to_blender(0.0, 1.0, 0.0) - Vector((0.0, 0.0, 1.0))).length < 1e-9
    # mirror X then Rx(+90) equals P
    mirror_x = Matrix.Diagonal(Vector((-1.0, 1.0, 1.0)).to_4d())
    rx90 = Matrix.Rotation(math.radians(90.0), 4, 'X')
    assert max_element_deviation(rx90 @ mirror_x, P) < 1e-6
