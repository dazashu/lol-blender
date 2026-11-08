// SPDX-License-Identifier: MPL-2.0
// Rust translation of Eigen 3.4 SparseLU as bundled in Blender 4.2 (extern/Eigen3/Eigen/src/
// SparseLU/*.h, SparseCore/SparseColEtree.h, ConservativeSparseSparseProduct.h,
// SparseDenseProduct.h, SparseMatrix.h set_from_triplets), driven the way
// intern/eigen/intern/linear_solver.cc drives it for least squares.
// Copyright (C) 2012 Desire Nuentsa-Wakam, 2012-2015 Gael Guennebaud and the Eigen authors.
// Based on SuperLU, copyright (c) 1994 Xerox Corporation; Univ. of California Berkeley,
// Xerox Palo Alto Research Center, and Lawrence Berkeley National Lab: permission to use or
// copy for any purpose is granted with these notices; permission to modify and distribute
// modified code is granted with a notice that the code was modified. Modified: translated to
// Rust. Provided AS IS, with absolutely no warranty, express or implied.
//
// Bit-for-bit target: tests/eigen_heat_reference.cpp, built from the same headers, which matches
// native Blender. Every arithmetic step keeps Eigen's order (see eigen_dense.rs for why).

use crate::eigen_dense::{self as dense, At, PACKET};
use std::collections::HashMap;

const EMPTY: isize = -1;
const PANEL_SIZE: usize = 16;
const RELAX: usize = 1;
const MAXSUPER: usize = 128;

/// Compressed column storage with sorted row indices.
pub struct Csc {
    pub n: usize,
    pub colptr: Vec<usize>,
    pub rows: Vec<usize>,
    pub vals: Vec<f64>,
}

impl Csc {
    /// `setFromTriplets`: duplicates are summed onto the first occurrence in insertion order.
    pub fn from_triplets(n: usize, triplets: &[(usize, usize, f64)]) -> Csc {
        let mut map: HashMap<(usize, usize), f64> = HashMap::with_capacity(triplets.len());
        for &(i, j, v) in triplets {
            map.entry((j, i)).and_modify(|x| *x += v).or_insert(v);
        }
        let mut entries: Vec<((usize, usize), f64)> = map.into_iter().collect();
        entries.sort_unstable_by_key(|&(key, _)| key);
        let mut colptr = vec![0usize; n + 1];
        for &((j, _), _) in &entries {
            colptr[j + 1] += 1;
        }
        for j in 0..n {
            colptr[j + 1] += colptr[j];
        }
        Csc {
            n,
            colptr,
            rows: entries.iter().map(|&((_, i), _)| i).collect(),
            vals: entries.iter().map(|&(_, v)| v).collect(),
        }
    }

    fn col(&self, j: usize) -> std::ops::Range<usize> {
        self.colptr[j]..self.colptr[j + 1]
    }

    /// `M.transpose() * M` through conservative_sparse_sparse_product_selector<RowMajor,
    /// ColMajor, ColMajor>: entry (j,i) = sum over k ascending of M(k,i)*M(k,j), the first
    /// product assigned rather than added to zero.
    pub fn transpose_times_self(&self) -> Csc {
        let n = self.n;
        // Row-major copy of M: rows in order, columns ascending inside each row.
        let mut rowptr = vec![0usize; n + 1];
        for &r in &self.rows {
            rowptr[r + 1] += 1;
        }
        for r in 0..n {
            rowptr[r + 1] += rowptr[r];
        }
        let mut fill = rowptr.clone();
        let mut row_cols = vec![0usize; self.rows.len()];
        let mut row_vals = vec![0f64; self.rows.len()];
        for j in 0..n {
            for p in self.col(j) {
                let r = self.rows[p];
                row_cols[fill[r]] = j;
                row_vals[fill[r]] = self.vals[p];
                fill[r] += 1;
            }
        }
        let mut mask = vec![false; n];
        let mut values = vec![0f64; n];
        let mut indices = Vec::new();
        // result rows j (row-major), each an unordered list of columns i
        let mut res_rows: Vec<Vec<(usize, f64)>> = Vec::with_capacity(n);
        for j in 0..n {
            indices.clear();
            for p in self.col(j) {
                let k = self.rows[p];
                let y = self.vals[p];
                for q in rowptr[k]..rowptr[k + 1] {
                    let i = row_cols[q];
                    let x = row_vals[q];
                    if !mask[i] {
                        mask[i] = true;
                        values[i] = x * y;
                        indices.push(i);
                    } else {
                        values[i] += x * y;
                    }
                }
            }
            let mut row = Vec::with_capacity(indices.len());
            for &i in &indices {
                row.push((i, values[i]));
                mask[i] = false;
            }
            res_rows.push(row);
        }
        // res = resRow: column i holds (row j, value) for j ascending.
        let mut colptr = vec![0usize; n + 1];
        for row in &res_rows {
            for &(i, _) in row {
                colptr[i + 1] += 1;
            }
        }
        for i in 0..n {
            colptr[i + 1] += colptr[i];
        }
        let mut fill = colptr.clone();
        let nnz = colptr[n];
        let mut rows = vec![0usize; nnz];
        let mut vals = vec![0f64; nnz];
        for (j, row) in res_rows.iter().enumerate() {
            for &(i, v) in row {
                rows[fill[i]] = j;
                vals[fill[i]] = v;
                fill[i] += 1;
            }
        }
        Csc { n, colptr, rows, vals }
    }

