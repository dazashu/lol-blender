use std::io::{Cursor, Read, Seek, SeekFrom};

use rs_io::{Parse, ReaderExt};
use rs_math::{Quat, Vec3};

use crate::animation::{AnimFrame, AnimTrack, Animation};
use crate::compressed::Compressed;
use crate::quantized::decompress_quat;
use crate::raw::RawAnim;
use crate::{Error, Result};

fn section_count(size: i32, element: i32) -> usize {
    if size <= 0 || element <= 0 {
        0
    } else {
        (size / element) as usize
    }
}

impl Animation {
    fn read_uncompressed<R: Read + Seek>(reader: &mut R, version: u32) -> Result<Self> {
        match version {
            5 => Self::read_v5(reader),
            4 => Self::read_v4(reader),
            3 => Self::read_v3(reader),
            _ => Err(Error::UnsupportedVersion(version)),
        }
    }

    fn read_v5<R: Read + Seek>(reader: &mut R) -> Result<Self> {
        let _resource_size = reader.read_u32()?;
        let _format_token = reader.read_u32()?;
        let _flags1 = reader.read_u32()?;
        let _flags2 = reader.read_u32()?;

        let _track_count = reader.read_u32()?;
        let frame_count = reader.read_u32()?;
        let frame_duration = reader.read_f32()?;
        let fps = if frame_duration != 0.0 {
            1.0 / frame_duration
        } else {
            0.0
        };

        let joint_hashes_offset = reader.read_i32()?;
        let _asset_name_offset = reader.read_i32()?;
        let _time_offset = reader.read_i32()?;
        let vecs_offset = reader.read_i32()?;
        let quats_offset = reader.read_i32()?;
        let frames_offset = reader.read_i32()?;

        if joint_hashes_offset <= 0 || vecs_offset <= 0 || quats_offset <= 0 || frames_offset <= 0 {
            return Err(Error::Unsupported("anm v5 missing data section"));
        }

        let frame_count = frame_count as usize;
        let joint_hash_count = section_count(frames_offset - joint_hashes_offset, 4);
        let vec_count = section_count(quats_offset - vecs_offset, 12);
        let quat_count = section_count(joint_hashes_offset - quats_offset, 6);

        reader
            .seek(SeekFrom::Start(joint_hashes_offset as u64 + 12))
            .map_err(rs_io::Error::from)?;
        let mut joint_hashes = Vec::with_capacity(joint_hash_count);
        for _ in 0..joint_hash_count {
            joint_hashes.push(reader.read_u32()?);
        }

        reader
            .seek(SeekFrom::Start(vecs_offset as u64 + 12))
            .map_err(rs_io::Error::from)?;
        let mut vecs = Vec::with_capacity(vec_count);
        for _ in 0..vec_count {
            vecs.push(reader.read_vec3()?);
        }

        reader
            .seek(SeekFrom::Start(quats_offset as u64 + 12))
            .map_err(rs_io::Error::from)?;
        let mut quats = Vec::with_capacity(quat_count);
        for _ in 0..quat_count {
            let bytes = reader.read_byte_array::<6>()?;
            quats.push(decompress_quat(&bytes).normalize());
        }

        let mut tracks: Vec<AnimTrack> = joint_hashes
            .iter()
            .map(|&h| AnimTrack {
                joint_hash: h,
                frames: Vec::with_capacity(frame_count),
            })
            .collect();

        reader
            .seek(SeekFrom::Start(frames_offset as u64 + 12))
            .map_err(rs_io::Error::from)?;
        for frame_id in 0..frame_count {
            let time = frame_id as f32 * frame_duration;
            for track in tracks.iter_mut() {
                let translate_id = reader.read_u16()?;
                let scale_id = reader.read_u16()?;
                let rotate_id = reader.read_u16()?;
                track.frames.push(AnimFrame {
                    time,
                    rotation: quats
                        .get(rotate_id as usize)
                        .copied()
                        .unwrap_or(Quat::IDENTITY),
                    translation: vecs
                        .get(translate_id as usize)
                        .copied()
                        .unwrap_or(Vec3::ZERO),
                    scale: vecs.get(scale_id as usize).copied().unwrap_or(Vec3::ONE),
                });
            }
        }

        Ok(Self {
            fps,
            tracks,
            raw: None,
        })
    }

