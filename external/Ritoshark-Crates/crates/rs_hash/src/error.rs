#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error(transparent)]
    Io(#[from] std::io::Error),

    #[error("invalid hash dictionary line: {0:?}")]
    InvalidLine(String),

    #[error("invalid hash value: {0:?}")]
    InvalidHash(String),
}

pub type Result<T> = core::result::Result<T, Error>;
