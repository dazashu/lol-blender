#![forbid(unsafe_code)]
/*!
rs_anim reads and writes League skeleton (`.skl`) and animation (`.anm`) files. The skeleton side
handles the modern `0x22FD4FC3` format: a flat joint list with local and inverse-bind transforms,
the skin-influence list, and skeleton/asset names, round-tripping byte-for-byte. The animation side
reads the uncompressed `r3d2anmd` container in versions 3, 4, and 5, expanding the shared
vector/quaternion palettes into explicit per-joint keyframes, and decodes the compressed `r3d2canm`
container (versions 1-3) by dequantizing and resampling its sparse, per-component keyframe stream
into the same explicit keyframes. v3 joint hashes use the shared lowercased ELF hash
(`rs_hash::elf_lower`). Every accepted container additionally retains its complete source bytes, so
an unedited `read -> write` reproduces the original file byte-for-byte (uncompressed v3/v4/v5 and
compressed `r3d2canm` alike). Editing a parsed animation (after `make_editable`) or writing an
in-memory animation emits uncompressed version 4, where full quaternions survive a round-trip
without quantization loss; `Animation::to_compressed_bytes` encodes a compressed `r3d2canm` instead.
Legacy `r3d2sklt` skeletons (versions 1 and 2) are read by `Skeleton::from_legacy_bytes`,
and `from_any_bytes` accepts either container.
*/

mod animation;
mod animation_read;
mod animation_write;
mod compressed;
mod compressed_write;
mod error;
pub mod quantized;
mod raw;
mod skeleton;
mod skeleton_legacy;
mod skeleton_read;
mod skeleton_write;

pub use animation::{AnimFrame, AnimTrack, Animation};
pub use compressed_write::CompressionTolerances;
pub use error::{Error, Result};
pub use skeleton::{Joint, Skeleton};
pub use skeleton_legacy::{is_legacy, LEGACY_MAGIC};
