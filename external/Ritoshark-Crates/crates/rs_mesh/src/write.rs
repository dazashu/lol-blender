use std::io::Write;

use rs_io::{Serialize, WriterExt};

use crate::error::{Error, Result};
use crate::skinned::{
    MAGIC, SkinnedMesh, SkinnedMeshRange, SkinnedMeshVertex, SkinnedMeshVertexType,
};
use crate::static_mesh::{SCB_MAGIC, StaticMesh, StaticMeshFace};

impl Serialize for SkinnedMesh {
    type Error = Error;

    fn to_writer<W: Write>(&self, w: &mut W) -> Result<()> {
        w.write_u32(MAGIC)?;
        w.write_u16(self.major)?;
        w.write_u16(self.minor)?;

        if self.major == 0 {
            w.write_u32(self.indices.len() as u32)?;
            w.write_u32(self.vertices.len() as u32)?;
        } else {
            w.write_u32(self.ranges.len() as u32)?;
            for range in &self.ranges {
                write_range(w, range)?;
            }

            if self.major == 4 {
                w.write_u32(self.flags)?;
            }

            w.write_u32(self.indices.len() as u32)?;
            w.write_u32(self.vertices.len() as u32)?;

            if self.major == 4 {
                w.write_u32(self.vertex_type.vertex_size())?;
                w.write_u32(self.vertex_type.to_u32())?;
                w.write_vec3(self.bounding_box.min)?;
                w.write_vec3(self.bounding_box.max)?;
                w.write_vec3(self.bounding_sphere.center)?;
                w.write_f32(self.bounding_sphere.radius)?;
            }
        }

        if self.major == 4 && self.flags & 1 != 0 {
            let size = u16::try_from(self.skinning_block.len()).map_err(|_| {
                std::io::Error::new(std::io::ErrorKind::InvalidInput, "skinning block exceeds u16")
            })?;
            w.write_u16(size)?;
            w.write_bytes(&self.skinning_block)?;
        }
        for &index in &self.indices {
            w.write_u16(index)?;
        }

        for vertex in &self.vertices {
            write_vertex(w, vertex, self.vertex_type)?;
        }

        w.write_bytes(&self.trailing)?;

        Ok(())
    }
}

fn write_range<W: Write>(w: &mut W, range: &SkinnedMeshRange) -> Result<()> {
    write_fixed_string::<_, 64>(w, &range.name)?;
    w.write_u32(range.vertex_start)?;
    w.write_u32(range.vertex_count)?;
    w.write_u32(range.index_start)?;
    w.write_u32(range.index_count)?;
    Ok(())
}

fn write_vertex<W: Write>(
    w: &mut W,
    vertex: &SkinnedMeshVertex,
    vertex_type: SkinnedMeshVertexType,
) -> Result<()> {
    w.write_vec3(vertex.position)?;
    w.write_bytes(&vertex.blend_indices)?;
    for weight in vertex.blend_weights {
        w.write_f32(weight)?;
    }
    w.write_vec3(vertex.normal)?;
    w.write_vec2(vertex.uv)?;

    if matches!(
        vertex_type,
        SkinnedMeshVertexType::Color | SkinnedMeshVertexType::Tangent
    ) {
        let color = vertex.color.unwrap_or([0; 4]);
        w.write_bytes(&color)?;
        if vertex_type == SkinnedMeshVertexType::Tangent {
            let tangent = vertex.tangent.unwrap_or(rs_math::Vec4::ZERO);
            w.write_vec4(tangent)?;
        }
    }

    Ok(())
}

fn write_fixed_string<W: Write, const N: usize>(w: &mut W, s: &str) -> Result<()> {
    let bytes = s.as_bytes();
    let len = bytes.len().min(N);
    let mut buf = [0u8; N];
    buf[..len].copy_from_slice(&bytes[..len]);
    w.write_bytes(&buf).map_err(Error::from)
}

impl Serialize for StaticMesh {
    type Error = Error;

    fn to_writer<W: Write>(&self, w: &mut W) -> Result<()> {
        self.to_scb_writer(w)
    }
}

impl StaticMesh {
    /// Writes the binary `.scb` (`"r3d2Mesh"`) static mesh, reproducing the on-disk version, flags,
    /// bounds, vertex-type word, and post-face tail verbatim so a read of a real file round-trips
    /// byte-for-byte.
    pub fn to_scb_writer<W: Write>(&self, w: &mut W) -> Result<()> {
        let (major, minor) = if self.version == (0, 0) {
            (3, 2)
        } else {
            self.version
        };

        w.write_bytes(SCB_MAGIC)?;
        w.write_u16(major)?;
        w.write_u16(minor)?;
        write_fixed_string::<_, 128>(w, &self.name)?;
        w.write_u32(self.positions.len() as u32)?;
        w.write_u32(self.faces.len() as u32)?;
        w.write_u32(self.flags)?;
        w.write_vec3(self.bounding_box.min)?;
        w.write_vec3(self.bounding_box.max)?;

        if major == 3 && minor == 2 {
            // Prefer the original vertex-type word; fall back to deriving it from the color block.
            let vtype = self.vertex_type.unwrap_or(u32::from(self.colors.is_some()));
            w.write_u32(vtype)?;
        }

        for &position in &self.positions {
            w.write_vec3(position)?;
        }

        if let Some(colors) = &self.colors {
            for color in colors {
                w.write_bytes(color)?;
            }
        }

        w.write_vec3(self.central)?;

        for face in &self.faces {
            write_scb_face(w, face)?;
        }

        w.write_bytes(&self.trailing)?;

        Ok(())
    }

