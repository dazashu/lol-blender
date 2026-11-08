// SPDX-FileCopyrightText: 2002-2023 Blender Authors
// SPDX-License-Identifier: GPL-2.0-or-later
// Port of Blender 4.2 intern/dualcon/intern/Projections.cpp.
// Integer projections intentionally retain arithmetic right shifts when descending
// the octree; recomputing a child's bounds with floating point changes edge parity.

#[derive(Clone, Copy, Default, Debug)]
pub struct CubeProjection {
    pub origin: i64,
    pub edges: [i64; 3],
    pub min: i64,
    pub max: i64,
}

#[derive(Clone, Debug)]
pub struct TriangleProjection {
    pub ranges: [[i64; 2]; 13],
    pub normal: [f64; 3],
    pub index: usize,
}

#[derive(Clone, Debug)]
pub struct CubeTriangle {
    pub triangle: std::sync::Arc<TriangleProjection>,
    pub cube: [CubeProjection; 13],
}

fn cross(a: [i64; 3], b: [i64; 3]) -> [i64; 3] {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}

fn dot(a: [i64; 3], b: [i64; 3]) -> i64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

impl CubeTriangle {
    pub fn new(bounds: [[i64; 3]; 2], vertices: [[i64; 3]; 3], index: usize) -> Self {
        let mut axes = [[0; 3]; 13];
        for i in 0..3 {
            axes[i][i] = 1;
        }
        let edges: [[i64; 3]; 3] = std::array::from_fn(|i| {
            std::array::from_fn(|j| vertices[(i + 1) % 3][j] - vertices[i][j])
        });
        axes[3] = cross(edges[0], edges[1]);
        for i in 0..3 {
            for j in 0..3 {
                axes[4 + i * 3 + j] = cross(axes[j], edges[i]);
            }
        }
        let a: [f64; 3] = std::array::from_fn(|i| vertices[1][i] as f64 - vertices[0][i] as f64);
        let b: [f64; 3] = std::array::from_fn(|i| vertices[2][i] as f64 - vertices[1][i] as f64);
        let mut normal = [
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        ];
        let mag = normal[0] * normal[0] + normal[1] * normal[1] + normal[2] * normal[2];
        if mag > 0. {
            let length = mag.sqrt();
            for v in &mut normal {
                *v /= length;
            }
        }
        let cube = std::array::from_fn(|i| {
            let edges: [i64; 3] =
                std::array::from_fn(|j| axes[i][j] * (bounds[1][j] - bounds[0][j]));
            let mut min = 0;
            let mut max = 0;
            for vertex in 1..8 {
                let p = ((vertex >> 2) & 1) * edges[0]
                    + ((vertex >> 1) & 1) * edges[1]
                    + (vertex & 1) * edges[2];
                min = min.min(p);
                max = max.max(p);
            }
            CubeProjection {
                origin: dot(axes[i], bounds[0]),
                edges,
                min,
                max,
            }
        });
        let ranges = std::array::from_fn(|i| {
            let values = vertices.map(|v| dot(axes[i], v));
            [*values.iter().min().unwrap(), *values.iter().max().unwrap()]
        });
        Self {
            triangle: std::sync::Arc::new(TriangleProjection {
                ranges,
                normal,
                index,
            }),
            cube,
        }
    }

    pub fn half(&self) -> Self {
        Self {
            triangle: self.triangle.clone(),
            cube: self.cube.map(|p| CubeProjection {
                origin: p.origin,
                edges: p.edges.map(|v| v >> 1),
                min: p.min >> 1,
                max: p.max >> 1,
            }),
        }
    }

    pub fn shift(&mut self, offset: [i64; 3]) {
        for p in &mut self.cube {
            p.origin += dot(offset, p.edges);
        }
    }

    pub fn box_mask(&self) -> u8 {
        let half = self.cube[0].edges[0] >> 1;
        let masks: [[bool; 2]; 3] = std::array::from_fn(|i| {
            let mid = self.cube[i].origin + half;
            [
                mid >= self.triangle.ranges[i][0],
                mid < self.triangle.ranges[i][1],
            ]
        });
        let mut result = 0;
        for i in 0..8 {
            if masks[0][i >> 2] && masks[1][(i >> 1) & 1] && masks[2][i & 1] {
                result |= 1 << i;
            }
        }
        result
    }

    pub fn intersects(&self) -> bool {
        self.cube
            .iter()
            .zip(&self.triangle.ranges)
            .all(|(p, t)| p.origin + p.min <= t[1] && p.origin + p.max >= t[0])
    }

    pub fn intersects_primary(&self, edge: usize) -> bool {
        self.cube.iter().zip(&self.triangle.ranges).all(|(p, t)| {
            let end = p.origin + p.edges[edge];
            p.origin.min(end) <= t[1] && p.origin.max(end) >= t[0]
        })
    }

    pub fn intersection_primary(&self, edge: usize) -> f32 {
        let p = self.cube[3];
        let d = p.edges[edge];
        if d == 0 {
            return 0.5;
        }
        let alpha = (self.triangle.ranges[3][1] - p.origin) as f64 / d as f64;
        if !(0.0..=1.0).contains(&alpha) {
            0.5
        } else {
            alpha as f32
        }
    }
}
