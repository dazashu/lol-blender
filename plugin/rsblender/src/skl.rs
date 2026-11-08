//! `.skl` skeletons for Python (parsing is `rs_anim`'s).
//!
//! - `bind_global`: the inverted inverse bind. The rest pose is built from it (the engine skins
//!   with it).
//! - `chain_global`: the local transforms composed down the parent chain.
//! - `bind_divergence`: how far apart the two are (they can differ in real files).
//!
//! All math in f64 (see `crate::mat`).

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rs_anim::{Joint as RsJoint, Skeleton as RsSkeleton};

use crate::mat::{compose, deviation_from_identity, is_usable, to_row_major};
use glam::DMat4;

/// One joint.
#[pyclass(module = "_rsblender", frozen)]
pub struct Joint {
    #[pyo3(get)]
    pub name: String,
    /// The hash as stored in the file (not recomputed from `name`). Animation tracks use it.
    #[pyo3(get)]
    pub hash: u32,
    #[pyo3(get)]
    pub id: i16,
    #[pyo3(get)]
    pub parent_id: i16,
    #[pyo3(get)]
    pub flags: u16,
    #[pyo3(get)]
    pub radius: f32,

    #[pyo3(get)]
    pub local_translation: (f32, f32, f32),
    /// (x, y, z, w), the file's order.
    #[pyo3(get)]
    pub local_rotation: (f32, f32, f32, f32),
    #[pyo3(get)]
    pub local_scale: (f32, f32, f32),

    #[pyo3(get)]
    pub inverse_bind_translation: (f32, f32, f32),
    #[pyo3(get)]
    pub inverse_bind_rotation: (f32, f32, f32, f32),
    #[pyo3(get)]
    pub inverse_bind_scale: (f32, f32, f32),

    /// Row-major 4x4: the bind transform in League space. `chain_global` when not invertible.
    #[pyo3(get)]
    pub bind_global: Vec<f64>,
    /// False when the inverse bind could not be inverted and `bind_global` is the local chain.
    #[pyo3(get)]
    pub bind_usable: bool,
    /// Row-major 4x4: the same joint composed from the local chain instead.
    #[pyo3(get)]
    pub chain_global: Vec<f64>,
    /// Row-major 4x4: the joint's local transform, the animation fallback for an untracked joint.
    #[pyo3(get)]
    pub local_matrix: Vec<f64>,
    /// max |chain_global * inverse_bind - I|. Zero when the two agree.
    #[pyo3(get)]
    pub bind_divergence: f64,
}

#[pymethods]
impl Joint {
    fn __repr__(&self) -> String {
        format!(
            "<Joint {:?} hash=0x{:08X} id={} parent={}>",
            self.name, self.hash, self.id, self.parent_id
        )
    }
}

#[pyclass(module = "_rsblender")]
pub struct Skeleton {
    #[pyo3(get)]
    pub joints: Vec<Py<Joint>>,
    /// Joint indices bound to the skin. A `.skn` blend index points into this list.
    #[pyo3(get)]
    pub influences: Vec<u16>,
    #[pyo3(get)]
    pub name: String,
    #[pyo3(get)]
    pub asset: String,
    #[pyo3(get)]
    pub flags: u16,
    /// True when every parent comes before its children (not always true after a DCC).
    #[pyo3(get)]
    pub parents_precede_children: bool,

    /// The parsed skeleton, for `Animation.palette`. Not exposed.
    pub(crate) inner: RsSkeleton,
}

#[pymethods]
impl Skeleton {
    #[staticmethod]
    fn from_path(path: &str) -> PyResult<Self> {
        // from_any_path also opens the legacy r3d2sklt container
        let skl = RsSkeleton::from_any_path(path)
            .map_err(|e| PyValueError::new_err(format!("{path}: {e}")))?;
        Python::attach(|py| build(py, &skl))
    }

    /// True for the legacy container (no skeleton or asset name).
    #[staticmethod]
    fn is_legacy_file(path: &str) -> PyResult<bool> {
        let mut head = [0u8; 8];
        use std::io::Read;
        let mut file = std::fs::File::open(path)
            .map_err(|e| PyValueError::new_err(format!("{path}: {e}")))?;
        if file.read_exact(&mut head).is_err() {
            return Ok(false);
        }
        Ok(rs_anim::is_legacy(&head))
    }

    #[getter]
    fn joint_count(&self) -> usize {
        self.joints.len()
    }

    fn __repr__(&self) -> String {
        format!(
            "<Skeleton {:?} joints={} influences={}>",
            self.name,
            self.joints.len(),
            self.influences.len()
        )
    }
}

