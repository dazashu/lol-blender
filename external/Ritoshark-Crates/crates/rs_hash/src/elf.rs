/*!
SystemV ELF hash, used for skeleton joint names and animation joint references. [`elf`] hashes
the bytes as given; [`elf_lower`] lowercases ASCII first, which is the variant the skeleton and
animation formats actually use to key joints.
*/

fn hash_bytes(bytes: impl Iterator<Item = u8>) -> u32 {
    let mut hash: u32 = 0;
    for b in bytes {
        hash = (hash << 4).wrapping_add(b as u32);
        let high = hash & 0xF000_0000;
        if high != 0 {
            hash ^= high >> 24;
        }
        hash &= !high;
    }
    hash
}

pub fn elf(name: &str) -> u32 {
    hash_bytes(name.bytes())
}

pub fn elf_lower(name: &str) -> u32 {
    hash_bytes(name.bytes().map(|b| b.to_ascii_lowercase()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_is_zero() {
        assert_eq!(elf(""), 0);
        assert_eq!(elf_lower(""), 0);
    }

    #[test]
    fn lower_is_case_insensitive() {
        assert_eq!(elf_lower("Root"), elf_lower("root"));
        assert_eq!(elf_lower("ROOT"), elf("root"));
    }

    #[test]
    fn raw_differs_on_case() {
        assert_ne!(elf("Root"), elf("root"));
    }
}
