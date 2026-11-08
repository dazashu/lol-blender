// SPDX-License-Identifier: MPL-2.0
// Rust translation of Eigen 3.4 OrderingMethods/Eigen_Colamd.h and COLAMDOrdering
// (Ordering.h) as bundled in Blender 4.2. Eigen's file is "Modified version of COLAMD,
// Copyright (c) 1998-2003 Timothy A. Davis, University of Florida, and Stefan I. Larimore,
// Xerox Palo Alto Research Center", distributed with permission to use, copy, modify and
// distribute provided the notices are retained. Modified: translated to Rust.
//
// Replaces the faer-derived ordering for the bone heat solve: that one uses different
// dense-row/column thresholds and differed from Eigen on Graves (99 of 540 columns).

const EMPTY: i32 = -1;
const ALIVE: i32 = 0;
const DEAD: i32 = -1;
const DEAD_PRINCIPAL: i32 = -1;
const DEAD_NON_PRINCIPAL: i32 = -2;

#[derive(Clone, Copy, Default)]
struct Col {
    start: i32,
    length: i32,
    shared1: i32, // thickness | parent
    shared2: i32, // score | order
    shared3: i32, // headhash | hash | prev
    shared4: i32, // degree_next | hash_next
}
impl Col {
    fn is_dead(&self) -> bool {
        self.start < ALIVE
    }
    fn is_alive(&self) -> bool {
        self.start >= ALIVE
    }
    fn is_dead_principal(&self) -> bool {
        self.start == DEAD_PRINCIPAL
    }
}

#[derive(Clone, Copy, Default)]
struct Row {
    start: i32,
    length: i32,
    shared1: i32, // degree | p
    shared2: i32, // mark | first_column
}
impl Row {
    fn is_dead(&self) -> bool {
        self.shared2 < ALIVE
    }
    fn is_alive(&self) -> bool {
        self.shared2 >= ALIVE
    }
}

/// COLAMDOrdering::operator(): returns perm with perm[old] = new (Eigen's perm.indices()).
pub fn colamd_ordering(n_row: usize, n_col: usize, colptr: &[usize], rows: &[usize]) -> Option<Vec<usize>> {
    let nnz = colptr[n_col] as i32;
    let (n_row, n_col) = (n_row as i32, n_col as i32);
    // recommended(): 2*nnz + colamd_c + colamd_r + n_col + nnz/5; colamd_c/r are the Col/Row
    // structure sizes in ints (6 and 4 per entry), which compute_ordering subtracts again.
    let col_size = (n_col + 1) * 6;
    let row_size = (n_row + 1) * 4;
    let alen_total = 2 * nnz + col_size + row_size + n_col + nnz / 5;
    let need = 2 * nnz + n_col + col_size + row_size;
    if need > alen_total {
        return None;
    }
    let alen = alen_total - col_size - row_size;
    let mut a = vec![0i32; alen as usize];
    for (k, &r) in rows.iter().enumerate().take(nnz as usize) {
        a[k] = r as i32;
    }
    let mut p: Vec<i32> = colptr.iter().map(|&x| x as i32).collect();
    let mut col = vec![Col::default(); n_col as usize + 1];
    let mut row = vec![Row::default(); n_row as usize + 1];
    if !init_rows_cols(n_row, n_col, &mut row, &mut col, &mut a, &mut p) {
        return None;
    }
    let (_n_row2, n_col2, max_deg) = init_scoring(n_row, n_col, &mut row, &mut col, &mut a, &mut p);
    find_ordering(n_row, n_col, alen, &mut row, &mut col, &mut a, &mut p, n_col2, max_deg, 2 * nnz);
    order_children(n_col, &mut col, &mut p);
    let mut perm = vec![0usize; n_col as usize];
    for i in 0..n_col as usize {
        perm[p[i] as usize] = i;
    }
    Some(perm)
}