    /// `M.transpose() * b` (sparse_time_dense_product_impl, RowMajor): per column j,
    /// tmp = 0, tmp += v*b[row] in row order, res = 0 + 1*tmp.
    pub fn transpose_times(&self, b: &[f64]) -> Vec<f64> {
        (0..self.n)
            .map(|j| {
                let mut tmp = 0.0f64;
                for p in self.col(j) {
                    tmp += self.vals[p] * b[self.rows[p]];
                }
                0.0 + 1.0 * tmp
            })
            .collect()
    }
}

/// linear_solver.cc least squares: M^T M factorized once, `solve` per right-hand side.
pub struct LeastSquares {
    m: Csc,
    lu: SparseLu,
}

impl LeastSquares {
    /// None when Eigen reports anything but Success (a zero pivot).
    pub fn new(n: usize, triplets: &[(usize, usize, f64)]) -> Option<LeastSquares> {
        let prof = std::env::var_os("DAZA_PROFILE").is_some();
        let t = std::time::Instant::now();
        let m = Csc::from_triplets(n, triplets);
        if prof { eprintln!("PROFILE   triplets {:?}", t.elapsed()); }
        let t = std::time::Instant::now();
        let mtm = m.transpose_times_self();
        if prof { eprintln!("PROFILE   mtm {:?}", t.elapsed()); }
        let t = std::time::Instant::now();
        let lu = SparseLu::compute(&mtm)?;
        if prof { eprintln!("PROFILE   lu {:?} nnz(lu)={}", t.elapsed(), lu.lusup.len()); }
        Some(LeastSquares { m, lu })
    }

    pub fn solve(&self, b: &[f64]) -> Vec<f64> {
        let mtb = self.m.transpose_times(b);
        self.lu.solve(&mtb)
    }
}

pub struct SparseLu {
    n: usize,
    perm_r: Vec<isize>,
    perm_c: Vec<usize>,
    xsup: Vec<isize>,
    lusup: Vec<f64>,
    xlusup: Vec<isize>,
    lsub: Vec<isize>,
    xlsub: Vec<isize>,
    ucol: Vec<f64>,
    usub: Vec<isize>,
    xusub: Vec<isize>,
    nsuper: usize,
}

fn ensure<T: Clone + Default>(v: &mut Vec<T>, len: usize) {
    if v.len() < len {
        v.resize(len, T::default());
    }
}

/// COLAMDOrdering (the existing faer-derived port, checked against Eigen's), then
/// analyzePattern: column etree of the permuted matrix, postorder, combined permutation.
/// Returns (perm_c with perm_c[old] = new, etree renumbered in postorder).
/// COLAMDOrdering: (perm with perm[new] = old, inv with inv[old] = new).
fn colamd(a: &Csc) -> Option<(Vec<usize>, Vec<usize>)> {
    let inv = crate::eigen_colamd_exact::colamd_ordering(a.n, a.n, &a.colptr, &a.rows)?;
    let mut perm = vec![0usize; a.n];
    for (old, &new) in inv.iter().enumerate() {
        perm[new] = old;
    }
    Some((perm, inv))
}

fn analyze(a: &Csc) -> Option<(Vec<usize>, Vec<usize>)> {
    let n = a.n;
    let (perm, inv) = colamd(a)?;
    // perm[new] = old; Eigen's m_perm_c.indices()(old) = new = inv.
    // coletree on m_mat (column c of m_mat is column perm[c] of a).
    let mut first = vec![n; n];
    for (r, f) in first.iter_mut().enumerate() {
        *f = r;
    }
    for col in 0..n {
        for p in a.col(perm[col]) {
            let row = a.rows[p];
            first[row] = first[row].min(col);
        }
    }
    let mut root = vec![0usize; n];
    let mut pp = vec![0usize; n];
    let mut parent = vec![n; n];
    for col in 0..n {
        let mut found_diag = false;
        pp[col] = col;
        let mut cset = col;
        root[cset] = col;
        parent[col] = n;
        let entries: Vec<usize> = a.col(perm[col]).map(|p| a.rows[p]).collect();
        let mut idx = 0;
        while idx < entries.len() || !found_diag {
            let i = if idx < entries.len() { entries[idx] } else { col };
            idx += 1;
            if i == col {
                found_diag = true;
            }
            let row = first[i];
            if row >= col {
                continue;
            }
            // etree_find
            let mut node = row;
            let mut p = pp[node];
            let mut gp = pp[p];
            while gp != p {
                pp[node] = gp;
                node = gp;
                p = pp[node];
                gp = pp[p];
            }
            let rset = p;
            let rroot = root[rset];
            if rroot != col {
                parent[rroot] = col;
                pp[cset] = rset;
                cset = rset;
                root[cset] = col;
            }
        }
    }
    // treePostorder / nr_etdfs
    let mut first_kid = vec![EMPTY; n + 1];
    let mut next_kid = vec![0isize; n + 1];
    for v in (0..n).rev() {
        let dad = parent[v];
        next_kid[v] = first_kid[dad];
        first_kid[dad] = v as isize;
    }
    let mut post = vec![0usize; n + 1];
    let mut postnum = 0usize;
    let mut current = n;
    let mut parent_ext = parent.clone();
    parent_ext.push(n);
    while postnum != n {
        let first = first_kid[current];
        if first == EMPTY {
            post[current] = postnum;
            postnum += 1;
            let mut next = next_kid[current];
            while next == EMPTY {
                current = parent_ext[current];
                post[current] = postnum;
                postnum += 1;
                next = next_kid[current];
            }
            if postnum == n + 1 {
                break;
            }
            current = next as usize;
        } else {
            current = first as usize;
        }
    }
    let mut etree = vec![0usize; n];
    for i in 0..n {
        etree[post[i]] = post[parent[i]];
    }
    let perm_c: Vec<usize> = (0..n).map(|old| post[inv[old]]).collect();
    Some((perm_c, etree))
}

