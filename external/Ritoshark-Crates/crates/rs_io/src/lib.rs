#![deny(unsafe_op_in_unsafe_fn)]
/*!
rs_io is the shared I/O contract the format crates build on. It defines the little-endian
reader and writer extension traits that add typed scalar, string, vector, quaternion, matrix,
and color reads and writes to any std reader or writer, the one `Error`/`Result` pair those
helpers surface, and the universal `Parse`/`Serialize` traits every format type implements so
that parsing from a reader, byte slice, or memory-mapped file and serializing back are spelled
the same way everywhere.
*/

mod error;
mod ext;
mod traits;

pub use error::{Error, Result};
pub use ext::{ReaderExt, WriterExt};
pub use traits::{Parse, Serialize};
