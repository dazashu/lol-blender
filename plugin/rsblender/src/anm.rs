//! `.anm` animations: sampling, and baking into Blender pose channels.
//!
//! Every frame is sampled here (lerp for translation and scale, slerp for rotation), because
//! Blender does not slerp quaternion curves. Each pose channel is
//!
//!     basis = PRE * (P * local_league * P^-1) * POST
//!
//! with PRE and POST constant per bone (see `bake`).

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use rs_anim::{AnimFrame, AnimTrack, Animation as RsAnimation};
use rs_io::Parse;

use crate::mat::{from_row_major, riot_to_blender, to_row_major};
use crate::skl::{self, Skeleton};
use glam::{DMat4, DQuat, DVec3};

/// Below this angle slerp is unstable, so nlerp is used (same threshold as the reference sampler).
const SLERP_LINEAR_THRESHOLD: f64 = 0.9995;

fn frame_trs(f: &AnimFrame) -> (DVec3, DQuat, DVec3) {
    (
        DVec3::new(
            f.translation.x as f64,
            f.translation.y as f64,
            f.translation.z as f64,
        ),
        // not normalised, like the engine and rs_anim
        DQuat::from_xyzw(
            f.rotation.x as f64,
            f.rotation.y as f64,
            f.rotation.z as f64,
            f.rotation.w as f64,
        ),
        DVec3::new(f.scale.x as f64, f.scale.y as f64, f.scale.z as f64),
    )
}

/// Shortest-arc slerp, as the reference sampler (negate when `dot < 0`).
fn slerp(a: DQuat, b: DQuat, t: f64) -> DQuat {
    let mut b = b;
    let mut dot = a.x * b.x + a.y * b.y + a.z * b.z + a.w * b.w;
    if dot < 0.0 {
        b = DQuat::from_xyzw(-b.x, -b.y, -b.z, -b.w);
        dot = -dot;
    }
    if dot > SLERP_LINEAR_THRESHOLD {
        let r = DQuat::from_xyzw(
            a.x + (b.x - a.x) * t,
            a.y + (b.y - a.y) * t,
            a.z + (b.z - a.z) * t,
            a.w + (b.w - a.w) * t,
        );
        let len = (r.x * r.x + r.y * r.y + r.z * r.z + r.w * r.w).sqrt();
        let inv = if len > 0.0 { 1.0 / len } else { 1.0 };
        return DQuat::from_xyzw(r.x * inv, r.y * inv, r.z * inv, r.w * inv);
    }
    let theta0 = dot.clamp(-1.0, 1.0).acos();
    let theta = theta0 * t;
    let sin0 = theta0.sin();
    let s0 = (theta0 - theta).sin() / sin0;
    let s1 = theta.sin() / sin0;
    DQuat::from_xyzw(
        a.x * s0 + b.x * s1,
        a.y * s0 + b.y * s1,
        a.z * s0 + b.z * s1,
        a.w * s0 + b.w * s1,
    )
}

/// Sample one track at `time` seconds, clamped at both ends like the engine.
fn sample(track: &AnimTrack, time: f32) -> Option<DMat4> {
    let frames = &track.frames;
    if frames.is_empty() {
        return None;
    }
    if time <= frames[0].time {
        let (t, r, s) = frame_trs(&frames[0]);
        return Some(DMat4::from_scale_rotation_translation(s, r, t));
    }
    let last = &frames[frames.len() - 1];
    if time >= last.time {
        let (t, r, s) = frame_trs(last);
        return Some(DMat4::from_scale_rotation_translation(s, r, t));
    }

    // binary search for the bracketing keys
    let mut lo = 0usize;
    let mut hi = frames.len() - 1;
    while hi - lo > 1 {
        let mid = (lo + hi) / 2;
        if frames[mid].time <= time {
            lo = mid;
        } else {
            hi = mid;
        }
    }

    let (ta, ra, sa) = frame_trs(&frames[lo]);
    let (tb, rb, sb) = frame_trs(&frames[hi]);
    let span = (frames[hi].time - frames[lo].time) as f64;
    let u = if span > 1e-9 {
        ((time - frames[lo].time) as f64) / span
    } else {
        0.0
    };

    Some(DMat4::from_scale_rotation_translation(
        sa.lerp(sb, u),
        slerp(ra, rb, u),
        ta.lerp(tb, u),
    ))
}

