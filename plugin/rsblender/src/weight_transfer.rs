// SPDX-FileCopyrightText: 2024 Blender Authors
// SPDX-License-Identifier: GPL-2.0-or-later
//! Blender 4.2 mesh_remap.cc / math_geom.cc interpolation for ARP transfers.
use pyo3::{exceptions::PyValueError, prelude::*};
type V = [f32; 3];
type Weights = Vec<Vec<(usize, f32)>>;

#[pyfunction]
pub fn transfer_values(source: Weights, links: Weights, target: Weights) -> PyResult<Weights> {
    if links.len() != target.len()
        || links
            .iter()
            .flatten()
            .any(|&(i, w)| i >= source.len() || !w.is_finite())
        || source.iter().flatten().any(|&(_, w)| !w.is_finite())
    {
        return Err(PyValueError::new_err("Invalid transfer weight buffers"));
    }
    let mut result = Vec::with_capacity(target.len());
    for (map, old) in links.into_iter().zip(target) {
        if map.is_empty() {
            result.push(vec![]);
            continue;
        }
        let mut values = std::collections::BTreeMap::<usize, f32>::new();
        for (group, _) in old {
            values.insert(group, 0.0);
        }
        // deform.cc traverses source vertices in reverse order.
        for (vertex, coefficient) in map.into_iter().rev() {
            for &(group, weight) in source[vertex].iter().rev() {
                *values.entry(group).or_default() += weight * coefficient;
            }
        }
        result.push(
            values
                .into_iter()
                .map(|(g, w)| (g, w.clamp(0.0, 1.0)))
                .collect(),
        );
    }
    Ok(result)
}
fn sub(a: V, b: V) -> V {
    std::array::from_fn(|i| a[i] - b[i])
}
fn dot(a: V, b: V) -> f32 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}
fn add_scaled(a: V, b: V, s: f32) -> V {
    std::array::from_fn(|i| a[i] + b[i] * s)
}
fn norm(a: V) -> f32 {
    dot(a, a).sqrt()
}
fn half_tangent(a: V, b: V) -> f32 {
    let cross = [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ];
    let area = norm(cross);
    if area != 0.0 {
        let result = (norm(a) * norm(b) - dot(a, b)) / area;
        if result.is_finite() {
            return result;
        }
    }
    0.0
}
fn segment_factor(p: V, a: V, b: V) -> f32 {
    let d = sub(b, a);
    let length = dot(d, d);
    if length > 0.0 {
        dot(sub(p, a), d) / length
    } else {
        0.0
    }
}
fn closest_triangle(p: V, [a, b, c]: [V; 3]) -> V {
    let ab = sub(b, a);
    let ac = sub(c, a);
    let ap = sub(p, a);
    let d1 = dot(ab, ap);
    let d2 = dot(ac, ap);
    if d1 <= 0.0 && d2 <= 0.0 {
        return a;
    }
    let bp = sub(p, b);
    let d3 = dot(ab, bp);
    let d4 = dot(ac, bp);
    if d3 >= 0.0 && d4 <= d3 {
        return b;
    }
    let vc = d1 * d4 - d3 * d2;
    if vc <= 0.0 && d1 >= 0.0 && d3 <= 0.0 {
        return add_scaled(a, ab, d1 / (d1 - d3));
    }
    let cp = sub(p, c);
    let d5 = dot(ab, cp);
    let d6 = dot(ac, cp);
    if d6 >= 0.0 && d5 <= d6 {
        return c;
    }
    let vb = d5 * d2 - d1 * d6;
    if vb <= 0.0 && d2 >= 0.0 && d6 <= 0.0 {
        return add_scaled(a, ac, d2 / (d2 - d6));
    }
    let va = d3 * d6 - d5 * d4;
    if va <= 0.0 && d4 - d3 >= 0.0 && d5 - d6 >= 0.0 {
        return add_scaled(b, sub(c, b), (d4 - d3) / ((d4 - d3) + (d5 - d6)));
    }
    let inv = 1.0 / (va + vb + vc);
    let v = vb * inv;
    let w = vc * inv;
    add_scaled(add_scaled(a, ab, v), ac, w)
}
fn polygon_weights(vertices: &[V], p: V) -> Vec<f32> {
    let n = vertices.len();
    let mut weights = vec![0.0; n];
    let maximum = vertices
        .iter()
        .flat_map(|&v| sub(v, p))
        .fold(0.0f32, |m, x| m.max(x.abs()));
    let epsilon = 16.0 * f32::EPSILON * maximum;
    let mut previous = n - 1;
    let mut direction = sub(vertices[previous], p);
    let mut ht_previous = half_tangent(sub(vertices[n - 2], p), direction);
    let mut total = 0.0;
    for next in 0..n {
        let length = norm(direction);
        if length < epsilon {
            weights.fill(0.0);
            weights[previous] = 1.0;
            return weights;
        }
        let factor = segment_factor(p, vertices[previous], vertices[next]).clamp(0.0, 1.0);
        let nearest = add_scaled(
            vertices[previous],
            sub(vertices[next], vertices[previous]),
            factor,
        );
        if dot(sub(p, nearest), sub(p, nearest)) < epsilon * epsilon {
            weights.fill(0.0);
            weights[previous] = 1.0 - factor;
            weights[next] = factor;
            return weights;
        }
        let next_direction = sub(vertices[next], p);
        let ht = half_tangent(direction, next_direction);
        weights[previous] = (ht_previous + ht) / length;
        total += weights[previous];
        previous = next;
        direction = next_direction;
        ht_previous = ht;
    }
    if total != 0.0 {
        for w in &mut weights {
            *w /= total;
        }
    }
    weights
}

