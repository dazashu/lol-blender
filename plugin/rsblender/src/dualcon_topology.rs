// SPDX-FileCopyrightText: 2011-2023 Blender Authors
// SPDX-License-Identifier: GPL-2.0-or-later
use crate::dualcon_scan::{Scan, EDGE_VERTICES};
use std::collections::{BTreeSet, VecDeque};
type Pos = [i64; 3];
type Edge = (Pos, usize);
fn child(p: Pos, len: i64, i: usize) -> Pos {
    [
        p[0] + len * (i >> 2) as i64,
        p[1] + len * ((i >> 1) & 1) as i64,
        p[2] + len * (i & 1) as i64,
    ]
}
pub fn morton(p: Pos, depth: u32) -> u64 {
    let mut key = 0;
    for bit in (0..depth).rev() {
        for coord in p {
            key = (key << 1) | ((coord >> bit) & 1) as u64;
        }
    }
    key
}
impl Scan {
    pub fn build_signs(&mut self) {
        let mut table = [0u8; 4096];
        for mask in 0..256u16 {
            let mut parity = 0;
            for (edge, [a, b]) in EDGE_VERTICES.into_iter().enumerate() {
                parity |= (((mask >> a) ^ (mask >> b)) & 1) << edge;
            }
            table[parity as usize] = mask as u8;
        }
        let mut nodes = BTreeSet::new();
        for &p in self.cells.keys() {
            for depth in 0..=self.depth {
                let len = 1 << depth;
                nodes.insert((p.map(|v| v / len * len), len));
            }
        }
        self.signs_node([0; 3], 1 << self.depth, 1, &nodes, &table);
    }
    fn signs_node(
        &mut self,
        p: Pos,
        len: i64,
        start: u8,
        nodes: &BTreeSet<(Pos, i64)>,
        table: &[u8; 4096],
    ) -> [u8; 8] {
        if !nodes.contains(&(p, len)) {
            return [start; 8];
        }
        if len == 1 {
            let cell = self.cells.get_mut(&p).unwrap();
            cell.signs = table[cell.parity as usize];
            if (start ^ cell.signs) & 1 != 0 {
                cell.signs = !cell.signs;
            }
            return std::array::from_fn(|i| (cell.signs >> i) & 1);
        }
        let corners = self.signs_node(p, len / 2, start, nodes, table);
        let mut result = [0; 8];
        result[0] = start;
        for i in 1..8 {
            result[i] = self.signs_node(child(p, len / 2, i), len / 2, corners[i], nodes, table)[i];
        }
        result
    }
    pub fn flood_fill(&mut self, threshold: f32) -> Result<(), String> {
        let largest = self.flood_pass(0)?;
        self.flood_pass((largest as f32 * threshold) as usize)?;
        Ok(())
    }
    fn edge_key(p: Pos, edge: usize) -> Edge {
        (child(p, 1, EDGE_VERTICES[edge][0]), edge / 4)
    }
    fn edge_flag(&self, key: Edge) -> bool {
        self.cells
            .get(&key.0)
            .map_or(false, |c| c.parity & (1 << (key.1 * 4)) != 0)
    }
    fn flip_all(&mut self, (p, axis): Edge) -> Result<(), String> {
        let other: Vec<_> = (0..3).filter(|&i| i != axis).collect();
        for i in 0..4 {
            let mut n = p;
            n[other[0]] -= ((i >> 1) & 1) as i64;
            n[other[1]] -= (i & 1) as i64;
            let delta: Pos = std::array::from_fn(|j| p[j] - n[j]);
            let vertex = (delta[0] * 4 + delta[1] * 2 + delta[2]) as usize;
            let edge = EDGE_VERTICES
                .iter()
                .position(|e| e[0] == vertex && e[1] == vertex + (1 << (2 - axis)))
                .unwrap();
            let cell = self
                .cells
                .get_mut(&n)
                .ok_or("Dual contouring flood edge has a missing incident cell")?;
            cell.parity ^= 1 << edge;
        }
        Ok(())
    }
    fn neighbors(
        &self,
        (p, axis): Edge,
        visited: Option<&BTreeSet<Edge>>,
    ) -> Result<Vec<Edge>, String> {
        const FACE_EDGES: [[[usize; 3]; 4]; 3] = [
            [[9, 2, 11], [8, 1, 10], [5, 1, 7], [4, 2, 6]],
            [[10, 6, 11], [8, 5, 9], [1, 5, 3], [0, 6, 2]],
            [[6, 10, 7], [4, 9, 5], [2, 9, 3], [0, 10, 1]],
        ];
        let negative = std::array::from_fn(|i| p[i] - i64::from(i != axis));
        let positive = self.cells.get(&p).ok_or("Missing positive flood cell")?;
        let mut result = Vec::new();
        for (face, cell_pos) in [negative, p, negative, p].into_iter().enumerate() {
            let cell = self
                .cells
                .get(&cell_pos)
                .ok_or("Missing negative flood cell")?;
            let mut order = FACE_EDGES[axis][face];
            if positive.signs & 1 != 0 {
                order.reverse();
            }
            for edge in order {
                let key = Self::edge_key(cell_pos, edge);
                let flag = visited.map_or(cell.parity & (1 << edge) != 0, |set| set.contains(&key));
                if flag {
                    result.push(key);
                    break;
                }
            }
        }
        Ok(result)
    }
    fn flood_pass(&mut self, threshold: usize) -> Result<usize, String> {
        let mut positions: Vec<_> = self.cells.keys().copied().collect();
        positions.sort_by_key(|&p| morton(p, self.depth));
        let mut visited = BTreeSet::new();
        let mut largest = 0;
        for p in positions {
            for edge in 0..12 {
                let start = Self::edge_key(p, edge);
                if self.cells[&p].parity & (1 << edge) == 0 || visited.contains(&start) {
                    continue;
                }
                let mut count = 1;
                visited.insert(start);
                let mut queue = VecDeque::from([start]);
                while let Some(current) = queue.pop_front() {
                    for next in self.neighbors(current, None)? {
                        if visited.insert(next) {
                            queue.push_back(next);
                            count += 1;
                        }
                    }
                }
                largest = largest.max(count);
                if count < threshold {
                    self.flip_all(start)?;
                    queue.push_back(start);
                    while let Some(current) = queue.pop_front() {
                        for next in self.neighbors(current, Some(&visited))? {
                            if self.edge_flag(next) {
                                self.flip_all(next)?;
                                queue.push_back(next);
                            }
                        }
                    }
                }
            }
        }
        Ok(largest)
    }
}
