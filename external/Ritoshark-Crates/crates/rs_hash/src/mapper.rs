use std::collections::HashMap;
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::Path;

use crate::Error;
use crate::error::Result;

/// Resolves raw integer hashes back to their original names, loaded from CDTB-style dictionaries.
///
/// Dictionary lines are `<hex> <name>`, where `<hex>` is an 8- or 16-character hexadecimal value
/// parsed as a `u64` and `<name>` is the remainder of the line after the first space. Both FNV1a-32
/// and XXH64 dictionaries share this layout; 32-bit hashes simply occupy the low bits.
#[derive(Debug, Default, Clone)]
pub struct HashMapper {
    map: HashMap<u64, String>,
}

impl HashMapper {
    pub fn new() -> Self {
        Self {
            map: HashMap::new(),
        }
    }

    pub fn insert(&mut self, hash: u64, name: impl Into<String>) {
        self.map.insert(hash, name.into());
    }

    pub fn get(&self, hash: u64) -> Option<&str> {
        self.map.get(&hash).map(String::as_str)
    }

    pub fn contains(&self, hash: u64) -> bool {
        self.map.contains_key(&hash)
    }

    pub fn len(&self) -> usize {
        self.map.len()
    }

    pub fn is_empty(&self) -> bool {
        self.map.is_empty()
    }

    /// Parses `<hex> <name>` lines from `reader`, inserting each into the map and returning the
    /// number of entries loaded. Blank (or whitespace-only) lines are skipped.
    pub fn load_text<R: BufRead>(&mut self, reader: R) -> Result<usize> {
        let mut count = 0;
        for line in reader.lines() {
            let line = line?;
            let trimmed = line.trim_end_matches(['\r', '\n']);
            if trimmed.trim().is_empty() {
                continue;
            }
            let (hex, name) = trimmed
                .split_once(' ')
                .ok_or_else(|| Error::InvalidLine(trimmed.to_string()))?;
            let hash =
                u64::from_str_radix(hex, 16).map_err(|_| Error::InvalidHash(hex.to_string()))?;
            self.map.insert(hash, name.to_string());
            count += 1;
        }
        Ok(count)
    }

    /// Loads a dictionary file into a fresh mapper, returning it on success.
    pub fn load_file(path: impl AsRef<Path>) -> Result<Self> {
        let mut this = Self::new();
        let file = File::open(path)?;
        this.load_text(BufReader::new(file))?;
        Ok(this)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn loads_two_lines_from_text() {
        let data = "0123456789abcdef Common/Path\n811c9dc5 fnv_name\n";
        let mut mapper = HashMapper::new();
        let n = mapper.load_text(data.as_bytes()).unwrap();
        assert_eq!(n, 2);
        assert_eq!(mapper.len(), 2);
        assert_eq!(mapper.get(0x0123456789abcdef), Some("Common/Path"));
        assert_eq!(mapper.get(0x811c9dc5), Some("fnv_name"));
        assert!(mapper.contains(0x811c9dc5));
        assert_eq!(mapper.get(0xdead), None);
    }

    #[test]
    fn skips_blank_lines_and_preserves_name_spaces() {
        let data = "\n00000001 name with spaces\n\n";
        let mut mapper = HashMapper::new();
        let n = mapper.load_text(data.as_bytes()).unwrap();
        assert_eq!(n, 1);
        assert_eq!(mapper.get(1), Some("name with spaces"));
    }

    #[test]
    fn insert_and_query() {
        let mut mapper = HashMapper::new();
        assert!(mapper.is_empty());
        mapper.insert(42, "answer");
        assert!(!mapper.is_empty());
        assert_eq!(mapper.get(42), Some("answer"));
        assert!(mapper.contains(42));
    }
}