struct Node {
    low: V,
    high: V,
    children: Option<(usize, usize)>,
    items: Vec<usize>,
    axis: usize,
}

// BLI_kdopbvh.c partition_nth_element, retaining its equal-key permutation.
fn partition_nth(items: &mut [usize], nth: usize, key: impl Fn(usize) -> f32) {
    let mut begin = 0;
    let mut end = items.len();
    while end - begin > 3 {
        let a = key(items[begin]);
        let b = key(items[(begin + end) / 2]);
        let c = key(items[end - 1]);
        let pivot = if b < a {
            if c < b {
                b
            } else if c < a {
                c
            } else {
                a
            }
        } else if c < b {
            if c < a {
                a
            } else {
                c
            }
        } else {
            b
        };
        let mut i = begin;
        let mut j = end;
        let cut = loop {
            while key(items[i]) < pivot {
                i += 1;
            }
            j -= 1;
            while pivot < key(items[j]) {
                j -= 1;
            }
            if i >= j {
                break i;
            }
            items.swap(i, j);
            i += 1;
        };
        if cut <= nth {
            begin = cut;
        } else {
            end = cut;
        }
    }
    for i in begin..end {
        let value = items[i];
        let mut j = i;
        while j != begin && key(value) < key(items[j - 1]) {
            items[j] = items[j - 1];
            j -= 1;
        }
        items[j] = value;
    }
}
struct Search {
    nodes: Vec<Node>,
    primitives: Vec<[V; 3]>,
    points: bool,
}
impl Search {
    fn new(primitives: Vec<[V; 3]>, points: bool) -> Self {
        let mut tree = Self {
            nodes: Vec::new(),
            primitives,
            points,
        };
        if !tree.primitives.is_empty() {
            tree.build((0..tree.primitives.len()).collect());
        }
        tree
    }
    fn build(&mut self, mut items: Vec<usize>) -> usize {
        let mut low = [f32::MAX; 3];
        let mut high = [f32::MIN; 3];
        for &i in &items {
            for v in self.primitives[i] {
                for j in 0..3 {
                    low[j] = low[j].min(v[j]);
                    high[j] = high[j].max(v[j]);
                }
            }
        }
        // Blender inflates every leaf by at least FLT_EPSILON.
        for j in 0..3 {
            low[j] -= f32::EPSILON;
            high[j] += f32::EPSILON;
        }
        let size = sub(high, low);
        let axis = if size[0] > size[1] {
            if size[0] > size[2] {
                0
            } else {
                2
            }
        } else if size[1] > size[2] {
            1
        } else {
            2
        };
        let index = self.nodes.len();
        self.nodes.push(Node {
            low,
            high,
            children: None,
            items: Vec::new(),
            axis,
        });
        if items.len() == 1 {
            self.nodes[index].items = items;
        } else {
            let key = |i: usize| {
                let t = self.primitives[i];
                t[0][axis].max(t[1][axis]).max(t[2][axis]) + f32::EPSILON
            };
            // Binary specialization of Blender's complete implicit tree shape.
            let power = 1usize << (usize::BITS - 1 - items.len().leading_zeros());
            let split = power / 2 + (items.len() - power).min(power / 2);
            partition_nth(&mut items, split, key);
            let right = items.split_off(split);
            let a = self.build(items);
            let b = self.build(right);
            self.nodes[index].children = Some((a, b));
        }
        index
    }
    fn bound(&self, i: usize, p: V) -> f32 {
        let n = &self.nodes[i];
        let d = std::array::from_fn(|j| {
            if p[j] < n.low[j] {
                n.low[j] - p[j]
            } else if p[j] > n.high[j] {
                p[j] - n.high[j]
            } else {
                0.0
            }
        });
        dot(d, d)
    }
    fn query(&self, p: V, previous: Option<(usize, V)>) -> Option<(usize, V)> {
        if self.nodes.is_empty() {
            return None;
        }
        let mut result = previous;
        let mut distance = previous
            .map(|(_, q)| dot(sub(p, q), sub(p, q)))
            .unwrap_or(f32::MAX);
        let mut stack = vec![0];
        while let Some(i) = stack.pop() {
            if self.bound(i, p) >= distance {
                continue;
            }
            let n = &self.nodes[i];
            if let Some((a, b)) = n.children {
                if p[n.axis] <= self.nodes[a].high[n.axis] {
                    stack.push(b);
                    stack.push(a);
                } else {
                    stack.push(a);
                    stack.push(b);
                }
            } else {
                for &item in &n.items {
                    let q = if self.points {
                        std::array::from_fn(|j| p[j].clamp(n.low[j], n.high[j]))
                    } else {
                        closest_triangle(p, self.primitives[item])
                    };
                    let d = dot(sub(p, q), sub(p, q));
                    if d < distance {
                        distance = d;
                        result = Some((item, q));
                    }
                }
            }
        }
        result
    }
}

