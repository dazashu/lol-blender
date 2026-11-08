// SPDX-FileCopyrightText: 2011-2023 Blender Authors
// SPDX-License-Identifier: GPL-2.0-or-later
// Blender dualcon's path tracing and planar hole repair. All leaves, including
// repair-created leaves, have the requested depth; positions here use cell units.
use crate::dualcon_scan::{Intersection, Scan, EDGE_VERTICES, GRID};
use std::collections::BTreeSet;
type Pos = [i64; 3];
type Path = Vec<Pos>;
type Paths = Vec<Path>;
const FACES: [[usize; 4]; 6] = [
    [4, 8, 5, 9],
    [6, 10, 7, 11],
    [0, 8, 1, 10],
    [2, 9, 3, 11],
    [0, 4, 2, 6],
    [1, 5, 3, 7],
];
const CELL_FACES: [[usize; 3]; 12] = [
    [0, 4, 0],
    [1, 5, 0],
    [2, 6, 0],
    [3, 7, 0],
    [0, 2, 1],
    [4, 6, 1],
    [1, 3, 1],
    [5, 7, 1],
    [0, 1, 2],
    [2, 3, 2],
    [4, 5, 2],
    [6, 7, 2],
];
const FACE_FACES: [[[usize; 2]; 4]; 3] = [
    [[4, 0], [5, 1], [6, 2], [7, 3]],
    [[2, 0], [6, 4], [3, 1], [7, 5]],
    [[1, 0], [3, 2], [5, 4], [7, 6]],
];
fn child(p: Pos, len: i64, i: usize) -> Pos {
    [
        p[0] + len * (i >> 2) as i64,
        p[1] + len * ((i >> 1) & 1) as i64,
        p[2] + len * (i & 1) as i64,
    ]
}