pub(crate) fn local_matrix(j: &RsJoint) -> DMat4 {
    compose(j.local_translation, j.local_rotation, j.local_scale)
}

pub(crate) fn inverse_bind_matrix(j: &RsJoint) -> DMat4 {
    compose(
        j.inverse_bind_translation,
        j.inverse_bind_rotation,
        j.inverse_bind_scale,
    )
}

/// Global transforms composed from the local chain. Does not assume parents come first;
/// `seen` stops a cycle in a corrupt file.
pub(crate) fn chain_globals(skl: &RsSkeleton) -> PyResult<Vec<DMat4>> {
    let n = skl.joints().len();
    let mut out: Vec<Option<DMat4>> = vec![None; n];

    for start in 0..n {
        if out[start].is_some() {
            continue;
        }
        // Walk up to an already-resolved joint or a root, recording the path.
        let mut path: Vec<usize> = Vec::new();
        let mut seen = vec![false; n];
        let mut cur = start;
        loop {
            if seen[cur] {
                return Err(PyValueError::new_err(format!(
                    "skeleton has a parent cycle at joint {:?} (index {cur})",
                    skl.joints()[cur].name
                )));
            }
            seen[cur] = true;
            path.push(cur);

            let parent = skl.joints()[cur].parent_id;
            if parent < 0 {
                break;
            }
            let parent = parent as usize;
            if parent >= n {
                return Err(PyValueError::new_err(format!(
                    "joint {:?} has parent index {parent}, out of range (joint count {n})",
                    skl.joints()[cur].name
                )));
            }
            if out[parent].is_some() {
                break;
            }
            cur = parent;
        }

        // Resolve back down the path: everything above the deepest entry is known by now.
        for &idx in path.iter().rev() {
            let local = local_matrix(&skl.joints()[idx]);
            let parent = skl.joints()[idx].parent_id;
            out[idx] = Some(if parent < 0 {
                local
            } else {
                out[parent as usize].expect("parent resolved first by construction") * local
            });
        }
    }

    Ok(out.into_iter().map(|m| m.expect("all resolved")).collect())
}

fn build(py: Python<'_>, skl: &RsSkeleton) -> PyResult<Skeleton> {
    let chains = chain_globals(skl)?;

    let mut joints = Vec::with_capacity(skl.joints().len());
    for (i, j) in skl.joints().iter().enumerate() {
        let inverse_bind = inverse_bind_matrix(j);
        let usable = is_usable(&inverse_bind);
        let bind_global = if usable {
            inverse_bind.inverse()
        } else {
            chains[i]
        };
        let divergence = if usable {
            deviation_from_identity(&(chains[i] * inverse_bind))
        } else {
            f64::INFINITY
        };

        joints.push(Py::new(
            py,
            Joint {
                name: j.name.clone(),
                hash: j.hash,
                id: j.id,
                parent_id: j.parent_id,
                flags: j.flags,
                radius: j.radius,
                local_translation: (
                    j.local_translation.x,
                    j.local_translation.y,
                    j.local_translation.z,
                ),
                local_rotation: (
                    j.local_rotation.x,
                    j.local_rotation.y,
                    j.local_rotation.z,
                    j.local_rotation.w,
                ),
                local_scale: (j.local_scale.x, j.local_scale.y, j.local_scale.z),
                inverse_bind_translation: (
                    j.inverse_bind_translation.x,
                    j.inverse_bind_translation.y,
                    j.inverse_bind_translation.z,
                ),
                inverse_bind_rotation: (
                    j.inverse_bind_rotation.x,
                    j.inverse_bind_rotation.y,
                    j.inverse_bind_rotation.z,
                    j.inverse_bind_rotation.w,
                ),
                inverse_bind_scale: (
                    j.inverse_bind_scale.x,
                    j.inverse_bind_scale.y,
                    j.inverse_bind_scale.z,
                ),
                bind_global: to_row_major(&bind_global),
                bind_usable: usable,
                chain_global: to_row_major(&chains[i]),
                local_matrix: to_row_major(&local_matrix(j)),
                bind_divergence: divergence,
            },
        )?);
    }

    let ordered = skl
        .joints()
        .iter()
        .enumerate()
        .all(|(i, j)| j.parent_id < 0 || (j.parent_id as usize) < i);

    Ok(Skeleton {
        joints,
        influences: skl.influences().to_vec(),
        name: skl.name.clone(),
        asset: skl.asset.clone(),
        flags: skl.flags,
        parents_precede_children: ordered,
        inner: skl.clone(),
    })
}