/// Per-bone pose channels over a frame range, laid out for `foreach_set`.
///
/// Layout `[bone][channel][frame]`: one F-curve's values are contiguous.
#[pyclass(module = "_rsblender")]
pub struct Baked {
    #[pyo3(get)]
    pub bone_count: usize,
    #[pyo3(get)]
    pub frame_count: usize,
    /// f32 `[bone][3][frame]`.
    #[pyo3(get)]
    pub locations: Py<PyBytes>,
    /// f32 `[bone][4][frame]`, (w, x, y, z), kept in one hemisphere along the frames.
    #[pyo3(get)]
    pub quaternions: Py<PyBytes>,
    /// f32 `[bone][3][frame]`.
    #[pyo3(get)]
    pub scales: Py<PyBytes>,
    /// Per bone: did the animation actually carry a track for it?
    #[pyo3(get)]
    pub tracked: Vec<bool>,
    /// Per bone: is every frame at rest (within `IDENTITY_EPSILON`)?
    #[pyo3(get)]
    pub is_static: Vec<bool>,
    /// Worst |recomposed - original| over all channels: the shear a pose channel cannot hold.
    #[pyo3(get)]
    pub max_decompose_error: f64,
    #[pyo3(get)]
    pub worst_decompose_bone: usize,
}

/// A channel this close to rest counts as no animation at all.
const IDENTITY_EPSILON: f64 = 1e-7;

#[pyclass(module = "_rsblender")]
pub struct Animation {
    #[pyo3(get)]
    pub fps: f32,
    /// Time of the last key across all tracks, in seconds (tracks can differ in length).
    #[pyo3(get)]
    pub duration: f32,
    #[pyo3(get)]
    pub track_count: usize,
    /// Joint hashes this animation carries tracks for, in file order.
    #[pyo3(get)]
    pub track_hashes: Vec<u32>,
    /// True when the file was read from a byte-exact container, so an unedited write reproduces it.
    #[pyo3(get)]
    pub byte_exact: bool,
    /// True for the compressed `r3d2canm` container.
    #[pyo3(get)]
    pub compressed: bool,

    inner: RsAnimation,
}

#[pymethods]
impl Animation {
    #[staticmethod]
    fn from_path(path: &str) -> PyResult<Self> {
        // the crate does not report the container, so read the magic
        let compressed = std::fs::File::open(path)
            .and_then(|mut f| {
                use std::io::Read;
                let mut magic = [0u8; 8];
                f.read_exact(&mut magic)?;
                Ok(&magic == b"r3d2canm")
            })
            .unwrap_or(false);

        let anm = RsAnimation::from_path(path)
            .map_err(|e| PyValueError::new_err(format!("{path}: {e}")))?;

        let duration = anm
            .tracks()
            .iter()
            .filter_map(|t| t.frames.last().map(|f| f.time))
            .fold(0.0f32, f32::max);

        Ok(Animation {
            fps: anm.fps,
            duration,
            track_count: anm.tracks().len(),
            track_hashes: anm.tracks().iter().map(|t| t.joint_hash).collect(),
            byte_exact: anm.is_byte_exact(),
            compressed,
            inner: anm,
        })
    }

    /// Frames needed to cover the clip at its own rate, inclusive of both ends.
    #[getter]
    fn frame_count(&self) -> usize {
        if self.fps <= 0.0 {
            return 1;
        }
        (self.duration * self.fps).round().max(0.0) as usize + 1
    }

    /// One track's local transform at `time`, row-major, or None. For tests.
    fn sample_local(&self, joint_hash: u32, time: f32) -> Option<Vec<f64>> {
        self.inner
            .tracks()
            .iter()
            .find(|t| t.joint_hash == joint_hash)
            .and_then(|t| sample(t, time))
            .map(|m| to_row_major(&m))
    }

