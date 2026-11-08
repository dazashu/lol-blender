/*!
The reader supports OEGM versions 5, 6, 7, 9, 11, 12, 13, 14, 15, 17 and 18. It parses the full
file: shader/texture overrides, vertex declarations, vertex/index buffer descriptions, the model
list (buffer references, submeshes, transform, bounding box, layer, flags and per-version lighting),
and the trailing bucketed scene graphs and planar reflectors. Vertex buffers are kept as raw bytes
plus their declaration so callers can decode them. Every per-version layout delta mirrors the
trusted C# oracle: the leading `separate_point_lights` byte and per-mesh point light of versions
< 7; the embedded per-mesh names of versions < 12; the nine spherical-harmonics coefficients of
versions < 9 (which replace the stationary-light channel); implicit sampler strings before version
17; the `u8`-vs-`u16` render flags (the `u16` form starts at version 16); the single baked-paint
channel of versions 12..=16; the visibility-controller hash and counted scene-graph list of
versions >= 15; the planar reflectors of versions >= 13; the extra mesh `u32` of version 18; and
the v5 special case that omits the backface-culling byte. Versions 8, 10 and 16 are not defined by
the oracle and return `Error::UnsupportedVersion`.
*/

use std::io::{Read, Seek};

use rs_io::ReaderExt;
use rs_math::{Aabb, Vec2, Vec3};

use crate::error::{Error, Result};
use crate::mapgeo::{
    AssetChannel, ElementFormat, ElementName, GeometryBucket, IndexBuffer, MapGeometry, MapModel,
    PlanarReflector, SceneGraph, Submesh, TextureOverride, VertexBuffer, VertexDescription,
    VertexElement, VertexUsage,
};

const MAX_VERTEX_ELEMENTS: usize = 15;

fn is_supported(version: u32) -> bool {
    matches!(version, 5 | 6 | 7 | 9 | 11 | 12 | 13 | 14 | 15 | 17 | 18)
}

impl MapGeometry {
    pub fn from_reader<R: Read + Seek>(reader: &mut R) -> Result<Self> {
        let magic = reader.read_byte_array::<4>()?;
        if &magic != MapGeometry::magic() {
            return Err(Error::InvalidMagic);
        }

        let version = reader.read_u32()?;
        if !is_supported(version) {
            return Err(Error::UnsupportedVersion(version));
        }

        let separate_point_lights = version < 7 && reader.read_bool()?;

        let texture_overrides = read_shader_overrides(reader, version)?;
        let vertex_descriptions = read_vertex_descriptions(reader)?;
        let vertex_buffers = read_vertex_buffers(reader, version)?;
        let index_buffers = read_index_buffers(reader, version)?;
        let models = read_models(reader, version, separate_point_lights)?;
        let scene_graphs = read_scene_graphs(reader, version)?;
        let planar_reflectors = read_planar_reflectors(reader, version)?;

        Ok(MapGeometry {
            version,
            separate_point_lights,
            texture_overrides,
            vertex_descriptions,
            vertex_buffers,
            index_buffers,
            models,
            scene_graphs,
            planar_reflectors,
        })
    }
}

/* Shader (sampler) texture overrides. Version >= 17 stores a counted `[index, name]` list; older
versions store implicit bare strings: a `BAKED_DIFFUSE_TEXTURE` sampler from version 9 and a
`BAKED_DIFFUSE_TEXTURE_ALPHA` sampler from version 11, each as a sized string with no index byte. */
fn read_shader_overrides<R: Read>(reader: &mut R, version: u32) -> Result<Vec<TextureOverride>> {
    if version >= 17 {
        let count = reader.read_u32()? as usize;
        let mut overrides = Vec::with_capacity(count);
        for _ in 0..count {
            let index = reader.read_u32()?;
            let path = reader.read_string_u32()?;
            overrides.push(TextureOverride { index, path });
        }
        return Ok(overrides);
    }

    let mut overrides = Vec::new();
    if version >= 9 {
        overrides.push(TextureOverride {
            index: 0,
            path: reader.read_string_u32()?,
        });
    }
    if version >= 11 {
        overrides.push(TextureOverride {
            index: 1,
            path: reader.read_string_u32()?,
        });
    }
    Ok(overrides)
}

