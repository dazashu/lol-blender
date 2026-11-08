use rs_math::{Quat, Vec3};

use crate::raw::RawAnim;

/// A single keyframe for one joint: a pose sampled at `time` seconds.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct AnimFrame {
    pub time: f32,
    pub rotation: Quat,
    pub translation: Vec3,
    pub scale: Vec3,
}

impl AnimFrame {
    pub fn new(time: f32, rotation: Quat, translation: Vec3, scale: Vec3) -> Self {
        Self {
            time,
            rotation,
            translation,
            scale,
        }
    }
}

/// All keyframes belonging to one joint, identified by its hash.
#[derive(Clone, Debug, PartialEq)]
pub struct AnimTrack {
    pub joint_hash: u32,
    pub frames: Vec<AnimFrame>,
}

impl AnimTrack {
    pub fn new(joint_hash: u32) -> Self {
        Self {
            joint_hash,
            frames: Vec::new(),
        }
    }
}

/// A League animation (`.anm`).
///
/// Holds one [`AnimTrack`] per animated joint. Reading supports the uncompressed `r3d2anmd`
/// container (versions 3, 4, 5) and the compressed `r3d2canm` container (versions 1-3). Every
/// accepted container preserves its source bytes so an unedited `read -> write` is byte-exact;
/// after [`Animation::make_editable`] (or for in-memory animations) writing emits uncompressed
/// version 4 (full quaternions) so values round-trip without quantization loss.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Animation {
    pub fps: f32,
    pub tracks: Vec<AnimTrack>,
    pub(crate) raw: Option<RawAnim>,
}

impl Animation {
    pub fn new(fps: f32) -> Self {
        Self {
            fps,
            tracks: Vec::new(),
            raw: None,
        }
    }

    pub fn tracks(&self) -> &[AnimTrack] {
        &self.tracks
    }

    /// True when this animation was read from a file and still carries the preserved source bytes
    /// that let the writer reproduce the original file exactly (any accepted container: uncompressed
    /// v3/v4/v5 or compressed `r3d2canm`).
    pub fn is_byte_exact(&self) -> bool {
        self.raw.is_some()
    }

    /// Drops the preserved source bytes so the writer rebuilds the file from the decoded tracks
    /// (emitting uncompressed v4). Call this after mutating `tracks` if the source was a byte-exact
    /// file, otherwise the writer re-emits the original, unedited bytes.
    pub fn make_editable(&mut self) {
        self.raw = None;
    }

    /// Frame count of the first track, or `0` when empty. Every track is expected to share the
    /// same frame count in the uncompressed format.
    pub fn frame_count(&self) -> usize {
        self.tracks.first().map(|t| t.frames.len()).unwrap_or(0)
    }
}
