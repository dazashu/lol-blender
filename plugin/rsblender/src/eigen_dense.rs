// SPDX-License-Identifier: MPL-2.0
// Rust translation of the Eigen 3.4 dense kernels SparseLU reaches in Blender 4.2
// (extern/Eigen3): TriangularSolverVector.h, GeneralMatrixVector.h (col-major),
// TriangularSolverMatrix.h (OnTheLeft, col-major), GeneralBlockPanelKernel.h (gebp for
// double/SSE2, mr = nr = 4, LhsProgress = 2, blocking heuristic), SparseLU_gemm_kernel.h and
// util/Memory.h (queryCacheSizes). Copyright (C) 2008-2015 Gael Guennebaud, 2012 Desire
// Nuentsa-Wakam and the Eigen authors. Modified: translated to Rust.
//
// THE POINT IS THE ORDER OF THE ROUNDINGS. Blender's build of these kernels (MSVC, x64, SSE2,
// no FMA) gives every output element a fixed sequence of IEEE double operations; packets only
// run independent elements side by side, so each element is computed here with exactly the
// sequence its packet lane or scalar path performs: where a kernel splits a sum (sparselu_gemm's
// scalar tail, gebp's even/odd accumulators), the split is reproduced, and nothing else is.

/// Elements of `f64` per SSE2 packet.
pub const PACKET: usize = 2;

/// `MatrixRef`: a column-major block inside a buffer, `data[off + i + j*ld]`.
#[derive(Clone, Copy)]
pub struct At {
    pub off: usize,
    pub ld: usize,
}
impl At {
    #[inline]
    pub fn idx(&self, i: usize, j: usize) -> usize {
        self.off + i + j * self.ld
    }
}

/// general_matrix_vector_product<ColMajor>::run: `res[i] += alpha * sum_j A(i,j) * x[j]`.
/// Each element: per block of columns, `c = 0; c = a*b + c` in column order, then
/// `res = c*alpha + res` (packet lanes) or `res + alpha*c` (scalar rows): the same value.
pub fn gemv(rows: usize, cols: usize, a: &[f64], at: At, x: &[f64], x_off: usize, res: &mut [f64], res_off: usize, alpha: f64) {
    let block_cols = if cols < 128 {
        cols
    } else if at.ld * 8 < 32000 {
        16
    } else {
        4
    };
    if block_cols == 0 || rows == 0 {
        return;
    }
    // Column by column into per-row accumulators: each element still gets c = a*b + c in
    // column order from 0, only the order between elements changes (cache friendly).
    let mut acc = vec![0.0f64; rows];
    let mut j2 = 0;
    while j2 < cols {
        let jend = (j2 + block_cols).min(cols);
        acc.iter_mut().for_each(|c| *c = 0.0);
        for j in j2..jend {
            let xj = x[x_off + j];
            let start = at.idx(0, j);
            for (c, &aij) in acc.iter_mut().zip(&a[start..start + rows]) {
                *c = aij * xj + *c;
            }
        }
        for (r, &c) in res[res_off..res_off + rows].iter_mut().zip(&acc) {
            *r = c * alpha + *r;
        }
        j2 += block_cols;
    }
}

/// generic_product_impl<..., GemvProduct>::scaleAndAddTo (ProductEvaluators.h 376): a
/// one-row product falls back to `dst(0) += alpha * row.dot(x)`, a scalar redux that starts
/// from the first product; anything else is the gemv kernel.
#[allow(clippy::too_many_arguments)]
pub fn gemv_product(rows: usize, cols: usize, a: &[f64], at: At, x: &[f64], x_off: usize, res: &mut [f64], res_off: usize, alpha: f64) {
    if rows == 1 && cols > 0 {
        let mut dot = a[at.idx(0, 0)] * x[x_off];
        for j in 1..cols {
            dot = dot + a[at.idx(0, j)] * x[x_off + j];
        }
        res[res_off] += alpha * dot;
        return;
    }
    gemv(rows, cols, a, at, x, x_off, res, res_off, alpha);
}

