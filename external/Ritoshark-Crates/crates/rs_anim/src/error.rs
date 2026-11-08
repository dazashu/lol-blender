#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error(transparent)]
    Io(#[from] rs_io::Error),

    #[error("invalid magic: {0:?}")]
    InvalidMagic([u8; 8]),

    #[error("unsupported version: {0}")]
    UnsupportedVersion(u32),

    #[error("unsupported: {0}")]
    Unsupported(&'static str),
}

pub type Result<T> = core::result::Result<T, Error>;
