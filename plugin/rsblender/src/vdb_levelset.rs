// Copyright Contributors to the OpenVDB Project
// SPDX-License-Identifier: MPL-2.0
// MeshToVolume's one-voxel narrow band used by Blender.
use std::collections::VecDeque;
type P = [i32; 3];
type V = [f64; 3];
fn sub(a: V, b: V) -> V {
    std::array::from_fn(|i| a[i] - b[i])
}
fn add_scaled(a: V, b: V, t: f64) -> V {
    std::array::from_fn(|i| a[i] + b[i] * t)
}
fn dot(a: V, b: V) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}
fn normalize(mut v: V) -> V {
    let length = dot(v, v).sqrt();
    if length.abs() > 1e-7 {
        let inverse = 1. / length;
        for x in &mut v {
            *x *= inverse;
        }
    }
    v
}
fn close(a: V, b: V) -> bool {
    (0..3).all(|i| (a[i] - b[i]).abs() <= 1e-7)
}
fn checked_neighbor(p: P, offset: P) -> P {
    let mut next = std::array::from_fn(|j| p[j] + offset[j]);
    // OpenVDB 11 checkNeighbours reads +X (without -Z) for its
    // leaf-internal (+1, 0, -1) slot. Preserve the shipped behavior.
    if offset == [1, 0, -1] && (0..3).all(|j| p[j] >> 3 == next[j] >> 3) {
        next[2] = p[2];
    }
    next
}
fn segment(a: V, b: V, p: V) -> V {
    let ab = sub(b, a);
    let t = dot(sub(p, a), ab);
    if t <= 0. {
        a
    } else {
        let d = dot(ab, ab);
        if t >= d {
            b
        } else {
            add_scaled(a, ab, t / d)
        }
    }
}
pub fn closest(a: V, b: V, c: V, p: V) -> V {
    if close(a, b) && close(a, c) {
        return a;
    }
    let ab = sub(b, a);
    let ac = sub(c, a);
    let ap = sub(p, a);
    let d1 = dot(ab, ap);
    let d2 = dot(ac, ap);
    if close(a, b) {
        return segment(a, c, p);
    } else if close(a, c) || close(b, c) {
        return segment(a, b, p);
    }
    if d1 <= 0. && d2 <= 0. {
        return a;
    }
    let bp = sub(p, b);
    let d3 = dot(ab, bp);
    let d4 = dot(ac, bp);
    if d3 >= 0. && d4 <= d3 {
        return b;
    }
    let vc = d1 * d4 - d3 * d2;
    if vc <= 0. && d1 >= 0. && d3 <= 0. {
        return add_scaled(a, ab, d1 / (d1 - d3));
    }
    let cp = sub(p, c);
    let d5 = dot(ab, cp);
    let d6 = dot(ac, cp);
    if d6 >= 0. && d5 <= d6 {
        return c;
    }
    let vb = d5 * d2 - d1 * d6;
    if vb <= 0. && d2 >= 0. && d6 <= 0. {
        return add_scaled(a, ac, d2 / (d2 - d6));
    }
    let va = d3 * d6 - d5 * d4;
    if va <= 0. && (d4 - d3) >= 0. && (d5 - d6) >= 0. {
        return add_scaled(b, sub(c, b), (d4 - d3) / ((d4 - d3) + (d5 - d6)));
    }
    let denominator = 1. / (va + vb + vc);
    add_scaled(add_scaled(a, ab, vb * denominator), ac, vc * denominator)
}
pub struct Grid {
    pub min: P,
    pub dimensions: [usize; 3],
    pub values: Vec<f32>,
    pub active: Vec<bool>,
    pub voxel_size: f32,
}
impl Grid {
    pub fn index(&self, p: P) -> Option<usize> {
        let q: P = std::array::from_fn(|i| p[i] - self.min[i]);
        if (0..3).any(|i| q[i] < 0 || q[i] as usize >= self.dimensions[i]) {
            None
        } else {
            Some(
                (q[0] as usize * self.dimensions[1] + q[1] as usize) * self.dimensions[2]
                    + q[2] as usize,
            )
        }
    }
    pub fn position(&self, i: usize) -> P {
        [
            self.min[0] + (i / (self.dimensions[1] * self.dimensions[2])) as i32,
            self.min[1] + ((i / self.dimensions[2]) % self.dimensions[1]) as i32,
            self.min[2] + (i % self.dimensions[2]) as i32,
        ]
    }
    pub fn value(&self, p: P) -> f32 {
        self.index(p).map_or(self.voxel_size, |i| self.values[i])
    }
    pub fn from_mesh(
        vertices: &[[f32; 3]],
        faces: &[[usize; 3]],
        voxel_size: f32,
    ) -> Result<Self, String> {
        if vertices.is_empty()
            || vertices.iter().flatten().any(|v| !v.is_finite())
            || !voxel_size.is_finite()
            || voxel_size <= 0.
            || faces.iter().flatten().any(|&i| i >= vertices.len())
        {
            return Err("Invalid level set mesh".into());
        }
        let inverse = 1. / voxel_size as f64;
        let points: Vec<V> = vertices
            .iter()
            .map(|p| p.map(|x| (x as f64 * inverse) as f32 as f64))
            .collect();
        if points.iter().flatten().any(|v| !v.is_finite() || v.abs() > (i32::MAX - 16) as f64)
            || faces.len() >= u32::MAX as usize {
            return Err("Voxel size is too small for this mesh's coordinates".into());
        }
        let mut low = points[0];
        let mut high = points[0];
        for p in &points {
            for i in 0..3 {
                low[i] = low[i].min(p[i]);
                high[i] = high[i].max(p[i]);
            }
        }
        let min = low.map(|v| v.floor() as i32 - 4);
        let max = high.map(|v| v.ceil() as i32 + 4);
        let dimensions = std::array::from_fn(|i| (max[i] as i64 - min[i] as i64 + 1) as usize);
        let count = dimensions
            .iter()
            .try_fold(1usize, |a, &b| a.checked_mul(b))
            .ok_or("Level set grid overflow")?;
        if count > 134_217_728 {
            return Err("Level set grid exceeds the supported cell limit".into());
        }
        let mut grid = Self {
            min,
            dimensions,
            values: vec![f32::MAX; count],
            active: vec![false; count],
            voxel_size,
        };
        let mut indices = vec![u32::MAX; count];
        let mut visited = vec![u32::MAX; count];
        let offsets: Vec<P> = (-1..=1)
            .flat_map(|x| {
                (-1..=1).flat_map(move |y| {
                    (-1..=1).filter_map(move |z| {
                        if x == 0 && y == 0 && z == 0 {
                            None
                        } else {
                            Some([x, y, z])
                        }
                    })
                })
            })
            .collect();
        let mut primitives = Vec::new();
        for (triangle, face) in faces.iter().enumerate() {
            let original = [points[face[0]], points[face[1]], points[face[2]]];
            let extent = (0..3).map(|j| original.iter().map(|p| p[j]).fold(f64::NEG_INFINITY, f64::max)
                - original.iter().map(|p| p[j]).fold(f64::INFINITY, f64::min)).fold(0., f64::max);
            let depth = if faces.len() < 1000 { (extent / 16.) as usize } else { 0 };
            let mut pending = vec![(original, depth, faces.len())];
            while let Some((p, depth, count)) = pending.pop() {
                if depth == 0 || count >= 1000 {
                    primitives.push((triangle, p));
                } else {
                    let ab = std::array::from_fn(|j| (p[0][j] + p[1][j]) * 0.5);
                    let ac = std::array::from_fn(|j| (p[0][j] + p[2][j]) * 0.5);
                    let bc = std::array::from_fn(|j| (p[1][j] + p[2][j]) * 0.5);
                    for child in [[p[0], ab, ac], [ab, bc, ac], [ab, p[1], bc], [ac, bc, p[2]]] {
                        pending.push((child, depth - 1, count * 4));
                    }
                }
            }
        }
        for (visit_id, (triangle, [a, c, b])) in primitives.into_iter().enumerate() {
            let visit_id = u32::try_from(visit_id).map_err(|_| "Too many voxelization primitives")?;
            let triangle = triangle as u32;
            let start = a.map(|v| v.floor() as i32);
            let mut stack = vec![start];
            let index = grid.index(start).ok_or("Triangle outside level set grid")?;
            visited[index] = visit_id;
            let update = |grid: &mut Grid, indices: &mut [u32], i: usize, p: P| {
                let center = p.map(|v| v as f64);
                let delta = sub(center, closest(a, b, c, center));
                let d = dot(delta, delta) as f32;
                if d.is_nan() {
                    return false;
                }
                if d < grid.values[i] {
                    grid.values[i] = d;
                    grid.active[i] = true;
                    indices[i] = triangle;
                } else if d == grid.values[i] {
                    indices[i] = indices[i].min(triangle);
                }
                d <= 0.75
            };
            update(&mut grid, &mut indices, index, start);
            while let Some(p) = stack.pop() {
                for &offset in &offsets {
                    let next = std::array::from_fn(|i| p[i] + offset[i]);
                    let Some(i) = grid.index(next) else {
                        continue;
                    };
                    if visited[i] == visit_id {
                        continue;
                    }
                    visited[i] = visit_id;
                    if update(&mut grid, &mut indices, i, next) {
                        stack.push(next);
                    }
                }
            }
        }
        let axes: [[i32; 3]; 6] = [
            [1, 0, 0],
            [-1, 0, 0],
            [0, 1, 0],
            [0, -1, 0],
            [0, 0, 1],
            [0, 0, -1],
        ];
        let mut exterior = vec![false; count];
        let mut queue = VecDeque::from([0usize]);
        exterior[0] = true;
        while let Some(i) = queue.pop_front() {
            let p = grid.position(i);
            for offset in axes {
                let next = std::array::from_fn(|j| p[j] + offset[j]);
                let Some(n) = grid.index(next) else {
                    continue;
                };
                if !exterior[n] && grid.values[n] > 0.75 {
                    exterior[n] = true;
                    queue.push_back(n);
                }
            }
        }
        for (i, &outside) in exterior.iter().enumerate() {
            if outside {
                grid.values[i] = -grid.values[i];
            }
        }
        // Intersecting voxels use nearby exterior points' closest primitive.
        for i in 0..count {
            if !grid.active[i] || grid.values[i] < 0. || grid.values[i] > 0.75 {
                continue;
            }
            let p = grid.position(i);
            let center = p.map(|v| v as f64);
            for &offset in &offsets {
                let next = std::array::from_fn(|j| p[j] + offset[j]);
                let Some(n) = grid.index(next) else {
                    continue;
                };
                if !grid.active[n] || grid.values[n] >= -0.75 {
                    continue;
                }
                let f = faces[indices[n] as usize];
                let sample = next.map(|v| v as f64);
                let nearest = closest(points[f[0]], points[f[2]], points[f[1]], sample);
                if dot(
                    normalize(sub(center, nearest)),
                    normalize(sub(sample, nearest)),
                ) > 0.
                {
                    grid.values[i] = -grid.values[i];
                    break;
                }
            }
        }
        for i in 0..count {
            if !grid.active[i] || grid.values[i] < 0. || grid.values[i] > 0.75 {
                continue;
            }
            let p = grid.position(i);
            if !offsets.iter().any(|o| {
                grid.index(checked_neighbor(p, *o))
                    .is_some_and(|n| grid.values[n] < 0.)
            }) {
                grid.values[i] = 0.75 + 1e-7;
            }
        }
        for i in 0..count {
            if !grid.active[i] || grid.values[i] <= 0.75 {
                continue;
            }
            let p = grid.position(i);
            if !offsets.iter().any(|o| {
                grid.index(checked_neighbor(p, *o))
                    .is_some_and(|n| grid.values[n] <= 0.75)
            }) {
                grid.active[i] = false;
            }
        }
        for i in 0..count {
            grid.values[i] = if grid.active[i] {
                (if grid.values[i] < 0. {
                    voxel_size
                } else {
                    -voxel_size
                }) * grid.values[i].abs().sqrt()
            } else {
                if exterior[i] {
                    voxel_size
                } else {
                    -voxel_size
                }
            };
        }
        let offset = (0.8 * voxel_size as f64) as f32;
        for i in 0..count {
            if grid.active[i] {
                grid.values[i] += -offset;
            }
        }
        let mut updated = grid.values.clone();
        let inv_dx = 1. / voxel_size;
        for i in 0..count {
            if grid.active[i] {
                let p = grid.position(i);
                let phi = grid.values[i];
                let mut norm = 0.0f32;
                for axis in 0..3 {
                    let mut positive = p;
                    positive[axis] += 1;
                    let mut negative = p;
                    negative[axis] -= 1;
                    let up = grid.value(positive) - phi;
                    let down = phi - grid.value(negative);
                    let (a, b) = if phi > 0. {
                        (down.max(0.), up.min(0.))
                    } else {
                        (down.min(0.), up.max(0.))
                    };
                    norm += (a * a).max(b * b);
                }
                let diff = norm.sqrt() * inv_dx - 1.;
                let s = phi / (phi * phi + norm).sqrt();
                let value = phi - voxel_size * s * diff;
                updated[i] = if value < phi { value } else { phi };
            }
        }
        for i in 0..count {
            if grid.active[i] {
                let value = updated[i] + (offset - 1e-7);
                grid.values[i] = value;
                if value < 0. {
                    if value <= -voxel_size {
                        grid.values[i] = -voxel_size;
                        grid.active[i] = false;
                    }
                } else if value >= voxel_size {
                    grid.values[i] = voxel_size;
                    grid.active[i] = false;
                }
            }
        }
        Ok(grid)
    }
}