/// gemv where the right-hand side lives in the same buffer as the result (TRSV's update).
fn gemv_in_place(rows: usize, cols: usize, a: &[f64], at: At, v: &mut [f64], x_off: usize, res_off: usize, alpha: f64) {
    let block_cols = if cols < 128 {
        cols
    } else if at.ld * 8 < 32000 {
        16
    } else {
        4
    };
    if block_cols == 0 || rows == 0 {
        return;
    }
    let mut acc = vec![0.0f64; rows];
    let mut j2 = 0;
    while j2 < cols {
        let jend = (j2 + block_cols).min(cols);
        acc.iter_mut().for_each(|c| *c = 0.0);
        for j in j2..jend {
            let xj = v[x_off + j];
            let start = at.idx(0, j);
            for (c, &aij) in acc.iter_mut().zip(&a[start..start + rows]) {
                *c = aij * xj + *c;
            }
        }
        for (r, &c) in v[res_off..res_off + rows].iter_mut().zip(&acc) {
            *r = c * alpha + *r;
        }
        j2 += block_cols;
    }
}

const PANEL_WIDTH: usize = 8; // EIGEN_TUNE_TRIANGULAR_PANEL_WIDTH

/// triangular_solve_vector<OnTheLeft, Lower(|UnitDiag), ColMajor>.
pub fn trsv_lower(size: usize, a: &[f64], at: At, x: &mut [f64], x_off: usize, unit: bool) {
    let mut pi = 0;
    while pi < size {
        let width = (size - pi).min(PANEL_WIDTH);
        let end_block = pi + width;
        for k in 0..width {
            let i = pi + k;
            if x[x_off + i] != 0.0 {
                if !unit {
                    x[x_off + i] /= a[at.idx(i, i)];
                }
                let r = width - k - 1;
                let s = i + 1;
                let xi = x[x_off + i];
                for t in 0..r {
                    x[x_off + s + t] -= xi * a[at.idx(s + t, i)];
                }
            }
        }
        let r = size - end_block;
        if r > 0 {
            let lhs = At { off: at.idx(end_block, pi), ld: at.ld };
            gemv_in_place(r, width, a, lhs, x, x_off + pi, x_off + end_block, -1.0);
        }
        pi += PANEL_WIDTH;
    }
}

/// triangular_solve_vector<OnTheLeft, Upper(|UnitDiag), ColMajor>.
pub fn trsv_upper(size: usize, a: &[f64], at: At, x: &mut [f64], x_off: usize, unit: bool) {
    let mut pi = size;
    while pi > 0 {
        let width = pi.min(PANEL_WIDTH);
        let start_block = pi - width;
        for k in 0..width {
            let i = pi - k - 1;
            if x[x_off + i] != 0.0 {
                if !unit {
                    x[x_off + i] /= a[at.idx(i, i)];
                }
                let r = width - k - 1;
                let s = i - r;
                let xi = x[x_off + i];
                for t in 0..r {
                    x[x_off + s + t] -= xi * a[at.idx(s + t, i)];
                }
            }
        }
        let r = start_block;
        if r > 0 {
            let lhs = At { off: at.idx(0, start_block), ld: at.ld };
            gemv_in_place(r, width, a, lhs, x, x_off + start_block, x_off, -1.0);
        }
        pi -= width;
    }
}

