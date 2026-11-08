use std::io::{Read, Seek, SeekFrom};

use rs_io::{Parse, ReaderExt};

use crate::skeleton::{Joint, Skeleton};
use crate::{Error, Result};

fn read_name_at<R: Read + Seek>(reader: &mut R, abs_offset: u64) -> Result<String> {
    let here = reader.stream_position().map_err(rs_io::Error::from)?;
    reader
        .seek(SeekFrom::Start(abs_offset))
        .map_err(rs_io::Error::from)?;
    let name = reader.read_cstring()?;
    reader
        .seek(SeekFrom::Start(here))
        .map_err(rs_io::Error::from)?;
    Ok(name)
}

impl Skeleton {
    fn read<R: Read + Seek>(reader: &mut R) -> Result<Self> {
        let file_size = reader.read_u32()?;
        let magic = reader.read_u32()?;
        if magic != Self::MAGIC {
            let mut bytes = [0u8; 8];
            bytes[0..4].copy_from_slice(&file_size.to_le_bytes());
            bytes[4..8].copy_from_slice(&magic.to_le_bytes());
            return Err(Error::InvalidMagic(bytes));
        }
        let version = reader.read_u32()?;
        if version != 0 {
            return Err(Error::UnsupportedVersion(version));
        }

        let flags = reader.read_u16()?;
        let joint_count = reader.read_u16()? as usize;
        let influence_count = reader.read_u32()? as usize;

        let joints_offset = reader.read_i32()?;
        let _joint_indices_offset = reader.read_i32()?;
        let influences_offset = reader.read_i32()?;
        let name_offset = reader.read_i32()?;
        let asset_offset = reader.read_i32()?;
        let _bone_names_offset = reader.read_i32()?;
        for _ in 0..5 {
            let _reserved = reader.read_i32()?;
        }

        let mut joints = Vec::with_capacity(joint_count);
        if joints_offset > 0 && joint_count > 0 {
            reader
                .seek(SeekFrom::Start(joints_offset as u64))
                .map_err(rs_io::Error::from)?;
            for _ in 0..joint_count {
                joints.push(read_joint(reader)?);
            }
        }

        let mut influences = Vec::with_capacity(influence_count);
        if influences_offset > 0 && influence_count > 0 {
            reader
                .seek(SeekFrom::Start(influences_offset as u64))
                .map_err(rs_io::Error::from)?;
            for _ in 0..influence_count {
                influences.push(reader.read_u16()?);
            }
        }

        let name = if name_offset > 0 {
            read_name_at(reader, name_offset as u64)?
        } else {
            String::new()
        };
        let asset = if asset_offset > 0 {
            read_name_at(reader, asset_offset as u64)?
        } else {
            String::new()
        };

        Ok(Self {
            flags,
            name,
            asset,
            joints,
            influences,
        })
    }
}

fn read_joint<R: Read + Seek>(reader: &mut R) -> Result<Joint> {
    let flags = reader.read_u16()?;
    let id = reader.read_i16()?;
    let parent_id = reader.read_i16()?;
    let _pad = reader.read_u16()?;
    let hash = reader.read_u32()?;
    let radius = reader.read_f32()?;

    let local_translation = reader.read_vec3()?;
    let local_scale = reader.read_vec3()?;
    let local_rotation = reader.read_quat()?;

    let inverse_bind_translation = reader.read_vec3()?;
    let inverse_bind_scale = reader.read_vec3()?;
    let inverse_bind_rotation = reader.read_quat()?;

    let name_offset = reader.read_i32()?;
    let return_pos = reader.stream_position().map_err(rs_io::Error::from)?;
    let name_abs = (return_pos as i64 - 4 + name_offset as i64) as u64;
    let name = read_name_at(reader, name_abs)?;
    reader
        .seek(SeekFrom::Start(return_pos))
        .map_err(rs_io::Error::from)?;

    Ok(Joint {
        name,
        flags,
        id,
        parent_id,
        radius,
        hash,
        local_translation,
        local_scale,
        local_rotation,
        inverse_bind_translation,
        inverse_bind_scale,
        inverse_bind_rotation,
    })
}

impl Parse for Skeleton {
    type Error = Error;

    fn from_reader<R: Read + Seek>(reader: &mut R) -> Result<Self> {
        reader
            .seek(SeekFrom::Start(4))
            .map_err(rs_io::Error::from)?;
        let magic = reader.read_u32()?;
        reader
            .seek(SeekFrom::Start(0))
            .map_err(rs_io::Error::from)?;
        if magic == Self::MAGIC {
            Self::read(reader)
        } else {
            let signature = reader.read_byte_array::<8>()?;
            if &signature == b"r3d2sklt" {
                let version = reader.read_u32()?;
                Err(Error::UnsupportedVersion(version))
            } else {
                Err(Error::InvalidMagic(signature))
            }
        }
    }
}