    fn read_v4<R: Read + Seek>(reader: &mut R) -> Result<Self> {
        let _resource_size = reader.read_u32()?;
        let _format_token = reader.read_u32()?;
        let _flags1 = reader.read_u32()?;
        let _flags2 = reader.read_u32()?;

        let track_count = reader.read_u32()? as usize;
        let frame_count = reader.read_u32()? as usize;
        let frame_duration = reader.read_f32()?;
        let fps = if frame_duration != 0.0 {
            1.0 / frame_duration
        } else {
            0.0
        };

        let _joint_hashes_offset = reader.read_i32()?;
        let _asset_name_offset = reader.read_i32()?;
        let _time_offset = reader.read_i32()?;
        let vecs_offset = reader.read_i32()?;
        let quats_offset = reader.read_i32()?;
        let frames_offset = reader.read_i32()?;

        if vecs_offset <= 0 || quats_offset <= 0 || frames_offset <= 0 {
            return Err(Error::Unsupported("anm v4 missing data section"));
        }

        let vec_count = section_count(quats_offset - vecs_offset, 12);
        let quat_count = section_count(frames_offset - quats_offset, 16);

        reader
            .seek(SeekFrom::Start(vecs_offset as u64 + 12))
            .map_err(rs_io::Error::from)?;
        let mut vecs = Vec::with_capacity(vec_count);
        for _ in 0..vec_count {
            vecs.push(reader.read_vec3()?);
        }

        reader
            .seek(SeekFrom::Start(quats_offset as u64 + 12))
            .map_err(rs_io::Error::from)?;
        let mut quats = Vec::with_capacity(quat_count);
        for _ in 0..quat_count {
            quats.push(reader.read_quat()?);
        }

        let mut tracks: Vec<AnimTrack> = Vec::with_capacity(track_count);
        let mut hash_to_index: std::collections::HashMap<u32, usize> =
            std::collections::HashMap::with_capacity(track_count);

        reader
            .seek(SeekFrom::Start(frames_offset as u64 + 12))
            .map_err(rs_io::Error::from)?;
        for frame_id in 0..frame_count {
            let time = frame_id as f32 * frame_duration;
            for _ in 0..track_count {
                let joint_hash = reader.read_u32()?;
                let translate_id = reader.read_u16()? as usize;
                let scale_id = reader.read_u16()? as usize;
                let rotate_id = reader.read_u16()? as usize;
                let _pad = reader.read_u16()?;

                let index = *hash_to_index.entry(joint_hash).or_insert_with(|| {
                    tracks.push(AnimTrack {
                        joint_hash,
                        frames: Vec::with_capacity(frame_count),
                    });
                    tracks.len() - 1
                });

                tracks[index].frames.push(AnimFrame {
                    time,
                    rotation: quats.get(rotate_id).copied().unwrap_or(Quat::IDENTITY),
                    translation: vecs.get(translate_id).copied().unwrap_or(Vec3::ZERO),
                    scale: vecs.get(scale_id).copied().unwrap_or(Vec3::ONE),
                });
            }
        }

        Ok(Self {
            fps,
            tracks,
            raw: None,
        })
    }

    fn read_v3<R: Read + Seek>(reader: &mut R) -> Result<Self> {
        let _skeleton_id = reader.read_u32()?;
        let track_count = reader.read_u32()? as usize;
        let frame_count = reader.read_u32()? as usize;
        let fps = reader.read_u32()? as f32;
        let frame_duration = if fps != 0.0 { 1.0 / fps } else { 0.0 };

        let mut tracks = Vec::with_capacity(track_count);
        for _ in 0..track_count {
            let name = reader.read_fixed_string::<32>()?;
            let joint_hash = rs_hash::elf_lower(&name);
            let _flags = reader.read_u32()?;

            let mut frames = Vec::with_capacity(frame_count);
            for frame_id in 0..frame_count {
                let rotation = reader.read_quat()?;
                let translation = reader.read_vec3()?;
                frames.push(AnimFrame {
                    time: frame_id as f32 * frame_duration,
                    rotation,
                    translation,
                    scale: Vec3::ONE,
                });
            }
            tracks.push(AnimTrack { joint_hash, frames });
        }

        Ok(Self {
            fps,
            tracks,
            raw: None,
        })
    }
}

impl Animation {
    fn read_compressed<R: Read + Seek>(reader: &mut R, version: u32) -> Result<Self> {
        if !matches!(version, 1..=3) {
            return Err(Error::UnsupportedVersion(version));
        }

        let compressed = Compressed::from_reader(reader)?;
        let fps = compressed.fps;
        let tracks = compressed.bake();

        Ok(Self {
            fps,
            tracks,
            raw: None,
        })
    }
}

impl Parse for Animation {
    type Error = Error;

    /** Reads the whole stream into a buffer, decodes the tracks from it, and keeps the buffer so an
    unedited `read -> write` reproduces the source bytes exactly for every accepted container
    (uncompressed v3/v4/v5 and compressed `r3d2canm`). [`Animation::make_editable`] drops the buffer
    to opt into a v4 re-emit. */
    fn from_reader<R: Read + Seek>(reader: &mut R) -> Result<Self> {
        let mut bytes = Vec::new();
        reader.read_to_end(&mut bytes).map_err(rs_io::Error::from)?;

        let mut cursor = Cursor::new(&bytes);
        let magic = cursor.read_byte_array::<8>()?;
        let version = cursor.read_u32()?;
        let mut anim = match &magic {
            b"r3d2anmd" => Self::read_uncompressed(&mut cursor, version),
            b"r3d2canm" => Self::read_compressed(&mut cursor, version),
            _ => Err(Error::InvalidMagic(magic)),
        }?;

        anim.raw = Some(RawAnim { bytes });
        Ok(anim)
    }
}
