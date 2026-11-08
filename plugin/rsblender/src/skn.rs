//! `.skn` skinned meshes, handed to Python as f32/u8/u16 byte buffers (zero copy with numpy).
//!
//! Positions and normals are converted to Blender space here. UVs are raw; Python flips V.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use rs_io::Parse;
use rs_mesh::{SkinnedMesh, SkinnedMeshVertexType};

/// One submesh: a contiguous span of the vertex and index buffers. A `.bin` material override
/// keys on its name.
#[pyclass(module = "_rsblender", frozen)]
pub struct Submesh {
    #[pyo3(get)]
    pub name: String,
    #[pyo3(get)]
    pub vertex_start: u32,
    #[pyo3(get)]
    pub vertex_count: u32,
    #[pyo3(get)]
    pub index_start: u32,
    #[pyo3(get)]
    pub index_count: u32,
}

#[pymethods]
impl Submesh {
    fn __repr__(&self) -> String {
        format!(
            "<Submesh {:?} verts {}..{} indices {}..{}>",
            self.name,
            self.vertex_start,
            self.vertex_start + self.vertex_count,
            self.index_start,
            self.index_start + self.index_count
        )
    }
}

#[pyclass(module = "_rsblender")]
pub struct Mesh {
    #[pyo3(get)]
    pub major: u16,
    #[pyo3(get)]
    pub minor: u16,
    /// "Basic", "Color" or "Tangent" - which optional per-vertex streams the file carries.
    #[pyo3(get)]
    pub vertex_type: String,
    /// The v4 header flags word, written back as read. 0 on older versions.
    #[pyo3(get)]
    pub flags: u32,
    #[pyo3(get)]
    pub vertex_count: usize,
    #[pyo3(get)]
    pub index_count: usize,
    #[pyo3(get)]
    pub submeshes: Vec<Py<Submesh>>,

    /// f32 xyz per vertex, already in Blender space.
    #[pyo3(get)]
    pub positions: Py<PyBytes>,
    /// f32 xyz per vertex, Blender space, NOT renormalised - see `normal_length_error`.
    #[pyo3(get)]
    pub normals: Py<PyBytes>,
    /// f32 uv per vertex, raw (V is flipped in Python).
    #[pyo3(get)]
    pub uvs: Py<PyBytes>,
    /// u8 rgba per vertex, or None for the Basic layout.
    #[pyo3(get)]
    pub colors: Option<Py<PyBytes>>,
    /// u8, 4 per vertex, indices into the skeleton's influence list (not its joint list).
    #[pyo3(get)]
    pub blend_indices: Py<PyBytes>,
    /// f32, 4 per vertex, as stored. Not renormalised - see `weight_sum_error`.
    #[pyo3(get)]
    pub blend_weights: Py<PyBytes>,
    /// u16 triangle indices.
    #[pyo3(get)]
    pub indices: Py<PyBytes>,
    /// CPU indices, resolving the v4 range-relative flag in 32 bits.
    #[pyo3(get)]
    pub absolute_indices: Py<PyBytes>,
    /// Influence-table indices after per-range palettes, four u16 per vertex.
    #[pyo3(get)]
    pub resolved_blend_indices: Py<PyBytes>,

    /// Largest |sum(weights) - 1|. Reported, not corrected.
    #[pyo3(get)]
    pub weight_sum_error: f32,
    /// Largest |len(normal) - 1| over all vertices; 0 when every normal is already unit.
    #[pyo3(get)]
    pub normal_length_error: f32,
    /// Highest influence index used with a non-zero weight, to check against the skeleton.
    #[pyo3(get)]
    pub max_blend_index: u32,
    /// True when a vertex has all four weights at zero.
    #[pyo3(get)]
    pub has_unweighted_vertices: bool,

    /// The bytes after the vertex buffer (the v4 12-byte end tab), handed back on export.
    #[pyo3(get)]
    pub trailing: Py<PyBytes>,

