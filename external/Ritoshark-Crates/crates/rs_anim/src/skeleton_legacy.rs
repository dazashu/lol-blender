//! The legacy `r3d2sklt` skeleton, which the modern reader rejects.
//!
//! Ported from lol_maya's `SKL.read` (GPL-3.0, `lol_maya.py:1094-1148`), which is the only
//! implementation that still reads these. Old champion assets and a lot of community files are
//! still in this format, and "unsupported version" is not a useful thing to tell someone holding
//! one.
//!
//! THE FORMAT, and the three things about it that are easy to get wrong:
//!
//!   * the magic is 8 ASCII bytes at offset 0, not a u32 at offset 4 like the modern one;
//!   * each joint stores its GLOBAL transform as a 3x4 matrix in COLUMN-major order - three
//!     columns of four - so it has to be transposed on the way in, and the fourth row is implied;
//!   * version 1 has no influence list at all and every joint is an influence; version 2 stores
//!     one explicitly.
//!
//! The local transforms the modern struct wants are derived here, as `global * parent_global^-1`,
//! because the legacy format simply does not store them.

use std::io::{Cursor, Read};

use rs_io::ReaderExt;
use rs_math::Mat4;

use crate::skeleton::{Joint, Skeleton};
use crate::{Error, Result};

pub const LEGACY_MAGIC: &[u8; 8] = b"r3d2sklt";

/// Fixed-width joint name field. Names are NUL-padded to 32 bytes.
const NAME_SIZE: usize = 32;

/// True when these bytes start with the legacy magic.
pub fn is_legacy(bytes: &[u8]) -> bool {
    bytes.len() >= 8 && &bytes[..8] == LEGACY_MAGIC
}

impl Skeleton {
    /// Parse an `r3d2sklt` skeleton into the modern in-memory form.
    pub fn from_legacy_bytes(bytes: &[u8]) -> Result<Self> {
        let mut cursor = Cursor::new(bytes);
        let mut magic = [0u8; 8];
        cursor
            .read_exact(&mut magic)
            .map_err(|e| Error::from(rs_io::Error::from(e)))?;
        if &magic != LEGACY_MAGIC {
            return Err(Error::InvalidMagic(magic));
        }

        let version = cursor.read_u32()?;
        if version != 1 && version != 2 {
            return Err(Error::UnsupportedVersion(version));
        }
        let _designer_id = cursor.read_u32()?;

        let joint_count = cursor.read_u32()? as usize;
        let mut names = Vec::with_capacity(joint_count);
        let mut parents = Vec::with_capacity(joint_count);
        let mut globals = Vec::with_capacity(joint_count);

        for _ in 0..joint_count {
            let mut raw = [0u8; NAME_SIZE];
            cursor
                .read_exact(&mut raw)
                .map_err(|e| Error::from(rs_io::Error::from(e)))?;
            let end = raw.iter().position(|&b| b == 0).unwrap_or(NAME_SIZE);
            names.push(String::from_utf8_lossy(&raw[..end]).into_owned());

            parents.push(cursor.read_i32()? as i16);
            let _radius = cursor.read_f32()?;

            // Three COLUMNS of four floats. The modern struct wants row-major, so this walks the
            // file in the file's order and writes into the transposed slot.
            let mut m = [0.0f32; 16];
            for column in 0..3 {
                for row in 0..4 {
                    m[row * 4 + column] = cursor.read_f32()?;
                }
            }
            m[15] = 1.0;
            globals.push(Mat4::from_cols_array(&[
                m[0], m[4], m[8], m[12], m[1], m[5], m[9], m[13], m[2], m[6], m[10], m[14], m[3],
                m[7], m[11], m[15],
            ]));
        }

        let influences: Vec<u16> = if version == 1 {
            (0..joint_count as u16).collect()
        } else {
            let count = cursor.read_u32()? as usize;
            let mut out = Vec::with_capacity(count);
            for _ in 0..count {
                out.push(cursor.read_u32()? as u16);
            }
            out
        };

        let mut joints = Vec::with_capacity(joint_count);
        for index in 0..joint_count {
            let global = globals[index];
            let parent_id = parents[index];
            let local = if parent_id < 0 {
                global
            } else {
                let parent = globals[parent_id as usize];
                parent.inverse() * global
            };
            let (local_scale, local_rotation, local_translation) =
                local.to_scale_rotation_translation();
            let inverse = global.inverse();
            let (inverse_bind_scale, inverse_bind_rotation, inverse_bind_translation) =
                inverse.to_scale_rotation_translation();

            joints.push(Joint {
                hash: rs_hash::elf_lower(&names[index]),
                name: std::mem::take(&mut names[index]),
                flags: 0,
                id: index as i16,
                parent_id,
                radius: 2.1,
                local_translation,
                local_rotation,
                local_scale,
                inverse_bind_translation,
                inverse_bind_rotation,
                inverse_bind_scale,
            });
        }

        Ok(Self {
            flags: 0,
            name: String::new(),
            asset: String::new(),
            joints,
            influences,
        })
    }

    /// Parse either container. Modern files go through the normal reader; legacy ones through the
    /// one above, so a caller never has to sniff the magic itself.
    pub fn from_any_bytes(bytes: &[u8]) -> Result<Self> {
        if is_legacy(bytes) {
            return Self::from_legacy_bytes(bytes);
        }
        <Self as rs_io::Parse>::from_bytes(bytes)
    }

    pub fn from_any_path(path: impl AsRef<std::path::Path>) -> Result<Self> {
        let bytes = std::fs::read(path).map_err(|e| Error::from(rs_io::Error::from(e)))?;
        Self::from_any_bytes(&bytes)
    }
}