    /// The engine's skinning palette at `time`: `world[j] * inverse_bind[j]`, row-major. The
    /// reference for the pose tests. Untracked joints use their local transform, like the engine.
    fn palette(&self, skeleton: PyRef<'_, Skeleton>, time: f32) -> PyResult<Vec<f64>> {
        let skl_inner = &skeleton.inner;
        let joints = skl_inner.joints();
        let n = joints.len();

        let locals: Vec<DMat4> = joints
            .iter()
            .map(|j| {
                self.inner
                    .tracks()
                    .iter()
                    .find(|t| t.joint_hash == j.hash)
                    .and_then(|t| sample(t, time))
                    .unwrap_or_else(|| skl::local_matrix(j))
            })
            .collect();

        // resolve parents; file order is not guaranteed
        let mut world: Vec<Option<DMat4>> = vec![None; n];
        for start in 0..n {
            if world[start].is_some() {
                continue;
            }
            let mut path = Vec::new();
            let mut seen = vec![false; n];
            let mut cur = start;
            loop {
                if seen[cur] {
                    return Err(PyValueError::new_err("skeleton has a parent cycle"));
                }
                seen[cur] = true;
                path.push(cur);
                let parent = joints[cur].parent_id;
                if parent < 0 {
                    break;
                }
                let parent = parent as usize;
                if parent >= n {
                    return Err(PyValueError::new_err("joint parent index out of range"));
                }
                if world[parent].is_some() {
                    break;
                }
                cur = parent;
            }
            for &idx in path.iter().rev() {
                let parent = joints[idx].parent_id;
                world[idx] = Some(if parent < 0 {
                    locals[idx]
                } else {
                    world[parent as usize].expect("resolved") * locals[idx]
                });
            }
        }

        let mut out = Vec::with_capacity(n * 16);
        for (i, j) in joints.iter().enumerate() {
            let m = world[i].expect("resolved") * skl::inverse_bind_matrix(j);
            out.extend_from_slice(&to_row_major(&m));
        }
        Ok(out)
    }

