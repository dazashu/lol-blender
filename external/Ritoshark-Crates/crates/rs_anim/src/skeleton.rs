use rs_math::{Mat4, Quat, Vec3};

/// One joint (bone) of a [`Skeleton`].
#[derive(Clone, Debug, PartialEq)]
pub struct Joint {
    pub name: String,
    pub flags: u16,
    pub id: i16,
    pub parent_id: i16,
    pub radius: f32,
    pub hash: u32,
    pub local_translation: Vec3,
    pub local_scale: Vec3,
    pub local_rotation: Quat,
    pub inverse_bind_translation: Vec3,
    pub inverse_bind_scale: Vec3,
    pub inverse_bind_rotation: Quat,
}

impl Joint {
    /// Local transform composed from the stored translation, scale, and rotation.
    pub fn local_transform(&self) -> Mat4 {
        Mat4::from_scale_rotation_translation(
            self.local_scale,
            self.local_rotation,
            self.local_translation,
        )
    }

    /// Inverse bind transform composed from the stored translation, scale, and rotation.
    pub fn inverse_bind_transform(&self) -> Mat4 {
        Mat4::from_scale_rotation_translation(
            self.inverse_bind_scale,
            self.inverse_bind_rotation,
            self.inverse_bind_translation,
        )
    }
}

/// A League skeleton / rig (`.skl`).
///
/// Models the modern format (magic `0x22FD4FC3`, version `0`): a flat joint list, a list of
/// skin-influence joint ids, and optional skeleton/asset names. Legacy `r3d2sklt` skeletons are
/// rejected as [`crate::Error::UnsupportedVersion`].
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Skeleton {
    pub flags: u16,
    pub name: String,
    pub asset: String,
    pub joints: Vec<Joint>,
    pub influences: Vec<u16>,
}

impl Skeleton {
    /// Modern skeleton magic, found at byte offset 4 (bytes 0..4 hold the file size).
    pub const MAGIC: u32 = 0x22FD_4FC3;

    pub fn new() -> Self {
        Self::default()
    }

    pub fn joints(&self) -> &[Joint] {
        &self.joints
    }

    pub fn influences(&self) -> &[u16] {
        &self.influences
    }
}
