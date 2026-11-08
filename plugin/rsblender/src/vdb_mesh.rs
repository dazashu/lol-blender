// Copyright Contributors to the OpenVDB Project
// SPDX-License-Identifier: MPL-2.0
use crate::vdb_levelset::Grid;
use crate::vdb_tables::{AMBIGUOUS, EDGE_GROUPS};
use std::collections::BTreeMap;
type P = [i32; 3];
const CORNERS: [P; 8] = [
    [0, 0, 0],
    [1, 0, 0],
    [1, 0, 1],
    [0, 0, 1],
    [0, 1, 0],
    [1, 1, 0],
    [1, 1, 1],
    [0, 1, 1],
];
const EDGES: [[usize; 3]; 12] = [
    [0, 1, 0],
    [1, 2, 2],
    [3, 2, 0],
    [0, 3, 2],
    [4, 5, 0],
    [5, 6, 2],
    [7, 6, 0],
    [4, 7, 2],
    [0, 4, 1],
    [1, 5, 1],
    [2, 6, 1],
    [3, 7, 1],
];
fn add(p: P, q: P) -> P {
    std::array::from_fn(|i| p[i] + q[i])
}
fn tree_order(p: P) -> [i32; 12] {
    [
        p[0] >> 12,
        p[1] >> 12,
        p[2] >> 12,
        (p[0] >> 7) & 31,
        (p[1] >> 7) & 31,
        (p[2] >> 7) & 31,
        (p[0] >> 3) & 15,
        (p[1] >> 3) & 15,
        (p[2] >> 3) & 15,
        p[0] & 7,
        p[1] & 7,
        p[2] & 7,
    ]
}
fn values(grid: &Grid, p: P) -> [f64; 8] {
    CORNERS.map(|o| grid.value(add(p, o)) as f64)
}
fn signs(values: [f64; 8]) -> usize {
    values
        .into_iter()
        .enumerate()
        .fold(0, |mask, (i, v)| mask | (((v < 0.) as usize) << i))
}
pub struct Mesh {
    pub vertices: Vec<[f32; 3]>,
    pub quads: Vec<[usize; 4]>,
}
pub fn mesh(grid: &Grid) -> Result<Mesh, String> {
    let mut cells = Vec::new();
    for i in 0..grid.values.len() {
        let p = grid.position(i);
        let values = values(grid, p);
        let original = signs(values);
        if original == 0 || original == 255 {
            continue;
        }
        let mut sign = original;
        let face = AMBIGUOUS[sign] as usize;
        if face != 0 {
            let offset = [
                [0, 0, -1],
                [1, 0, 0],
                [0, 0, 1],
                [-1, 0, 0],
                [0, -1, 0],
                [0, 1, 0],
            ][face - 1];
            let opposite = [3, 4, 1, 2, 6, 5][face - 1];
            if AMBIGUOUS[signs(crate::vdb_mesh::values(grid, add(p, offset)))] == opposite {
                sign = (!sign) & 255;
            }
        }
        cells.push((p, original, sign, values));
    }
    cells.sort_by_key(|(p, _, _, _)| tree_order(*p));
    let mut vertices = Vec::new();
    let mut indices = BTreeMap::new();
    for &(p, _, sign, values) in &cells {
        indices.insert(p, (vertices.len(), sign));
        for group in 1..=EDGE_GROUPS[sign][0] {
            let mut average = [0.0f64; 3];
            let mut samples = 0;
            for (edge, [a, b, axis]) in EDGES.into_iter().enumerate() {
                if EDGE_GROUPS[sign][edge + 1] != group {
                    continue;
                }
                for j in 0..3 {
                    if j == axis {
                        average[j] += (0. - values[a]) / (values[b] - values[a]);
                    } else {
                        average[j] += CORNERS[a][j] as f64;
                    }
                }
                samples += 1;
            }
            if samples > 1 {
                let weight = 1. / samples as f64;
                for v in &mut average {
                    *v *= weight;
                }
            }
            let point = std::array::from_fn(|i| {
                ((average[i] + p[i] as f64) * grid.voxel_size as f64) as f32
            });
            if point.iter().any(|v| !v.is_finite()) {
                return Err("OpenVDB contour produced a nonfinite point".into());
            }
            vertices.push(point);
        }
    }
    let mut quads = Vec::new();
    for &(p, original, _, _) in &cells {
        let inside = original & 1 != 0;
        for (axis, bit) in [1, 4, 3].into_iter().enumerate() {
            if inside == (original & (1 << bit) != 0) {
                continue;
            }
            let (offsets, edges) = match axis {
                0 => (
                    [[0, 0, 0], [0, -1, 0], [0, -1, -1], [0, 0, -1]],
                    [1, 5, 7, 3],
                ),
                1 => (
                    [[0, 0, 0], [0, 0, -1], [-1, 0, -1], [-1, 0, 0]],
                    [9, 12, 11, 10],
                ),
                _ => (
                    [[0, 0, 0], [0, -1, 0], [-1, -1, 0], [-1, 0, 0]],
                    [4, 8, 6, 2],
                ),
            };
            let mut quad = [0; 4];
            let mut valid = true;
            for i in 0..4 {
                if let Some(&(base, sign)) = indices.get(&add(p, offsets[i])) {
                    quad[i] = base
                        + if EDGE_GROUPS[sign][0] > 1 {
                            EDGE_GROUPS[sign][edges[i]] as usize - 1
                        } else {
                            0
                        };
                } else {
                    valid = false;
                    break;
                }
            }
            if valid {
                if inside != (axis == 2) {
                    quad.reverse();
                }
                quads.push(quad);
            }
        }
    }
    Ok(Mesh { vertices, quads })
}