    /// The file's bounds: `(min xyz, max xyz)` and `(centre xyz, radius)`, League space. Kept
    /// because Riot's bounding sphere cannot be recomputed exactly.
    #[pyo3(get)]
    pub bounding_box: (f32, f32, f32, f32, f32, f32),
    #[pyo3(get)]
    pub bounding_sphere: (f32, f32, f32, f32),
}

#[pymethods]
impl Mesh {
    #[staticmethod]
    fn from_path(path: &str) -> PyResult<Self> {
        let skn = SkinnedMesh::from_path(path)
            .map_err(|e| PyValueError::new_err(format!("{path}: {e}")))?;
        Python::attach(|py| build(py, &skn))
    }

    #[getter]
    fn triangle_count(&self) -> usize {
        self.index_count / 3
    }

    fn __repr__(&self) -> String {
        format!(
            "<Mesh v{}.{} type={} verts={} tris={} submeshes={}>",
            self.major,
            self.minor,
            self.vertex_type,
            self.vertex_count,
            self.index_count / 3,
            self.submeshes.len()
        )
    }
}

fn build(py: Python<'_>, skn: &SkinnedMesh) -> PyResult<Mesh> {
    let verts = skn.vertices();
    let n = verts.len();
    let (absolute, resolved) = resolve_indices(skn)?;

    let mut positions = Vec::with_capacity(n * 12);
    let mut normals = Vec::with_capacity(n * 12);
    let mut uvs = Vec::with_capacity(n * 8);
    let mut blend_indices = Vec::with_capacity(n * 4);
    let mut blend_weights = Vec::with_capacity(n * 16);
    let mut colors: Option<Vec<u8>> = match skn.vertex_type {
        SkinnedMeshVertexType::Basic => None,
        _ => Some(Vec::with_capacity(n * 4)),
    };

    let mut weight_sum_error = 0.0f32;
    let mut normal_length_error = 0.0f32;
    let mut max_blend_index = 0u32;
    let mut has_unweighted = false;

    for (vertex_index, v) in verts.iter().enumerate() {
        // League -> Blender: (x, y, z) -> (-x, -z, y), for positions and normals
        for c in [-v.position.x, -v.position.z, v.position.y] {
            positions.extend_from_slice(&c.to_le_bytes());
        }
        for c in [-v.normal.x, -v.normal.z, v.normal.y] {
            normals.extend_from_slice(&c.to_le_bytes());
        }

        let len =
            (v.normal.x * v.normal.x + v.normal.y * v.normal.y + v.normal.z * v.normal.z).sqrt();
        normal_length_error = normal_length_error.max((len - 1.0).abs());

        uvs.extend_from_slice(&v.uv.x.to_le_bytes());
        uvs.extend_from_slice(&v.uv.y.to_le_bytes());

        let mut sum = 0.0f32;
        for k in 0..4 {
            let w = v.blend_weights[k];
            sum += w;
            blend_weights.extend_from_slice(&w.to_le_bytes());
            blend_indices.push(v.blend_indices[k]);
            if w > 0.0 {
                max_blend_index = max_blend_index.max(resolved[vertex_index * 4 + k] as u32);
            }
        }
        weight_sum_error = weight_sum_error.max((sum - 1.0).abs());
        if sum <= 0.0 {
            has_unweighted = true;
        }

        if let Some(buf) = colors.as_mut() {
            // a missing colour becomes opaque white
            buf.extend_from_slice(&v.color.unwrap_or([255, 255, 255, 255]));
        }
    }

    let mut submeshes = Vec::with_capacity(skn.ranges().len());
    for r in skn.ranges() {
        submeshes.push(Py::new(
            py,
            Submesh {
                name: r.name.clone(),
                vertex_start: r.vertex_start,
                vertex_count: r.vertex_count,
                index_start: r.index_start,
                index_count: r.index_count,
            },
        )?);
    }

    let mut index_bytes = Vec::with_capacity(skn.indices().len() * 2);
    for i in skn.indices() {
        index_bytes.extend_from_slice(&i.to_le_bytes());
    }

    let vertex_type = match skn.vertex_type {
        SkinnedMeshVertexType::Basic => "Basic",
        SkinnedMeshVertexType::Color => "Color",
        SkinnedMeshVertexType::Tangent => "Tangent",
    }
    .to_string();

    Ok(Mesh {
        absolute_indices: PyBytes::new(
            py,
            &absolute
                .iter()
                .flat_map(|v| v.to_le_bytes())
                .collect::<Vec<_>>(),
        )
        .unbind(),
        resolved_blend_indices: PyBytes::new(
            py,
            &resolved
                .iter()
                .flat_map(|v| v.to_le_bytes())
                .collect::<Vec<_>>(),
        )
        .unbind(),
        major: skn.major,
        minor: skn.minor,
        vertex_type,
        flags: skn.flags,
        vertex_count: n,
        index_count: skn.indices().len(),
        submeshes,
        positions: PyBytes::new(py, &positions).unbind(),
        normals: PyBytes::new(py, &normals).unbind(),
        uvs: PyBytes::new(py, &uvs).unbind(),
        colors: colors.map(|b| PyBytes::new(py, &b).unbind()),
        blend_indices: PyBytes::new(py, &blend_indices).unbind(),
        blend_weights: PyBytes::new(py, &blend_weights).unbind(),
        indices: PyBytes::new(py, &index_bytes).unbind(),
        weight_sum_error,
        normal_length_error,
        max_blend_index,
        has_unweighted_vertices: has_unweighted,
        trailing: PyBytes::new(py, &skn.trailing).unbind(),
        bounding_box: (
            skn.bounding_box.min.x,
            skn.bounding_box.min.y,
            skn.bounding_box.min.z,
            skn.bounding_box.max.x,
            skn.bounding_box.max.y,
            skn.bounding_box.max.z,
        ),
        bounding_sphere: (
            skn.bounding_sphere.center.x,
            skn.bounding_sphere.center.y,
            skn.bounding_sphere.center.z,
            skn.bounding_sphere.radius,
        ),
    })
}