    /// Bake pose channels for a set of bones.
    ///
    /// `bones` is `(joint_hash, PRE, POST, fallback_local_league)` per bone, matrices row-major.
    /// The fallback is the joint's local transform, used when the clip has no track for it.
    #[pyo3(signature = (bones, frame_count=None, sample_fps=None))]
    fn bake(
        &self,
        py: Python<'_>,
        bones: Vec<(u32, Vec<f64>, Vec<f64>, Vec<f64>)>,
        frame_count: Option<usize>,
        sample_fps: Option<f64>,
    ) -> PyResult<Baked> {
        let source_fps=if self.fps>0.0 {self.fps as f64} else {30.0};
        let fps=sample_fps.unwrap_or(source_fps);
        if !fps.is_finite() || fps<=0.0 {
            return Err(PyValueError::new_err("sample_fps must be finite and positive"));
        }
        let frames = frame_count.unwrap_or_else(||
            (((self.frame_count().saturating_sub(1)) as f64*fps/source_fps).round() as usize)+1).max(1);
        let bone_count = bones.len();

        let mut locations = Vec::with_capacity(bone_count * 3 * frames * 4);
        let mut quaternions = Vec::with_capacity(bone_count * 4 * frames * 4);
        let mut scales = Vec::with_capacity(bone_count * 3 * frames * 4);
        let mut tracked = Vec::with_capacity(bone_count);
        let mut is_static = Vec::with_capacity(bone_count);
        let mut max_decompose_error = 0.0f64;
        let mut worst_decompose_bone = 0usize;

        for (bone_index, (hash, pre, post, fallback)) in bones.iter().enumerate() {
            //     basis(j) = PRE(j) @ P*local*P^-1 @ POST(j)
            //     PRE(j)   = rest(j)^-1 @ bind_global(blender parent)
            //     POST(j)  = bind_global(j)^-1 @ rest(j)
            // POST carries the bind scale an orthonormal rest bone cannot hold (identity otherwise).
            let pre_m = from_row_major(pre).ok_or_else(|| {
                PyValueError::new_err(format!(
                    "bone {bone_index}: pre needs 16 values, got {}",
                    pre.len()
                ))
            })?;
            let post_m = from_row_major(post).ok_or_else(|| {
                PyValueError::new_err(format!(
                    "bone {bone_index}: post needs 16 values, got {}",
                    post.len()
                ))
            })?;
            let fallback_local = from_row_major(fallback).ok_or_else(|| {
                PyValueError::new_err(format!(
                    "bone {bone_index}: fallback_local needs 16 values, got {}",
                    fallback.len()
                ))
            })?;
            if pre_m.determinant().abs() < 1e-20 || post_m.determinant().abs() < 1e-20 {
                return Err(PyValueError::new_err(format!(
                    "bone {bone_index}: its rest or bind transform is singular, so its pose                      channel is undefined"
                )));
            }

            let track = self.inner.tracks().iter().find(|t| t.joint_hash == *hash);
            tracked.push(track.is_some());

            let mut loc = vec![0.0f32; 3 * frames];
            let mut quat = vec![0.0f32; 4 * frames];
            let mut scl = vec![0.0f32; 3 * frames];

            let mut previous: Option<DQuat> = None;
            let mut static_bone = true;

            for frame in 0..frames {
                let time = (frame as f64 / fps) as f32;
                let local_league = track
                    .and_then(|t| sample(t, time))
                    .unwrap_or(fallback_local);

                let basis = pre_m * riot_to_blender(&local_league) * post_m;
                let (scale, rotation, translation) = basis.to_scale_rotation_translation();

                // what loc/rot/scale cannot capture (shear)
                let error = crate::mat::deviation_from_identity(
                    &(basis.inverse()
                        * DMat4::from_scale_rotation_translation(scale, rotation, translation)),
                );
                if error > max_decompose_error {
                    max_decompose_error = error;
                    worst_decompose_bone = bone_index;
                }

                // keep neighbouring keys in one hemisphere, or Blender spins the long way
                let mut q = rotation;
                if let Some(p) = previous {
                    if p.x * q.x + p.y * q.y + p.z * q.z + p.w * q.w < 0.0 {
                        q = DQuat::from_xyzw(-q.x, -q.y, -q.z, -q.w);
                    }
                }
                previous = Some(q);

                loc[0 * frames + frame] = translation.x as f32;
                loc[1 * frames + frame] = translation.y as f32;
                loc[2 * frames + frame] = translation.z as f32;
                // Blender order (w, x, y, z)
                quat[0 * frames + frame] = q.w as f32;
                quat[1 * frames + frame] = q.x as f32;
                quat[2 * frames + frame] = q.y as f32;
                quat[3 * frames + frame] = q.z as f32;
                scl[0 * frames + frame] = scale.x as f32;
                scl[1 * frames + frame] = scale.y as f32;
                scl[2 * frames + frame] = scale.z as f32;

                if static_bone {
                    let moved = translation.length() > IDENTITY_EPSILON
                        || (scale - DVec3::ONE).length() > IDENTITY_EPSILON
                        || (q.w.abs() - 1.0).abs() > IDENTITY_EPSILON;
                    if moved {
                        static_bone = false;
                    }
                }
            }

            is_static.push(static_bone);
            for v in &loc {
                locations.extend_from_slice(&v.to_le_bytes());
            }
            for v in &quat {
                quaternions.extend_from_slice(&v.to_le_bytes());
            }
            for v in &scl {
                scales.extend_from_slice(&v.to_le_bytes());
            }
        }

        Ok(Baked {
            bone_count,
            frame_count: frames,
            locations: PyBytes::new(py, &locations).unbind(),
            quaternions: PyBytes::new(py, &quaternions).unbind(),
            scales: PyBytes::new(py, &scales).unbind(),
            tracked,
            is_static,
            max_decompose_error,
            worst_decompose_bone,
        })
    }

    fn __repr__(&self) -> String {
        format!(
            "<Animation fps={} duration={:.4}s tracks={} frames={}>",
            self.fps,
            self.duration,
            self.track_count,
            self.frame_count()
        )
    }
}