fn read_vertex_descriptions<R: Read>(reader: &mut R) -> Result<Vec<VertexDescription>> {
    let count = reader.read_u32()? as usize;
    let mut descriptions = Vec::with_capacity(count);
    for _ in 0..count {
        let usage = VertexUsage::from_u32(reader.read_u32()?);
        let element_count = reader.read_u32()? as usize;
        if element_count > MAX_VERTEX_ELEMENTS {
            return Err(Error::Unsupported("vertex declaration element count > 15"));
        }

        let mut elements = Vec::with_capacity(element_count);
        for _ in 0..element_count {
            let name_raw = reader.read_u32()?;
            let format_raw = reader.read_u32()?;
            let name =
                ElementName::from_u32(name_raw).ok_or(Error::Unsupported("vertex element name"))?;
            let format = ElementFormat::from_u32(format_raw)
                .ok_or(Error::Unsupported("vertex element format"))?;
            elements.push(VertexElement { name, format });
        }

        let padding = (MAX_VERTEX_ELEMENTS - element_count) * 8;
        reader.read_bytes(padding)?;

        descriptions.push(VertexDescription { usage, elements });
    }
    Ok(descriptions)
}

/* The per-buffer visibility-layer byte is present only from version 13 onward; earlier versions
store no layer and default to all-layers (0). */
fn read_vertex_buffers<R: Read>(reader: &mut R, version: u32) -> Result<Vec<VertexBuffer>> {
    let count = reader.read_u32()? as usize;
    let mut buffers = Vec::with_capacity(count);
    for _ in 0..count {
        let layer = if version >= 13 { reader.read_u8()? } else { 0 };
        let size = reader.read_u32()? as usize;
        let data = reader.read_bytes(size)?;
        buffers.push(VertexBuffer { layer, data });
    }
    Ok(buffers)
}

fn read_index_buffers<R: Read>(reader: &mut R, version: u32) -> Result<Vec<IndexBuffer>> {
    let count = reader.read_u32()? as usize;
    let mut buffers = Vec::with_capacity(count);
    for _ in 0..count {
        let layer = if version >= 13 { reader.read_u8()? } else { 0 };
        let size = reader.read_u32()? as usize;
        let mut indices = Vec::with_capacity(size / 2);
        for _ in 0..size / 2 {
            indices.push(reader.read_u16()?);
        }
        buffers.push(IndexBuffer { layer, indices });
    }
    Ok(buffers)
}

fn read_models<R: Read>(
    reader: &mut R,
    version: u32,
    separate_point_lights: bool,
) -> Result<Vec<MapModel>> {
    let count = reader.read_u32()? as usize;
    let mut models = Vec::with_capacity(count);
    for id in 0..count {
        models.push(read_model(reader, id, version, separate_point_lights)?);
    }
    Ok(models)
}

