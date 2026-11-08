#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error(transparent)]
    Io(#[from] rs_io::Error),

    #[error(transparent)]
    StdIo(#[from] std::io::Error),

    #[error("invalid mesh magic")]
    InvalidMagic,

    #[error("unsupported mesh version: {0}")]
    UnsupportedVersion(u32),

    #[error("invalid vertex type: {0}")]
    InvalidVertexType(u32),

    #[error("index count {0} is not a multiple of 3")]
    BadIndexCount(u32),

    #[error("malformed text mesh: {0}")]
    MalformedText(String),

    #[error("unsupported: {0}")]
    Unsupported(&'static str),
}

pub type Result<T> = core::result::Result<T, Error>;
