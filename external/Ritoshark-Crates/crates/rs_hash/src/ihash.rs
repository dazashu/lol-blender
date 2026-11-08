/*!
The 65599-based rolling "ihash" used by the legacy `.troybin` (and inibin v1/v2) formats to key
section and property names. [`ihash`] seeds from zero; [`ihash_seeded`] continues from a previous
result, which the troybin name scheme uses to chain a section hash into its property hashes. Input
is ASCII-lowercased first, matching the on-disk convention.
*/

pub fn ihash(name: &str) -> u32 {
    ihash_seeded(0, name)
}

pub fn ihash_seeded(seed: u32, name: &str) -> u32 {
    let mut hash = seed;
    for b in name.bytes() {
        hash = (b.to_ascii_lowercase() as u32).wrapping_add(hash.wrapping_mul(65599));
    }
    hash
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_is_seed() {
        assert_eq!(ihash(""), 0);
        assert_eq!(ihash_seeded(42, ""), 42);
    }

    #[test]
    fn matches_manual_computation() {
        assert_eq!(ihash("a"), 97);
        assert_eq!(ihash("ab"), 98u32.wrapping_add(97 * 65599));
    }

    #[test]
    fn lowercases_ascii() {
        assert_eq!(ihash("System"), ihash("system"));
        assert_eq!(ihash("P-LIFE"), ihash("p-life"));
    }

    #[test]
    fn seeded_chains() {
        let seed = ihash("System");
        assert_eq!(
            ihash_seeded(seed, "p-life"),
            ihash_seeded(ihash("System"), "p-life")
        );
    }
}
