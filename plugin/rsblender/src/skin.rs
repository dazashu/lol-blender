//! Mesh diagnostics for Daza Weight.
use pyo3::{prelude::*, exceptions::PyValueError};
use glam::DVec3;

/// Everything measured about one mesh.
#[pyclass(skip_from_py_object)]
#[derive(Clone, Default)]
pub struct MeshReport {
    #[pyo3(get)]
    vertices: usize,
    #[pyo3(get)]
    triangles: usize,
    /// Faces with no area at all.
    #[pyo3(get)]
    degenerate: usize,
    /// Vertices sharing a position with an earlier one.
    #[pyo3(get)]
    doubled: usize,
    /// Edges with exactly one face (holes).
    #[pyo3(get)]
    boundary: usize,
    /// Edges with three or more faces.
    #[pyo3(get)]
    nonmanifold: usize,
    /// Edges belonging to no face at all.
    #[pyo3(get)]
    wire: usize,
    /// Faces pointing into the mesh rather than out of it.
    #[pyo3(get)]
    inward: usize,
    /// The mesh's signed volume. Negative means the whole surface is inside out.
    #[pyo3(get)]
    volume: f64,
}

#[pymethods]
impl MeshReport {
    /// The problems as `(summary, fix, blocking)`. A mesh without faces blocks the bind.
    /// `high_poly` is the slow-bind triangle count; `shape_keys` comes from Blender.
    #[pyo3(signature = (high_poly = 150000, shape_keys = false))]
    fn problems(&self, high_poly: usize, shape_keys: bool) -> Vec<(String, String, bool)> {
        let mut out: Vec<(String, String, bool)> = Vec::new();

        if self.vertices == 0 {
            out.push((
                "the mesh has no vertices".into(),
                "Add mesh geometry or leave this object out of the selection".into(),
                true,
            ));
            return out;
        }
        if self.triangles == 0 {
            out.push((
                "the mesh has no faces, only vertices or edges".into(),
                "Binding requires mesh faces. Give this object faces or leave it out of the selection".into(),
                true,
            ));
            return out;
        }

        if self.triangles > high_poly {
            out.push((
                format!("has {} triangles", self.triangles),
                format!("Over {} the bind takes a while. Use Optimize High Res for Heat Maps or reduce the voxel resolution", high_poly),
                false,
            ));
        }
        if self.doubled > 0 {
            out.push((
                format!("has {} doubled vertices", self.doubled),
                "Edit Mode, select all, M > By Distance. Split Parts can help at UV seams; merge unintended duplicate vertices before binding".into(),
                false,
            ));
        }
        if self.degenerate > 0 {
            out.push((
                format!("has {} faces with no area", self.degenerate),
                "Edit Mode, select all, Mesh > Clean Up > Degenerate Dissolve".into(),
                false,
            ));
        }
        if self.volume < 0.0 {
            out.push((
                "has a negative signed surface volume".into(),
                "Check face orientation. For a closed surface this suggests inverted normals; use Shift+N in Edit Mode if needed".into(),
                false,
            ));
        } else if self.inward * 50 > self.triangles {
            out.push((
                format!(
                    "has {} faces ({:.0}%) pointing toward the mesh center",
                    self.inward,
                    100.0 * self.inward as f64 / self.triangles as f64
                ),
                "This can be normal on a concave mesh. Inspect face orientation before changing normals".into(),
                false,
            ));
        }
        if self.boundary > 0 {
            out.push((
                format!("is open - {} edges have only one face", self.boundary),
                "Inspect the open boundaries. Close unintended holes, or use Voxelized if Heat Maps fails".into(),
                false,
            ));
        }
        if self.nonmanifold > 0 {
            out.push((
                format!("has {} non-manifold edges", self.nonmanifold),
                "Edit Mode, Select > All by Trait > Non Manifold to find them. Clean unintended intersections before binding, or try Voxelized".into(),
                false,
            ));
        }
        if self.wire > 0 {
            out.push((
                format!("has {} loose edges with no face", self.wire),
                "Edit Mode, Select > All by Trait > Loose Geometry, then X > Edges. They take weight from wherever they happen to sit and drag behind the model".into(),
                false,
            ));
        }
        if shape_keys {
            out.push((
                "has shape keys".into(),
                "Shape keys are preserved. Apply Shape Keys controls whether binding uses the evaluated shape in Heat Maps".into(),
                false,
            ));
        }
        out
    }
}

