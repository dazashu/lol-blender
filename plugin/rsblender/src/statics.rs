//! `.sco` (text) and `.scb` (binary) static meshes: positions, triangles, a material name per
//! face and per-corner UVs. The `.sco` writer is ported from lol_maya, field for field.

use pyo3::exceptions::{PyIOError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyBytes;

use rs_io::{Parse, Serialize};
use rs_math::{Vec2, Vec3};
use rs_mesh::{StaticMesh as RsStaticMesh, StaticMeshFace};

/// A `.sco` / `.scb` static mesh, handed to Python as flat buffers.
#[pyclass(module = "_rsblender")]
pub struct StaticMesh {
    #[pyo3(get)]
    pub name: String,
    #[pyo3(get)]
    pub vertex_count: usize,
    #[pyo3(get)]
    pub face_count: usize,
    /// f32 xyz per vertex, already in BLENDER space.
    #[pyo3(get)]
    pub positions: Py<PyBytes>,
    /// u32 x3 per face, into `positions`.
    #[pyo3(get)]
    pub indices: Py<PyBytes>,
    /// f32 uv per corner (three per face).
    #[pyo3(get)]
    pub uvs: Py<PyBytes>,
    /// One material name per face.
    #[pyo3(get)]
    pub materials: Vec<String>,
    /// The central point, in Blender space like `positions`.
    #[pyo3(get)]
    pub central: (f32, f32, f32),
    /// The file's own bounding box, LEAGUE space, `(min xyz, max xyz)`.
    #[pyo3(get)]
    pub bounding_box: (f32, f32, f32, f32, f32, f32),
    #[pyo3(get)]
    pub is_text: bool,

    /// Container fields geometry does not describe, passed back as is on export.
    #[pyo3(get)]
    pub version: (u16, u16),
    #[pyo3(get)]
    pub flags: u32,
    #[pyo3(get)]
    pub vertex_type: Option<u32>,
    #[pyo3(get)]
    pub colors: Option<Py<PyBytes>>,
    #[pyo3(get)]
    pub trailing: Py<PyBytes>,
}

fn to_blender(v: Vec3) -> [f32; 3] {
    [-v.x, -v.z, v.y]
}

fn to_league(x: f32, y: f32, z: f32) -> Vec3 {
    Vec3::new(-x, z, -y)
}

#[pymethods]
impl StaticMesh {
    #[staticmethod]
    fn from_path(py: Python<'_>, path: &str) -> PyResult<Self> {
        let bytes = std::fs::read(path).map_err(|e| PyIOError::new_err(format!("{path}: {e}")))?;
        let text = path.to_ascii_lowercase().ends_with(".sco");
        let mesh = if text {
            let source = String::from_utf8_lossy(&bytes);
            RsStaticMesh::from_sco_str(&source)
                .map_err(|e| PyValueError::new_err(format!("{path}: {e}")))?
        } else {
            RsStaticMesh::from_bytes(&bytes)
                .map_err(|e| PyValueError::new_err(format!("{path}: {e}")))?
        };

        let mut positions = Vec::with_capacity(mesh.positions.len() * 12);
        for p in &mesh.positions {
            for c in to_blender(*p) {
                positions.extend_from_slice(&c.to_le_bytes());
            }
        }

        let mut indices = Vec::with_capacity(mesh.faces.len() * 12);
        let mut uvs = Vec::with_capacity(mesh.faces.len() * 24);
        let mut materials = Vec::with_capacity(mesh.faces.len());
        for face in &mesh.faces {
            for i in face.indices {
                indices.extend_from_slice(&i.to_le_bytes());
            }
            for uv in face.uvs {
                uvs.extend_from_slice(&uv.x.to_le_bytes());
                uvs.extend_from_slice(&uv.y.to_le_bytes());
            }
            materials.push(face.material.clone());
        }

        Ok(Self {
            name: mesh.name.clone(),
            vertex_count: mesh.positions.len(),
            face_count: mesh.faces.len(),
            positions: PyBytes::new(py, &positions).unbind(),
            indices: PyBytes::new(py, &indices).unbind(),
            uvs: PyBytes::new(py, &uvs).unbind(),
            materials,
            central: {
                let c = to_blender(mesh.central);
                (c[0], c[1], c[2])
            },
            bounding_box: (
                mesh.bounding_box.min.x, mesh.bounding_box.min.y, mesh.bounding_box.min.z,
                mesh.bounding_box.max.x, mesh.bounding_box.max.y, mesh.bounding_box.max.z,
            ),
            is_text: text,
            version: mesh.version,
            flags: mesh.flags,
            vertex_type: mesh.vertex_type,
            colors: mesh.colors.as_ref().map(|c| {
                let mut bytes = Vec::with_capacity(c.len() * 4);
                for rgba in c {
                    bytes.extend_from_slice(rgba);
                }
                PyBytes::new(py, &bytes).unbind()
            }),
            trailing: PyBytes::new(py, &mesh.trailing).unbind(),
        })
    }
}

/// Write a `.sco` or `.scb`, chosen by the path's extension.
///
/// `positions` are BLENDER-space f32 xyz, `indices` u32 triples, `uvs` two f32 per corner, and
/// `materials` one name per face.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
#[pyo3(signature = (
    path, name, positions, indices, uvs, materials, central=None, bounds=None,
    version=None, flags=0, vertex_type=None, colors=None, trailing=None
))]
pub fn write_static_mesh(
    path: &str,
    name: &str,
    positions: &[u8],
    indices: &[u8],
    uvs: &[u8],
    materials: Vec<String>,
    central: Option<(f32, f32, f32)>,
    bounds: Option<(f32, f32, f32, f32, f32, f32)>,
    version: Option<(u16, u16)>,
    flags: u32,
    vertex_type: Option<u32>,
    colors: Option<&[u8]>,
    trailing: Option<&[u8]>,
) -> PyResult<usize> {
    let vertex_count = positions.len() / 12;
    let face_count = indices.len() / 12;
    if indices.len() % 12 != 0 {
        return Err(PyValueError::new_err(
            "indices must be three u32 per face".to_string(),
        ));
    }
    if uvs.len() != face_count * 24 {
        return Err(PyValueError::new_err(format!(
            "uvs: {} bytes for {face_count} faces, expected {} - a static mesh stores a uv per \
             CORNER, not per vertex",
            uvs.len(),
            face_count * 24
        )));
    }
    if materials.len() != face_count {
        return Err(PyValueError::new_err(format!(
            "materials: {} names for {face_count} faces",
            materials.len()
        )));
    }

    let f32_at = |buf: &[u8], i: usize| f32::from_le_bytes(buf[i * 4..i * 4 + 4].try_into().unwrap());
    let u32_at = |buf: &[u8], i: usize| u32::from_le_bytes(buf[i * 4..i * 4 + 4].try_into().unwrap());

    let league_positions: Vec<Vec3> = (0..vertex_count)
        .map(|v| {
            to_league(
                f32_at(positions, v * 3),
                f32_at(positions, v * 3 + 1),
                f32_at(positions, v * 3 + 2),
            )
        })
        .collect();

    let mut min = Vec3::splat(f32::INFINITY);
    let mut max = Vec3::splat(f32::NEG_INFINITY);
    for p in &league_positions {
        min = min.min(*p);
        max = max.max(*p);
    }
    if league_positions.is_empty() {
        min = Vec3::ZERO;
        max = Vec3::ZERO;
    }

    let faces: Vec<StaticMeshFace> = (0..face_count)
        .map(|f| StaticMeshFace {
            material: materials[f].clone(),
            indices: [u32_at(indices, f * 3), u32_at(indices, f * 3 + 1), u32_at(indices, f * 3 + 2)],
            uvs: [
                Vec2::new(f32_at(uvs, f * 6), f32_at(uvs, f * 6 + 1)),
                Vec2::new(f32_at(uvs, f * 6 + 2), f32_at(uvs, f * 6 + 3)),
                Vec2::new(f32_at(uvs, f * 6 + 4), f32_at(uvs, f * 6 + 5)),
            ],
        })
        .collect();

    let (box_min, box_max) = match bounds {
        // the file's own box when given (recomputing could differ in the last bit)
        Some((a, b, c, d, e, f)) => (Vec3::new(a, b, c), Vec3::new(d, e, f)),
        None => (min, max),
    };

    let mesh = RsStaticMesh {
        name: name.to_string(),
        // (0, 0) lets the writer pick the version; a real `.scb` passes its own
        version: version.unwrap_or((0, 0)),
        flags,
        bounding_box: rs_math::Aabb::new(box_min, box_max),
        vertex_type,
        central: match central {
            Some((x, y, z)) => to_league(x, y, z),
            None => (min + max) * 0.5,
        },
        positions: league_positions,
        colors: colors.map(|c| {
            c.chunks_exact(4)
                .map(|p| [p[0], p[1], p[2], p[3]])
                .collect()
        }),
        faces,
        trailing: trailing.map(|t| t.to_vec()).unwrap_or_default(),
    };

    if path.to_ascii_lowercase().ends_with(".sco") {
        let text = mesh.to_sco_string();
        std::fs::write(path, &text).map_err(|e| PyIOError::new_err(format!("{path}: {e}")))?;
        Ok(text.len())
    } else {
        let bytes = mesh
            .to_bytes()
            .map_err(|e| PyValueError::new_err(format!("{path}: {e}")))?;
        let written = bytes.len();
        std::fs::write(path, bytes).map_err(|e| PyIOError::new_err(format!("{path}: {e}")))?;
        Ok(written)
    }
}