/// SparseLU_gemm_kernel.h `sparselu_gemm`: C += A*B, all column-major. `a_parity` is the
/// element offset of A from a 16-byte boundary (Eigen allocates its vectors 16-byte aligned,
/// so it is the parity of A's index in `lusup`).
#[allow(clippy::too_many_arguments)]
pub fn sparselu_gemm(m: usize, n: usize, d: usize, a: &[f64], at: At, a_parity: usize, b: &[f64], bt: At, c: &mut [f64], ct: At) {
    const RK: usize = 4; // EIGEN_ARCH_DEFAULT_NUMBER_OF_REGISTERS = 16 on x64
    const BM: usize = 4096 / 8;
    let d_end = (d / RK) * RK;
    let i0 = a_parity.min(m);
    // Non aligned rows: plain sequential dot products.
    for i in 0..i0 {
        for j in 0..n {
            let mut v = c[ct.idx(i, j)];
            for k in 0..d {
                v += b[bt.idx(k, j)] * a[at.idx(i, k)];
            }
            c[ct.idx(i, j)] = v;
        }
    }
    // Each element keeps its own sequence: per block of 4 depths either four `c = c + a*b`
    // (packet rows) or `c += ((p0+p1)+p2)+p3` (scalar tail rows), then the remaining depths
    // summed first and added. The loops run column by column for the cache; only the order
    // between different elements differs from Eigen's.
    let mut ib = i0;
    while ib < m {
        let actual_b = BM.min(m - ib);
        let end2 = (actual_b / PACKET) * PACKET;
        for j in 0..n {
            let cstart = ct.idx(ib, j);
            let ccol = &mut c[cstart..cstart + actual_b];
            let col = |k: usize| {
                let s = at.idx(ib, k);
                &a[s..s + actual_b]
            };
            let mut k = 0;
            while k < d_end {
                let (b0, b1, b2, b3) = (b[bt.idx(k, j)], b[bt.idx(k + 1, j)], b[bt.idx(k + 2, j)], b[bt.idx(k + 3, j)]);
                let (a0, a1, a2, a3) = (col(k), col(k + 1), col(k + 2), col(k + 3));
                // KMADD: c = c + a*b, four times (separate multiply and add: Rust never fuses
                // them into an FMA, so vectorizing this loop keeps every rounding).
                for ((((cv, &x0), &x1), &x2), &x3) in ccol[..end2]
                    .iter_mut()
                    .zip(&a0[..end2])
                    .zip(&a1[..end2])
                    .zip(&a2[..end2])
                    .zip(&a3[..end2])
                {
                    let mut v = *cv;
                    v = v + x0 * b0;
                    v = v + x1 * b1;
                    v = v + x2 * b2;
                    v = v + x3 * b3;
                    *cv = v;
                }
                for r in end2..actual_b {
                    // C0[i] += A0[i]*Bc0[0]+A1[i]*Bc0[1]+A2[i]*Bc0[2]+A3[i]*Bc0[3];
                    ccol[r] += ((a0[r] * b0 + a1[r] * b1) + a2[r] * b2) + a3[r] * b3;
                }
                k += RK;
            }
            let rd = d - d_end;
            if rd == 1 {
                let (b0, a0) = (b[bt.idx(d_end, j)], col(d_end));
                for r in 0..actual_b {
                    ccol[r] += b0 * a0[r];
                }
            } else if rd == 2 {
                let (b0, a0) = (b[bt.idx(d_end, j)], col(d_end));
                let (b1, a1) = (b[bt.idx(d_end + 1, j)], col(d_end + 1));
                for r in 0..actual_b {
                    ccol[r] += b0 * a0[r] + b1 * a1[r];
                }
            } else if rd == 3 {
                let (b0, a0) = (b[bt.idx(d_end, j)], col(d_end));
                let (b1, a1) = (b[bt.idx(d_end + 1, j)], col(d_end + 1));
                let (b2, a2) = (b[bt.idx(d_end + 2, j)], col(d_end + 2));
                for r in 0..actual_b {
                    ccol[r] += (b0 * a0[r] + b1 * a1[r]) + b2 * a2[r];
                }
            }
        }
        ib += BM;
    }
}

// --------------------------------------------------------------------------------------------
// Cache sizes and gebp blocking

#[derive(Clone, Copy, Debug)]
pub struct Caches {
    pub l1: isize,
    pub l2: isize,
    pub l3: isize,
}

/// util/Memory.h `queryCacheSizes`, then `CacheSizes()`' defaults for anything reported <= 0.
pub fn caches() -> Caches {
    static CACHES: std::sync::OnceLock<Caches> = std::sync::OnceLock::new();
    *CACHES.get_or_init(|| {
        let (l1, l2, l3) = query_cache_sizes();
        let pick = |a: i32, b: isize| if a <= 0 { b } else { a as isize };
        Caches { l1: pick(l1, 32 * 1024), l2: pick(l2, 256 * 1024), l3: pick(l3, 2 * 1024 * 1024) }
    })
}

#[cfg(target_arch = "x86_64")]
fn cpuid(func: u32, id: u32) -> [i32; 4] {
    let r = core::arch::x86_64::__cpuid_count(func, id);
    [r.eax as i32, r.ebx as i32, r.ecx as i32, r.edx as i32]
}

