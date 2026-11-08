use std::fs::File;
use std::io::{BufWriter, Cursor, Read, Seek, Write};
use std::path::Path;

pub trait Parse: Sized {
    type Error: From<crate::Error>;

    fn from_reader<R: Read + Seek>(reader: &mut R) -> core::result::Result<Self, Self::Error>;

    fn from_bytes(bytes: &[u8]) -> core::result::Result<Self, Self::Error> {
        Self::from_reader(&mut Cursor::new(bytes))
    }

    fn from_path(path: impl AsRef<Path>) -> core::result::Result<Self, Self::Error> {
        let file = File::open(path).map_err(crate::Error::from)?;
        // SAFETY: read-only mapping; the slice is only used to fully parse Self before the map is dropped.
        let mmap = unsafe { memmap2::Mmap::map(&file) }.map_err(crate::Error::from)?;
        Self::from_bytes(&mmap)
    }
}

pub trait Serialize {
    type Error: From<crate::Error>;

    fn to_writer<W: Write>(&self, writer: &mut W) -> core::result::Result<(), Self::Error>;

    fn to_bytes(&self) -> core::result::Result<Vec<u8>, Self::Error> {
        let mut buf = Vec::new();
        self.to_writer(&mut buf)?;
        Ok(buf)
    }

    fn to_path(&self, path: impl AsRef<Path>) -> core::result::Result<(), Self::Error> {
        let mut w = BufWriter::new(File::create(path).map_err(crate::Error::from)?);
        self.to_writer(&mut w)
    }
}
