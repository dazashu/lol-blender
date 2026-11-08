// SPDX-FileCopyrightText: 2011-2023 Blender Authors
// SPDX-License-Identifier: GPL-2.0-or-later
// Initial uniform-depth scan conversion from Blender 4.2 dualcon/octree.cpp.
use crate::dualcon_projection::CubeTriangle;
use std::collections::BTreeMap;

pub const GRID: i64 = 1 << 20;
pub const EDGE_VERTICES: [[usize; 2]; 12] = [
    [0, 4],
    [1, 5],
    [2, 6],
    [3, 7],
    [0, 2],
    [1, 3],
    [4, 6],
    [5, 7],
    [0, 1],
    [2, 3],
    [4, 5],
    [6, 7],
];

#[derive(Clone, Copy, Debug)]
pub struct Intersection {
    pub offset: f32,
    pub normal: [f32; 3],
}

#[derive(Clone, Default, Debug)]
pub struct Cell {
    pub primary: [Option<Intersection>; 3],
    pub parity: u16,
    pub signs: u8,
}

pub struct Scan {
    pub cells: BTreeMap<[i64; 3], Cell>,
    pub origin: [f32; 3],
    pub range: f32,
    pub depth: u32,
}

impl Scan {
    pub fn new(
        vertices: &[[f32; 3]],
        triangles: &[[usize; 3]],
        scale: f32,
        depth: u32,
    ) -> Result<Self, String> {
        if !(1..=12).contains(&depth) || !scale.is_finite() || scale <= 0. || scale > 1. {
            return Err("Invalid dual contouring depth or scale".into());
        }
        if vertices.is_empty()
            || vertices.iter().flatten().any(|v| !v.is_finite())
            || triangles.iter().flatten().any(|&i| i >= vertices.len())
        {
            return Err("Invalid dual contouring input mesh".into());
        }
        let mut min = vertices[0];
        let mut max = min;
        for v in vertices {
            for i in 0..3 {
                min[i] = min[i].min(v[i]);
                max[i] = max[i].max(v[i]);
            }
        }
        let mut range = 0.0f32;
        for i in 0..3 {
            range = range.max(max[i] - min[i]);
        }
        if range == 0. {
            return Err("Dual contouring requires a nonzero extent".into());
        }
        let origin = std::array::from_fn(|i| {
            (max[i] + min[i]) / 2. - range / 2. - range * (1. / scale - 1.) / 2.
        });
        range *= 1. / scale;
        if !range.is_finite() || origin.iter().any(|v| !v.is_finite()) {
            return Err("Dual contouring bounds exceed the supported numeric range".into());
        }
        let mut result = Self {
            cells: BTreeMap::new(),
            origin,
            range,
            depth,
        };
        for (index, tri) in triangles.iter().enumerate() {
            let positions = tri.map(|v| {
                std::array::from_fn(|i| (GRID as f32 * (vertices[v][i] - origin[i]) / range) as i64)
            });
            let projection = CubeTriangle::new([[0; 3], [GRID; 3]], positions, index);
            result.insert_triangle(&projection, [0; 3], depth)?;
        }
        Ok(result)
    }

    fn insert_triangle(
        &mut self,
        projection: &CubeTriangle,
        position: [i64; 3],
        height: u32,
    ) -> Result<(), String> {
        let mask = projection.box_mask();
        let half = projection.half();
        for i in 0..8 {
            if mask & (1 << i) == 0 {
                continue;
            }
            let offset = [(i >> 2) as i64, ((i >> 1) & 1) as i64, (i & 1) as i64];
            let mut child = half.clone();
            child.shift(offset);
            if !child.intersects() {
                continue;
            }
            let pos = std::array::from_fn(|j| position[j] * 2 + offset[j]);
            if height > 1 {
                self.insert_triangle(&child, pos, height - 1)?;
            } else {
                if self.cells.len() >= 8_000_000 {
                    return Err(format!("Dual contouring cell limit exceeded at triangle {}", child.triangle.index));
                }
                let cell = self.cells.entry(pos).or_default();
                for edge in 0..3 {
                    if cell.primary[edge].is_none() && child.intersects_primary(edge) {
                        cell.primary[edge] = Some(Intersection {
                            offset: child.intersection_primary(edge),
                            normal: child.triangle.normal.map(|v| v as f32),
                        });
                        cell.parity |= 1 << (edge * 4);
                    }
                }
            }
        }
        Ok(())
    }

    // All initial leaves have the requested depth. An intersected primary edge
    // propagates to its four incident cells only if all four exist, exactly as
    // cellProcParity/faceProcParity/edgeProcParity do on that uniform octree.
    pub fn reset_minimal_edges(&mut self) {
        let sources: Vec<_> = self
            .cells
            .iter()
            .flat_map(|(p, c)| {
                (0..3)
                    .filter(|&axis| c.primary[axis].is_some())
                    .map(move |axis| (*p, axis))
            })
            .collect();
        for (position, axis) in sources {
            let others: Vec<_> = (0..3).filter(|&i| i != axis).collect();
            let incident: [[i64; 3]; 4] = std::array::from_fn(|i| {
                let mut p = position;
                p[others[0]] -= ((i >> 1) & 1) as i64;
                p[others[1]] -= (i & 1) as i64;
                p
            });
            if !incident.iter().all(|p| self.cells.contains_key(p)) {
                continue;
            }
            for p in incident {
                let delta: [i64; 3] = std::array::from_fn(|i| position[i] - p[i]);
                let vertex = (delta[0] * 4 + delta[1] * 2 + delta[2]) as usize;
                let edge = EDGE_VERTICES
                    .iter()
                    .position(|e| e[0] == vertex && e[1] == vertex + (1 << (2 - axis)))
                    .unwrap();
                self.cells.get_mut(&p).unwrap().parity |= 1 << edge;
            }
        }
    }
}
