use std::io::{Read, Seek, SeekFrom};

use rs_io::{Parse, ReaderExt};
use rs_math::{Aabb, Vec2, Vec3};

use crate::error::{Error, Result};
use crate::static_mesh::{SCB_MAGIC, SCO_MAGIC, StaticMesh, StaticMeshFace};

impl Parse for StaticMesh {
    type Error = Error;

    fn from_reader<R: Read + Seek>(reader: &mut R) -> Result<Self> {
        let mut magic = [0u8; 8];
        reader.read_exact(&mut magic)?;
        reader.seek(SeekFrom::Start(0))?;

        if &magic == SCB_MAGIC {
            Self::from_scb_reader(reader)
        } else if magic.starts_with(b"[Object") {
            let mut text = String::new();
            reader.read_to_string(&mut text)?;
            Self::from_sco_str(&text)
        } else {
            Err(Error::InvalidMagic)
        }
    }
}

impl StaticMesh {
    /// Reads the binary `.scb` (`"r3d2Mesh"`) static mesh format.
    pub fn from_scb_reader<R: Read>(reader: &mut R) -> Result<Self> {
        let magic = reader.read_byte_array::<8>()?;
        if &magic != SCB_MAGIC {
            return Err(Error::InvalidMagic);
        }

        let major = reader.read_u16()?;
        let minor = reader.read_u16()?;
        // Accept major 2 or 3 regardless of minor, matching Jade and the ltk
        // reference. 2.1, 2.2, and 3.2 all occur in the wild; the only layout
        // branch that depends on the exact version is the 3.2 `vertex_type`
        // field read below. Anything outside major {2,3} is unknown - reject.
        if !matches!(major, 2 | 3) {
            return Err(Error::UnsupportedVersion(
                (u32::from(major) << 16) | u32::from(minor),
            ));
        }

        let name = reader.read_fixed_string::<128>()?;
        let vertex_count = reader.read_u32()? as usize;
        let face_count = reader.read_u32()? as usize;
        let flags = reader.read_u32()?;

        let bounding_box = Aabb::new(reader.read_vec3()?, reader.read_vec3()?);

        let vertex_type = if major == 3 && minor == 2 {
            Some(reader.read_u32()?)
        } else {
            None
        };

        let mut positions = Vec::with_capacity(vertex_count);
        for _ in 0..vertex_count {
            positions.push(reader.read_vec3()?);
        }

        let colors = match vertex_type {
            Some(t) if t >= 1 => {
                let mut c = Vec::with_capacity(vertex_count);
                for _ in 0..vertex_count {
                    c.push(reader.read_byte_array::<4>()?);
                }
                Some(c)
            }
            _ => None,
        };

        let central = reader.read_vec3()?;

        let mut faces = Vec::with_capacity(face_count);
        for _ in 0..face_count {
            let indices = [reader.read_u32()?, reader.read_u32()?, reader.read_u32()?];
            let material = reader.read_fixed_string::<64>()?;
            let u = [reader.read_f32()?, reader.read_f32()?, reader.read_f32()?];
            let v = [reader.read_f32()?, reader.read_f32()?, reader.read_f32()?];
            faces.push(StaticMeshFace {
                material,
                indices,
                uvs: [
                    Vec2::new(u[0], v[0]),
                    Vec2::new(u[1], v[1]),
                    Vec2::new(u[2], v[2]),
                ],
            });
        }

        // Capture any post-face bytes verbatim: when `HasVcp` (bit 0) is set the game writes a
        // per-face RGB color block here, and a local-origin/pivot pair may follow. The exact layout
        // is not modelled, but keeping the raw tail makes the `.scb` round-trip byte-exact.
        let mut trailing = Vec::new();
        reader.read_to_end(&mut trailing)?;

        Ok(Self {
            name,
            version: (major, minor),
            flags,
            bounding_box,
            vertex_type,
            central,
            positions,
            colors,
            faces,
            trailing,
        })
    }

    /// Reads the text `.sco` (`[ObjectBegin]`) static mesh format (best-effort).
    pub fn from_sco_str(text: &str) -> Result<Self> {
        let mut lines = text.lines();
        let header = lines
            .next()
            .map(str::trim)
            .ok_or_else(|| Error::MalformedText("empty file".into()))?;
        if header != SCO_MAGIC {
            return Err(Error::InvalidMagic);
        }

        let mut name = String::new();
        let mut central = Vec3::ZERO;
        let mut positions: Vec<Vec3> = Vec::new();
        let mut faces: Vec<StaticMeshFace> = Vec::new();

        while let Some(line) = lines.next() {
            let tokens: Vec<&str> = line.split_whitespace().collect();
            let Some(&key) = tokens.first() else {
                continue;
            };
            match key {
                "Name=" => {
                    name = tokens.get(1).copied().unwrap_or("").to_string();
                }
                "CentralPoint=" => {
                    central = parse_vec3(&tokens[1..])?;
                }
                "Verts=" => {
                    let count = parse_count(tokens.get(1))?;
                    positions.reserve(count);
                    for _ in 0..count {
                        let v = lines
                            .next()
                            .ok_or_else(|| Error::MalformedText("truncated verts".into()))?;
                        let comps: Vec<&str> = v.split_whitespace().collect();
                        positions.push(parse_vec3(&comps)?);
                    }
                }
                "Faces=" => {
                    let count = parse_count(tokens.get(1))?;
                    faces.reserve(count);
                    for _ in 0..count {
                        let f = lines
                            .next()
                            .ok_or_else(|| Error::MalformedText("truncated faces".into()))?;
                        faces.push(parse_sco_face(f)?);
                    }
                }
                _ => {}
            }
        }

        Ok(Self {
            name,
            version: (0, 0),
            flags: 0,
            bounding_box: Aabb::new(Vec3::ZERO, Vec3::ZERO),
            vertex_type: None,
            central,
            positions,
            colors: None,
            faces,
            trailing: Vec::new(),
        })
    }
}

fn parse_count(tok: Option<&&str>) -> Result<usize> {
    tok.and_then(|s| s.parse().ok())
        .ok_or_else(|| Error::MalformedText("invalid count".into()))
}

fn parse_f32(s: &str) -> Result<f32> {
    s.parse()
        .map_err(|_| Error::MalformedText(format!("invalid float: {s}")))
}

fn parse_vec3(comps: &[&str]) -> Result<Vec3> {
    if comps.len() < 3 {
        return Err(Error::MalformedText("expected 3 components".into()));
    }
    Ok(Vec3::new(
        parse_f32(comps[0])?,
        parse_f32(comps[1])?,
        parse_f32(comps[2])?,
    ))
}

fn parse_sco_face(line: &str) -> Result<StaticMeshFace> {
    let t: Vec<&str> = line.split_whitespace().collect();
    if t.len() < 11 {
        return Err(Error::MalformedText("face needs 11 fields".into()));
    }
    let indices = [
        t[1].parse()
            .map_err(|_| Error::MalformedText("bad face index".into()))?,
        t[2].parse()
            .map_err(|_| Error::MalformedText("bad face index".into()))?,
        t[3].parse()
            .map_err(|_| Error::MalformedText("bad face index".into()))?,
    ];
    let material = t[4].to_string();
    let uvs = [
        Vec2::new(parse_f32(t[5])?, parse_f32(t[6])?),
        Vec2::new(parse_f32(t[7])?, parse_f32(t[8])?),
        Vec2::new(parse_f32(t[9])?, parse_f32(t[10])?),
    ];
    Ok(StaticMeshFace {
        material,
        indices,
        uvs,
    })
}