fn init_rows_cols(n_row: i32, n_col: i32, row: &mut [Row], col: &mut [Col], a: &mut [i32], p: &mut [i32]) -> bool {
    for c in 0..n_col as usize {
        col[c].start = p[c];
        col[c].length = p[c + 1] - p[c];
        if col[c].length < 0 {
            return false;
        }
        col[c].shared1 = 1;
        col[c].shared2 = 0;
        col[c].shared3 = EMPTY;
        col[c].shared4 = EMPTY;
    }
    let mut jumbled = false;
    for r in 0..n_row as usize {
        row[r].length = 0;
        row[r].shared2 = -1;
    }
    for c in 0..n_col {
        let mut last_row = -1;
        let mut cp = p[c as usize];
        let cp_end = p[c as usize + 1];
        while cp < cp_end {
            let r = a[cp as usize];
            cp += 1;
            if r < 0 || r >= n_row {
                return false;
            }
            if r <= last_row || row[r as usize].shared2 == c {
                jumbled = true;
            }
            if row[r as usize].shared2 != c {
                row[r as usize].length += 1;
            } else {
                col[c as usize].length -= 1;
            }
            row[r as usize].shared2 = c;
            last_row = r;
        }
    }
    if n_row > 0 {
        row[0].start = p[n_col as usize];
        row[0].shared1 = row[0].start;
        row[0].shared2 = -1;
        for r in 1..n_row as usize {
            row[r].start = row[r - 1].start + row[r - 1].length;
            row[r].shared1 = row[r].start;
            row[r].shared2 = -1;
        }
    }
    if jumbled {
        for c in 0..n_col {
            let mut cp = p[c as usize];
            let cp_end = p[c as usize + 1];
            while cp < cp_end {
                let r = a[cp as usize] as usize;
                cp += 1;
                if row[r].shared2 != c {
                    let at = row[r].shared1;
                    row[r].shared1 += 1;
                    a[at as usize] = c;
                    row[r].shared2 = c;
                }
            }
        }
    } else {
        for c in 0..n_col {
            let mut cp = p[c as usize];
            let cp_end = p[c as usize + 1];
            while cp < cp_end {
                let r = a[cp as usize] as usize;
                cp += 1;
                let at = row[r].shared1;
                row[r].shared1 += 1;
                a[at as usize] = c;
            }
        }
    }
    for r in 0..n_row as usize {
        row[r].shared2 = 0;
        row[r].shared1 = row[r].length;
    }
    if jumbled {
        col[0].start = 0;
        p[0] = col[0].start;
        for c in 1..n_col as usize {
            col[c].start = col[c - 1].start + col[c - 1].length;
            p[c] = col[c].start;
        }
        for r in 0..n_row as usize {
            let mut rp = row[r].start;
            let rp_end = rp + row[r].length;
            while rp < rp_end {
                let c = a[rp as usize] as usize;
                rp += 1;
                let at = p[c];
                p[c] += 1;
                a[at as usize] = r as i32;
            }
        }
    }
    true
}

fn init_scoring(n_row: i32, n_col: i32, row: &mut [Row], col: &mut [Col], a: &mut [i32], head: &mut [i32]) -> (i32, i32, i32) {
    let dense_row_count = 0.max(((0.5 * n_col as f64) as i32).min(n_col));
    let dense_col_count = 0.max(((0.5 * n_row as f64) as i32).min(n_row));
    let mut max_deg = 0;
    let mut n_col2 = n_col;
    let mut n_row2 = n_row;
    for c in (0..n_col as usize).rev() {
        if col[c].length == 0 {
            n_col2 -= 1;
            col[c].shared2 = n_col2;
            col[c].start = DEAD_PRINCIPAL;
        }
    }
    for c in (0..n_col as usize).rev() {
        if col[c].is_dead() {
            continue;
        }
        let deg = col[c].length;
        if deg > dense_col_count {
            n_col2 -= 1;
            col[c].shared2 = n_col2;
            let mut cp = col[c].start;
            let cp_end = cp + col[c].length;
            while cp < cp_end {
                let r = a[cp as usize] as usize;
                cp += 1;
                row[r].shared1 -= 1;
            }
            col[c].start = DEAD_PRINCIPAL;
        }
    }
    for r in 0..n_row as usize {
        let deg = row[r].shared1;
        if deg > dense_row_count || deg == 0 {
            row[r].shared2 = DEAD;
            n_row2 -= 1;
        } else {
            max_deg = max_deg.max(deg);
        }
    }
    for c in (0..n_col as usize).rev() {
        if col[c].is_dead() {
            continue;
        }
        let mut score = 0;
        let mut cp = col[c].start;
        let mut new_cp = cp;
        let cp_end = cp + col[c].length;
        while cp < cp_end {
            let r = a[cp as usize];
            cp += 1;
            if row[r as usize].is_dead() {
                continue;
            }
            a[new_cp as usize] = r;
            new_cp += 1;
            score += row[r as usize].shared1 - 1;
            score = score.min(n_col);
        }
        let col_length = new_cp - col[c].start;
        if col_length == 0 {
            n_col2 -= 1;
            col[c].shared2 = n_col2;
            col[c].start = DEAD_PRINCIPAL;
        } else {
            col[c].length = col_length;
            col[c].shared2 = score;
        }
    }
    for h in head.iter_mut().take(n_col as usize + 1) {
        *h = EMPTY;
    }
    for c in (0..n_col as usize).rev() {
        if col[c].is_alive() {
            let score = col[c].shared2 as usize;
            let next_col = head[score];
            col[c].shared3 = EMPTY;
            col[c].shared4 = next_col;
            if next_col != EMPTY {
                col[next_col as usize].shared3 = c as i32;
            }
            head[score] = c as i32;
        }
    }
    (n_row2, n_col2, max_deg)
}