struct Work {
    m: usize,
    // permuted matrix: column j = original column q[j]
    q: Vec<usize>,
    iperm_c: Vec<usize>,
    perm_r: Vec<isize>,
    segrep: Vec<isize>,
    parent: Vec<isize>,
    xplore: Vec<isize>,
    repfnz: Vec<isize>,
    panel_lsub: Vec<isize>,
    xprune: Vec<isize>,
    marker: Vec<isize>,
    dense: Vec<f64>,
    tempv: Vec<f64>,
    xsup: Vec<isize>,
    supno: Vec<isize>,
    lusup: Vec<f64>,
    xlusup: Vec<isize>,
    lsub: Vec<isize>,
    xlsub: Vec<isize>,
    ucol: Vec<f64>,
    usub: Vec<isize>,
    xusub: Vec<isize>,
}

#[derive(Clone, Copy)]
enum Dfs {
    /// panel_dfs: writes panel_lsub, marks segments in marker1 (offset m).
    Panel { jcol: usize },
    /// column_dfs: writes glu.lsub, may break the supernode.
    Column { jcol: usize },
}

impl Work {
    fn ilsub(&self, i: isize) -> isize {
        self.lsub[i as usize]
    }

    /// SparseLUImpl::dfs_kernel. `marker_off`: 0 for panel_dfs, 2m for column_dfs.
    #[allow(clippy::too_many_arguments)]
    fn dfs_kernel(&mut self, jj: isize, nseg: &mut usize, repfnz_off: usize, marker_off: usize, nextl: &mut usize, krow: usize, mode: Dfs, jsuper: &mut isize) {
        let kmark = self.marker[marker_off + krow];
        self.marker[marker_off + krow] = jj;
        let kperm = self.perm_r[krow];
        if kperm == EMPTY {
            self.push_l(mode, nextl, krow as isize, kmark, jsuper);
        } else {
            let mut krep = self.xsup[(self.supno[kperm as usize] + 1) as usize] - 1;
            let mut myfnz = self.repfnz[repfnz_off + krep as usize];
            if myfnz != EMPTY {
                if myfnz > kperm {
                    self.repfnz[repfnz_off + krep as usize] = kperm;
                }
            } else {
                let mut oldrep = EMPTY;
                self.parent[krep as usize] = oldrep;
                self.repfnz[repfnz_off + krep as usize] = kperm;
                let mut xdfs = self.xlsub[krep as usize];
                let mut maxdfs = self.xprune[krep as usize];
                loop {
                    while xdfs < maxdfs {
                        let kchild = self.ilsub(xdfs);
                        xdfs += 1;
                        let chmark = self.marker[marker_off + kchild as usize];
                        if chmark != jj {
                            self.marker[marker_off + kchild as usize] = jj;
                            let chperm = self.perm_r[kchild as usize];
                            if chperm == EMPTY {
                                self.push_l(mode, nextl, kchild, chmark, jsuper);
                            } else {
                                let chrep = self.xsup[(self.supno[chperm as usize] + 1) as usize] - 1;
                                myfnz = self.repfnz[repfnz_off + chrep as usize];
                                if myfnz != EMPTY {
                                    if myfnz > chperm {
                                        self.repfnz[repfnz_off + chrep as usize] = chperm;
                                    }
                                } else {
                                    self.xplore[krep as usize] = xdfs;
                                    oldrep = krep;
                                    krep = chrep;
                                    self.parent[krep as usize] = oldrep;
                                    self.repfnz[repfnz_off + krep as usize] = chperm;
                                    xdfs = self.xlsub[krep as usize];
                                    maxdfs = self.xprune[krep as usize];
                                }
                            }
                        }
                    }
                    let record = match mode {
                        Dfs::Panel { jcol } => {
                            let m = self.m;
                            if self.marker[m + krep as usize] < jcol as isize {
                                self.marker[m + krep as usize] = jj;
                                true
                            } else {
                                false
                            }
                        }
                        Dfs::Column { .. } => true,
                    };
                    if record {
                        self.segrep[*nseg] = krep;
                        *nseg += 1;
                    }
                    let kpar = self.parent[krep as usize];
                    if kpar == EMPTY {
                        break;
                    }
                    krep = kpar;
                    xdfs = self.xplore[krep as usize];
                    maxdfs = self.xprune[krep as usize];
                }
            }
        }
    }

    fn push_l(&mut self, mode: Dfs, nextl: &mut usize, row: isize, mark: isize, jsuper: &mut isize) {
        match mode {
            Dfs::Panel { .. } => {
                self.panel_lsub[*nextl] = row;
                *nextl += 1;
            }
            Dfs::Column { jcol } => {
                ensure(&mut self.lsub, *nextl + 1);
                self.lsub[*nextl] = row;
                *nextl += 1;
                if mark != jcol as isize - 1 {
                    *jsuper = EMPTY;
                }
            }
        }
    }

    fn panel_dfs(&mut self, a: &Csc, w: usize, jcol: usize) -> usize {
        let m = self.m;
        let mut nseg = 0usize;
        let mut dummy = 0isize;
        for jj in jcol..jcol + w {
            let mut nextl = (jj - jcol) * m;
            let col_off = (jj - jcol) * m;
            let src = self.q[jj];
            for p in a.col(src) {
                let krow = a.rows[p];
                self.dense[col_off + krow] = a.vals[p];
                let kmark = self.marker[krow];
                if kmark == jj as isize {
                    continue;
                }
                self.dfs_kernel(jj as isize, &mut nseg, col_off, 0, &mut nextl, krow, Dfs::Panel { jcol }, &mut dummy);
            }
        }
        nseg
    }

