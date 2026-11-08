/** Captures the exact on-disk form of an animation so the writer can reproduce the original bytes
verbatim, for every container the reader accepts (uncompressed `r3d2anmd` v3/v4/v5 and compressed
`r3d2canm`). The decoded [`crate::Animation`] keeps human-editable tracks, but several lossy steps
happen on read - the quaternion palette is normalized, the v5 palette ordering is not recoverable
from decoded poses, and compressed keyframes are dequantized and resampled - so the source bytes are
retained alongside to guarantee a byte-exact round-trip. Calling [`crate::Animation::make_editable`]
drops this, after which the writer rebuilds the file from the decoded tracks (emitting v4). */
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct RawAnim {
    pub bytes: Vec<u8>,
}