/// Return source vertex indices and interpolation coefficients. RNA group creation
/// and mesh evaluation remain Blender adapter responsibilities.
#[pyfunction]
pub fn transfer_map(
    source: Vec<V>,
    faces: Vec<Vec<usize>>,
    triangles: Vec<[usize; 3]>,
    triangle_faces: Vec<usize>,
    targets: Vec<V>,
    mapping: &str,
) -> PyResult<Vec<Vec<(usize, f32)>>> {
    if source
        .iter()
        .chain(&targets)
        .flatten()
        .any(|x| !x.is_finite())
        || faces
            .iter()
            .any(|f| f.len() < 3 || f.iter().any(|&i| i >= source.len()))
        || triangles.iter().flatten().any(|&i| i >= source.len())
        || triangle_faces.len() != triangles.len()
        || triangle_faces.iter().any(|&i| i >= faces.len())
    {
        return Err(PyValueError::new_err("Invalid transfer geometry"));
    }
    if mapping == "TOPOLOGY" {
        if source.len() != targets.len() {
            return Err(PyValueError::new_err(
                "Topology transfer requires matching vertex counts",
            ));
        }
        return Ok((0..targets.len()).map(|i| vec![(i, 1.0)]).collect());
    }
    let points = mapping == "NEAREST";
    if !points && mapping != "POLYINTERP_NEAREST" {
        return Err(PyValueError::new_err("Unsupported transfer mapping"));
    }
    let primitives = if points {
        source.iter().map(|&p| [p; 3]).collect()
    } else {
        triangles.iter().map(|t| t.map(|i| source[i])).collect()
    };
    let tree = Search::new(primitives, points);
    let mut previous = None;
    let mut out = Vec::with_capacity(targets.len());
    for p in targets {
        previous = tree.query(p, previous);
        let links = match previous {
            None => vec![],
            Some((i, _)) if points => vec![(i, 1.0)],
            Some((i, q)) => {
                let face = &faces[triangle_faces[i]];
                let polygon: Vec<_> = face.iter().map(|&j| source[j]).collect();
                face.iter()
                    .copied()
                    .zip(polygon_weights(&polygon, q))
                    .collect()
            }
        };
        out.push(links);
    }
    Ok(out)
}
