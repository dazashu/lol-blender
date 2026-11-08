// SPDX-FileCopyrightText: 2023 Blender Authors
// SPDX-License-Identifier: GPL-2.0-or-later
// Blender KD-tree layout and calc_duplicates_fast, used by BMesh remove doubles.
use pyo3::prelude::*;
use std::collections::{BTreeMap, BTreeSet};
#[derive(Clone)]
struct Node {
    co: [f32; 3],
    index: usize,
    axis: usize,
    left: Option<usize>,
    right: Option<usize>,
}
fn balance(nodes: &mut [Node], axis: usize, offset: usize) -> Option<usize> {
    if nodes.is_empty() {
        return None;
    }
    if nodes.len() == 1 {
        return Some(offset);
    }
    let median = nodes.len() / 2;
    let mut left = 0;
    let mut right = nodes.len() - 1;
    while right > left {
        let value = nodes[right].co[axis];
        let mut i = left;
        let mut j = right - 1;
        loop {
            while nodes[i].co[axis] < value {
                i += 1;
            }
            while nodes[j].co[axis] > value && j > left {
                j -= 1;
            }
            if i >= j {
                break;
            }
            nodes.swap(i, j);
            i += 1;
            j -= 1;
        }
        nodes.swap(i, right);
        if i >= median {
            right = i - 1;
        }
        if i <= median {
            left = i + 1;
        }
    }
    nodes[median].axis = axis;
    nodes[median].left = balance(&mut nodes[..median], (axis + 1) % 3, offset);
    nodes[median].right = balance(
        &mut nodes[median + 1..],
        (axis + 1) % 3,
        offset + median + 1,
    );
    Some(offset + median)
}
fn search(
    nodes: &[Node],
    node: usize,
    point: [f32; 3],
    source: usize,
    distance: f32,
    targets: &mut [Option<usize>],
) -> usize {
    let n = &nodes[node];
    let mut count = 0;
    if point[n.axis] + distance <= n.co[n.axis] {
        if let Some(left) = n.left {
            count += search(nodes, left, point, source, distance, targets);
        }
    } else if point[n.axis] - distance >= n.co[n.axis] {
        if let Some(right) = n.right {
            count += search(nodes, right, point, source, distance, targets);
        }
    } else {
        if source != n.index && targets[n.index].is_none() {
            let mut d = 0.0;
            for i in 0..3 {
                let delta = n.co[i] - point[i];
                d += delta * delta;
            }
            if d <= distance * distance {
                targets[n.index] = Some(source);
                count += 1;
            }
        }
        if let Some(left) = n.left {
            count += search(nodes, left, point, source, distance, targets);
        }
        if let Some(right) = n.right {
            count += search(nodes, right, point, source, distance, targets);
        }
    }
    count
}
pub fn duplicate_map(vertices: &[[f32; 3]], distance: f32) -> Vec<usize> {
    let mut nodes: Vec<_> = vertices
        .iter()
        .enumerate()
        .map(|(index, &co)| Node {
            co,
            index,
            axis: 0,
            left: None,
            right: None,
        })
        .collect();
    let mut targets = vec![None; vertices.len()];
    if let Some(root) = balance(&mut nodes, 0, 0) {
        for n in &nodes {
            if targets[n.index].is_none() || targets[n.index] == Some(n.index) {
                if search(&nodes, root, n.co, n.index, distance, &mut targets) > 0 {
                    targets[n.index] = Some(n.index);
                }
            }
        }
    }
    targets
        .into_iter()
        .enumerate()
        .map(|(i, t)| t.unwrap_or(i))
        .collect()
}
#[pyfunction]
pub fn merge_map(py: Python<'_>, vertices: Vec<f32>, distance: f32) -> PyResult<Vec<usize>> {
    if vertices.len() % 3 != 0
        || vertices.iter().any(|v| !v.is_finite())
        || !distance.is_finite()
        || distance < 0.
    {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "Invalid merge-by-distance input",
        ));
    }
    let vertices: Vec<_> = vertices
        .chunks_exact(3)
        .map(|v| [v[0], v[1], v[2]])
        .collect();
    Ok(py.detach(move || duplicate_map(&vertices, distance)))
}

