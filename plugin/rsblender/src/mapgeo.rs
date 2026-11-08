//! `.mapgeo` map geometry: read, and write through a donor.
//!
//! Scene graphs and planar reflectors cannot be rebuilt from Blender, so the writer starts from
//! the original file and re-encodes only edited models. Untouched models keep their bytes.

use std::collections::HashMap;

use pyo3::exceptions::{PyIOError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyBytes;

use rs_io::Serialize;
use rs_mapgeo::{
    ElementFormat, ElementName, IndexBuffer, MapGeometry, VertexBuffer, VertexDescription,
};
use rs_math::Vec3;

use crate::mat;

/// League to Blender, for a point: `(x, y, z) -> (-x, -z, y)`.
fn to_blender(v: Vec3) -> [f32; 3] {
    [-v.x, -v.z, v.y]
}

fn to_league(x: f32, y: f32, z: f32) -> Vec3 {
    Vec3::new(-x, z, -y)
}

fn read_f32(buffer: &[u8], at: usize) -> f32 {
    f32::from_le_bytes(buffer[at..at + 4].try_into().unwrap())
}

/// Decode one element out of a packed vertex, as up to four floats.
fn decode_element(buffer: &[u8], at: usize, format: ElementFormat) -> [f32; 4] {
    match format {
        ElementFormat::XFloat32 => [read_f32(buffer, at), 0.0, 0.0, 0.0],
        ElementFormat::XyFloat32 => [read_f32(buffer, at), read_f32(buffer, at + 4), 0.0, 0.0],
        ElementFormat::XyzFloat32 => [
            read_f32(buffer, at),
            read_f32(buffer, at + 4),
            read_f32(buffer, at + 8),
            0.0,
        ],
        ElementFormat::XyzwFloat32 => [
            read_f32(buffer, at),
            read_f32(buffer, at + 4),
            read_f32(buffer, at + 8),
            read_f32(buffer, at + 12),
        ],
        // the three packed 8888 formats differ only in channel order
        ElementFormat::BgraPacked8888 | ElementFormat::ZyxwPacked8888 => {
            let b = &buffer[at..at + 4];
            [
                b[2] as f32 / 255.0,
                b[1] as f32 / 255.0,
                b[0] as f32 / 255.0,
                b[3] as f32 / 255.0,
            ]
        }
        ElementFormat::RgbaPacked8888 => {
            let b = &buffer[at..at + 4];
            [
                b[0] as f32 / 255.0,
                b[1] as f32 / 255.0,
                b[2] as f32 / 255.0,
                b[3] as f32 / 255.0,
            ]
        }
        ElementFormat::XyPacked1616 => {
            let x = u16::from_le_bytes(buffer[at..at + 2].try_into().unwrap());
            let y = u16::from_le_bytes(buffer[at + 2..at + 4].try_into().unwrap());
            [x as f32 / 65535.0, y as f32 / 65535.0, 0.0, 0.0]
        }
        ElementFormat::XyzPacked161616 => {
            let mut out = [0.0f32; 4];
            for (i, slot) in out.iter_mut().take(3).enumerate() {
                let v = u16::from_le_bytes(buffer[at + i * 2..at + i * 2 + 2].try_into().unwrap());
                *slot = v as f32 / 65535.0;
            }
            out
        }
        ElementFormat::XyzwPacked16161616 => {
            let mut out = [0.0f32; 4];
            for (i, slot) in out.iter_mut().enumerate() {
                let v = u16::from_le_bytes(buffer[at + i * 2..at + i * 2 + 2].try_into().unwrap());
                *slot = v as f32 / 65535.0;
            }
            out
        }
    }
}

/// Write four floats back into a packed vertex, the inverse of `decode_element`.
fn encode_element(buffer: &mut [u8], at: usize, format: ElementFormat, value: [f32; 4]) {
    let put = |buffer: &mut [u8], offset: usize, v: f32| {
        buffer[offset..offset + 4].copy_from_slice(&v.to_le_bytes());
    };
    let byte = |v: f32| (v.clamp(0.0, 1.0) * 255.0).round() as u8;
    let short = |v: f32| ((v.clamp(0.0, 1.0) * 65535.0).round() as u16).to_le_bytes();
    match format {
        ElementFormat::XFloat32 => put(buffer, at, value[0]),
        ElementFormat::XyFloat32 => {
            put(buffer, at, value[0]);
            put(buffer, at + 4, value[1]);
        }
        ElementFormat::XyzFloat32 => {
            put(buffer, at, value[0]);
            put(buffer, at + 4, value[1]);
            put(buffer, at + 8, value[2]);
        }
        ElementFormat::XyzwFloat32 => {
            for i in 0..4 {
                put(buffer, at + i * 4, value[i]);
            }
        }
        ElementFormat::BgraPacked8888 | ElementFormat::ZyxwPacked8888 => {
            buffer[at] = byte(value[2]);
            buffer[at + 1] = byte(value[1]);
            buffer[at + 2] = byte(value[0]);
            buffer[at + 3] = byte(value[3]);
        }
        ElementFormat::RgbaPacked8888 => {
            for i in 0..4 {
                buffer[at + i] = byte(value[i]);
            }
        }
        ElementFormat::XyPacked1616 => {
            buffer[at..at + 2].copy_from_slice(&short(value[0]));
            buffer[at + 2..at + 4].copy_from_slice(&short(value[1]));
        }
        ElementFormat::XyzPacked161616 => {
            for i in 0..3 {
                buffer[at + i * 2..at + i * 2 + 2].copy_from_slice(&short(value[i]));
            }
        }
        ElementFormat::XyzwPacked16161616 => {
            for i in 0..4 {
                buffer[at + i * 2..at + i * 2 + 2].copy_from_slice(&short(value[i]));
            }
        }
    }
}

/// Byte offset of an element inside its packed vertex, and its format.
fn locate(description: &VertexDescription, wanted: ElementName) -> Option<(usize, ElementFormat)> {
    let mut offset = 0usize;
    for element in &description.elements {
        if element.name == wanted {
            return Some((offset, element.format));
        }
        offset += element.format.byte_size();
    }
    None
}

/// One placed model, decoded into the flat buffers Blender's `foreach_set` wants.
#[pyclass(module = "_rsblender")]
pub struct MapModel {
    #[pyo3(get)]
    pub name: String,
    #[pyo3(get)]
    pub vertex_count: usize,
    #[pyo3(get)]
    pub index_count: usize,
    /// f32 xyz per vertex, BLENDER space, in the model's own local frame.
    #[pyo3(get)]
    pub positions: Py<PyBytes>,
    #[pyo3(get)]
    pub normals: Option<Py<PyBytes>>,
    /// The diffuse UV set, already V-flipped for Blender.
    #[pyo3(get)]
    pub uvs: Option<Py<PyBytes>>,
    /// The lightmap UV set, when the model has one.
    #[pyo3(get)]
    pub uvs2: Option<Py<PyBytes>>,
    /// f32 rgba per vertex, 0..1.
    #[pyo3(get)]
    pub colors: Option<Py<PyBytes>>,
    /// u32 triples, already wound for Blender (the conversion mirrors, so the winding flips).
    #[pyo3(get)]
    pub indices: Py<PyBytes>,
    /// `(name, index_start, index_count)` per draw range.
    #[pyo3(get)]
    pub submeshes: Vec<(String, u32, u32)>,
    /// Row-major 4x4, BLENDER space.
    #[pyo3(get)]
    pub transform: Vec<f64>,
    #[pyo3(get)]
    pub layer: u8,
    #[pyo3(get)]
    pub disable_backface_culling: bool,
    #[pyo3(get)]
    pub render_flags: u16,
    #[pyo3(get)]
    pub quality: u8,
    /// `(sampler index, texture path)` per override.
    #[pyo3(get)]
    pub texture_overrides: Vec<(u32, String)>,
    #[pyo3(get)]
    pub baked_light: String,
    #[pyo3(get)]
    pub stationary_light: String,
    /// True when another model shares this vertex buffer (an instance); its geometry cannot be
    /// edited.
    #[pyo3(get)]
    pub shared: bool,
}

/// A parsed `.mapgeo`.
#[pyclass(module = "_rsblender")]
pub struct MapGeo {
    #[pyo3(get)]
    pub version: u32,
    #[pyo3(get)]
    pub models: Vec<Py<MapModel>>,
    /// Every texture path the file names at the top level, in order.
    #[pyo3(get)]
    pub texture_overrides: Vec<(u32, String)>,
    #[pyo3(get)]
    pub scene_graph_count: usize,
    #[pyo3(get)]
    pub planar_reflector_count: usize,
}

/// The versions the crate can write.
const WRITABLE_VERSIONS: [u32; 11] = [5, 6, 7, 9, 11, 12, 13, 14, 15, 17, 18];

fn read_geometry(path: &str) -> PyResult<MapGeometry> {
    let bytes = std::fs::read(path).map_err(|e| PyIOError::new_err(format!("{path}: {e}")))?;
    let mut cursor = std::io::Cursor::new(bytes);
    MapGeometry::from_reader(&mut cursor).map_err(|e| PyValueError::new_err(format!("{path}: {e}")))
}

#[pymethods]
impl MapGeo {
    #[staticmethod]
    fn from_path(py: Python<'_>, path: &str) -> PyResult<Self> {
        let geometry = read_geometry(path)?;
        Self::build(py, &geometry)
    }

    /// The versions `write_map_geometry` accepts.
    #[staticmethod]
    fn writable_versions() -> Vec<u32> {
        WRITABLE_VERSIONS.to_vec()
    }
}

impl MapGeo {
    fn build(py: Python<'_>, geometry: &MapGeometry) -> PyResult<Self> {
        let mut uses: HashMap<i32, usize> = HashMap::new();
        for model in &geometry.models {
            for &id in &model.vertex_buffer_ids {
                *uses.entry(id).or_default() += 1;
            }
        }

        let mut models = Vec::with_capacity(geometry.models.len());
        for (index, model) in geometry.models.iter().enumerate() {
            let count = model.vertex_count as usize;

            let mut positions = Vec::with_capacity(count * 12);
            let mut normals: Option<Vec<u8>> = None;
            let mut uvs: Option<Vec<u8>> = None;
            let mut uvs2: Option<Vec<u8>> = None;
            let mut colors: Option<Vec<u8>> = None;

            for (stream, &buffer_id) in model.vertex_buffer_ids.iter().enumerate() {
                let buffer = geometry
                    .vertex_buffers
                    .get(buffer_id as usize)
                    .ok_or_else(|| {
                        PyValueError::new_err(format!(
                            "model {index} references vertex buffer {buffer_id}, which is not in \
                             the file"
                        ))
                    })?;
                let description = geometry
                    .vertex_descriptions
                    .get(model.vertex_description_id as usize + stream)
                    .ok_or_else(|| {
                        PyValueError::new_err(format!(
                            "model {index} stream {stream} has no vertex declaration"
                        ))
                    })?;
                let stride = description.vertex_size();
                if stride == 0 || buffer.data.len() < count * stride {
                    return Err(PyValueError::new_err(format!(
                        "model {index} stream {stream}: {} bytes for {count} vertices of {stride}",
                        buffer.data.len()
                    )));
                }

                if let Some((offset, format)) = locate(description, ElementName::Position) {
                    for v in 0..count {
                        let value = decode_element(&buffer.data, v * stride + offset, format);
                        for c in to_blender(Vec3::new(value[0], value[1], value[2])) {
                            positions.extend_from_slice(&c.to_le_bytes());
                        }
                    }
                }
                if let Some((offset, format)) = locate(description, ElementName::Normal) {
                    let out = normals.get_or_insert_with(|| Vec::with_capacity(count * 12));
                    for v in 0..count {
                        let value = decode_element(&buffer.data, v * stride + offset, format);
                        for c in to_blender(Vec3::new(value[0], value[1], value[2])) {
                            out.extend_from_slice(&c.to_le_bytes());
                        }
                    }
                }
                if let Some((offset, format)) = locate(description, ElementName::Texcoord0) {
                    let out = uvs.get_or_insert_with(|| Vec::with_capacity(count * 8));
                    for v in 0..count {
                        let value = decode_element(&buffer.data, v * stride + offset, format);
                        out.extend_from_slice(&value[0].to_le_bytes());
                        // V flip, as for .skn
                        out.extend_from_slice(&(1.0 - value[1]).to_le_bytes());
                    }
                }
                if let Some((offset, format)) = locate(description, ElementName::Texcoord1) {
                    let out = uvs2.get_or_insert_with(|| Vec::with_capacity(count * 8));
                    for v in 0..count {
                        let value = decode_element(&buffer.data, v * stride + offset, format);
                        out.extend_from_slice(&value[0].to_le_bytes());
                        out.extend_from_slice(&(1.0 - value[1]).to_le_bytes());
                    }
                }
                if let Some((offset, format)) = locate(description, ElementName::PrimaryColor) {
                    let out = colors.get_or_insert_with(|| Vec::with_capacity(count * 16));
                    for v in 0..count {
                        let value = decode_element(&buffer.data, v * stride + offset, format);
                        for c in value {
                            out.extend_from_slice(&c.to_le_bytes());
                        }
                    }
                }
            }

            if positions.len() != count * 12 {
                return Err(PyValueError::new_err(format!(
                    "model {index} has no position element in its vertex declaration"
                )));
            }

            let empty = IndexBuffer {
                layer: 0,
                indices: Vec::new(),
            };
            let index_buffer = geometry
                .index_buffers
                .get(model.index_buffer_id as usize)
                .unwrap_or(&empty);
            let take = (model.index_count as usize).min(index_buffer.indices.len());
            let mut indices = Vec::with_capacity(take * 4);
            for triangle in index_buffer.indices[..take].chunks_exact(3) {
                // the axis change mirrors, so reverse the winding
                for i in [triangle[0], triangle[2], triangle[1]] {
                    indices.extend_from_slice(&(i as u32).to_le_bytes());
                }
            }

            // column-major on both sides: no transpose
            let league = glam::DMat4::from_cols_array(
                &model.transform.to_cols_array().map(|v| v as f64),
            );
            let transform = mat::riot_to_blender(&league);

            models.push(Py::new(
                py,
                MapModel {
                    name: if model.name.is_empty() {
                        format!("MapGeo_Instance_{index}")
                    } else {
                        model.name.clone()
                    },
                    vertex_count: count,
                    index_count: take,
                    positions: PyBytes::new(py, &positions).unbind(),
                    normals: normals.map(|n| PyBytes::new(py, &n).unbind()),
                    uvs: uvs.map(|n| PyBytes::new(py, &n).unbind()),
                    uvs2: uvs2.map(|n| PyBytes::new(py, &n).unbind()),
                    colors: colors.map(|n| PyBytes::new(py, &n).unbind()),
                    indices: PyBytes::new(py, &indices).unbind(),
                    submeshes: model
                        .submeshes
                        .iter()
                        .map(|s| (s.name.clone(), s.index_start, s.index_count))
                        .collect(),
                    transform: mat::to_row_major(&transform),
                    layer: model.layer,
                    disable_backface_culling: model.disable_backface_culling,
                    render_flags: model.render_flags,
                    quality: model.quality,
                    texture_overrides: model
                        .texture_overrides
                        .iter()
                        .map(|t| (t.index, t.path.clone()))
                        .collect(),
                    baked_light: model.baked_light.path.clone(),
                    stationary_light: model.stationary_light.path.clone(),
                    shared: model
                        .vertex_buffer_ids
                        .iter()
                        .any(|id| uses.get(id).copied().unwrap_or(0) > 1),
                },
            )?);
        }

        Ok(Self {
            version: geometry.version,
            models,
            texture_overrides: geometry
                .texture_overrides
                .iter()
                .map(|t| (t.index, t.path.clone()))
                .collect(),
            scene_graph_count: geometry.scene_graphs.len(),
            planar_reflector_count: geometry.planar_reflectors.len(),
        })
    }
}

/// One model's replacement geometry. Anything left `None` keeps what the donor had.
// `from_py_object`: taken by value in a Vec, which needs FromPyObject (opt-in in PyO3 0.29).
#[pyclass(from_py_object, module = "_rsblender")]
#[derive(Clone, Default)]
pub struct ModelEdit {
    index: usize,
    removed: bool,
    transform: Option<Vec<f64>>,
    positions: Option<Vec<u8>>,
    normals: Option<Vec<u8>>,
    uvs: Option<Vec<u8>>,
    uvs2: Option<Vec<u8>>,
    colors: Option<Vec<u8>>,
    indices: Option<Vec<u8>>,
}

#[pymethods]
impl ModelEdit {
    #[new]
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        index, removed=false, transform=None, positions=None, normals=None, uvs=None,
        uvs2=None, colors=None, indices=None
    ))]
    fn new(
        index: usize,
        removed: bool,
        transform: Option<Vec<f64>>,
        positions: Option<Vec<u8>>,
        normals: Option<Vec<u8>>,
        uvs: Option<Vec<u8>>,
        uvs2: Option<Vec<u8>>,
        colors: Option<Vec<u8>>,
        indices: Option<Vec<u8>>,
    ) -> Self {
        Self {
            index,
            removed,
            transform,
            positions,
            normals,
            uvs,
            uvs2,
            colors,
            indices,
        }
    }
}

