/*!
The writer is the byte-exact inverse of the reader for every version it supports (5, 6, 7, 9, 11,
12, 13, 14, 15, 17, 18). It reproduces the same field order, padding and per-version layout: the
leading `separate_point_lights` byte and per-mesh point light of versions < 7; the embedded per-mesh
names of versions < 12; the nine spherical-harmonics coefficients of versions < 9; the implicit
sampler strings, `u8` render-flag word and single baked-paint channel of versions before 17; the
post-transform layer byte of versions 7..=12; the extra mesh `u32` of version 18; the v5 omission of
the backface-culling byte; and the trailing bucketed scene graphs and planar reflectors. A parsed
file therefore re-serializes identically. Any unsupported version is rejected with
`Error::UnsupportedVersion`.
*/

use std::io::Write;

use rs_io::WriterExt;
use rs_math::{Mat4, Vec2, Vec3};

use crate::error::{Error, Result};
use crate::mapgeo::{
    AssetChannel, ElementFormat, ElementName, MapGeometry, MapModel, PlanarReflector, SceneGraph,
    VertexDescription,
};

const MAX_VERTEX_ELEMENTS: usize = 15;

fn is_supported(version: u32) -> bool {
    matches!(version, 5 | 6 | 7 | 9 | 11 | 12 | 13 | 14 | 15 | 17 | 18)
}

impl MapGeometry {
    pub fn to_writer<W: Write>(&self, writer: &mut W) -> Result<()> {
        let version = self.version;
        if !is_supported(version) {
            return Err(Error::UnsupportedVersion(version));
        }

        writer.write_bytes(MapGeometry::magic())?;
        writer.write_u32(version)?;

        if version < 7 {
            writer.write_bool(self.separate_point_lights)?;
        }

        write_shader_overrides(writer, self, version)?;

        writer.write_u32(self.vertex_descriptions.len() as u32)?;
        for desc in &self.vertex_descriptions {
            write_vertex_description(writer, desc)?;
        }

        writer.write_u32(self.vertex_buffers.len() as u32)?;
        for vb in &self.vertex_buffers {
            if version >= 13 {
                writer.write_u8(vb.layer)?;
            }
            writer.write_u32(vb.data.len() as u32)?;
            writer.write_bytes(&vb.data)?;
        }

        writer.write_u32(self.index_buffers.len() as u32)?;
        for ib in &self.index_buffers {
            if version >= 13 {
                writer.write_u8(ib.layer)?;
            }
            writer.write_u32((ib.indices.len() * 2) as u32)?;
            for &index in &ib.indices {
                writer.write_u16(index)?;
            }
        }

        writer.write_u32(self.models.len() as u32)?;
        for model in &self.models {
            write_model(writer, model, version, self.separate_point_lights)?;
        }

        write_scene_graphs(writer, &self.scene_graphs, version)?;
        write_planar_reflectors(writer, &self.planar_reflectors, version)?;

        Ok(())
    }
}

fn write_shader_overrides<W: Write>(writer: &mut W, geo: &MapGeometry, version: u32) -> Result<()> {
    if version >= 17 {
        writer.write_u32(geo.texture_overrides.len() as u32)?;
        for ov in &geo.texture_overrides {
            writer.write_u32(ov.index)?;
            writer.write_string_u32(&ov.path)?;
        }
        return Ok(());
    }

    /* Versions 9..=16 store the sampler strings implicitly: one bare string from version 9 and a
    second from version 11, with no count and no index. */
    let mut iter = geo.texture_overrides.iter();
    if version >= 9 {
        writer.write_string_u32(iter.next().map_or("", |ov| ov.path.as_str()))?;
    }
    if version >= 11 {
        writer.write_string_u32(iter.next().map_or("", |ov| ov.path.as_str()))?;
    }
    Ok(())
}

fn write_vertex_description<W: Write>(writer: &mut W, desc: &VertexDescription) -> Result<()> {
    let element_count = desc.elements.len();
    if element_count > MAX_VERTEX_ELEMENTS {
        return Err(Error::Unsupported("vertex declaration element count > 15"));
    }

    writer.write_u32(desc.usage as u32)?;
    writer.write_u32(element_count as u32)?;
    for element in &desc.elements {
        writer.write_u32(element.name as u32)?;
        writer.write_u32(element.format as u32)?;
    }

    /* The on-disk declaration always reserves 15 element slots; the game fills the unused tail
    with a default element (Position, XYZW_Float32 = 0, 3) rather than zero bytes, so the writer
    must reproduce that pattern to round-trip byte-for-byte. */
    for _ in element_count..MAX_VERTEX_ELEMENTS {
        writer.write_u32(ElementName::Position as u32)?;
        writer.write_u32(ElementFormat::XyzwFloat32 as u32)?;
    }
    Ok(())
}