#[cfg(target_arch = "x86_64")]
fn query_cache_sizes() -> (i32, i32, i32) {
    const INTEL: [i32; 3] = [0x756e6547, 0x49656e69, 0x6c65746e];
    const AMD: [i32; 3] = [0x68747541, 0x69746e65, 0x444d4163];
    const AMD2: [i32; 3] = [0x69444d41, 0x74656273, 0x21726574];
    let abcd = cpuid(0, 0);
    let vendor = |v: [i32; 3]| abcd[1] == v[0] && abcd[3] == v[1] && abcd[2] == v[2];
    let max_std = abcd[0];
    if vendor(INTEL) {
        intel(max_std)
    } else if vendor(AMD) || vendor(AMD2) {
        let abcd = cpuid(0x8000_0000, 0);
        if (abcd[0] as u32) >= 0x8000_0006 {
            let c5 = cpuid(0x8000_0005, 0);
            let l1 = (c5[2] >> 24) * 1024;
            let c6 = cpuid(0x8000_0006, 0);
            let l2 = (c6[2] >> 16) * 1024;
            let l3 = ((c6[3] & 0xFFFC000) >> 18) * 512 * 1024;
            (l1, l2, l3)
        } else {
            (0, 0, 0)
        }
    } else {
        intel(max_std)
    }
}

#[cfg(target_arch = "x86_64")]
fn intel(max_std: i32) -> (i32, i32, i32) {
    if max_std >= 4 {
        let (mut l1, mut l2, mut l3) = (0, 0, 0);
        let mut cache_id = 0;
        loop {
            let abcd = cpuid(4, cache_id);
            let cache_type = abcd[0] & 0x0F;
            if cache_type == 1 || cache_type == 3 {
                let level = (abcd[0] & 0xE0) >> 5;
                let ways = ((abcd[1] as u32 & 0xFFC0_0000) >> 22) as i32;
                let partitions = (abcd[1] & 0x003F_F000) >> 12;
                let line = abcd[1] & 0x0000_0FFF;
                let sets = abcd[2];
                let size = (ways + 1).wrapping_mul(partitions + 1).wrapping_mul(line + 1).wrapping_mul(sets.wrapping_add(1));
                match level {
                    1 => l1 = size,
                    2 => l2 = size,
                    3 => l3 = size,
                    _ => {}
                }
            }
            cache_id += 1;
            if !(cache_type > 0 && cache_id < 16) {
                break;
            }
        }
        (l1, l2, l3)
    } else if max_std >= 2 {
        intel_codes()
    } else {
        (0, 0, 0)
    }
}

#[cfg(target_arch = "x86_64")]
fn intel_codes() -> (i32, i32, i32) {
    let abcd = cpuid(2, 0);
    let mut raw = [0u8; 16];
    for (k, v) in abcd.iter().enumerate() {
        raw[k * 4..k * 4 + 4].copy_from_slice(&v.to_le_bytes());
    }
    let (mut l1, mut l2, mut l3) = (0i32, 0i32, 0i32);
    let mut p2 = false;
    for &byte in &raw[2..16] {
        match byte {
            0x0A => l1 = 8, 0x0C => l1 = 16, 0x0E => l1 = 24, 0x10 => l1 = 16, 0x15 => l1 = 16,
            0x2C => l1 = 32, 0x30 => l1 = 32, 0x60 => l1 = 16, 0x66 => l1 = 8, 0x67 => l1 = 16,
            0x68 => l1 = 32, 0x1A => l2 = 96, 0x22 => l3 = 512, 0x23 => l3 = 1024, 0x25 => l3 = 2048,
            0x29 => l3 = 4096, 0x39 => l2 = 128, 0x3A => l2 = 192, 0x3B => l2 = 128, 0x3C => l2 = 256,
            0x3D => l2 = 384, 0x3E => l2 = 512, 0x40 => l2 = 0, 0x41 => l2 = 128, 0x42 => l2 = 256,
            0x43 => l2 = 512, 0x44 => l2 = 1024, 0x45 => l2 = 2048, 0x46 => l3 = 4096, 0x47 => l3 = 8192,
            0x48 => l2 = 3072,
            0x49 => {
                if l2 != 0 {
                    l3 = 4096
                } else {
                    p2 = true;
                    l3 = 4096;
                    l2 = 4096;
                }
            }
            0x4A => l3 = 6144, 0x4B => l3 = 8192, 0x4C => l3 = 12288, 0x4D => l3 = 16384, 0x4E => l2 = 6144,
            0x78 => l2 = 1024, 0x79 => l2 = 128, 0x7A => l2 = 256, 0x7B => l2 = 512, 0x7C => l2 = 1024,
            0x7D => l2 = 2048, 0x7E => l2 = 256, 0x7F => l2 = 512, 0x80 => l2 = 512, 0x81 => l2 = 128,
            0x82 => l2 = 256, 0x83 => l2 = 512, 0x84 => l2 = 1024, 0x85 => l2 = 2048, 0x86 => l2 = 512,
            0x87 => l2 = 1024, 0x88 => l3 = 2048, 0x89 => l3 = 4096, 0x8A => l3 = 8192, 0x8D => l3 = 3072,
            _ => {}
        }
    }
    if p2 && l2 == l3 {
        l3 = 0;
    }
    (l1 * 1024, l2 * 1024, l3 * 1024)
}