    fn column_dfs(&mut self, jcol: usize, nseg: &mut usize, lsub_off: usize, repfnz_off: usize) {
        let m = self.m;
        let mut jsuper = self.supno[jcol];
        let mut nextl = self.xlsub[jcol] as usize;
        let mut k = 0;
        while k < m && self.panel_lsub[lsub_off + k] != EMPTY {
            let krow = self.panel_lsub[lsub_off + k] as usize;
            self.panel_lsub[lsub_off + k] = EMPTY;
            let kmark = self.marker[2 * m + krow];
            k += 1;
            if kmark == jcol as isize {
                continue;
            }
            self.dfs_kernel(jcol as isize, nseg, repfnz_off, 2 * m, &mut nextl, krow, Dfs::Column { jcol }, &mut jsuper);
        }
        let mut nsuper = self.supno[jcol];
        let jcolp1 = jcol + 1;
        if jcol == 0 {
            nsuper = 0;
            self.supno[0] = 0;
        } else {
            let jcolm1 = jcol - 1;
            let fsupc = self.xsup[nsuper as usize] as usize;
            let jptr = self.xlsub[jcol];
            let jm1ptr = self.xlsub[jcolm1];
            if nextl as isize - jptr != jptr - jm1ptr - 1 {
                jsuper = EMPTY;
            }
            if jcol - fsupc >= MAXSUPER {
                jsuper = EMPTY;
            }
            if jsuper == EMPTY {
                if (fsupc as isize) < jcolm1 as isize - 1 {
                    let mut ito = self.xlsub[fsupc + 1];
                    self.xlsub[jcolm1] = ito;
                    let istop = ito + jptr - jm1ptr;
                    self.xprune[jcolm1] = istop;
                    self.xlsub[jcol] = istop;
                    let mut ifrom = jm1ptr;
                    while ifrom < nextl as isize {
                        let v = self.lsub[ifrom as usize];
                        self.lsub[ito as usize] = v;
                        ifrom += 1;
                        ito += 1;
                    }
                    nextl = ito as usize;
                }
                nsuper += 1;
                self.supno[jcol] = nsuper;
            }
        }
        self.xsup[(nsuper + 1) as usize] = jcolp1 as isize;
        self.supno[jcolp1] = nsuper;
        self.xprune[jcol] = nextl as isize;
        self.xlsub[jcolp1] = nextl as isize;
    }

    /// LU_kernel_bmod<1> and LU_kernel_bmod<Dynamic>.
    #[allow(clippy::too_many_arguments)]
    fn kernel_bmod(&mut self, segsize: usize, dense_off: usize, luptr: &mut usize, lda: usize, nrow: usize, lptr: usize, no_zeros: usize) {
        if segsize == 1 {
            let f = self.dense[dense_off + self.lsub[lptr + no_zeros] as usize];
            *luptr += lda * no_zeros + no_zeros + 1;
            let a = *luptr;
            let irow = lptr + no_zeros + 1;
            let mut i = 0;
            while i + 1 < nrow {
                let i0 = self.lsub[irow + i] as usize;
                let i1 = self.lsub[irow + i + 1] as usize;
                let a0 = self.lusup[a + i];
                let a1 = self.lusup[a + i + 1];
                let mut d0 = self.dense[dense_off + i0];
                let mut d1 = self.dense[dense_off + i1];
                d0 -= f * a0;
                d1 -= f * a1;
                self.dense[dense_off + i0] = d0;
                self.dense[dense_off + i1] = d1;
                i += 2;
            }
            if i < nrow {
                let r = self.lsub[irow + i] as usize;
                self.dense[dense_off + r] -= f * self.lusup[a + i];
            }
            return;
        }
        let mut isub = lptr + no_zeros;
        for i in 0..segsize {
            let irow = self.lsub[isub] as usize;
            self.tempv[i] = self.dense[dense_off + irow];
            isub += 1;
        }
        *luptr += lda * no_zeros + no_zeros;
        dense::trsv_lower(segsize, &self.lusup, At { off: *luptr, ld: lda }, &mut self.tempv, 0, true);
        *luptr += segsize;
        let ldl = nrow.div_ceil(PACKET) * PACKET;
        // l lives in tempv after u; its exact place does not change any arithmetic.
        let l_off = segsize + PACKET + ldl.max(1);
        ensure(&mut self.tempv, l_off + nrow);
        for v in &mut self.tempv[l_off..l_off + nrow] {
            *v = 0.0;
        }
        {
            let (head, tail) = self.tempv.split_at_mut(l_off);
            dense::sparselu_gemm(nrow, 1, segsize, &self.lusup, At { off: *luptr, ld: lda }, *luptr % 2,
                head, At { off: 0, ld: segsize }, tail, At { off: 0, ld: ldl.max(1) });
        }
        isub = lptr + no_zeros;
        for i in 0..segsize {
            let irow = self.lsub[isub] as usize;
            isub += 1;
            self.dense[dense_off + irow] = self.tempv[i];
        }
        for i in 0..nrow {
            let irow = self.lsub[isub] as usize;
            isub += 1;
            self.dense[dense_off + irow] -= self.tempv[l_off + i];
        }
    }

