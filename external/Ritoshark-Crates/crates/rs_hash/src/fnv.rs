const FNV_BASIS: u32 = 0x811c9dc5;
const FNV_PRIME: u32 = 0x01000193;

/// FNV1a-32 over the lowercased ASCII bytes of `s`, as used for bin field, class, entry, and
/// hash/link names. Bytes `A`–`Z` fold to `a`–`z`; all other bytes pass through unchanged.
pub const fn fnv1a(s: &str) -> u32 {
    let bytes = s.as_bytes();
    let mut h = FNV_BASIS;
    let mut i = 0;
    while i < bytes.len() {
        let b = bytes[i];
        let c = if b >= b'A' && b <= b'Z' {
            b + (b'a' - b'A')
        } else {
            b
        };
        h = (h ^ c as u32).wrapping_mul(FNV_PRIME);
        i += 1;
    }
    h
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_is_basis() {
        assert_eq!(fnv1a(""), 0x811c9dc5);
    }

    #[test]
    fn case_insensitive() {
        assert_eq!(fnv1a("Hello"), fnv1a("hello"));
        assert_eq!(fnv1a("MixedCase123"), fnv1a("mixedcase123"));
    }

    #[test]
    fn usable_in_const() {
        const H: u32 = fnv1a("mevent");
        assert_eq!(H, fnv1a("mEvent"));
    }
}