/// Write a `.mapgeo`, taking everything the scene cannot describe from `donor`.
///
/// `edits` replaces the named models; the others keep their original bytes.
#[pyfunction]
#[pyo3(signature = (path, donor, version=None, edits=vec![]))]
pub fn write_map_geometry(
    path: &str,
    donor: &str,
    version: Option<u32>,
    edits: Vec<ModelEdit>,
) -> PyResult<usize> {
    let mut geometry = read_geometry(donor)?;

    if let Some(wanted) = version {
        if !WRITABLE_VERSIONS.contains(&wanted) {
            return Err(PyValueError::new_err(format!(
                "version {wanted} is not one the crate writes: {WRITABLE_VERSIONS:?}"
            )));
        }
        geometry.version = wanted;
    }

    // instances share buffers; only re-encoding a shared buffer is refused
    let mut owners: HashMap<i32, Vec<usize>> = HashMap::new();
    for (index, model) in geometry.models.iter().enumerate() {
        for &id in &model.vertex_buffer_ids {
            owners.entry(id).or_default().push(index);
        }
    }

    let mut edited: HashMap<usize, ModelEdit> = HashMap::new();
    for edit in edits {
        if edit.index >= geometry.models.len() {
            return Err(PyValueError::new_err(format!(
                "edit names model {} but the donor has {}",
                edit.index,
                geometry.models.len()
            )));
        }
        edited.insert(edit.index, edit);
    }

    let mut order: Vec<usize> = edited.keys().copied().collect();
    order.sort_unstable();

    for index in order {
        let edit = edited[&index].clone();
        if edit.removed {
            continue;
        }

        if let Some(rows) = &edit.transform {
            let m = mat::from_row_major(rows).ok_or_else(|| {
                PyValueError::new_err("a transform must be 16 row-major values".to_string())
            })?;
            let columns = mat::blender_to_riot(&m).to_cols_array().map(|v| v as f32);
            geometry.models[index].transform = rs_math::Mat4::from_cols_array(&columns);
        }

        let Some(positions) = edit.positions.as_ref() else {
            continue;
        };
        let count = positions.len() / 12;
        if count == 0 || positions.len() % 12 != 0 {
            return Err(PyValueError::new_err(format!(
                "model {index}: positions must be f32 xyz per vertex"
            )));
        }
        if count > u16::MAX as usize + 1 {
            return Err(PyValueError::new_err(format!(
                "model {index} would have {count} vertices; a .mapgeo index buffer is u16, so \
                 65536 is the ceiling. Split the mesh"
            )));
        }

        // re-encode this model's streams in their existing vertex declaration
        let buffer_ids = geometry.models[index].vertex_buffer_ids.clone();
        let description_id = geometry.models[index].vertex_description_id as usize;
        for (stream, &buffer_id) in buffer_ids.iter().enumerate() {
            if let Some(sharers) = owners.get(&buffer_id) {
                if sharers.len() > 1 {
                    return Err(PyValueError::new_err(format!(
                        "model {index} shares vertex buffer {buffer_id} with {} other placed \
                         copies of itself ({sharers:?}); re-encoding it would move all of them. \
                         Export this model unedited, or edit every instance the same way",
                        sharers.len() - 1
                    )));
                }
            }
            let description: VertexDescription = geometry
                .vertex_descriptions
                .get(description_id + stream)
                .cloned()
                .ok_or_else(|| {
                    PyValueError::new_err(format!("model {index} stream {stream}: no declaration"))
                })?;
            let stride = description.vertex_size();
            let mut data = vec![0u8; count * stride];

            for v in 0..count {
                let base = v * stride;
                if let Some((offset, format)) = locate(&description, ElementName::Position) {
                    let league = to_league(
                        read_f32(positions, v * 12),
                        read_f32(positions, v * 12 + 4),
                        read_f32(positions, v * 12 + 8),
                    );
                    encode_element(
                        &mut data,
                        base + offset,
                        format,
                        [league.x, league.y, league.z, 0.0],
                    );
                }
                if let (Some((offset, format)), Some(normals)) =
                    (locate(&description, ElementName::Normal), &edit.normals)
                {
                    let league = to_league(
                        read_f32(normals, v * 12),
                        read_f32(normals, v * 12 + 4),
                        read_f32(normals, v * 12 + 8),
                    );
                    encode_element(
                        &mut data,
                        base + offset,
                        format,
                        [league.x, league.y, league.z, 0.0],
                    );
                }
                for (name, source) in [
                    (ElementName::Texcoord0, &edit.uvs),
                    (ElementName::Texcoord1, &edit.uvs2),
                ] {
                    if let (Some((offset, format)), Some(uv)) = (locate(&description, name), source)
                    {
                        encode_element(
                            &mut data,
                            base + offset,
                            format,
                            [read_f32(uv, v * 8), 1.0 - read_f32(uv, v * 8 + 4), 0.0, 0.0],
                        );
                    }
                }
                if let Some((offset, format)) = locate(&description, ElementName::PrimaryColor) {
                    let value = match &edit.colors {
                        Some(colors) => [
                            read_f32(colors, v * 16),
                            read_f32(colors, v * 16 + 4),
                            read_f32(colors, v * 16 + 8),
                            read_f32(colors, v * 16 + 12),
                        ],
                        None => [1.0, 1.0, 1.0, 1.0],
                    };
                    encode_element(&mut data, base + offset, format, value);
                }
            }

            let layer = geometry.vertex_buffers[buffer_id as usize].layer;
            geometry.vertex_buffers[buffer_id as usize] = VertexBuffer { layer, data };
        }

        let mut bounds_min = Vec3::splat(f32::INFINITY);
        let mut bounds_max = Vec3::splat(f32::NEG_INFINITY);
        for v in 0..count {
            let league = to_league(
                read_f32(positions, v * 12),
                read_f32(positions, v * 12 + 4),
                read_f32(positions, v * 12 + 8),
            );
            bounds_min = bounds_min.min(league);
            bounds_max = bounds_max.max(league);
        }
        geometry.models[index].bounds = rs_math::Aabb::new(bounds_min, bounds_max);
        geometry.models[index].vertex_count = count as u32;

        if let Some(raw) = edit.indices.as_ref() {
            if raw.len() % 12 != 0 {
                return Err(PyValueError::new_err(format!(
                    "model {index}: indices must be three u32 per triangle"
                )));
            }
            let submesh_count = geometry.models[index].submeshes.len();
            if submesh_count > 1 {
                return Err(PyValueError::new_err(format!(
                    "model {index} has {submesh_count} submeshes; re-splitting a multi-material \
                     model is not something this writer will guess at. Export it unedited, or \
                     give it one material"
                )));
            }
            let triangles = raw.len() / 12;
            let mut indices = Vec::with_capacity(triangles * 3);
            for t in 0..triangles {
                let read = |k: usize| {
                    u32::from_le_bytes(raw[t * 12 + k * 4..t * 12 + k * 4 + 4].try_into().unwrap())
                        as u16
                };
                // back to League winding
                indices.extend_from_slice(&[read(0), read(2), read(1)]);
            }
            let id = geometry.models[index].index_buffer_id as usize;
            let layer = geometry.index_buffers[id].layer;
            geometry.models[index].index_count = indices.len() as u32;
            if submesh_count == 1 {
                let submesh = &mut geometry.models[index].submeshes[0];
                submesh.index_start = 0;
                submesh.index_count = indices.len() as u32;
                submesh.min_vertex = 0;
                submesh.max_vertex = count.saturating_sub(1) as u32;
            }
            geometry.index_buffers[id] = IndexBuffer { layer, indices };
        }
    }

    // removals last, so edit indices match the donor's numbering
    if edited.values().any(|e| e.removed) {
        let mut kept = Vec::with_capacity(geometry.models.len());
        for (index, model) in geometry.models.iter().enumerate() {
            if !edited.get(&index).map(|e| e.removed).unwrap_or(false) {
                kept.push(model.clone());
            }
        }
        geometry.models = kept;
    }

    let bytes = geometry
        .to_bytes()
        .map_err(|e| PyValueError::new_err(format!("{path}: {e}")))?;
    let written = bytes.len();
    std::fs::write(path, bytes).map_err(|e| PyIOError::new_err(format!("{path}: {e}")))?;
    Ok(written)
}