/// Measure one mesh from flat `foreach_get` buffers. `merge_distance` is the doubled-vertex
/// tolerance (Blender's Merge by Distance default).
#[pyfunction]
#[pyo3(signature = (vertices, triangles, edges, merge_distance = 0.0001))]
pub fn inspect_mesh(
    vertices: Vec<f64>,
    triangles: Vec<u32>,
    edges: Vec<u32>,
    merge_distance: f64,
) -> PyResult<MeshReport> {
    if vertices.len() % 3 != 0 {
        return Err(PyValueError::new_err("vertices must be 3 values each"));
    }
    if triangles.len() % 3 != 0 {
        return Err(PyValueError::new_err("triangles must be 3 indices each"));
    }
    if edges.len() % 2 != 0 {
        return Err(PyValueError::new_err("edges must be 2 indices each"));
    }
    let count = vertices.len() / 3;
    let mut report = MeshReport {
        vertices: count,
        triangles: triangles.len() / 3,
        ..Default::default()
    };
    if count == 0 || triangles.is_empty() {
        return Ok(report);
    }

    let point = |i: usize| DVec3::new(vertices[i * 3], vertices[i * 3 + 1], vertices[i * 3 + 2]);

    // doubled vertices: hashed onto a lattice of the tolerance size (one pass, not pairwise)
    if merge_distance > 0.0 {
        use std::collections::HashSet;
        let inverse = 1.0 / merge_distance;
        let mut seen: HashSet<(i64, i64, i64)> = HashSet::with_capacity(count);
        for i in 0..count {
            let p = point(i);
            let key = (
                (p.x * inverse).floor() as i64,
                (p.y * inverse).floor() as i64,
                (p.z * inverse).floor() as i64,
            );
            if !seen.insert(key) {
                report.doubled += 1;
            }
        }
    }

    let mut centre = DVec3::ZERO;
    for i in 0..count {
        centre += point(i);
    }
    centre /= count as f64;

    use std::collections::HashMap;
    let mut edge_faces: HashMap<(u32, u32), u32> = HashMap::with_capacity(triangles.len());
    for tri in triangles.chunks_exact(3) {
        if tri.iter().any(|&i| (i as usize) >= count) {
            continue;
        }
        let a = point(tri[0] as usize);
        let b = point(tri[1] as usize);
        let c = point(tri[2] as usize);
        let cross = (b - a).cross(c - a);
        let area = cross.length() * 0.5;
        if area <= 1e-12 {
            report.degenerate += 1;
        }
        // signed tetrahedron volume with the mesh centre: its sign says which way the face looks
        let middle = (a + b + c) / 3.0;
        let signed = cross.normalize_or_zero().dot(middle - centre) * area;
        report.volume += signed;
        if signed < 0.0 {
            report.inward += 1;
        }
        for (u, v) in [(tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])] {
            let key = if u <= v { (u, v) } else { (v, u) };
            *edge_faces.entry(key).or_insert(0) += 1;
        }
    }

    for uses in edge_faces.values() {
        if *uses == 1 {
            report.boundary += 1;
        } else if *uses > 2 {
            report.nonmanifold += 1;
        }
    }
    for pair in edges.chunks_exact(2) {
        let key = if pair[0] <= pair[1] {
            (pair[0], pair[1])
        } else {
            (pair[1], pair[0])
        };
        if !edge_faces.contains_key(&key) {
            report.wire += 1;
        }
    }

    Ok(report)
}
