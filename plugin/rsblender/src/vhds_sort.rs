// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
// Rust adaptation of MSVC STL algorithm and __msvc_heap_algorithms.hpp.
// Preserve the Windows VHDS ordering of equal weights before truncating to K.
type Weight = (usize, f32);
fn before(a: Weight, b: Weight) -> bool {
    a.1 > b.1
}
fn median(v: &mut [Weight], a: usize, b: usize, c: usize) {
    if before(v[b], v[a]) {
        v.swap(a, b);
    }
    if before(v[c], v[b]) {
        v.swap(c, b);
        if before(v[b], v[a]) {
            v.swap(b, a);
        }
    }
}
fn partition(v: &mut [Weight]) -> (usize, usize) {
    let last = v.len();
    let mid = last / 2;
    if last - 1 > 40 {
        let step = last >> 3;
        median(v, 0, step, 2 * step);
        median(v, mid - step, mid, mid + step);
        median(v, last - 1 - 2 * step, last - 1 - step, last - 1);
        median(v, step, mid, last - 1 - step);
    } else {
        median(v, 0, mid, last - 1);
    }
    let mut pfirst = mid;
    let mut plast = mid + 1;
    while pfirst > 0 && !before(v[pfirst - 1], v[pfirst]) && !before(v[pfirst], v[pfirst - 1]) {
        pfirst -= 1;
    }
    while plast < last && !before(v[plast], v[pfirst]) && !before(v[pfirst], v[plast]) {
        plast += 1;
    }
    let mut gfirst = plast;
    let mut glast = pfirst;
    loop {
        while gfirst < last {
            if before(v[pfirst], v[gfirst]) {
            } else if before(v[gfirst], v[pfirst]) {
                break;
            } else {
                if plast != gfirst {
                    v.swap(plast, gfirst);
                }
                plast += 1;
            }
            gfirst += 1;
        }
        while glast > 0 {
            let prev = glast - 1;
            if before(v[prev], v[pfirst]) {
            } else if before(v[pfirst], v[prev]) {
                break;
            } else {
                pfirst -= 1;
                if pfirst != prev {
                    v.swap(pfirst, prev);
                }
            }
            glast -= 1;
        }
        if glast == 0 && gfirst == last {
            return (pfirst, plast);
        }
        if glast == 0 {
            if plast != gfirst {
                v.swap(pfirst, plast);
            }
            plast += 1;
            v.swap(pfirst, gfirst);
            pfirst += 1;
            gfirst += 1;
        } else if gfirst == last {
            glast -= 1;
            pfirst -= 1;
            if glast != pfirst {
                v.swap(glast, pfirst);
            }
            plast -= 1;
            v.swap(pfirst, plast);
        } else {
            glast -= 1;
            v.swap(gfirst, glast);
            gfirst += 1;
        }
    }
}
fn heap_hole(v: &mut [Weight], mut hole: usize, bottom: usize, value: Weight) {
    let top = hole;
    let mut index = hole;
    let max = (bottom - 1) >> 1;
    while index < max {
        index = 2 * index + 2;
        if before(v[index], v[index - 1]) {
            index -= 1;
        }
        v[hole] = v[index];
        hole = index;
    }
    if index == max && bottom % 2 == 0 {
        v[hole] = v[bottom - 1];
        hole = bottom - 1;
    }
    while hole > top {
        let parent = (hole - 1) >> 1;
        if !before(v[parent], value) {
            break;
        }
        v[hole] = v[parent];
        hole = parent;
    }
    v[hole] = value;
}
fn sort_with_budget(mut v: &mut [Weight], mut budget: usize) {
    loop {
        if v.len() <= 32 {
            for i in 1..v.len() {
                let value = v[i];
                let mut j = i;
                while j > 0 && before(value, v[j - 1]) {
                    v[j] = v[j - 1];
                    j -= 1;
                }
                v[j] = value;
            }
            return;
        }
        if budget == 0 {
            for hole in (0..v.len() / 2).rev() {
                heap_hole(v, hole, v.len(), v[hole]);
            }
            for last in (1..v.len()).rev() {
                let value = v[last];
                v[last] = v[0];
                heap_hole(v, 0, last, value);
            }
            return;
        }
        let (a, b) = partition(v);
        budget = (budget >> 1) + (budget >> 2);
        if a < v.len() - b {
            sort_with_budget(&mut v[..a], budget);
            v = &mut v[b..];
        } else {
            sort_with_budget(&mut v[b..], budget);
            v = &mut v[..a];
        }
    }
}
pub fn sort(v: &mut [Weight]) {
    sort_with_budget(v, v.len());
}