fn read_model<R: Read>(
    reader: &mut R,
    id: usize,
    version: u32,
    separate_point_lights: bool,
) -> Result<MapModel> {
    /* Versions < 12 embed the mesh name as a sized string; later versions derive it from the
    instance index and store nothing. */
    let name = if version < 12 {
        reader.read_string_u32()?
    } else {
        format!("MapGeo_Instance_{id}")
    };

    let vertex_count = reader.read_u32()?;
    let vertex_buffer_count = reader.read_u32()? as usize;
    let vertex_description_id = reader.read_u32()?;

    let mut vertex_buffer_ids = Vec::with_capacity(vertex_buffer_count);
    for _ in 0..vertex_buffer_count {
        vertex_buffer_ids.push(reader.read_i32()?);
    }

    let index_count = reader.read_u32()?;
    let index_buffer_id = reader.read_i32()?;

    /* The visibility layer sits here from version 13 onward; versions 7..=12 instead store it
    after the transform (read below), and versions < 7 have no layer byte at all. */
    let mut layer = if version >= 13 { reader.read_u8()? } else { 0 };

    let unknown_v18 = if version >= 18 { reader.read_u32()? } else { 0 };
    let bucket_grid_hash = if version >= 15 { reader.read_u32()? } else { 0 };

    let submesh_count = reader.read_u32()? as usize;
    let mut submeshes = Vec::with_capacity(submesh_count);
    for _ in 0..submesh_count {
        let hash = reader.read_u32()?;
        let name = reader.read_string_u32()?;
        let index_start = reader.read_u32()?;
        let index_count = reader.read_u32()?;
        let min_vertex = reader.read_u32()?;
        let max_vertex = reader.read_u32()?;
        submeshes.push(Submesh {
            hash,
            name,
            index_start,
            index_count,
            min_vertex,
            max_vertex,
        });
    }

    /* Version 5 is the lone exception that omits the backface-culling byte entirely. */
    let disable_backface_culling = if version != 5 {
        reader.read_bool()?
    } else {
        false
    };

    let bounds = Aabb::new(read_vec3(reader)?, read_vec3(reader)?);
    let transform = read_mat4(reader)?;

    let quality = reader.read_u8()?;

    if (7..=12).contains(&version) {
        layer = reader.read_u8()?;
    }

    /* Render-flag layout by version: versions 11..=13 store a bare `u8` with no transition byte;
    versions >= 14 store the layer-transition behavior byte followed by the render-flag word, which
    widened from a `u8` to a `u16` at version 16; versions < 11 store neither. */
    let mut layer_transition = 0u8;
    let mut render_flags = 0u16;
    if (11..14).contains(&version) {
        render_flags = reader.read_u8()? as u16;
    } else if version >= 14 {
        layer_transition = reader.read_u8()?;
        render_flags = if version >= 16 {
            reader.read_u16()?
        } else {
            reader.read_u8()? as u16
        };
    }

    let point_light = if version < 7 && separate_point_lights {
        Some(read_vec3(reader)?)
    } else {
        None
    };

    /* Versions < 9 carry nine spherical-harmonics light-probe coefficients and only the baked-light
    channel; the stationary-light channel and any paint data are absent. */
    if version < 9 {
        let mut harmonics = [Vec3::ZERO; 9];
        for slot in &mut harmonics {
            *slot = read_vec3(reader)?;
        }
        let baked_light = read_channel(reader)?;
        return Ok(MapModel {
            name,
            vertex_count,
            vertex_description_id,
            vertex_buffer_ids,
            index_count,
            index_buffer_id,
            layer,
            unknown_v18,
            bucket_grid_hash,
            submeshes,
            disable_backface_culling,
            bounds,
            transform,
            quality,
            layer_transition,
            render_flags,
            point_light,
            spherical_harmonics: Some(harmonics),
            baked_light,
            stationary_light: AssetChannel::empty(),
            texture_overrides: Vec::new(),
            baked_paint_scale_offset: [0.0; 4],
            baked_paint: None,
        });
    }

    let baked_light = read_channel(reader)?;
    let stationary_light = read_channel(reader)?;

    let mut texture_overrides = Vec::new();
    let mut baked_paint_scale_offset = [0.0f32; 4];
    let mut baked_paint = None;

    if version >= 17 {
        let model_texture_count = reader.read_u32()? as usize;
        texture_overrides.reserve(model_texture_count);
        for _ in 0..model_texture_count {
            let index = reader.read_u32()?;
            let path = reader.read_string_u32()?;
            texture_overrides.push(TextureOverride { index, path });
        }
        baked_paint_scale_offset = [
            reader.read_f32()?,
            reader.read_f32()?,
            reader.read_f32()?,
            reader.read_f32()?,
        ];
    } else if version >= 12 {
        /* Versions 12..=16 carry a single baked-paint channel (path + scale + bias) in place of
        the counted override list and trailing scale/bias pair. The whole channel is preserved so
        the file round-trips byte-for-byte. */
        baked_paint = Some(read_channel(reader)?);
    }

    Ok(MapModel {
        name,
        vertex_count,
        vertex_description_id,
        vertex_buffer_ids,
        index_count,
        index_buffer_id,
        layer,
        unknown_v18,
        bucket_grid_hash,
        submeshes,
        disable_backface_culling,
        bounds,
        transform,
        quality,
        layer_transition,
        render_flags,
        point_light,
        spherical_harmonics: None,
        baked_light,
        stationary_light,
        texture_overrides,
        baked_paint_scale_offset,
        baked_paint,
    })
}