fn clear_mark(n_row: i32, row: &mut [Row]) -> i32 {
    for r in row.iter_mut().take(n_row as usize) {
        if r.is_alive() {
            r.shared2 = 0;
        }
    }
    1
}

#[allow(clippy::too_many_arguments)]
fn find_ordering(n_row: i32, n_col: i32, alen: i32, row: &mut [Row], col: &mut [Col], a: &mut [i32], head: &mut [i32], n_col2: i32, mut max_deg: i32, mut pfree: i32) -> i32 {
    let max_mark = i32::MAX - n_col;
    let mut tag_mark = clear_mark(n_row, row);
    let mut min_score = 0i32;
    let mut ngarbage = 0;
    let mut k = 0i32;
    while k < n_col2 {
        while min_score < n_col && head[min_score as usize] == EMPTY {
            min_score += 1;
        }
        let pivot_col = head[min_score as usize] as usize;
        let next_col = col[pivot_col].shared4;
        head[min_score as usize] = next_col;
        if next_col != EMPTY {
            col[next_col as usize].shared3 = EMPTY;
        }
        let pivot_col_score = col[pivot_col].shared2;
        col[pivot_col].shared2 = k;
        let pivot_col_thickness = col[pivot_col].shared1;
        k += pivot_col_thickness;
        let needed_memory = pivot_col_score.min(n_col - k);
        if pfree + needed_memory >= alen {
            pfree = garbage_collection(n_row, n_col, row, col, a, pfree);
            ngarbage += 1;
            tag_mark = clear_mark(n_row, row);
        }
        let pivot_row_start = pfree;
        let mut pivot_row_degree = 0;
        col[pivot_col].shared1 = -pivot_col_thickness;
        let mut cp = col[pivot_col].start;
        let mut cp_end = cp + col[pivot_col].length;
        while cp < cp_end {
            let r = a[cp as usize] as usize;
            cp += 1;
            if row[r].is_dead() {
                continue;
            }
            let mut rp = row[r].start;
            let rp_end = rp + row[r].length;
            while rp < rp_end {
                let c = a[rp as usize] as usize;
                rp += 1;
                let col_thickness = col[c].shared1;
                if col_thickness > 0 && col[c].is_alive() {
                    col[c].shared1 = -col_thickness;
                    a[pfree as usize] = c as i32;
                    pfree += 1;
                    pivot_row_degree += col_thickness;
                }
            }
        }
        col[pivot_col].shared1 = pivot_col_thickness;
        max_deg = max_deg.max(pivot_row_degree);
        cp = col[pivot_col].start;
        cp_end = cp + col[pivot_col].length;
        while cp < cp_end {
            let r = a[cp as usize] as usize;
            cp += 1;
            row[r].shared2 = DEAD;
        }
        let pivot_row_length = pfree - pivot_row_start;
        let pivot_row = if pivot_row_length > 0 { a[col[pivot_col].start as usize] } else { EMPTY };
        // set differences
        let mut rp = pivot_row_start;
        let mut rp_end = rp + pivot_row_length;
        while rp < rp_end {
            let c = a[rp as usize] as usize;
            rp += 1;
            let col_thickness = -col[c].shared1;
            col[c].shared1 = col_thickness;
            let cur_score = col[c].shared2;
            let prev_col = col[c].shared3;
            let next_col = col[c].shared4;
            if prev_col == EMPTY {
                head[cur_score as usize] = next_col;
            } else {
                col[prev_col as usize].shared4 = next_col;
            }
            if next_col != EMPTY {
                col[next_col as usize].shared3 = prev_col;
            }
            let mut cp = col[c].start;
            let cp_end = cp + col[c].length;
            while cp < cp_end {
                let r = a[cp as usize] as usize;
                cp += 1;
                if row[r].is_dead() {
                    continue;
                }
                let row_mark = row[r].shared2;
                let mut set_difference = row_mark - tag_mark;
                if set_difference < 0 {
                    set_difference = row[r].shared1;
                }
                set_difference -= col_thickness;
                if set_difference == 0 {
                    row[r].shared2 = DEAD;
                } else {
                    row[r].shared2 = set_difference + tag_mark;
                }
            }
        }
        // add set differences
        rp = pivot_row_start;
        rp_end = rp + pivot_row_length;
        while rp < rp_end {
            let c = a[rp as usize] as usize;
            rp += 1;
            let mut hash: u32 = 0;
            let mut cur_score = 0i32;
            let mut cp = col[c].start;
            let mut new_cp = cp;
            let cp_end = cp + col[c].length;
            while cp < cp_end {
                let r = a[cp as usize];
                cp += 1;
                if row[r as usize].is_dead() {
                    continue;
                }
                let row_mark = row[r as usize].shared2;
                a[new_cp as usize] = r;
                new_cp += 1;
                hash = hash.wrapping_add(r as u32);
                cur_score += row_mark - tag_mark;
                cur_score = cur_score.min(n_col);
            }
            col[c].length = new_cp - col[c].start;
            if col[c].length == 0 {
                col[c].start = DEAD_PRINCIPAL;
                pivot_row_degree -= col[c].shared1;
                col[c].shared2 = k;
                k += col[c].shared1;
            } else {
                col[c].shared2 = cur_score;
                hash %= (n_col + 1) as u32;
                let head_column = head[hash as usize];
                let first_col;
                if head_column > EMPTY {
                    first_col = col[head_column as usize].shared3;
                    col[head_column as usize].shared3 = c as i32;
                } else {
                    first_col = -(head_column + 2);
                    head[hash as usize] = -(c as i32 + 2);
                }
                col[c].shared4 = first_col;
                col[c].shared3 = hash as i32;
            }
        }
        detect_super_cols(col, a, head, pivot_row_start, pivot_row_length);
        col[pivot_col].start = DEAD_PRINCIPAL;
        tag_mark += max_deg + 1;
        if tag_mark >= max_mark {
            tag_mark = clear_mark(n_row, row);
        }
        // finalize scores
        rp = pivot_row_start;
        let mut new_rp = rp;
        rp_end = rp + pivot_row_length;
        while rp < rp_end {
            let c = a[rp as usize] as usize;
            rp += 1;
            if col[c].is_dead() {
                continue;
            }
            a[new_rp as usize] = c as i32;
            new_rp += 1;
            let at = col[c].start + col[c].length;
            col[c].length += 1;
            a[at as usize] = pivot_row;
            let mut cur_score = col[c].shared2 + pivot_row_degree;
            let max_score = n_col - k - col[c].shared1;
            cur_score -= col[c].shared1;
            cur_score = cur_score.min(max_score);
            col[c].shared2 = cur_score;
            let next_col = head[cur_score as usize];
            col[c].shared4 = next_col;
            col[c].shared3 = EMPTY;
            if next_col != EMPTY {
                col[next_col as usize].shared3 = c as i32;
            }
            head[cur_score as usize] = c as i32;
            min_score = min_score.min(cur_score);
        }
        if pivot_row_degree > 0 {
            let pr = pivot_row as usize;
            row[pr].start = pivot_row_start;
            row[pr].length = new_rp - pivot_row_start;
            row[pr].shared1 = pivot_row_degree;
            row[pr].shared2 = 0;
        }
    }
    ngarbage
}

