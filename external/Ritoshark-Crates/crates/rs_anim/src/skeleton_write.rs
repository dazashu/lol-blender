use std::io::{Cursor, Seek, SeekFrom, Write};

use rs_io::{Serialize, WriterExt};

use crate::skeleton::{Joint, Skeleton};
use crate::{Error, Result};

const JOINTS_OFFSET: usize = 64;

/// Riot's layout, which this writer did not reproduce and now does.
///
/// After the influence list come, in order and each padded to a 4-byte boundary: the skeleton
/// name, the asset name, then the joint name table. An EMPTY name still occupies its four bytes
/// and still has the header offset pointing at it - the previous code wrote both at the end of the
/// file and only when non-empty, and packed the joint names with no padding at all.
///
/// MEASURED across 18 `.skl` files on disk. Ten follow this layout, and for every one of them the
/// header's `name_offset`, `asset_offset` and `bone_names_offset` are exactly
/// `align4(influence_end)`, `+ align4(len(name) + 1)` and `+ align4(len(asset) + 1)`, with
/// `bone_names_offset + sum(align4(len + 1))` landing precisely on the file size. The other eight
/// are packed tight with the names at the end - those came from lol_maya, whose writer does none of
/// this. The two groups partition the sample with no leftovers, which is what makes this a rule
/// rather than a curve fitted to a couple of files.
const NAME_ALIGN: usize = 4;

const fn align_up(value: usize, to: usize) -> usize {
    value.div_ceil(to) * to
}
const JOINT_RECORD_SIZE: usize = 100;
const JOINT_INDEX_SIZE: usize = 8;

impl Skeleton {
    fn write_to(&self, buf: &mut Cursor<Vec<u8>>) -> Result<()> {
        let joint_count = self.joints.len();
        let influence_count = self.influences.len();

        buf.write_u32(0)?; // file size, patched last
        buf.write_u32(Self::MAGIC)?;
        buf.write_u32(0)?; // version

        buf.write_u16(self.flags)?;
        buf.write_u16(joint_count as u16)?;
        buf.write_u32(influence_count as u32)?;

        let joint_indices_offset = JOINTS_OFFSET + joint_count * JOINT_RECORD_SIZE;
        let influences_offset = joint_indices_offset + joint_count * JOINT_INDEX_SIZE;
        let skeleton_name_offset = align_up(influences_offset + influence_count * 2, NAME_ALIGN);
        let asset_name_offset =
            skeleton_name_offset + align_up(self.name.len() + 1, NAME_ALIGN);
        let joint_names_offset = asset_name_offset + align_up(self.asset.len() + 1, NAME_ALIGN);

        buf.write_i32(JOINTS_OFFSET as i32)?;
        buf.write_i32(joint_indices_offset as i32)?;
        buf.write_i32(influences_offset as i32)?;

        buf.write_i32(skeleton_name_offset as i32)?;
        buf.write_i32(asset_name_offset as i32)?;
        buf.write_i32(joint_names_offset as i32)?;
        for _ in 0..5 {
            buf.write_u32(0xFFFF_FFFF)?;
        }

        buf.seek(SeekFrom::Start(joint_names_offset as u64))
            .map_err(rs_io::Error::from)?;
        let mut joint_name_offsets = Vec::with_capacity(joint_count);
        for joint in &self.joints {
            let start = buf.stream_position().map_err(rs_io::Error::from)?;
            joint_name_offsets.push(start);
            write_padded_cstring(buf, &joint.name)?;
        }

        buf.seek(SeekFrom::Start(JOINTS_OFFSET as u64))
            .map_err(rs_io::Error::from)?;
        for (joint, &name_off) in self.joints.iter().zip(joint_name_offsets.iter()) {
            write_joint(buf, joint, name_off)?;
        }

        buf.seek(SeekFrom::Start(influences_offset as u64))
            .map_err(rs_io::Error::from)?;
        for &inf in &self.influences {
            buf.write_u16(inf)?;
        }

        buf.seek(SeekFrom::Start(joint_indices_offset as u64))
            .map_err(rs_io::Error::from)?;
        let mut hash_ids: Vec<(i16, u32)> = self.joints.iter().map(|j| (j.id, j.hash)).collect();
        hash_ids.sort_by_key(|&(_, hash)| hash);
        for (id, hash) in hash_ids {
            buf.write_i16(id)?;
            buf.write_i16(0)?;
            buf.write_u32(hash)?;
        }

        buf.seek(SeekFrom::Start(skeleton_name_offset as u64))
            .map_err(rs_io::Error::from)?;
        write_padded_cstring(buf, &self.name)?;
        buf.seek(SeekFrom::Start(asset_name_offset as u64))
            .map_err(rs_io::Error::from)?;
        write_padded_cstring(buf, &self.asset)?;

        let file_size = buf.seek(SeekFrom::End(0)).map_err(rs_io::Error::from)? as u32;
        buf.seek(SeekFrom::Start(0)).map_err(rs_io::Error::from)?;
        buf.write_u32(file_size)?;
        Ok(())
    }
}

/// A NUL-terminated string padded to the next 4-byte boundary, which is how Riot writes every name
/// in a `.skl`. Always leaves at least the terminator, so a name that is already a multiple of 4
/// still gains four bytes rather than running into the next one.
fn write_padded_cstring(buf: &mut Cursor<Vec<u8>>, value: &str) -> Result<()> {
    buf.write_cstring(value)?;
    let written = value.len() + 1;
    for _ in written..align_up(written, NAME_ALIGN) {
        buf.write_u8(0)?;
    }
    Ok(())
}

fn write_joint(buf: &mut Cursor<Vec<u8>>, joint: &Joint, name_off: u64) -> Result<()> {
    buf.write_u16(joint.flags)?;
    buf.write_i16(joint.id)?;
    buf.write_i16(joint.parent_id)?;
    buf.write_u16(0)?; // pad
    buf.write_u32(joint.hash)?;
    buf.write_f32(joint.radius)?;

    buf.write_vec3(joint.local_translation)?;
    buf.write_vec3(joint.local_scale)?;
    buf.write_quat(joint.local_rotation)?;

    buf.write_vec3(joint.inverse_bind_translation)?;
    buf.write_vec3(joint.inverse_bind_scale)?;
    buf.write_quat(joint.inverse_bind_rotation)?;

    let pos = buf.stream_position().map_err(rs_io::Error::from)?;
    buf.write_i32((name_off as i64 - pos as i64) as i32)?;
    Ok(())
}

impl Serialize for Skeleton {
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