/// Resolve the two v4 flags before exposing geometry. The flag-1 block holds (range u7 +
/// terminal bit, count u8, influence u16[count]) records; only ranges with a palette remap.
pub(crate) fn resolve_indices(skn: &SkinnedMesh) -> PyResult<(Vec<u32>, Vec<u16>)> {
    let invalid = |message: String| PyValueError::new_err(format!("Invalid SKN: {message}"));
    for (index, vertex) in skn.vertices.iter().enumerate() {
        if !vertex.position.is_finite()
            || !vertex.normal.is_finite()
            || !vertex.uv.is_finite()
            || vertex
                .blend_weights
                .iter()
                .any(|w| !w.is_finite() || *w < 0.0)
        {
            return Err(invalid(format!(
                "vertex {index} contains non-finite geometry or invalid weights"
            )));
        }
    }
    let mut palettes = std::collections::HashMap::<usize, Vec<u16>>::new();
    let block = &skn.skinning_block;
    let mut offset = 0;
    while offset < block.len() {
        if offset + 2 > block.len() {
            return Err(invalid("truncated skinning palette".into()));
        }
        let tag = block[offset];
        let range = (tag & 127) as usize;
        let count = block[offset + 1] as usize;
        offset += 2;
        if range >= skn.ranges.len() || count == 0 || offset + count * 2 > block.len() {
            return Err(invalid("invalid skinning palette range or length".into()));
        }
        let palette = block[offset..offset + count * 2]
            .chunks_exact(2)
            .map(|v| u16::from_le_bytes([v[0], v[1]]))
            .collect();
        if palettes.insert(range, palette).is_some() {
            return Err(invalid("duplicate skinning palette".into()));
        }
        offset += count * 2;
        if (tag & 128 != 0) != (offset == block.len()) {
            return Err(invalid("invalid skinning palette terminator".into()));
        }
    }
    let mut indices: Vec<u32> = skn.indices.iter().map(|&v| v as u32).collect();
    let mut blends: Vec<u16> = skn
        .vertices
        .iter()
        .flat_map(|v| v.blend_indices.map(u16::from))
        .collect();
    let mut covered = vec![false; indices.len()];
    for (order, r) in skn.ranges.iter().enumerate() {
        let v0 = r.vertex_start as usize;
        let v1 = v0
            .checked_add(r.vertex_count as usize)
            .ok_or_else(|| invalid("vertex range overflow".into()))?;
        let i0 = r.index_start as usize;
        let i1 = i0
            .checked_add(r.index_count as usize)
            .ok_or_else(|| invalid("index range overflow".into()))?;
        if v1 > skn.vertices.len() || i1 > indices.len() || i0 % 3 != 0 || r.index_count % 3 != 0 {
            return Err(invalid(format!(
                "submesh {:?} has invalid buffer ranges",
                r.name
            )));
        }
        for i in i0..i1 {
            if covered[i] {
                return Err(invalid("overlapping triangle ranges".into()));
            }
            covered[i] = true;
            if skn.flags & 2 != 0 {
                indices[i] += r.vertex_start;
            }
            if indices[i] < r.vertex_start || indices[i] as usize >= v1 {
                return Err(invalid(format!(
                    "submesh {:?}: vertex index {} outside {}..{}",
                    r.name, indices[i], v0, v1
                )));
            }
        }
        if let Some(palette) = palettes.get(&order) {
            for v in v0..v1 {
                for k in 0..4 {
                    let raw = skn.vertices[v].blend_indices[k] as usize;
                    blends[v * 4 + k] = match palette.get(raw) {
                        Some(&value) => value,
                        None if skn.vertices[v].blend_weights[k] == 0.0 => 0,
                        None => {
                            return Err(invalid(format!(
                                "submesh {:?}: weight references missing palette entry {raw}",
                                r.name
                            )))
                        }
                    };
                }
            }
        }
    }
    if covered.iter().any(|&v| !v) {
        return Err(invalid("triangles have no material range".into()));
    }
    Ok((indices, blends))
}