fn order_children(n_col: i32, col: &mut [Col], p: &mut [i32]) {
    for i in 0..n_col as usize {
        if !col[i].is_dead_principal() && col[i].shared2 == EMPTY {
            let mut parent = i;
            loop {
                parent = col[parent].shared1 as usize;
                if col[parent].is_dead_principal() {
                    break;
                }
            }
            let mut c = i;
            let mut order = col[parent].shared2;
            loop {
                col[c].shared2 = order;
                order += 1;
                col[c].shared1 = parent as i32;
                c = col[c].shared1 as usize;
                if col[c].shared2 != EMPTY {
                    break;
                }
            }
            col[parent].shared2 = order;
        }
    }
    for c in 0..n_col as usize {
        p[col[c].shared2 as usize] = c as i32;
    }
}

fn detect_super_cols(col: &mut [Col], a: &[i32], head: &mut [i32], row_start: i32, row_length: i32) {
    let mut rp = row_start;
    let rp_end = rp + row_length;
    while rp < rp_end {
        let c0 = a[rp as usize] as usize;
        rp += 1;
        if col[c0].is_dead() {
            continue;
        }
        let hash = col[c0].shared3 as usize;
        let head_column = head[hash];
        let first_col = if head_column > EMPTY { col[head_column as usize].shared3 } else { -(head_column + 2) };
        let mut super_c = first_col;
        while super_c != EMPTY {
            let s = super_c as usize;
            let length = col[s].length;
            let mut prev_c = s;
            let mut c = col[s].shared4;
            while c != EMPTY {
                let cu = c as usize;
                if col[cu].length != length || col[cu].shared2 != col[s].shared2 {
                    prev_c = cu;
                    c = col[cu].shared4;
                    continue;
                }
                let mut i = 0;
                while i < length {
                    if a[(col[s].start + i) as usize] != a[(col[cu].start + i) as usize] {
                        break;
                    }
                    i += 1;
                }
                if i != length {
                    prev_c = cu;
                    c = col[cu].shared4;
                    continue;
                }
                col[s].shared1 += col[cu].shared1;
                col[cu].shared1 = s as i32;
                col[cu].start = DEAD_NON_PRINCIPAL;
                col[cu].shared2 = EMPTY;
                col[prev_c].shared4 = col[cu].shared4;
                c = col[cu].shared4;
            }
            super_c = col[s].shared4;
        }
        if head_column > EMPTY {
            col[head_column as usize].shared3 = EMPTY;
        } else {
            head[hash] = EMPTY;
        }
    }
}

