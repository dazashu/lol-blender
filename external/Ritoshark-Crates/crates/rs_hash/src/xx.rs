/// XXH64 (seed 0) over the lowercased ASCII bytes of `s`, as used for `File` values and WAD paths.
pub fn xxh64(s: &str) -> u64 {
    xxhash_rust::xxh64::xxh64(s.to_ascii_lowercase().as_bytes(), 0)
}

/// xxh3-64 over the lowercased ASCII bytes of `s`, as used for RST string-table keys.
pub fn xxh3_64(s: &str) -> u64 {
    xxhash_rust::xxh3::xxh3_64(s.to_ascii_lowercase().as_bytes())
}

/// xxh3-64 over raw bytes verbatim (no lowercasing), as used for WAD v3.4 chunk checksums.
pub fn xxh3_64_bytes(bytes: &[u8]) -> u64 {
    xxhash_rust::xxh3::xxh3_64(bytes)
}

/// xxh3-128 over raw bytes verbatim, a wide content fingerprint for deduplicating identical blobs.
pub fn xxh3_128_bytes(bytes: &[u8]) -> u128 {
    xxhash_rust::xxh3::xxh3_128(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn case_insensitive() {
        assert_eq!(xxh64("Common/Stuff"), xxh64("common/stuff"));
        assert_eq!(xxh3_64("Common/Stuff"), xxh3_64("common/stuff"));
    }

    #[test]
    fn distinct_inputs_differ() {
        assert_ne!(xxh64("a"), xxh64("b"));
        assert_ne!(xxh3_64("a"), xxh3_64("b"));
    }
}
