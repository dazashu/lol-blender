#![forbid(unsafe_code)]
/*!
rs_mesh reads and writes the League of Legends mesh formats: the `.skn` skinned mesh and the
binary `.scb` static mesh. The skinned reader keeps the on-disk version, flags, vertex layout,
bounds, and the trailing end-tab verbatim and stores the index and vertex buffers without
dropping degenerate triangles, so `from_reader` followed by `to_writer` reproduces the input
bytes for the common versions (1, 2, and 4). The static reader parses the `r3d2Mesh` container,
keeping its flags, bounds, vertex-type word, and any post-face bytes (the `HasVcp` color block and
local-origin/pivot data) raw so the binary form also round-trips byte-for-byte; its writer mirrors
that. The legacy text `[ObjectBegin]` form is still read into the shared model but is no longer a
focus: it was removed from the game and is not written.
*/

mod error;
mod read;
mod skinned;
mod static_mesh;
mod static_read;
mod write;

pub use error::{Error, Result};
pub use skinned::{SkinnedMesh, SkinnedMeshRange, SkinnedMeshVertex, SkinnedMeshVertexType};
pub use static_mesh::{StaticMesh, StaticMeshFace};
