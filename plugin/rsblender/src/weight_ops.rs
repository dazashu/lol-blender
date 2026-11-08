//! Weight buffer operations used by the Blender binding adapter.
use pyo3::{exceptions::PyValueError, prelude::*};
use std::collections::{BTreeMap, HashMap};

/// Blender vgroup_parray_remove_zero, group_select_mode=ALL, keep_single=false.
/// bpy is in Object Mode, so the vertex paint selection mask does not apply.
#[pyfunction]
pub fn clean_weight_members(
    weights: Vec<Vec<(usize, f32)>>,
    limit: f32,
) -> PyResult<Vec<(usize, Vec<usize>)>> {
    if !limit.is_finite() || !(0.0..=1.0).contains(&limit) {
        return Err(PyValueError::new_err("Invalid weight cleanup limit"));
    }
    let mut groups = BTreeMap::<usize, Vec<usize>>::new();
    for (vertex, entries) in weights.into_iter().enumerate() {
        for (group, weight) in entries {
            if weight <= limit {
                groups.entry(group).or_default().push(vertex);
            }
        }
    }
    Ok(groups.into_iter().collect())
}

#[pyfunction]
pub fn weight_buckets(weights: Vec<Vec<f32>>) -> PyResult<Vec<(usize, f32, Vec<usize>)>> {
    let mut out = Vec::new();
    let count = weights.first().map_or(0, Vec::len);
    for (bone, row) in weights.into_iter().enumerate() {
        if row.len() != count || row.iter().any(|w| w.is_nan()) {
            return Err(PyValueError::new_err("Invalid weight buffer"));
        }
        let mut buckets = BTreeMap::<u32, Vec<usize>>::new();
        for (vertex, weight) in row.into_iter().enumerate() {
            if weight > 0. {
                buckets.entry(weight.to_bits()).or_default().push(vertex);
            }
        }
        out.extend(
            buckets
                .into_iter()
                .map(|(bits, vertices)| (bone, f32::from_bits(bits), vertices)),
        );
    }
    Ok(out)
}

#[pyfunction]
pub fn loose_components(count: usize, edges: Vec<usize>) -> PyResult<Vec<Vec<usize>>> {
    if edges.len() % 2 != 0 || edges.iter().any(|&i| i >= count) {
        return Err(PyValueError::new_err("Invalid edge buffer"));
    }
    let mut parent: Vec<_> = (0..count).collect();
    fn root(p: &mut [usize], mut i: usize) -> usize {
        while p[i] != i {
            p[i] = p[p[i]];
            i = p[i];
        }
        i
    }
    for e in edges.chunks_exact(2) {
        let a = root(&mut parent, e[0]);
        let b = root(&mut parent, e[1]);
        parent[b] = a;
    }
    let mut groups = Vec::<Vec<usize>>::new();
    let mut index = HashMap::new();
    for i in 0..count {
        let r = root(&mut parent, i);
        let at = *index.entry(r).or_insert_with(|| {
            groups.push(Vec::new());
            groups.len() - 1
        });
        groups[at].push(i);
    }
    Ok(groups)
}

/// ARP's two edge-neighbour passes for unweighted vertices after voxel transfer.
/// Preserve edge order and in-place propagation; do not average or normalize.
#[pyfunction]
pub fn fill_unweighted(
    mut weights: Vec<Vec<(usize, f32)>>,
    edges: Vec<usize>,
) -> PyResult<Vec<(usize, Vec<(usize, f32)>)>> {
    if edges.len() % 2 != 0 || edges.iter().any(|&i| i >= weights.len()) {
        return Err(PyValueError::new_err("Invalid edge buffer"));
    }
    let mut adjacency = vec![Vec::new(); weights.len()];
    for edge in edges.chunks_exact(2) {
        adjacency[edge[0]].push(edge[1]);
        adjacency[edge[1]].push(edge[0]);
    }
    let mut changed = BTreeMap::new();
    let started = std::time::Instant::now();
    for _ in 0..2 {
        for vertex in 0..weights.len() {
            if started.elapsed().as_secs_f64() > 6.0 {
                return Ok(changed.into_iter().collect());
            }
            if weights[vertex].is_empty() {
                if let Some(&other) = adjacency[vertex].iter().find(|&&i| !weights[i].is_empty()) {
                    weights[vertex] = weights[other].clone();
                    changed.insert(vertex, weights[vertex].clone());
                }
            }
        }
    }
    Ok(changed.into_iter().collect())
}

/// Per bone, the vertices with a positive weight and those weights, in vertex order.
/// What `weight_buckets` gives, without grouping equal weights: the caller writes per vertex.
#[pyfunction]
pub fn weight_lists(weights: Vec<Vec<f32>>) -> PyResult<Vec<(Vec<usize>, Vec<f32>)>> {
    let count = weights.first().map_or(0, Vec::len);
    let mut out = Vec::with_capacity(weights.len());
    for row in weights {
        if row.len() != count || row.iter().any(|w| w.is_nan()) {
            return Err(PyValueError::new_err("Invalid weight buffer"));
        }
        let mut vertices = Vec::new();
        let mut values = Vec::new();
        for (vertex, weight) in row.into_iter().enumerate() {
            if weight > 0. {
                vertices.push(vertex);
                values.push(weight);
            }
        }
        out.push((vertices, values));
    }
    Ok(out)
}

/// `matrix @ Vector(co)` for every point, as mathutils computes it (mathutils_Vector.cc
/// `column_vector_multiplication`, 4x4 by 3D): each product in float, summed in double from 0
/// with w = 1, rounded to float. `rows` is the matrix row by row, as `[x for row in m for x in row]`.
#[pyfunction]
pub fn mathutils_transform(points: Vec<f32>, rows: Vec<f32>) -> PyResult<Vec<f32>> {
    if points.len() % 3 != 0 || rows.len() != 16 {
        return Err(PyValueError::new_err("Invalid points or matrix"));
    }
    let mut out = Vec::with_capacity(points.len());
    for p in points.chunks_exact(3) {
        let v = [p[0], p[1], p[2], 1.0f32];
        for row in 0..3 {
            let mut dot = 0.0f64;
            for col in 0..4 {
                dot += (rows[row * 4 + col] * v[col]) as f64;
            }
            out.push(dot as f32);
        }
    }
    Ok(out)
}