fn face_key(face: &[usize]) -> Vec<usize> {
    let min = face
        .iter()
        .enumerate()
        .min_by_key(|(_, v)| *v)
        .map(|(i, _)| i)
        .unwrap_or(0);
    let forward: Vec<_> = (0..face.len())
        .map(|i| face[(min + i) % face.len()])
        .collect();
    let backward: Vec<_> = (0..face.len())
        .map(|i| face[(min + face.len() - i) % face.len()])
        .collect();
    forward.min(backward)
}
fn split_face(faces: &mut Vec<Vec<usize>>, index: usize, targets: &[usize]) {
    let face = &faces[index];
    let n = face.len();
    let pair = face.iter().enumerate().find_map(|(i, &v)| {
        if targets[v] == v {
            return None;
        }
        let target = targets[v];
        let j = face.iter().position(|&other| other == target)?;
        if (i + 1) % n == j || (j + 1) % n == i {
            None
        } else {
            Some((i, j))
        }
    });
    let Some((i, j)) = pair else {
        return;
    };
    let mut vertices = face.clone();
    vertices.push(face[j]);
    vertices.push(face[i]);
    let mut next: Vec<_> = (0..n).map(|k| (k + 1) % n).collect();
    next.push(i);
    next.push(j);
    next[(j + n - 1) % n] = n;
    next[(i + n - 1) % n] = n + 1;
    let mut previous = vec![0; n + 2];
    for (i, &j) in next.iter().enumerate() {
        previous[j] = i;
    }
    let mut cur = n;
    let mut first_in_one = false;
    loop {
        if cur == 0 {
            first_in_one = true;
        }
        cur = next[cur];
        if cur == n {
            break;
        }
    }
    let (one, two) = if first_in_one {
        (
            0,
            if previous[0] == n {
                previous[n + 1]
            } else if next[0] == n {
                next[n + 1]
            } else {
                n + 1
            },
        )
    } else {
        (
            if previous[0] == n + 1 {
                previous[n]
            } else if next[0] == n + 1 {
                next[n]
            } else {
                n
            },
            0,
        )
    };
    let cycle = |head| {
        let mut result = Vec::new();
        let mut cur = head;
        loop {
            result.push(vertices[cur]);
            cur = next[cur];
            if cur == head {
                break;
            }
        }
        result
    };
    let a = cycle(one);
    let b = cycle(two);
    faces[index] = a;
    let new = faces.len();
    faces.push(b);
    split_face(faces, index, targets);
    split_face(faces, new, targets);
}
pub fn weld(
    vertices: Vec<[f32; 3]>,
    mut faces: Vec<Vec<usize>>,
    distance: f32,
) -> (Vec<[f32; 3]>, Vec<Vec<usize>>) {
    let targets = duplicate_map(&vertices, distance);
    if targets.iter().enumerate().all(|(i, &t)| i == t) {
        return (vertices, faces);
    }
    let mut i = 0;
    while i < faces.len() {
        split_face(&mut faces, i, &targets);
        i += 1;
    }
    let mut lookup: BTreeMap<Vec<usize>, BTreeSet<usize>> = BTreeMap::new();
    for (i, f) in faces.iter().enumerate() {
        lookup.entry(face_key(f)).or_default().insert(i);
    }
    let mut retained = Vec::new();
    for (i, face) in faces.into_iter().enumerate() {
        if face.iter().all(|&v| targets[v] == v) {
            retained.push(face);
            continue;
        }
        let mapped: Vec<_> = face.iter().map(|&v| targets[v]).collect();
        let reduced: Vec<_> = mapped
            .iter()
            .enumerate()
            .filter_map(|(j, &v)| {
                if v != mapped[(j + 1) % mapped.len()] {
                    Some(v)
                } else {
                    None
                }
            })
            .collect();
        if reduced.len() < 3 || reduced.iter().collect::<BTreeSet<_>>().len() != reduced.len() {
            continue;
        }
        let key = face_key(&reduced);
        if lookup
            .get(&key)
            .is_some_and(|set| set.iter().any(|&index| index != i))
        {
            continue;
        }
        lookup.get_mut(&face_key(&face)).unwrap().remove(&i);
        lookup.entry(key).or_default().insert(i);
        retained.push(reduced);
    }
    let mut indices = vec![0; vertices.len()];
    let mut output = Vec::new();
    for (i, co) in vertices.into_iter().enumerate() {
        if targets[i] == i {
            indices[i] = output.len();
            output.push(co);
        }
    }
    for face in &mut retained {
        for v in face {
            *v = indices[*v];
        }
    }
    (output, retained)
}
#[pyfunction]
pub fn merge_mesh(
    py: Python<'_>,
    vertices: Vec<f32>,
    faces: Vec<Vec<usize>>,
    distance: f32,
) -> PyResult<(Vec<f32>, Vec<Vec<usize>>)> {
    if vertices.len() % 3 != 0
        || vertices.iter().any(|v| !v.is_finite())
        || !distance.is_finite()
        || distance < 0.
        || faces.iter().any(|f| {
            f.len() < 3
                || f.iter().any(|&i| i >= vertices.len() / 3)
                || f.iter().collect::<BTreeSet<_>>().len() != f.len()
        })
    {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "Invalid mesh merge input",
        ));
    }
    let vertices = vertices
        .chunks_exact(3)
        .map(|v| [v[0], v[1], v[2]])
        .collect();
    Ok(py.detach(move || {
        let (v, f) = weld(vertices, faces, distance);
        (v.into_iter().flatten().collect(), f)
    }))
}