    fn panel_bmod(&mut self, w: usize, jcol: usize, nseg: usize) {
        let m = self.m;
        let mut k = nseg as isize - 1;
        for _ksub in 0..nseg {
            let krep = self.segrep[k as usize] as usize;
            k -= 1;
            let fsupc = self.xsup[self.supno[krep] as usize] as usize;
            let nsupc = krep - fsupc + 1;
            let nsupr = (self.xlsub[fsupc + 1] - self.xlsub[fsupc]) as usize;
            let nrow = nsupr - nsupc;
            let lptr = self.xlsub[fsupc] as usize;
            let mut u_rows = 0usize;
            let mut u_cols = 0usize;
            for jj in jcol..jcol + w {
                let kfnz = self.repfnz[(jj - jcol) * m + krep];
                if kfnz == EMPTY {
                    continue;
                }
                let segsize = krep - kfnz as usize + 1;
                u_cols += 1;
                u_rows = u_rows.max(segsize);
            }
            if nsupc >= 2 {
                let ldu = u_rows.div_ceil(PACKET) * PACKET;
                let ldu = ldu.max(1);
                ensure(&mut self.tempv, w * ldu + 1);
                // gather U
                let mut u_col = 0;
                for jj in jcol..jcol + w {
                    let off_col = (jj - jcol) * m;
                    let kfnz = self.repfnz[off_col + krep];
                    if kfnz == EMPTY {
                        continue;
                    }
                    let segsize = krep - kfnz as usize + 1;
                    let no_zeros = kfnz as usize - fsupc;
                    let mut isub = lptr + no_zeros;
                    let off = u_rows - segsize;
                    for i in 0..off {
                        self.tempv[i + u_col * ldu] = 0.0;
                    }
                    for i in 0..segsize {
                        let irow = self.lsub[isub] as usize;
                        self.tempv[i + off + u_col * ldu] = self.dense[off_col + irow];
                        isub += 1;
                    }
                    u_col += 1;
                }
                let mut luptr = self.xlusup[fsupc] as usize;
                let lda = (self.xlusup[fsupc + 1] - self.xlusup[fsupc]) as usize;
                let no_zeros = (krep + 1 - u_rows) - fsupc;
                luptr += lda * no_zeros + no_zeros;
                dense::trsm_unit_lower(u_rows, u_cols, &self.lusup, At { off: luptr, ld: lda }, &mut self.tempv, At { off: 0, ld: ldu });
                luptr += u_rows;
                let ldl = nrow.div_ceil(PACKET) * PACKET;
                let ldl = ldl.max(1);
                let l_off = w * ldu + PACKET;
                ensure(&mut self.tempv, l_off + ldl * u_cols.max(1));
                for v in &mut self.tempv[l_off..l_off + ldl * u_cols] {
                    *v = 0.0;
                }
                {
                    let (head, tail) = self.tempv.split_at_mut(l_off);
                    dense::sparselu_gemm(nrow, u_cols, u_rows, &self.lusup, At { off: luptr, ld: lda }, luptr % 2,
                        head, At { off: 0, ld: ldu }, tail, At { off: 0, ld: ldl });
                }
                // scatter U and L
                u_col = 0;
                for jj in jcol..jcol + w {
                    let off_col = (jj - jcol) * m;
                    let kfnz = self.repfnz[off_col + krep];
                    if kfnz == EMPTY {
                        continue;
                    }
                    let segsize = krep - kfnz as usize + 1;
                    let no_zeros = kfnz as usize - fsupc;
                    let mut isub = lptr + no_zeros;
                    let off = u_rows - segsize;
                    for i in 0..segsize {
                        let irow = self.lsub[isub] as usize;
                        isub += 1;
                        self.dense[off_col + irow] = self.tempv[i + off + u_col * ldu];
                        self.tempv[i + off + u_col * ldu] = 0.0;
                    }
                    for i in 0..nrow {
                        let irow = self.lsub[isub] as usize;
                        isub += 1;
                        self.dense[off_col + irow] -= self.tempv[l_off + i + u_col * ldl];
                        self.tempv[l_off + i + u_col * ldl] = 0.0;
                    }
                    u_col += 1;
                }
            } else {
                for jj in jcol..jcol + w {
                    let off_col = (jj - jcol) * m;
                    let kfnz = self.repfnz[off_col + krep];
                    if kfnz == EMPTY {
                        continue;
                    }
                    let segsize = krep - kfnz as usize + 1;
                    let mut luptr = self.xlusup[fsupc] as usize;
                    let lda = (self.xlusup[fsupc + 1] - self.xlusup[fsupc]) as usize;
                    let no_zeros = kfnz as usize - fsupc;
                    self.kernel_bmod(segsize, off_col, &mut luptr, lda, nrow, lptr, no_zeros);
                }
            }
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn column_bmod(&mut self, jcol: usize, nseg: usize, dense_off: usize, segrep_off: usize, repfnz_off: usize, fpanelc: usize) {
        let jsupno = self.supno[jcol];
        let mut k = nseg as isize - 1;
        for _ksub in 0..nseg {
            let krep = self.segrep[segrep_off + k as usize] as usize;
            k -= 1;
            let ksupno = self.supno[krep];
            if jsupno != ksupno {
                let fsupc = self.xsup[ksupno as usize] as usize;
                let fst_col = fsupc.max(fpanelc);
                let d_fsupc = fst_col - fsupc;
                let mut luptr = self.xlusup[fst_col] as usize + d_fsupc;
                let lptr = self.xlsub[fsupc] as usize + d_fsupc;
                let kfnz = (self.repfnz[repfnz_off + krep] as usize).max(fpanelc);
                let segsize = krep - kfnz + 1;
                let nsupc = krep - fst_col + 1;
                let nsupr = (self.xlsub[fsupc + 1] - self.xlsub[fsupc]) as usize;
                let nrow = nsupr - d_fsupc - nsupc;
                let lda = (self.xlusup[fst_col + 1] - self.xlusup[fst_col]) as usize;
                let no_zeros = kfnz - fst_col;
                self.kernel_bmod(segsize, dense_off, &mut luptr, lda, nrow, lptr, no_zeros);
            }
        }
        let mut nextlu = self.xlusup[jcol] as usize;
        let fsupc = self.xsup[jsupno as usize] as usize;
        let mut new_next = nextlu + (self.xlsub[fsupc + 1] - self.xlsub[fsupc]) as usize;
        let offset = new_next.div_ceil(PACKET) * PACKET - new_next;
        new_next += offset;
        ensure(&mut self.lusup, new_next);
        for isub in self.xlsub[fsupc] as usize..self.xlsub[fsupc + 1] as usize {
            let irow = self.lsub[isub] as usize;
            self.lusup[nextlu] = self.dense[dense_off + irow];
            self.dense[dense_off + irow] = 0.0;
            nextlu += 1;
        }
        for _ in 0..offset {
            self.lusup[nextlu] = 0.0;
            nextlu += 1;
        }
        self.xlusup[jcol + 1] = nextlu as isize;
        let fst_col = fsupc.max(fpanelc);
        if fst_col < jcol {
            let d_fsupc = fst_col - fsupc;
            let luptr = self.xlusup[fst_col] as usize + d_fsupc;
            let nsupr = (self.xlsub[fsupc + 1] - self.xlsub[fsupc]) as usize;
            let nsupc = jcol - fst_col;
            let nrow = nsupr - d_fsupc - nsupc;
            let ufirst = self.xlusup[jcol] as usize + d_fsupc;
            let lda = (self.xlusup[jcol + 1] - self.xlusup[jcol]) as usize;
            // A (columns fst_col..jcol-1) lies wholly before column jcol, u and l inside it.
            let split = self.xlusup[jcol] as usize;
            let (head, tail) = self.lusup.split_at_mut(split);
            let (u, l) = tail[ufirst - split..ufirst - split + nsupc + nrow].split_at_mut(nsupc);
            // u = A.triangularView<UnitLower>().solve(u)
            dense::trsv_lower(nsupc, head, At { off: luptr, ld: lda }, u, 0, true);
            // l.noalias() -= A * u
            dense::gemv_product(nrow, nsupc, head, At { off: luptr + nsupc, ld: lda }, u, 0, l, 0, -1.0);
        }
    }

    fn copy_to_ucol(&mut self, jcol: usize, nseg: usize, repfnz_off: usize, dense_off: usize) {
        let jsupno = self.supno[jcol];
        let mut k = nseg as isize - 1;
        let mut nextu = self.xusub[jcol] as usize;
        for _ksub in 0..nseg {
            let krep = self.segrep[k as usize] as usize;
            k -= 1;
            let ksupno = self.supno[krep];
            if jsupno != ksupno {
                let kfnz = self.repfnz[repfnz_off + krep];
                if kfnz != EMPTY {
                    let kfnz = kfnz as usize;
                    let fsupc = self.xsup[ksupno as usize] as usize;
                    let mut isub = self.xlsub[fsupc] as usize + kfnz - fsupc;
                    let segsize = krep - kfnz + 1;
                    ensure(&mut self.ucol, nextu + segsize);
                    ensure(&mut self.usub, nextu + segsize);
                    for _ in 0..segsize {
                        let irow = self.lsub[isub] as usize;
                        self.usub[nextu] = self.perm_r[irow];
                        self.ucol[nextu] = self.dense[dense_off + irow];
                        self.dense[dense_off + irow] = 0.0;
                        nextu += 1;
                        isub += 1;
                    }
                }
            }
        }
        self.xusub[jcol + 1] = nextu as isize;
    }

    /// Returns the pivot row, or None for Eigen's zero-pivot failure.
    fn pivot_l(&mut self, jcol: usize) -> Option<usize> {
        let fsupc = self.xsup[self.supno[jcol] as usize] as usize;
        let nsupc = jcol - fsupc;
        let lptr = self.xlsub[fsupc] as usize;
        let nsupr = self.xlsub[fsupc + 1] as usize - lptr;
        let lda = (self.xlusup[fsupc + 1] - self.xlusup[fsupc]) as usize;
        let sup = self.xlusup[fsupc] as usize;
        let colp = self.xlusup[jcol] as usize;
        let diagind = self.iperm_c[jcol] as isize;
        let mut pivmax = -1.0f64;
        let mut pivptr = nsupc;
        let mut diag = EMPTY;
        for isub in nsupc..nsupr {
            let rtemp = self.lusup[colp + isub].abs();
            if rtemp > pivmax {
                pivmax = rtemp;
                pivptr = isub;
            }
            if self.lsub[lptr + isub] == diagind {
                diag = isub as isize;
            }
        }
        if pivmax <= 0.0 {
            let pivrow = if pivmax < 0.0 { diagind } else { self.lsub[lptr + pivptr] };
            self.perm_r[pivrow as usize] = jcol as isize;
            return None;
        }
        let thresh = 1.0 * pivmax;
        if diag >= 0 {
            let rtemp = self.lusup[colp + diag as usize].abs();
            if rtemp != 0.0 && rtemp >= thresh {
                pivptr = diag as usize;
            }
        }
        let pivrow = self.lsub[lptr + pivptr] as usize;
        self.perm_r[pivrow] = jcol as isize;
        if pivptr != nsupc {
            self.lsub.swap(lptr + pivptr, lptr + nsupc);
            for icol in 0..=nsupc {
                self.lusup.swap(sup + pivptr + icol * lda, sup + nsupc + icol * lda);
            }
        }
        let temp = 1.0 / self.lusup[colp + nsupc];
        for k in nsupc + 1..nsupr {
            self.lusup[colp + k] *= temp;
        }
        Some(pivrow)
    }

    fn prune_l(&mut self, jcol: usize, pivrow: usize, nseg: usize, repfnz_off: usize) {
        let jsupno = self.supno[jcol];
        for i in 0..nseg {
            let irep = self.segrep[i] as usize;
            let irep1 = irep + 1;
            let mut do_prune = false;
            if self.repfnz[repfnz_off + irep] == EMPTY {
                continue;
            }
            if self.supno[irep] == self.supno[irep1] {
                continue;
            }
            if self.supno[irep] != jsupno {
                let mut kmin = 0isize;
                let mut kmax = 0isize;
                if self.xprune[irep] >= self.xlsub[irep1] {
                    kmin = self.xlsub[irep];
                    kmax = self.xlsub[irep1] - 1;
                    let mut krow = kmin;
                    while krow <= kmax {
                        if self.lsub[krow as usize] == pivrow as isize {
                            do_prune = true;
                            break;
                        }
                        krow += 1;
                    }
                }
                if do_prune {
                    let movnum = irep as isize == self.xsup[self.supno[irep] as usize];
                    while kmin <= kmax {
                        if self.perm_r[self.lsub[kmax as usize] as usize] == EMPTY {
                            kmax -= 1;
                        } else if self.perm_r[self.lsub[kmin as usize] as usize] != EMPTY {
                            kmin += 1;
                        } else {
                            self.lsub.swap(kmin as usize, kmax as usize);
                            if movnum {
                                let minloc = (self.xlusup[irep] + (kmin - self.xlsub[irep])) as usize;
                                let maxloc = (self.xlusup[irep] + (kmax - self.xlsub[irep])) as usize;
                                self.lusup.swap(minloc, maxloc);
                            }
                            kmin += 1;
                            kmax -= 1;
                        }
                    }
                    self.xprune[irep] = kmin;
                }
            }
        }
    }
}

impl SparseLu {
    /// analyzePattern + factorize. None on Eigen's NumericalIssue (zero pivot).
    pub fn compute(a: &Csc) -> Option<SparseLu> {
        let n = a.n;
        if n == 0 {
            return None;
        }
        let t = std::time::Instant::now();
        let (perm_c, etree) = analyze(a)?;
        if std::env::var_os("DAZA_PROFILE").is_some() { eprintln!("PROFILE     analyze {:?}", t.elapsed()); }
        let m = n;
        let mut q = vec![0usize; n];
        for (old, &new) in perm_c.iter().enumerate() {
            q[new] = old;
        }
        let maxpanel = PANEL_SIZE * m;
        let mut w = Work {
            m,
            iperm_c: q.clone(),
            q,
            perm_r: vec![EMPTY; m],
            segrep: vec![0; m],
            parent: vec![0; m],
            xplore: vec![0; m],
            repfnz: vec![EMPTY; maxpanel],
            panel_lsub: vec![EMPTY; maxpanel],
            xprune: vec![0; n],
            marker: vec![0; m * 3],
            dense: vec![0.0; maxpanel],
            tempv: vec![0.0; m.max((MAXSUPER + m) * PANEL_SIZE)],
            xsup: vec![0; n + 1],
            supno: vec![0; n + 1],
            lusup: Vec::new(),
            xlusup: vec![0; n + 1],
            lsub: Vec::new(),
            xlsub: vec![0; n + 1],
            ucol: Vec::new(),
            usub: Vec::new(),
            xusub: vec![0; n + 1],
        };
        // relax_snode (marker reused as `descendants`, zeroed)
        let mut relax_end = vec![EMPTY; n];
        {
            let desc = &mut w.marker;
            for v in desc.iter_mut() {
                *v = 0;
            }
            for j in 0..n {
                let parent = etree[j];
                if parent != n {
                    desc[parent] += desc[j] + 1;
                }
            }
            let mut j = 0;
            while j < n {
                let mut parent = etree[j];
                let snode_start = j;
                while parent != n && (desc[parent] as usize) < RELAX {
                    j = parent;
                    parent = etree[j];
                }
                relax_end[snode_start] = j as isize;
                j += 1;
                while j < n && desc[j] != 0 {
                    j += 1;
                }
            }
        }
        for v in w.marker.iter_mut() {
            *v = EMPTY;
        }
        w.supno[0] = EMPTY;
        let mut jcol = 0;
        while jcol < n {
            let mut panel_size = PANEL_SIZE;
            let mut k = jcol + 1;
            while k < (jcol + panel_size).min(n) {
                if relax_end[k] != EMPTY {
                    panel_size = k - jcol;
                    break;
                }
                k += 1;
            }
            if k == n {
                panel_size = n - jcol;
            }
            let nseg1 = w.panel_dfs(a, panel_size, jcol);
            w.panel_bmod(panel_size, jcol, nseg1);
            for jj in jcol..jcol + panel_size {
                let k = (jj - jcol) * m;
                let mut nseg = nseg1;
                w.column_dfs(jj, &mut nseg, k, k);
                w.column_bmod(jj, nseg - nseg1, k, nseg1, k, jcol);
                w.copy_to_ucol(jj, nseg, k, k);
                let pivrow = w.pivot_l(jj)?;
                w.prune_l(jj, pivrow, nseg, k);
                for i in 0..nseg {
                    let irep = w.segrep[i] as usize;
                    w.repfnz[k + irep] = EMPTY;
                }
            }
            jcol += panel_size;
        }
        // fixupL
        let nsuper = w.supno[n] as usize;
        let mut nextl = 0isize;
        for i in 0..=nsuper {
            let fsupc = w.xsup[i] as usize;
            let jstart = w.xlsub[fsupc];
            w.xlsub[fsupc] = nextl;
            let mut j = jstart;
            while j < w.xlsub[fsupc + 1] {
                let row = w.lsub[j as usize] as usize;
                w.lsub[nextl as usize] = w.perm_r[row];
                nextl += 1;
                j += 1;
            }
            for k in fsupc + 1..w.xsup[i + 1] as usize {
                w.xlsub[k] = nextl;
            }
        }
        w.xlsub[n] = nextl;
        Some(SparseLu {
            n,
            perm_r: w.perm_r,
            perm_c,
            xsup: w.xsup,
            lusup: w.lusup,
            xlusup: w.xlusup,
            lsub: w.lsub,
            xlsub: w.xlsub,
            ucol: w.ucol,
            usub: w.usub,
            xusub: w.xusub,
            nsuper,
        })
    }

    /// _solve_impl for one right-hand side.
    pub fn solve(&self, b: &[f64]) -> Vec<f64> {
        let n = self.n;
        let mut x = vec![0.0f64; n];
        for i in 0..n {
            x[self.perm_r[i] as usize] = b[i];
        }
        // L: MappedSuperNodalMatrix::solveInPlace
        let mut work = vec![0.0f64; n];
        for k in 0..=self.nsuper {
            let fsupc = self.xsup[k] as usize;
            let istart = self.xlsub[fsupc] as usize;
            let nsupr = self.xlsub[fsupc + 1] as usize - istart;
            let nsupc = (self.xsup[k + 1] - self.xsup[k]) as usize;
            let nrow = nsupr - nsupc;
            if nsupc == 1 {
                let mut idval = self.xlusup[fsupc] as usize;
                let endval = self.xlusup[fsupc + 1] as usize;
                let mut idrow = istart;
                let endrow = self.xlsub[fsupc + 1] as usize;
                // skip the diagonal
                idval += 1;
                idrow += 1;
                while idval < endval && idrow < endrow {
                    let irow = self.lsub[idrow] as usize;
                    x[irow] -= x[fsupc] * self.lusup[idval];
                    idval += 1;
                    idrow += 1;
                }
            } else {
                let luptr = self.xlusup[fsupc] as usize;
                let lda = (self.xlusup[fsupc + 1] - self.xlusup[fsupc]) as usize;
                dense::trsv_lower(nsupc, &self.lusup, At { off: luptr, ld: lda }, &mut x, fsupc, true);
                for v in &mut work[..nrow] {
                    *v = 0.0;
                }
                dense::gemv_product(nrow, nsupc, &self.lusup, At { off: luptr + nsupc, ld: lda }, &x, fsupc, &mut work, 0, 1.0);
                let mut iptr = istart + nsupc;
                for i in 0..nrow {
                    let irow = self.lsub[iptr] as usize;
                    x[irow] -= work[i];
                    work[i] = 0.0;
                    iptr += 1;
                }
            }
        }
        // U: SparseLUMatrixUReturnType::solveInPlace
        for k in (0..=self.nsuper).rev() {
            let fsupc = self.xsup[k] as usize;
            let lda = (self.xlusup[fsupc + 1] - self.xlusup[fsupc]) as usize;
            let nsupc = (self.xsup[k + 1] - self.xsup[k]) as usize;
            let luptr = self.xlusup[fsupc] as usize;
            if nsupc == 1 {
                x[fsupc] /= self.lusup[luptr];
            } else {
                dense::trsv_upper(nsupc, &self.lusup, At { off: luptr, ld: lda }, &mut x, fsupc, false);
            }
            for jcol in fsupc..fsupc + nsupc {
                for p in self.xusub[jcol] as usize..self.xusub[jcol + 1] as usize {
                    let irow = self.usub[p] as usize;
                    x[irow] -= x[jcol] * self.ucol[p];
                }
            }
        }
        // X = Pc^-1 X: result[i] = X[perm_c[i]]
        (0..n).map(|i| x[self.perm_c[i]]).collect()
    }
}

/// Test oracle support: the arrays tests/eigen_lu_dump.cpp writes, from this port.
#[allow(clippy::type_complexity)]
#[pyo3::pyfunction]
pub fn eigen_lu_debug(n: usize, triplets: Vec<(usize, usize, f64)>, b: Vec<f64>)
    -> pyo3::PyResult<(Vec<f64>, Vec<usize>, Vec<isize>, Vec<isize>, Vec<f64>, Vec<isize>, Vec<f64>, Vec<usize>, Vec<usize>, Vec<f64>)> {
    let m = Csc::from_triplets(n, &triplets);
    let mtm = m.transpose_times_self();
    let lu = SparseLu::compute(&mtm).ok_or_else(|| pyo3::exceptions::PyValueError::new_err("zero pivot"))?;
    let x = lu.solve(&m.transpose_times(&b));
    let end_lu = lu.xlusup[n] as usize;
    let end_u = lu.xusub[n] as usize;
    let (_, raw) = colamd(&mtm).unwrap();
    let (_, etree) = analyze(&mtm).unwrap();
    Ok((mtm.vals.clone(), lu.perm_c.clone(), lu.perm_r.clone(), lu.xlusup.clone(), lu.lusup[..end_lu].to_vec(),
        lu.xusub.clone(), lu.ucol[..end_u].to_vec(), raw, etree, x))
}