    /// Serializes this mesh to an in-memory binary `.scb` (`"r3d2Mesh"`) buffer.
    ///
    /// This is the convenience entry point for the `.sco` -> `.scb` conversion path: parse the
    /// text form with [`StaticMesh::from_sco_str`], then call this to get the binary bytes. A mesh
    /// read from `.sco` carries version `(0, 0)`, which [`StaticMesh::to_scb_writer`] emits as the
    /// modern `3.2` container (no per-vertex color word unless `colors` is present).
    pub fn to_scb_bytes(&self) -> Result<Vec<u8>> {
        let mut buf = Vec::new();
        self.to_scb_writer(&mut buf)?;
        Ok(buf)
    }
}

fn write_scb_face<W: Write>(w: &mut W, face: &StaticMeshFace) -> Result<()> {
    for &index in &face.indices {
        w.write_u32(index)?;
    }
    write_fixed_string::<_, 64>(w, &face.material)?;
    // UVs are stored as three U floats then three V floats (not interleaved).
    for uv in &face.uvs {
        w.write_f32(uv.x)?;
    }
    for uv in &face.uvs {
        w.write_f32(uv.y)?;
    }
    Ok(())
}

/// Writes the text `.sco` (`[ObjectBegin]`) static mesh format.
///
/// The crate reads `.sco` but had no writer, because the format was dropped from the game. It is
/// still what a couple of tools round-trip through, so lol_maya kept a writer (`lol_maya.py:3262`)
/// and this is that writer, in Rust, formatting matched field for field:
///
///   * positions and the central point at four decimals, UVs at twelve;
///   * the vertex count on a `Verts=` line and the FACE count - not the index count - on `Faces=`;
///   * each face as `3`, three right-aligned 5-wide indices, the material right-aligned to 20,
///     then six UV components, tab-separated exactly where lol_maya puts tabs;
///   * `[ObjectEnd]` with NO trailing newline, which is also what lol_maya writes.
///
/// The formatting matters because these files are diffed and hand-edited; a writer that produced
/// the same data with different spacing would make every such diff useless.
impl StaticMesh {
    pub fn to_sco_string(&self) -> String {
        // `.sco` is whitespace-separated, so an EMPTY name is not a value it can hold - the field
        // simply vanishes and the line comes back one token short. Real `.scb` 3.2 files store an
        // empty material name on every face, so this is the common case rather than a corner: the
        // corpus's own `common_sphere.scb` is one. lol_maya hits the same wall and substitutes
        // (`SO.fix_material_name`, and its comment says Maya cannot make a shader with an empty
        // name either). Same substitution here, and it is the one lossy step in the whole
        // `.scb` -> `.sco` conversion.
        const FALLBACK_MATERIAL: &str = "lambert1";
        const FALLBACK_NAME: &str = "mesh";

        let mut out = String::new();
        out.push_str("[ObjectBegin]
");
        out.push_str(&format!(
            "Name= {}
",
            if self.name.is_empty() { FALLBACK_NAME } else { &self.name }
        ));
        out.push_str(&format!(
            "CentralPoint= {:.4} {:.4} {:.4}
",
            self.central.x, self.central.y, self.central.z
        ));

        out.push_str(&format!("Verts= {}
", self.positions.len()));
        for position in &self.positions {
            out.push_str(&format!("{:.4} {:.4} {:.4}
", position.x, position.y, position.z));
        }

        out.push_str(&format!("Faces= {}
", self.faces.len()));
        for face in &self.faces {
            out.push_str("3	");
            for index in face.indices {
                out.push_str(&format!(" {index:>5}"));
            }
            let material = if face.material.trim().is_empty() {
                FALLBACK_MATERIAL
            } else {
                face.material.as_str()
            };
            out.push_str(&format!("	{material:>20}	"));
            for uv in face.uvs {
                out.push_str(&format!("{:.12} {:.12} ", uv.x, uv.y));
            }
            // lol_maya emits a trailing space after the last uv pair and then the newline.
            out.push_str("\n");
        }

        out.push_str("[ObjectEnd]");
        out
    }

    pub fn to_sco_writer<W: Write>(&self, w: &mut W) -> Result<()> {
        w.write_all(self.to_sco_string().as_bytes())
            .map_err(|e| Error::from(rs_io::Error::from(e)))
    }

    pub fn to_sco_path(&self, path: impl AsRef<std::path::Path>) -> Result<()> {
        let mut file = std::fs::File::create(path).map_err(|e| Error::from(rs_io::Error::from(e)))?;
        self.to_sco_writer(&mut file)
    }
}
