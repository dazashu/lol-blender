#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("unexpected end of input: needed {needed} byte(s) at offset {offset}, had {available}")]
    UnexpectedEof {
        offset: usize,
        needed: usize,
        available: usize,
    },
    #[error("invalid utf-8 string")]
    InvalidUtf8(#[from] std::string::FromUtf8Error),
    #[error(transparent)]
    Io(#[from] std::io::Error),
}

pub type Result<T> = core::result::Result<T, Error>;