fn garbage_collection(n_row: i32, n_col: i32, row: &mut [Row], col: &mut [Col], a: &mut [i32], pfree: i32) -> i32 {
    let mut pdest = 0i32;
    for c in 0..n_col as usize {
        if col[c].is_alive() {
            let mut psrc = col[c].start;
            col[c].start = pdest;
            let length = col[c].length;
            for _ in 0..length {
                let r = a[psrc as usize];
                psrc += 1;
                if row[r as usize].is_alive() {
                    a[pdest as usize] = r;
                    pdest += 1;
                }
            }
            col[c].length = pdest - col[c].start;
        }
    }
    for r in 0..n_row as usize {
        if row[r].is_alive() {
            if row[r].length == 0 {
                row[r].shared2 = DEAD;
            } else {
                let psrc = row[r].start;
                row[r].shared2 = a[psrc as usize];
                a[psrc as usize] = -(r as i32) - 1;
            }
        }
    }
    let mut psrc = pdest;
    while psrc < pfree {
        let v = a[psrc as usize];
        psrc += 1;
        if v < 0 {
            psrc -= 1;
            let r = (-v - 1) as usize;
            a[psrc as usize] = row[r].shared2;
            row[r].start = pdest;
            let length = row[r].length;
            for _ in 0..length {
                let c = a[psrc as usize];
                psrc += 1;
                if col[c as usize].is_alive() {
                    a[pdest as usize] = c;
                    pdest += 1;
                }
            }
            row[r].length = pdest - row[r].start;
        }
    }
    pdest
}
