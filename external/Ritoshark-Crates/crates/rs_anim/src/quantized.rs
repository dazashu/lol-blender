use rs_math::{Quat, Vec3};

const SQRT_2: f32 = std::f32::consts::SQRT_2;
const ONE_DIV_SQRT2: f32 = SQRT_2 / 2.0;
const SQRT2_DIV_32767: f32 = SQRT_2 / 32767.0;

/** Expands a League 48-bit (6 byte) quantized quaternion. Two bits select the omitted largest
component; three 15-bit fields give the others in `[-1/√2, 1/√2]`; the omitted one is rebuilt as
`sqrt(1 - a² - b² - c²)`. */
pub fn decompress_quat(bytes: &[u8; 6]) -> Quat {
    let first = bytes[0] as u64 | ((bytes[1] as u64) << 8);
    let second = bytes[2] as u64 | ((bytes[3] as u64) << 8);
    let third = bytes[4] as u64 | ((bytes[5] as u64) << 8);
    let bits = first | (second << 16) | (third << 32);

    let max_index = (bits >> 45) & 3;
    let a = (((bits >> 30) & 32767) as f32) * SQRT2_DIV_32767 - ONE_DIV_SQRT2;
    let b = (((bits >> 15) & 32767) as f32) * SQRT2_DIV_32767 - ONE_DIV_SQRT2;
    let c = ((bits & 32767) as f32) * SQRT2_DIV_32767 - ONE_DIV_SQRT2;
    let d = (1.0 - (a * a + b * b + c * c)).max(0.0).sqrt();

    match max_index {
        0 => Quat::from_xyzw(d, a, b, c),
        1 => Quat::from_xyzw(a, d, b, c),
        2 => Quat::from_xyzw(a, b, d, c),
        _ => Quat::from_xyzw(a, b, c, d),
    }
}

/** Expands a 48-bit (6 byte) quantized vector: three `u16` channels mapped linearly from the
per-component `min`/`max` bounds carried in the compressed animation header. */
pub fn decompress_vec3(min: Vec3, max: Vec3, bytes: &[u8; 6]) -> Vec3 {
    let x = (bytes[0] as u32 | ((bytes[1] as u32) << 8)) as f32;
    let y = (bytes[2] as u32 | ((bytes[3] as u32) << 8)) as f32;
    let z = (bytes[4] as u32 | ((bytes[5] as u32) << 8)) as f32;
    Vec3::new(
        (max.x - min.x) / 65535.0 * x + min.x,
        (max.y - min.y) / 65535.0 * y + min.y,
        (max.z - min.z) / 65535.0 * z + min.z,
    )
}

/** Inverse of [`decompress_vec3`]: each component maps linearly onto `[0, 65535]` over its
`min`/`max` span and rounds to the nearest step. A zero span stores 0, which decodes to `min`. */
pub fn compress_vec3(min: Vec3, max: Vec3, vec: Vec3) -> [u8; 6] {
    let mut out = [0u8; 6];
    for (i, (lo, hi, value)) in [(min.x, max.x, vec.x), (min.y, max.y, vec.y), (min.z, max.z, vec.z)]
        .into_iter()
        .enumerate()
    {
        let span = hi - lo;
        let step = if span <= 0.0 {
            0
        } else {
            (((value - lo) / span * 65535.0).round() as i64).clamp(0, 65535) as u16
        };
        out[i * 2..i * 2 + 2].copy_from_slice(&step.to_le_bytes());
    }
    out
}

/** Packs a quaternion into League 48-bit (6 byte) quantized form: the largest-magnitude component
is dropped and flagged in 2 bits, the other three are stored in 15 bits each. */
pub fn compress_quat(quat: Quat) -> [u8; 6] {
    let abs_x = quat.x.abs();
    let abs_y = quat.y.abs();
    let abs_z = quat.z.abs();
    let abs_w = quat.w.abs();

    let (max_index, q) = if abs_x >= abs_w && abs_x >= abs_y && abs_x >= abs_z {
        (0u64, if quat.x < 0.0 { -quat } else { quat })
    } else if abs_y >= abs_w && abs_y >= abs_x && abs_y >= abs_z {
        (1u64, if quat.y < 0.0 { -quat } else { quat })
    } else if abs_z >= abs_w && abs_z >= abs_x && abs_z >= abs_y {
        (2u64, if quat.z < 0.0 { -quat } else { quat })
    } else {
        (3u64, if quat.w < 0.0 { -quat } else { quat })
    };

    let mut bits = max_index << 45;
    let values = [q.x, q.y, q.z, q.w];
    let mut compressed_index = 0u64;
    for (i, &val) in values.iter().enumerate() {
        if i as u64 == max_index {
            continue;
        }
        let temp = ((16383.5 * (SQRT_2 * val + 1.0)).round() as i64).clamp(0, 32767) as u64;
        bits |= temp << (30 - 15 * compressed_index);
        compressed_index += 1;
    }

    [
        (bits & 0xFF) as u8,
        ((bits >> 8) & 0xFF) as u8,
        ((bits >> 16) & 0xFF) as u8,
        ((bits >> 24) & 0xFF) as u8,
        ((bits >> 32) & 0xFF) as u8,
        ((bits >> 40) & 0xFF) as u8,
    ]
}