fn read_scene_graphs<R: Read>(reader: &mut R, version: u32) -> Result<Vec<SceneGraph>> {
    if version >= 15 {
        let count = reader.read_u32()? as usize;
        let mut graphs = Vec::with_capacity(count);
        for _ in 0..count {
            graphs.push(read_scene_graph(reader, version)?);
        }
        Ok(graphs)
    } else {
        Ok(vec![read_scene_graph(reader, version)?])
    }
}

fn read_scene_graph<R: Read>(reader: &mut R, version: u32) -> Result<SceneGraph> {
    let controller_hash = if version >= 15 { reader.read_u32()? } else { 0 };
    let unknown_v18 = if version >= 18 {
        reader.read_f32()?
    } else {
        0.0
    };

    let min_x = reader.read_f32()?;
    let min_z = reader.read_f32()?;
    let max_x = reader.read_f32()?;
    let max_z = reader.read_f32()?;
    let max_stick_out_x = reader.read_f32()?;
    let max_stick_out_z = reader.read_f32()?;
    let bucket_size_x = reader.read_f32()?;
    let bucket_size_z = reader.read_f32()?;

    let buckets_per_side = reader.read_u16()?;
    let is_disabled = reader.read_bool()?;
    let flags = reader.read_u8()?;

    let vertex_count = reader.read_u32()? as usize;
    let index_count = reader.read_u32()? as usize;

    let mut graph = SceneGraph {
        controller_hash,
        unknown_v18,
        min_x,
        min_z,
        max_x,
        max_z,
        max_stick_out_x,
        max_stick_out_z,
        bucket_size_x,
        bucket_size_z,
        buckets_per_side,
        is_disabled,
        flags,
        vertices: Vec::new(),
        indices: Vec::new(),
        buckets: Vec::new(),
        face_visibility_flags: Vec::new(),
    };

    if is_disabled {
        return Ok(graph);
    }

    graph.vertices.reserve(vertex_count);
    for _ in 0..vertex_count {
        graph.vertices.push(read_vec3(reader)?);
    }

    graph.indices.reserve(index_count);
    for _ in 0..index_count {
        graph.indices.push(reader.read_u16()?);
    }

    let bucket_count = buckets_per_side as usize * buckets_per_side as usize;
    graph.buckets.reserve(bucket_count);
    for _ in 0..bucket_count {
        graph.buckets.push(GeometryBucket {
            max_stick_out_x: reader.read_f32()?,
            max_stick_out_z: reader.read_f32()?,
            start_index: reader.read_u32()?,
            base_vertex: reader.read_u32()?,
            inside_face_count: reader.read_u16()?,
            sticking_out_face_count: reader.read_u16()?,
        });
    }

    if flags & 1 != 0 {
        let face_count = index_count / 3;
        graph.face_visibility_flags.reserve(face_count);
        for _ in 0..face_count {
            graph.face_visibility_flags.push(reader.read_u8()?);
        }
    }

    Ok(graph)
}

fn read_planar_reflectors<R: Read>(reader: &mut R, version: u32) -> Result<Vec<PlanarReflector>> {
    if version < 13 {
        return Ok(Vec::new());
    }
    let count = reader.read_u32()? as usize;
    let mut reflectors = Vec::with_capacity(count);
    for _ in 0..count {
        let transform = read_mat4(reader)?;
        let plane = Aabb::new(read_vec3(reader)?, read_vec3(reader)?);
        let normal = read_vec3(reader)?;
        reflectors.push(PlanarReflector {
            transform,
            plane,
            normal,
        });
    }
    Ok(reflectors)
}

fn read_channel<R: Read>(reader: &mut R) -> Result<AssetChannel> {
    let path = reader.read_string_u32()?;
    let scale = read_vec2(reader)?;
    let offset = read_vec2(reader)?;
    Ok(AssetChannel {
        path,
        scale,
        offset,
    })
}

fn read_vec2<R: Read>(reader: &mut R) -> Result<Vec2> {
    Ok(Vec2::new(reader.read_f32()?, reader.read_f32()?))
}

fn read_vec3<R: Read>(reader: &mut R) -> Result<Vec3> {
    Ok(Vec3::new(
        reader.read_f32()?,
        reader.read_f32()?,
        reader.read_f32()?,
    ))
}

fn read_mat4<R: Read>(reader: &mut R) -> Result<rs_math::Mat4> {
    let m = reader.read_mtx44()?;
    Ok(rs_math::Mat4::from_cols_array(&m))
}