fn write_model<W: Write>(
    writer: &mut W,
    model: &MapModel,
    version: u32,
    separate_point_lights: bool,
) -> Result<()> {
    if version < 12 {
        writer.write_string_u32(&model.name)?;
    }

    writer.write_u32(model.vertex_count)?;
    writer.write_u32(model.vertex_buffer_ids.len() as u32)?;
    writer.write_u32(model.vertex_description_id)?;
    for &id in &model.vertex_buffer_ids {
        writer.write_i32(id)?;
    }

    writer.write_u32(model.index_count)?;
    writer.write_i32(model.index_buffer_id)?;

    if version >= 13 {
        writer.write_u8(model.layer)?;
    }
    if version >= 18 {
        writer.write_u32(model.unknown_v18)?;
    }
    if version >= 15 {
        writer.write_u32(model.bucket_grid_hash)?;
    }

    writer.write_u32(model.submeshes.len() as u32)?;
    for submesh in &model.submeshes {
        writer.write_u32(submesh.hash)?;
        writer.write_string_u32(&submesh.name)?;
        writer.write_u32(submesh.index_start)?;
        writer.write_u32(submesh.index_count)?;
        writer.write_u32(submesh.min_vertex)?;
        writer.write_u32(submesh.max_vertex)?;
    }

    if version != 5 {
        writer.write_bool(model.disable_backface_culling)?;
    }

    write_vec3(writer, model.bounds.min)?;
    write_vec3(writer, model.bounds.max)?;
    writer.write_mtx44(&model.transform.to_cols_array())?;

    writer.write_u8(model.quality)?;

    if (7..=12).contains(&version) {
        writer.write_u8(model.layer)?;
    }

    if (11..14).contains(&version) {
        writer.write_u8(model.render_flags as u8)?;
    } else if version >= 14 {
        writer.write_u8(model.layer_transition)?;
        if version >= 16 {
            writer.write_u16(model.render_flags)?;
        } else {
            writer.write_u8(model.render_flags as u8)?;
        }
    }

    if version < 7 && separate_point_lights {
        write_vec3(writer, model.point_light.unwrap_or(Vec3::ZERO))?;
    }

    if version < 9 {
        let harmonics = model.spherical_harmonics.unwrap_or([Vec3::ZERO; 9]);
        for coefficient in harmonics {
            write_vec3(writer, coefficient)?;
        }
        write_channel(writer, &model.baked_light)?;
        return Ok(());
    }

    write_channel(writer, &model.baked_light)?;
    write_channel(writer, &model.stationary_light)?;

    if version >= 17 {
        writer.write_u32(model.texture_overrides.len() as u32)?;
        for ov in &model.texture_overrides {
            writer.write_u32(ov.index)?;
            writer.write_string_u32(&ov.path)?;
        }
        for &v in &model.baked_paint_scale_offset {
            writer.write_f32(v)?;
        }
    } else if version >= 12 {
        if let Some(channel) = &model.baked_paint {
            write_channel(writer, channel)?;
        } else {
            write_channel(writer, &AssetChannel::empty())?;
        }
    }

    Ok(())
}

fn write_scene_graphs<W: Write>(writer: &mut W, graphs: &[SceneGraph], version: u32) -> Result<()> {
    if version >= 15 {
        writer.write_u32(graphs.len() as u32)?;
    }
    for graph in graphs {
        write_scene_graph(writer, graph, version)?;
    }
    Ok(())
}

fn write_scene_graph<W: Write>(writer: &mut W, graph: &SceneGraph, version: u32) -> Result<()> {
    if version >= 15 {
        writer.write_u32(graph.controller_hash)?;
    }
    if version >= 18 {
        writer.write_f32(graph.unknown_v18)?;
    }

    writer.write_f32(graph.min_x)?;
    writer.write_f32(graph.min_z)?;
    writer.write_f32(graph.max_x)?;
    writer.write_f32(graph.max_z)?;
    writer.write_f32(graph.max_stick_out_x)?;
    writer.write_f32(graph.max_stick_out_z)?;
    writer.write_f32(graph.bucket_size_x)?;
    writer.write_f32(graph.bucket_size_z)?;

    writer.write_u16(graph.buckets_per_side)?;
    writer.write_bool(graph.is_disabled)?;
    writer.write_u8(graph.flags)?;

    writer.write_u32(graph.vertices.len() as u32)?;
    writer.write_u32(graph.indices.len() as u32)?;

    if graph.is_disabled {
        return Ok(());
    }

    for &vertex in &graph.vertices {
        write_vec3(writer, vertex)?;
    }
    for &index in &graph.indices {
        writer.write_u16(index)?;
    }
    for bucket in &graph.buckets {
        writer.write_f32(bucket.max_stick_out_x)?;
        writer.write_f32(bucket.max_stick_out_z)?;
        writer.write_u32(bucket.start_index)?;
        writer.write_u32(bucket.base_vertex)?;
        writer.write_u16(bucket.inside_face_count)?;
        writer.write_u16(bucket.sticking_out_face_count)?;
    }
    if graph.flags & 1 != 0 {
        for &flag in &graph.face_visibility_flags {
            writer.write_u8(flag)?;
        }
    }
    Ok(())
}

fn write_planar_reflectors<W: Write>(
    writer: &mut W,
    reflectors: &[PlanarReflector],
    version: u32,
) -> Result<()> {
    if version < 13 {
        return Ok(());
    }
    writer.write_u32(reflectors.len() as u32)?;
    for reflector in reflectors {
        write_mat4(writer, &reflector.transform)?;
        write_vec3(writer, reflector.plane.min)?;
        write_vec3(writer, reflector.plane.max)?;
        write_vec3(writer, reflector.normal)?;
    }
    Ok(())
}

fn write_channel<W: Write>(writer: &mut W, channel: &AssetChannel) -> Result<()> {
    writer.write_string_u32(&channel.path)?;
    write_vec2(writer, channel.scale)?;
    write_vec2(writer, channel.offset)?;
    Ok(())
}

fn write_mat4<W: Write>(writer: &mut W, m: &Mat4) -> Result<()> {
    writer.write_mtx44(&m.to_cols_array())?;
    Ok(())
}

fn write_vec2<W: Write>(writer: &mut W, v: Vec2) -> Result<()> {
    writer.write_f32(v.x)?;
    writer.write_f32(v.y)?;
    Ok(())
}

fn write_vec3<W: Write>(writer: &mut W, v: Vec3) -> Result<()> {
    writer.write_f32(v.x)?;
    writer.write_f32(v.y)?;
    writer.write_f32(v.z)?;
    Ok(())
}