#[derive(Clone)]
struct Element {
    pos: Pos,
    next: usize,
}
pub struct Repair<'a> {
    scan: &'a mut Scan,
    nodes: BTreeSet<(Pos, i64)>,
    arena: Vec<Element>,
    pub rings: usize,
}
impl<'a> Repair<'a> {
    pub fn new(scan: &'a mut Scan) -> Self {
        let mut result = Self {
            scan,
            nodes: BTreeSet::new(),
            arena: Vec::new(),
            rings: 0,
        };
        for p in result.scan.cells.keys().copied().collect::<Vec<_>>() {
            result.mark(p);
        }
        result
    }
    fn mark(&mut self, p: Pos) {
        for height in 0..=self.scan.depth {
            let len = 1 << height;
            self.nodes.insert((p.map(|v| v / len * len), len));
        }
    }
    pub fn trace(&mut self) -> Result<usize, String> {
        self.rings = 0;
        let _incomplete = self.trace_node([0; 3], 1 << self.scan.depth)?;
        Ok(self.rings)
    }
    fn trace_node(&mut self, pos: Pos, len: i64) -> Result<Paths, String> {
        let half = len / 2;
        let mut paths: [Paths; 8] = std::array::from_fn(|_| Vec::new());
        for i in 0..8 {
            let p = child(pos, half, i);
            if half > 1 && self.nodes.contains(&(p, half)) {
                paths[i] = self.trace_node(p, half)?;
            }
        }
        let mut connectors: [Paths; 12] = std::array::from_fn(|_| Vec::new());
        for (i, [a, b, dir]) in CELL_FACES.into_iter().enumerate() {
            self.find_paths(
                child(pos, half, a),
                child(pos, half, b),
                half,
                dir,
                &mut connectors[i],
            );
        }
        let mut rings = Vec::new();
        for (a, b, c) in [
            (0, Some(1), 8),
            (2, Some(3), 9),
            (4, Some(5), 10),
            (6, Some(7), 11),
            (0, Some(2), 4),
            (4, Some(6), 5),
            (0, None, 6),
            (4, None, 7),
            (0, Some(4), 0),
            (0, None, 1),
            (0, None, 2),
            (0, None, 3),
        ] {
            let first = std::mem::take(&mut paths[a]);
            let second = b.map(|b| std::mem::take(&mut paths[b])).unwrap_or_default();
            paths[a] = combine(
                first,
                second,
                std::mem::take(&mut connectors[c]),
                &mut rings,
            );
        }
        self.rings += rings.len();
        let mut heads = Vec::new();
        for ring in rings {
            let head = self.arena.len();
            let count = ring.len();
            for (i, pos) in ring.into_iter().enumerate() {
                self.arena.push(Element {
                    pos,
                    next: head + (i + 1) % count,
                });
            }
            heads.push(head);
        }
        self.patch(pos, len, heads)?;
        Ok(std::mem::take(&mut paths[0]))
    }
    fn find_paths(&self, a: Pos, b: Pos, len: i64, dir: usize, paths: &mut Paths) {
        if !self.nodes.contains(&(a, len)) || !self.nodes.contains(&(b, len)) {
            return;
        }
        if len > 1 {
            for [i, j] in FACE_FACES[dir] {
                self.find_paths(
                    child(a, len / 2, i),
                    child(b, len / 2, j),
                    len / 2,
                    dir,
                    paths,
                );
            }
        } else {
            let parity = self.scan.cells[&a].parity;
            if FACES[dir * 2 + 1]
                .iter()
                .map(|&e| (parity >> e) & 1)
                .sum::<u16>()
                & 1
                != 0
            {
                paths.insert(0, vec![a, b]);
            }
        }
    }
    fn patch(&mut self, pos: Pos, len: i64, rings: Vec<usize>) -> Result<(), String> {
        if rings.is_empty() {
            return Ok(());
        }
        if len <= 1 {
            return Err("Dual contouring ring remained inside a leaf".into());
        }
        let mut groups = vec![rings];
        for dir in 0..3 {
            let mut next = Vec::new();
            for rings in groups {
                let mut low = Vec::new();
                let mut high = Vec::new();
                for head in rings {
                    self.split(pos, len, head, dir, &mut low, &mut high)?;
                }
                next.push(low);
                next.push(high);
            }
            groups = next;
        }
        for (i, group) in groups.into_iter().enumerate() {
            self.patch(child(pos, len / 2, i), len / 2, group)?;
        }
        Ok(())
    }
    fn split(
        &mut self,
        pos: Pos,
        len: i64,
        head: usize,
        dir: usize,
        low: &mut Vec<usize>,
        high: &mut Vec<usize>,
    ) -> Result<(), String> {
        let mid = pos[dir] + len / 2;
        let side = self.arena[head].pos[dir] < mid;
        let mut pre1 = head;
        let mut cur = self.arena[head].next;
        while cur != head && (self.arena[cur].pos[dir] < mid) == side {
            pre1 = cur;
            cur = self.arena[cur].next;
        }
        if cur == head {
            if side {
                low.insert(0, head);
            } else {
                high.insert(0, head);
            }
            return Ok(());
        }
        let second_side = self.arena[cur].pos[dir] < mid;
        let mut pre2 = cur;
        cur = self.arena[cur].next;
        while (self.arena[cur].pos[dir] < mid) == second_side {
            pre2 = cur;
            cur = self.arena[cur].next;
        }
        if second_side {
            std::mem::swap(&mut pre1, &mut pre2);
        }
        let next1 = self.arena[pre1].next;
        let next2 = self.arena[pre2].next;
        self.arena[pre1].next = next2;
        self.arena[pre2].next = next1;
        self.connect(pos, len, dir, pre1, pre2)?;
        for h in [pre1, pre2] {
            let next = self.arena[h].next;
            if self.arena[h].pos == self.arena[next].pos {
                if next == h {
                    continue;
                }
                self.arena[h].next = self.arena[next].next;
            }
            if let Some(h) = self.compress(h) {
                self.split(pos, len, h, dir, low, high)?;
            }
        }
        Ok(())
    }
    fn compress(&mut self, head: usize) -> Option<usize> {
        let mut prepre = head;
        let mut pre = self.arena[head].next;
        let mut cur = self.arena[pre].next;
        let mut anchor = head;
        loop {
            while self.arena[cur].pos == self.arena[prepre].pos {
                if cur == prepre {
                    return None;
                }
                self.arena[prepre].next = self.arena[cur].next;
                pre = self.arena[prepre].next;
                cur = self.arena[pre].next;
                anchor = prepre;
            }
            prepre = pre;
            pre = cur;
            cur = self.arena[cur].next;
            if prepre == anchor {
                return Some(anchor);
            }
        }
    }
    fn face_point(&self, p: Pos, dir: usize) -> (i64, i64, f32, f32) {
        let step = (GRID >> self.scan.depth) as f32;
        let mut avg = [0.0f32; 3];
        let mut num = 0;
        for edge in FACES[dir * 2] {
            let v = EDGE_VERTICES[edge][0];
            let n = child(p, 1, v);
            if let Some(hit) = self.scan.cells.get(&n).and_then(|c| c.primary[edge / 4]) {
                let mut co = n.map(|v| v as f32 * step);
                co[edge / 4] += hit.offset * step;
                for i in 0..3 {
                    avg[i] += co[i];
                }
                num += 1;
            }
        }
        if num == 0 {
            avg = p.map(|v| v as f32 * step);
        } else {
            for v in &mut avg {
                *v /= num as f32;
            }
        }
        let x = (dir + 1) % 3;
        let y = (dir + 2) % 3;
        (
            p[x],
            p[y],
            (avg[x] - p[x] as f32 * step) / step,
            (avg[y] - p[y] as f32 * step) / step,
        )
    }
    fn connect(
        &mut self,
        pos: Pos,
        len: i64,
        dir: usize,
        f1: usize,
        f2: usize,
    ) -> Result<(), String> {
        let xdir = (dir + 1) % 3;
        let ydir = (dir + 2) % 3;
        let (x1, y1, p1, q1) = self.face_point(self.arena[self.arena[f2].next].pos, dir);
        let (x2, y2, p2, q2) = self.face_point(self.arena[f2].pos, dir);
        let dx = x2 as f32 + p2 - x1 as f32 - p1;
        let dy = y2 as f32 + q2 - y1 as f32 - q1;
        let incx = if x2 < x1 { -1 } else { 1 };
        let incy = if y2 < y1 { -1 } else { 1 };
        let mut rx = if incx < 0 { 1. - p1 } else { p1 };
        let mut ry = if incy < 0 { 1. - q1 } else { q1 };
        let sx = dx * incx as f32;
        let sy = dy * incy as f32;
        let mut ori = [0; 3];
        ori[dir] = pos[dir] + len / 2;
        ori[xdir] = x1;
        ori[ydir] = y1;
        let mut neg = f1;
        let mut positive = self.arena[f2].next;
        while ori[xdir] != x2 || ori[ydir] != y2 {
            let (choice, next) = if sy * (1. - rx) > sx * (1. - ry) {
                let next = ori[ydir] + incy;
                if next < y1.min(y2) || next > y1.max(y2) {
                    (4, ori[xdir] + incx)
                } else {
                    (1, next)
                }
            } else {
                let next = ori[xdir] + incx;
                if next < x1.min(x2) || next > x1.max(x2) {
                    (3, ori[ydir] + incy)
                } else {
                    (2, next)
                }
            };
            let (walk, inc, alpha) = if choice & 1 != 0 {
                ori[ydir] = next;
                if choice == 1 {
                    rx += if sy == 0. { 0. } else { (1. - ry) * sx / sy };
                    ry = 0.;
                }
                (2, incy, if incx < 0 { 1. - rx } else { rx })
            } else {
                ori[xdir] = next;
                if choice == 2 {
                    ry += if sx == 0. { 0. } else { (1. - rx) * sy / sx };
                    rx = 0.;
                }
                (1, incx, if incy < 0 { 1. - ry } else { ry })
            };
            let mut np = ori;
            np[dir] -= 1;
            for p in [np, ori] {
                if self.scan.cells.len() >= 8_000_000 {
                    return Err("Dual contouring repair cell limit exceeded".into());
                }
                if !self.scan.cells.contains_key(&p) {
                    self.scan.cells.insert(p, Default::default());
                    self.mark(p);
                }
            }
            if self.arena[neg].pos != np {
                let next = self.arena[neg].next;
                let new = if self.arena[next].pos == np {
                    next
                } else {
                    let i = self.arena.len();
                    self.arena.push(Element { pos: np, next });
                    self.arena[neg].next = i;
                    i
                };
                self.patch_adjacent(self.arena[neg].pos, np, walk, inc, dir, 1, alpha);
                neg = new;
            }
            if self.arena[positive].pos != ori {
                let new = if self.arena[f2].pos == ori {
                    f2
                } else {
                    let i = self.arena.len();
                    self.arena.push(Element {
                        pos: ori,
                        next: positive,
                    });
                    self.arena[f2].next = i;
                    i
                };
                self.patch_adjacent(self.arena[positive].pos, ori, walk, inc, dir, 0, alpha);
                positive = new;
            }
        }
        Ok(())
    }
    fn patch_adjacent(
        &mut self,
        a: Pos,
        b: Pos,
        walk: usize,
        inc: i64,
        dir: usize,
        side: usize,
        alpha: f32,
    ) {
        let edge_dir = (dir + 3 - walk) % 3;
        let inc_dir = (dir + walk) % 3;
        let bit = |axis| {
            if edge_dir == 1 {
                (axis + 3 - edge_dir) % 3 - 1
            } else {
                2 - (axis + 3 - edge_dir) % 3
            }
        };
        let e1 = (edge_dir << 2) | (side << bit(dir)) | (((inc > 0) as usize) << bit(inc_dir));
        let e2 = (edge_dir << 2) | (side << bit(dir)) | (((inc <= 0) as usize) << bit(inc_dir));
        for (p, e) in [(a, e1), (b, e2)] {
            let cell = self.scan.cells.get_mut(&p).unwrap();
            cell.parity ^= 1 << e;
            if e & 3 == 0 && cell.parity & (1 << e) != 0 && cell.primary[e / 4].is_none() {
                cell.primary[e / 4] = Some(Intersection {
                    offset: alpha,
                    normal: [0.; 3],
                });
            }
        }
    }
}

fn combine(mut first: Paths, mut second: Paths, connectors: Paths, rings: &mut Paths) -> Paths {
    let mut new = Vec::new();
    for mut single in connectors {
        for list in [&mut first, &mut second, &mut new] {
            let mut i = 0;
            while i < list.len() {
                let path = &mut list[i];
                if path.first() == single.first() || path.last() == single.last() {
                    if path.len() < single.len() {
                        path.reverse();
                    } else {
                        single.reverse();
                    }
                }
                if path.first() == single.last() {
                    let path = list.remove(i);
                    single.extend_from_slice(&path[1..]);
                } else if path.last() == single.first() {
                    let mut path = list.remove(i);
                    path.extend_from_slice(&single[1..]);
                    single = path;
                } else {
                    i += 1;
                }
            }
        }
        if single.first() == single.last() {
            single.remove(0);
            rings.insert(0, single);
        } else {
            new.insert(0, single);
        }
    }
    first.extend(second);
    first.extend(new);
    first
}
