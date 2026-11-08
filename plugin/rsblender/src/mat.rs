//! Matrix construction and hand-off to Python.
//!
//! - glam is column-major; `mathutils.Matrix` is built from rows (`to_row_major`).
//! - Composition and inversion run in f64 (widened from the file's f32), so no error is added.

use glam::{DMat4, DQuat, DVec3, DVec4};
use rs_math::{Quat, Vec3};

/// League -> Blender axis change: `(x, y, z) -> (-x, -z, y)`, same as `core/convert.py`'s `P`.
/// det = -1 (handedness flip). Each `DVec4` below is a column.
pub fn p_matrix() -> DMat4 {
    DMat4::from_cols(
        DVec4::new(-1.0, 0.0, 0.0, 0.0),
        DVec4::new(0.0, 0.0, 1.0, 0.0),
        DVec4::new(0.0, -1.0, 0.0, 0.0),
        DVec4::new(0.0, 0.0, 0.0, 1.0),
    )
}

/// Re-express a League-space transform in Blender space: `P * m * P^-1`.
///
/// `P` is orthogonal, so the inverse is the transpose.
pub fn riot_to_blender(m: &DMat4) -> DMat4 {
    let p = p_matrix();
    p * *m * p.transpose()
}

/// The inverse of [`riot_to_blender`]: `P^-1 * m * P`.
pub fn blender_to_riot(m: &DMat4) -> DMat4 {
    let p = p_matrix();
    p.transpose() * *m * p
}

/// Build a matrix from 16 row-major values, the layout `mathutils.Matrix` uses.
pub fn from_row_major(v: &[f64]) -> Option<DMat4> {
    if v.len() != 16 {
        return None;
    }
    // rows in, glam columns out
    Some(DMat4::from_cols(
        DVec4::new(v[0], v[4], v[8], v[12]),
        DVec4::new(v[1], v[5], v[9], v[13]),
        DVec4::new(v[2], v[6], v[10], v[14]),
        DVec4::new(v[3], v[7], v[11], v[15]),
    ))
}

/// Compose League's stored (translation, rotation, scale) into an f64 matrix. The quaternion is not
/// normalised, matching `rs_anim`.
pub fn compose(translation: Vec3, rotation: Quat, scale: Vec3) -> DMat4 {
    DMat4::from_scale_rotation_translation(
        DVec3::new(scale.x as f64, scale.y as f64, scale.z as f64),
        DQuat::from_xyzw(
            rotation.x as f64,
            rotation.y as f64,
            rotation.z as f64,
            rotation.w as f64,
        ),
        DVec3::new(
            translation.x as f64,
            translation.y as f64,
            translation.z as f64,
        ),
    )
}

/// A 4x4 as 16 row-major floats, for `mathutils.Matrix`.
pub fn to_row_major(m: &DMat4) -> Vec<f64> {
    let mut out = Vec::with_capacity(16);
    for r in 0..4 {
        let row = m.row(r);
        out.extend_from_slice(&[row.x, row.y, row.z, row.w]);
    }
    out
}

/// Worst absolute difference from the identity.
pub fn deviation_from_identity(m: &DMat4) -> f64 {
    let a = m.to_cols_array();
    let mut worst = 0.0f64;
    for (k, v) in a.iter().enumerate() {
        let want = if k % 5 == 0 { 1.0 } else { 0.0 };
        worst = worst.max((v - want).abs());
    }
    worst
}

/// True when a matrix is safe to invert (a `.skl` can hold an all-zero inverse bind).
pub fn is_usable(m: &DMat4) -> bool {
    let det = m.determinant();
    det.is_finite() && det.abs() > 1e-20 && m.to_cols_array().iter().all(|v| v.is_finite())
}
