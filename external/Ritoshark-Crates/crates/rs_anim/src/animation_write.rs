use std::collections::HashMap;
use std::io::{Cursor, Seek, SeekFrom, Write};

use rs_io::{Serialize, WriterExt};
use rs_math::{Quat, Vec3};

use crate::animation::Animation;
use crate::{Error, Result};

struct Palettes {
    vecs: Vec<Vec3>,
    quats: Vec<Quat>,
    vec_index: HashMap<[u32; 3], u16>,
    quat_index: HashMap<[u32; 4], u16>,
}

impl Palettes {
    fn new() -> Self {
        Self {
            vecs: Vec::new(),
            quats: Vec::new(),
            vec_index: HashMap::new(),
            quat_index: HashMap::new(),
        }
    }

    fn vec(&mut self, v: Vec3) -> u16 {
        let key = [v.x.to_bits(), v.y.to_bits(), v.z.to_bits()];
        if let Some(&i) = self.vec_index.get(&key) {
            return i;
        }
        let i = self.vecs.len() as u16;
        self.vecs.push(v);
        self.vec_index.insert(key, i);
        i
    }

    fn quat(&mut self, q: Quat) -> u16 {
        let key = [q.x.to_bits(), q.y.to_bits(), q.z.to_bits(), q.w.to_bits()];
        if let Some(&i) = self.quat_index.get(&key) {
            return i;
        }
        let i = self.quats.len() as u16;
        self.quats.push(q);
        self.quat_index.insert(key, i);
        i
    }
}

impl Animation {
    fn write_to(&self, buf: &mut Cursor<Vec<u8>>) -> Result<()> {
        if let Some(raw) = &self.raw {
            buf.write_bytes(&raw.bytes)?;
            return Ok(());
        }
        self.write_v4(buf)
    }

    fn write_v4(&self, buf: &mut Cursor<Vec<u8>>) -> Result<()> {
        let track_count = self.tracks.len();
        let frame_count = self
            .tracks
            .iter()
            .map(|t| t.frames.len())
            .max()
            .unwrap_or(0);

        let frame_duration = if self.fps != 0.0 { 1.0 / self.fps } else { 0.0 };

        let mut palettes = Palettes::new();
        let mut frame_indices: Vec<Vec<(u16, u16, u16)>> = Vec::with_capacity(track_count);
        for track in &self.tracks {
            let mut indices = Vec::with_capacity(frame_count);
            for f in 0..frame_count {
                let frame = track.frames.get(f).or_else(|| track.frames.last());
                let (t, s, r) = match frame {
                    Some(fr) => (
                        palettes.vec(fr.translation),
                        palettes.vec(fr.scale),
                        palettes.quat(fr.rotation),
                    ),
                    None => (
                        palettes.vec(Vec3::ZERO),
                        palettes.vec(Vec3::ONE),
                        palettes.quat(Quat::IDENTITY),
                    ),
                };
                indices.push((t, s, r));
            }
            frame_indices.push(indices);
        }

        if palettes.vecs.len() > 0xFFFF || palettes.quats.len() > 0xFFFF {
            return Err(Error::Unsupported("anm palette exceeds 65535 entries"));
        }

        buf.write_bytes(b"r3d2anmd")?;
        buf.write_u32(4)?; // version
        buf.write_u32(0)?; // file size, patched last
        buf.write_u32(0)?; // format token
        buf.write_u32(0)?; // flags1
        buf.write_u32(0)?; // flags2

        buf.write_u32(track_count as u32)?;
        buf.write_u32(frame_count as u32)?;
        buf.write_f32(frame_duration)?;

        let offsets_pos = buf.stream_position().map_err(rs_io::Error::from)?;
        for _ in 0..6 {
            buf.write_i32(0)?;
        }
        buf.write_bytes(&[0u8; 12])?;

        let vecs_offset = buf.stream_position().map_err(rs_io::Error::from)? as i32 - 12;
        for v in &palettes.vecs {
            buf.write_vec3(*v)?;
        }

        let quats_offset = buf.stream_position().map_err(rs_io::Error::from)? as i32 - 12;
        for q in &palettes.quats {
            buf.write_quat(*q)?;
        }

        let frames_offset = buf.stream_position().map_err(rs_io::Error::from)? as i32 - 12;
        for f in 0..frame_count {
            for (track, indices) in self.tracks.iter().zip(frame_indices.iter()) {
                let (t, s, r) = indices[f];
                buf.write_u32(track.joint_hash)?;
                buf.write_u16(t)?;
                buf.write_u16(s)?;
                buf.write_u16(r)?;
                buf.write_u16(0)?; // pad
            }
        }

        let file_size = buf.seek(SeekFrom::End(0)).map_err(rs_io::Error::from)? as u32;

        buf.seek(SeekFrom::Start(12)).map_err(rs_io::Error::from)?;
        buf.write_u32(file_size)?;

        buf.seek(SeekFrom::Start(offsets_pos))
            .map_err(rs_io::Error::from)?;
        buf.write_i32(0)?; // joint hashes (embedded per frame in v4)
        buf.write_i32(0)?; // asset name
        buf.write_i32(0)?; // time
        buf.write_i32(vecs_offset)?;
        buf.write_i32(quats_offset)?;
        buf.write_i32(frames_offset)?;
        Ok(())
    }
}

impl Serialize for Animation {
    type Error = Error;

    fn to_writer<W: Write>(&self, writer: &mut W) -> Result<()> {
        let mut buf = Cursor::new(Vec::new());
        self.write_to(&mut buf)?;
        writer
            .write_all(&buf.into_inner())
            .map_err(rs_io::Error::from)?;
        Ok(())
    }
}