/// Encode global u16 influence indices into the SKN per-range u8 representation.
/// Ranges that exceed u8 receive a palette. Geometry remains in the caller's order.
#[pyfunction]
pub fn pack_skin_weights(
    py: Python<'_>,
    ranges: Vec<(u32, u32)>,
    influences: &[u8],
) -> PyResult<(Py<PyBytes>, Py<PyBytes>)> {
    if influences.len() % 8 != 0 {
        return Err(PyValueError::new_err(
            "expected four u16 influences per vertex",
        ));
    }
    let values: Vec<u16> = influences
        .chunks_exact(2)
        .map(|v| u16::from_le_bytes([v[0], v[1]]))
        .collect();
    let mut packed = vec![0u8; values.len()];
    let mut block = Vec::new();
    let mut last_tag = None;
    for (order, (start, count)) in ranges.iter().enumerate() {
        let begin = *start as usize * 4;
        let end = begin + *count as usize * 4;
        let slice = values
            .get(begin..end)
            .ok_or_else(|| PyValueError::new_err("invalid skinning vertex range"))?;
        if slice.iter().all(|&v| v <= 255) {
            for (dst, &v) in packed[begin..end].iter_mut().zip(slice) {
                *dst = v as u8;
            }
        } else {
            let mut palette = Vec::<u16>::new();
            for (dst, &v) in packed[begin..end].iter_mut().zip(slice) {
                let index = match palette.iter().position(|&p| p == v) {
                    Some(index) => index,
                    None => {
                        palette.push(v);
                        palette.len() - 1
                    }
                };
                if palette.len() > 255 {
                    return Err(PyValueError::new_err("a material uses more than 255 palette entries; split it into smaller materials before export"));
                }
                *dst = index as u8;
            }
            if order > 127 {
                return Err(PyValueError::new_err("skinning palettes support material indices 0..127; reduce material count before export"));
            }
            last_tag = Some(block.len());
            block.push(order as u8);
            block.push(palette.len() as u8);
            for v in palette {
                block.extend_from_slice(&v.to_le_bytes());
            }
        }
    }
    if let Some(last) = last_tag {
        block[last] |= 128;
    }
    Ok((
        PyBytes::new(py, &packed).unbind(),
        PyBytes::new(py, &block).unbind(),
    ))
}