#[cfg(not(target_arch = "x86_64"))]
fn query_cache_sizes() -> (i32, i32, i32) {
    (-1, -1, -1)
}

const MR: usize = 4;
const NR: usize = 4;

/// evaluateProductBlockingSizesHeuristic<double,double,KcFactor=4>, single thread.
/// Returns (kc, mc); the caller's n is a copy (gemm_blocking_space, no l3 blocking).
pub fn blocking(k: usize, m: usize, n: usize) -> (usize, usize) {
    let (mut k, mut m, mut n) = (k as isize, m as isize, n as isize);
    let c = caches();
    let (l1, l2, l3) = (c.l1, c.l2, c.l3);
    if k.max(m.max(n)) < 48 {
        return (k as usize, m as usize);
    }
    let k_peeling = 8isize;
    let k_div = 4 * (MR as isize * 8 + NR as isize * 8);
    let k_sub = MR as isize * NR as isize * 8;
    let max_kc = (((l1 - k_sub) / k_div) & !(k_peeling - 1)).max(1);
    let old_k = k;
    if k > max_kc {
        k = if k % max_kc == 0 {
            max_kc
        } else {
            max_kc - k_peeling * ((max_kc - 1 - (k % max_kc)) / (k_peeling * (k / max_kc + 1)))
        };
    }
    let actual_l2: isize = 1572864;
    let lhs_bytes = m * k * 8;
    let remaining_l1 = l1 - k_sub - lhs_bytes;
    let max_nc = if remaining_l1 >= NR as isize * 8 * k {
        remaining_l1 / (k * 8)
    } else {
        (3 * actual_l2) / (2 * 2 * max_kc * 8)
    };
    let nc = (actual_l2 / (2 * k * 8)).min(max_nc) & !(NR as isize - 1);
    if n > nc {
        n = if n % nc == 0 { nc } else { nc - NR as isize * ((nc - (n % nc)) / (NR as isize * (n / nc + 1))) };
        let _ = n;
    } else if old_k == k {
        let problem_size = k * n * 8;
        let mut actual_lm = actual_l2;
        let mut max_mc = m;
        if problem_size <= 1024 {
            actual_lm = l1;
        } else if l3 != 0 && problem_size <= 32768 {
            actual_lm = l2;
            max_mc = max_mc.min(576);
        }
        let mut mc = (actual_lm / (3 * k * 8)).min(max_mc);
        if mc > MR as isize {
            mc -= mc % MR as isize;
        } else if mc == 0 {
            return (k as usize, m as usize);
        }
        m = if m % mc == 0 { mc } else { mc - MR as isize * ((mc - (m % mc)) / (MR as isize * (m / mc + 1))) };
    }
    (k as usize, m as usize)
}

/// gebp_kernel for double/SSE2 as seen by one result element: `acc` over the depth in the
/// order and accumulator split the micro kernel uses for row `r` (relative to the gebp call),
/// column `c`, then `res = acc*alpha + res`. `a(k)` and `b(k)` are the operands at depth k.
#[inline]
fn gebp_element(rows: usize, cols: usize, depth: usize, r: usize, c: usize, a: impl Fn(usize) -> f64, b: impl Fn(usize) -> f64, res: f64, alpha: f64) -> f64 {
    let packet_cols4 = (cols / 4) * 4;
    let peeled_mc2 = (rows / 4) * 4; // mr = 4 >= 2*LhsProgress; peeled_mc3 = 0
    let peeled_mc1 = peeled_mc2 + ((rows - peeled_mc2) / 2) * 2;
    let peeled_kc = depth & !7;
    let mut acc = 0.0f64;
    if r >= peeled_mc2 && r < peeled_mc1 && c < packet_cols4 {
        // lhs_process_one_packet 1x4: even k in C, odd k in D over the peeled part.
        let mut d = 0.0f64;
        let mut k = 0;
        while k < peeled_kc {
            for s in 0..8 {
                if s % 2 == 0 {
                    acc = acc + a(k + s) * b(k + s);
                } else {
                    d = d + a(k + s) * b(k + s);
                }
            }
            k += 8;
        }
        acc = acc + d;
        for k in peeled_kc..depth {
            acc = acc + a(k) * b(k);
        }
    } else {
        for k in 0..depth {
            acc = acc + a(k) * b(k);
        }
    }
    acc * alpha + res
}

