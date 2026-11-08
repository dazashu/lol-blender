// SPDX-FileCopyrightText: 2011-2023 Blender Authors
// SPDX-License-Identifier: GPL-2.0-or-later
use crate::dualcon_scan::{Scan, EDGE_VERTICES, GRID};
use crate::dualcon_topology::morton;
use std::collections::{BTreeMap, BTreeSet};
type Pos = [i64; 3];
fn child(p: Pos, len: i64, i: usize) -> Pos {
    [
        p[0] + len * (i >> 2) as i64,
        p[1] + len * ((i >> 1) & 1) as i64,
        p[2] + len * (i & 1) as i64,
    ]
}
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
const CELL_EDGES: [[usize; 5]; 6] = [
    [0, 1, 2, 3, 0],
    [4, 5, 6, 7, 0],
    [0, 4, 1, 5, 1],
    [2, 6, 3, 7, 1],
    [0, 2, 4, 6, 2],
    [1, 3, 5, 7, 2],
];
const FACE_FACES: [[[usize; 2]; 4]; 3] = [
    [[4, 0], [5, 1], [6, 2], [7, 3]],
    [[2, 0], [6, 4], [3, 1], [7, 5]],
    [[1, 0], [3, 2], [5, 4], [7, 6]],
];
const FACE_EDGES: [[[usize; 6]; 4]; 3] = [
    [
        [1, 4, 0, 5, 1, 1],
        [1, 6, 2, 7, 3, 1],
        [0, 4, 6, 0, 2, 2],
        [0, 5, 7, 1, 3, 2],
    ],
    [
        [0, 2, 3, 0, 1, 0],
        [0, 6, 7, 4, 5, 0],
        [1, 2, 0, 6, 4, 2],
        [1, 3, 1, 7, 5, 2],
    ],
    [
        [1, 1, 0, 3, 2, 0],
        [1, 5, 4, 7, 6, 0],
        [0, 1, 5, 0, 4, 1],
        [0, 3, 7, 2, 6, 1],
    ],
];
const EDGE_EDGES: [[[usize; 4]; 2]; 3] = [
    [[3, 2, 1, 0], [7, 6, 5, 4]],
    [[5, 1, 4, 0], [7, 3, 6, 2]],
    [[6, 4, 2, 0], [7, 5, 3, 1]],
];
pub struct Mesh {
    pub vertices: Vec<[f32; 3]>,
    pub quads: Vec<[usize; 4]>,
}
impl Scan {
    pub fn minimizer(&self, p: Pos, sharpness: f32) -> [f32; 3] {
        let step = (GRID >> self.depth) as f32;
        let start = p.map(|v| v as f32 * step);
        let mut ata = [[0.0; 3]; 3];
        let mut atb = [0.0; 3];
        let mut midpoint = [0.0; 3];
        let mut count = 0;
        for (edge, [vertex, _]) in EDGE_VERTICES.into_iter().enumerate() {
            let n = child(p, 1, vertex);
            if let Some(hit) = self.cells.get(&n).and_then(|c| c.primary[edge / 4]) {
                let mut point = n.map(|v| v as f32 * step);
                point[edge / 4] += hit.offset * step;
                let normal = hit.normal;
                let pn = point[0] * normal[0] + point[1] * normal[1] + point[2] * normal[2];
                for i in 0..3 {
                    for j in 0..3 {
                        ata[i][j] += normal[i] * normal[j];
                    }
                    atb[i] += normal[i] * pn;
                    midpoint[i] += point[i];
                }
                count += 1;
            }
        }
        if count == 0 {
            return start.map(|v| v + step / 2.);
        }
        for v in &mut midpoint {
            *v /= count as f32;
        }
        let pinv = crate::eigen_svd3::pseudoinverse(ata);
        let b: [f32; 3] = std::array::from_fn(|i| {
            atb[i]
                + (ata[i][0] * (-midpoint[0])
                    + (ata[i][1] * (-midpoint[1])
                    + ata[i][2] * (-midpoint[2])))
        });
        let result: [f32; 3] = std::array::from_fn(|i| {
            (pinv[i][0] * b[0] + (pinv[i][1] * b[1] + pinv[i][2] * b[2])) + midpoint[i]
        });
        let low = sharpness * step;
        let high = (1. + sharpness) * step;
        if (0..3).any(|i| result[i] < start[i] - low || result[i] > start[i] + high) {
            midpoint
        } else {
            result
        }
    }
    pub fn mesh(&self, sharpness: f32) -> Mesh {
        let mut positions: Vec<_> = self.cells.keys().copied().collect();
        positions.sort_by_key(|&p| morton(p, self.depth));
        let mut indices = BTreeMap::new();
        let mut vertices = Vec::new();
        let mut nodes = BTreeSet::new();
        for p in positions {
            for depth in 0..=self.depth {
                let len = 1 << depth;
                nodes.insert((p.map(|v| v / len * len), len));
            }
            indices.insert(p, vertices.len());
            if self.cells[&p].signs != 0 && self.cells[&p].signs != 255 {
                let m = self.minimizer(p, sharpness);
                vertices.push(std::array::from_fn(|i| {
                    m[i] * self.range / GRID as f32 + self.origin[i]
                }));
            }
        }
        let mut contour = Contour {
            scan: self,
            nodes,
            indices,
            quads: Vec::new(),
        };
        contour.cell([0; 3], 1 << self.depth);
        Mesh {
            vertices,
            quads: contour.quads,
        }
    }
}
struct Contour<'a> {
    scan: &'a Scan,
    nodes: BTreeSet<(Pos, i64)>,
    indices: BTreeMap<Pos, usize>,
    quads: Vec<[usize; 4]>,
}
impl Contour<'_> {
    fn cell(&mut self, p: Pos, len: i64) {
        if len == 1 || !self.nodes.contains(&(p, len)) {
            return;
        }
        let half = len / 2;
        for i in 0..8 {
            self.cell(child(p, half, i), half);
        }
        for [a, b, dir] in CELL_FACES {
            self.face([child(p, half, a), child(p, half, b)], half, dir);
        }
        for row in CELL_EDGES {
            self.edge(
                std::array::from_fn(|i| child(p, half, row[i])),
                half,
                row[4],
            );
        }
    }
    fn face(&mut self, p: [Pos; 2], len: i64, dir: usize) {
        if len == 1 || p.iter().any(|p| !self.nodes.contains(&(*p, len))) {
            return;
        }
        for [a, b] in FACE_FACES[dir] {
            self.face(
                [child(p[0], len / 2, a), child(p[1], len / 2, b)],
                len / 2,
                dir,
            );
        }
        for row in FACE_EDGES[dir] {
            let order = if row[0] == 0 {
                [0, 0, 1, 1]
            } else {
                [0, 1, 0, 1]
            };
            self.edge(
                std::array::from_fn(|i| child(p[order[i]], len / 2, row[i + 1])),
                len / 2,
                row[5],
            );
        }
    }
    fn edge(&mut self, p: [Pos; 4], len: i64, dir: usize) {
        if p.iter().any(|p| !self.nodes.contains(&(*p, len))) {
            return;
        }
        if len > 1 {
            for row in EDGE_EDGES[dir] {
                self.edge(
                    std::array::from_fn(|i| child(p[i], len / 2, row[i])),
                    len / 2,
                    dir,
                );
            }
        } else {
            let cell = &self.scan.cells[&p[3]];
            if cell.parity & (1 << (dir * 4)) == 0 {
                return;
            }
            let flip = cell.signs & (1 << EDGE_VERTICES[dir * 4][1]) != 0;
            let order = if flip { [2, 3, 1, 0] } else { [0, 1, 3, 2] };
            self.quads.push(order.map(|i| self.indices[&p[i]]));
        }
    }
}