/// triangular_solve_matrix<double, OnTheLeft, Lower|UnitDiag, ColMajor tri, ColMajor other>
/// for `size` x `cols` right-hand sides stored at `ot` in `other`.
pub fn trsm_unit_lower(size: usize, cols: usize, tri: &[f64], tt: At, other: &mut [f64], ot: At) {
    let small_panel = MR.max(NR);
    let (kc, mc_raw) = blocking(size, size, cols);
    let kc = kc.max(1);
    let mc = size.min(mc_raw).max(1);
    let l2 = caches().l2 as usize;
    let mut subcols = if cols > 0 { l2 / (4 * 8 * ot.ld.max(size)) } else { 0 };
    subcols = ((subcols / NR) * NR).max(NR);
    let mut k2 = 0;
    while k2 < size {
        let akc = (size - k2).min(kc);
        let mut j2 = 0;
        while j2 < cols {
            let acols = (cols - j2).min(subcols);
            let mut k1 = 0;
            while k1 < akc {
                let apw = (akc - k1).min(small_panel);
                for k in 0..apw {
                    let i = k2 + k1 + k;
                    let rs = apw - k - 1;
                    let s = i + 1;
                    for j in j2..j2 + acols {
                        let x = other[ot.idx(i, j)] * 1.0;
                        other[ot.idx(i, j)] = x;
                        for i3 in 0..rs {
                            let at = ot.idx(s + i3, j);
                            other[at] -= x * tri[tt.idx(s + i3, i)];
                        }
                    }
                }
                let length_target = akc - k1 - apw;
                let start_block = k2 + k1;
                if length_target > 0 {
                    // gebp with depth apw <= 4 < 8: peeled_kc = 0, so every row and column takes
                    // the plain sequential sum `acc = acc + a*b` from 0, then acc*(-1) + x.
                    let start_target = k2 + k1 + apw;
                    let mut acc = vec![0.0f64; length_target];
                    for c in 0..acols {
                        let col = j2 + c;
                        acc.iter_mut().for_each(|x| *x = 0.0);
                        for k in 0..apw {
                            let bk = other[ot.idx(start_block + k, col)];
                            let ts = tt.idx(start_target, start_block + k);
                            for (x, &t) in acc.iter_mut().zip(&tri[ts..ts + length_target]) {
                                *x = *x + t * bk;
                            }
                        }
                        let base = ot.idx(start_target, col);
                        for (o, &x) in other[base..base + length_target].iter_mut().zip(&acc) {
                            *o = x * -1.0 + *o;
                        }
                    }
                }
                k1 += small_panel;
            }
            j2 += subcols;
        }
        // R2 -= A21 * B
        let start = k2 + kc;
        let mut i2 = start;
        while i2 < size {
            let amc = mc.min(size - i2);
            for c in 0..cols {
                let bv: Vec<f64> = (0..akc).map(|k| other[ot.idx(k2 + k, c)]).collect();
                for r in 0..amc {
                    let row = i2 + r;
                    let current = other[ot.idx(row, c)];
                    let v = gebp_element(amc, cols, akc, r, c,
                        |k| tri[tt.idx(row, k2 + k)], |k| bv[k], current, -1.0);
                    other[ot.idx(row, c)] = v;
                }
            }
            i2 += mc;
        }
        k2 += kc;
    }
}

/// Test oracle support: `trsm_unit_lower` on caller data (tests/eigen_trsm_dump.cpp).
#[pyo3::pyfunction]
pub fn trsm_debug(size: usize, cols: usize, tri: Vec<f64>, lda: usize, mut other: Vec<f64>, ldu: usize) -> Vec<f64> {
    trsm_unit_lower(size, cols, &tri, At { off: 0, ld: lda }, &mut other, At { off: 0, ld: ldu });
    other
}

/// The cache sizes and the (kc, mc) blocking this machine gives, for the oracle comparison.
#[pyo3::pyfunction]
pub fn eigen_blocking_debug(k: usize, m: usize, n: usize) -> (isize, isize, isize, usize, usize) {
    let c = caches();
    let (kc, mc) = blocking(k, m, n);
    (c.l1, c.l2, c.l3, kc, mc)
}
