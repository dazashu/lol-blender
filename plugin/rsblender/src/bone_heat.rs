//! Blender 4.2 meshlaplacian.cc bone heat, GPL-2.0-or-later.
//! Geometry uses scalar f32 arithmetic, matrix accumulation/least squares use f64.
//! The least-squares solve is a bit-exact port of Blender's Eigen SparseLU (eigen_sparselu.rs).
use pyo3::{exceptions::PyValueError, prelude::*};
use std::collections::HashMap;

type V = [f32; 3];
fn sub(a: V, b: V) -> V {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}
fn add(a: V, b: V) -> V {
    [a[0] + b[0], a[1] + b[1], a[2] + b[2]]
}
fn mul(a: V, s: f32) -> V {
    [a[0] * s, a[1] * s, a[2] * s]
}
fn dot(a: V, b: V) -> f32 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}
fn cross(a: V, b: V) -> V {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}
fn norm(a: V) -> (V, f32) {
    let d = dot(a, a);
    if d > 1e-35 {
        let d = d.sqrt();
        (mul(a, 1.0 / d), d)
    } else {
        ([0.; 3], 0.)
    }
}
fn normal(a: V, b: V, c: V) -> V {
    norm(cross(sub(a, b), sub(b, c))).0
}
fn closest(p: V, a: V, b: V) -> V {
    let d = sub(b, a);
    let n = dot(d, d);
    if n == 0. {
        return a;
    }
    let t = dot(sub(p, a), d) / n;
    if t <= 0. {
        a
    } else if t >= 1. {
        b
    } else {
        add(a, mul(d, t))
    }
}
fn cot(a: V, b: V, c: V) -> f32 {
    let x = sub(b, a);
    let y = sub(c, a);
    let n = cross(x, y);
    let l = dot(n, n).sqrt();
    if l > f32::EPSILON {
        dot(x, y) / l
    } else {
        0.
    }
}
fn angle(a: V, b: V) -> f32 {
    let a = norm(a).0;
    let b = norm(b).0;
    // Blender angle_normalized_v3v3 uses asin, not acos (math_vector_inline.c).
    if dot(a, b) >= 0. {
        2. * (dot(sub(a, b), sub(a, b)).sqrt() / 2.).min(1.).asin()
    } else {
        std::f32::consts::PI - 2. * (dot(add(a, b), add(a, b)).sqrt() / 2.).min(1.).asin()
    }
}

struct Tree {
    min: V,
    max: V,
    children: Option<(Box<Tree>, Box<Tree>)>,
    faces: Vec<usize>,
}
impl Tree {
    fn new(v: &[V], t: &[[usize; 3]], mut ids: Vec<usize>) -> Self {
        let mut min = [f32::MAX; 3];
        let mut max = [-f32::MAX; 3];
        for &i in &ids {
            for &j in &t[i] {
                for k in 0..3 {
                    min[k] = min[k].min(v[j][k]);
                    max[k] = max[k].max(v[j][k]);
                }
            }
        }
        // BLI_bvhtree_new clamps even an explicitly zero epsilon to FLT_EPSILON.
        for k in 0..3 {
            min[k] -= f32::EPSILON;
            max[k] += f32::EPSILON;
        }
        if ids.len() <= 8 {
            return Self {
                min,
                max,
                children: None,
                faces: ids,
            };
        }
        let e = sub(max, min);
        let axis = if e[0] > e[1] {
            if e[0] > e[2] {
                0
            } else {
                2
            }
        } else if e[1] > e[2] {
            1
        } else {
            2
        };
        ids.sort_unstable_by(|&a, &b| {
            let ac = v[t[a][0]][axis] + v[t[a][1]][axis] + v[t[a][2]][axis];
            let bc = v[t[b][0]][axis] + v[t[b][1]][axis] + v[t[b][2]][axis];
            ac.total_cmp(&bc).then(a.cmp(&b))
        });
        let right = ids.split_off(ids.len() / 2);
        Self {
            min,
            max,
            children: Some((
                Box::new(Self::new(v, t, ids)),
                Box::new(Self::new(v, t, right)),
            )),
            faces: Vec::new(),
        }
    }
    /// Blender's `BLI_bvhtree_ray_cast` with `bvh_callback` (meshlaplacian.cc 372), as a boolean.
    ///
    /// Blender inserts every triangle as its own leaf (tree type 4, kdop 6) and rejects it with
    /// `fast_ray_nearest_hit` before the triangle test. An ancestor box is the exact float union
    /// of its children, so it can never reject what one of its leaves accepts; whether any facing
    /// hit exists is therefore decided by the leaf tests alone. This tree only gathers candidates
    /// with deliberately inflated boxes, and every candidate goes through Blender's own leaf test.
    fn blocked(&self, v: &[V], t: &[[usize; 3]], cast: &RayData, start: V, dir: V, dist: f32) -> bool {
        // Conservative candidate filter: generous absolute and relative slack around the box.
        let mut near = -1e-3 * dist;
        let mut far = dist * (1. + 1e-3);
        for k in 0..3 {
            let slack = 1e-4 * (1. + self.min[k].abs().max(self.max[k].abs()));
            let (lo, hi) = (self.min[k] - slack, self.max[k] + slack);
            if dir[k].abs() < 1e-6 {
                let end = start[k] + dir[k] * dist;
                if start[k].max(end) < lo || start[k].min(end) > hi {
                    return false;
                }
            } else {
                let a = (lo - start[k]) / dir[k];
                let b = (hi - start[k]) / dir[k];
                near = near.max(a.min(b));
                far = far.min(a.max(b));
                if near > far {
                    return false;
                }
            }
        }
        if let Some((a, b)) = &self.children {
            return a.blocked(v, t, cast, start, dir, dist) || b.blocked(v, t, cast, start, dir, dist);
        }
        self.faces.iter().any(|&i| {
            let [a, b, c] = t[i];
            cast.leaf_hit(v[a], v[b], v[c], start, dist)
                && ray(start, dir, v[a], v[b], v[c]).is_some_and(|d| d < dist)
                && dot(normal(v[a], v[b], v[c]), dir) < -1e-5
        })
    }
}

/// `bvhtree_ray_cast_data_precalc` (BLI_kdopbvh.c 1946) for kdop 6.
struct RayData {
    idot: [f32; 3],
    index: [usize; 6],
}
impl RayData {
    fn new(dir: V) -> Self {
        let mut idot = [0.0f32; 3];
        let mut index = [0usize; 6];
        for i in 0..3 {
            // dot_v3v3(direction, bvhtree_kdop_axes[i]) with a unit axis.
            let axis = [
                if i == 0 { 1.0f32 } else { 0. },
                if i == 1 { 1.0f32 } else { 0. },
                if i == 2 { 1.0f32 } else { 0. },
            ];
            let ray_dot_axis = dot(dir, axis);
            idot[i] = if ray_dot_axis.abs() < f32::EPSILON {
                f32::MAX
            } else {
                1.0 / ray_dot_axis
            };
            index[2 * i] = if idot[i] < 0. { 1 } else { 0 };
            index[2 * i + 1] = 1 - index[2 * i];
            index[2 * i] += 2 * i;
            index[2 * i + 1] += 2 * i;
        }
        Self { idot, index }
    }

    /// One leaf: `heat_ray_tree_create` bounds (min/max of the triangle, inserted as 2 points,
    /// `create_kdop_hull`, inflated by FLT_EPSILON * axis length 1), then `fast_ray_nearest_hit`
    /// and the `dist >= hit.dist` cut of `dfs_raycast`, with `hit.dist` still the ray length.
    fn leaf_hit(&self, a: V, b: V, c: V, origin: V, hit_dist: f32) -> bool {
        let mut bb = [f32::MAX, f32::MAX, f32::MAX, -f32::MAX, -f32::MAX, -f32::MAX];
        for p in [a, b, c] {
            for k in 0..3 {
                bb[k] = bb[k].min(p[k]);
                bb[k + 3] = bb[k + 3].max(p[k]);
            }
        }
        // node_minmax_init then create_kdop_hull over the two points of bb.
        let mut bv = [f32::MAX, -f32::MAX, f32::MAX, -f32::MAX, f32::MAX, -f32::MAX];
        for point in [[bb[0], bb[1], bb[2]], [bb[3], bb[4], bb[5]]] {
            for axis in 0..3 {
                let unit = [
                    if axis == 0 { 1.0f32 } else { 0. },
                    if axis == 1 { 1.0f32 } else { 0. },
                    if axis == 2 { 1.0f32 } else { 0. },
                ];
                let value = dot(point, unit);
                if value < bv[2 * axis] {
                    bv[2 * axis] = value;
                }
                if value > bv[2 * axis + 1] {
                    bv[2 * axis + 1] = value;
                }
            }
        }
        for axis in 0..3 {
            bv[2 * axis] -= f32::EPSILON * 1.0;
            bv[2 * axis + 1] += f32::EPSILON * 1.0;
        }
        let i = &self.index;
        let d = &self.idot;
        let t1x = (bv[i[0]] - origin[0]) * d[0];
        let t2x = (bv[i[1]] - origin[0]) * d[0];
        let t1y = (bv[i[2]] - origin[1]) * d[1];
        let t2y = (bv[i[3]] - origin[1]) * d[1];
        let t1z = (bv[i[4]] - origin[2]) * d[2];
        let t2z = (bv[i[5]] - origin[2]) * d[2];
        if (t1x > t2y || t2x < t1y || t1x > t2z || t2x < t1z || t1y > t2z || t2y < t1z)
            || (t2x < 0. || t2y < 0. || t2z < 0.)
            || (t1x > hit_dist || t1y > hit_dist || t1z > hit_dist)
        {
            return false;
        }
        // max_fff, then dfs_raycast: `if (dist >= data->hit.dist) return;`
        let max_ff = |a: f32, b: f32| if a > b { a } else { b };
        max_ff(max_ff(t1x, t1y), t1z) < hit_dist
    }
}

fn ray(o: V, d: V, a: V, b: V, c: V) -> Option<f32> {
    let abs = [d[0].abs(), d[1].abs(), d[2].abs()];
    let z = if abs[0] > abs[1] {
        if abs[0] > abs[2] {
            0
        } else {
            2
        }
    } else if abs[1] > abs[2] {
        1
    } else {
        2
    };
    if d[z] == 0. {
        return None;
    }
    let mut x = (z + 1) % 3;
    let mut y = (x + 1) % 3;
    if d[z] < 0. {
        std::mem::swap(&mut x, &mut y);
    }
    let sz = 1. / d[z];
    let sx = d[x] * sz;
    let sy = d[y] * sz;
    let a = sub(a, o);
    let b = sub(b, o);
    let c = sub(c, o);
    let ax = a[x] - sx * a[z];
    let ay = a[y] - sy * a[z];
    let bx = b[x] - sx * b[z];
    let by = b[y] - sy * b[z];
    let cx = c[x] - sx * c[z];
    let cy = c[y] - sy * c[z];
    let u = cx * by - cy * bx;
    let v = ax * cy - ay * cx;
    let w = bx * ay - by * ax;
    if (u < 0. || v < 0. || w < 0.) && (u > 0. || v > 0. || w > 0.) {
        return None;
    }
    let det = u + v + w;
    if det == 0. || !det.is_finite() {
        return None;
    }
    let t = (u * a[z] + v * b[z] + w * c[z]) * sz;
    if f32::from_bits(t.to_bits() ^ (det.to_bits() & 0x80000000)) < 0. {
        return None;
    }
    Some(t * (1. / det))
}

fn vectors(v: Vec<f32>) -> PyResult<Vec<V>> {
    if v.len() % 3 != 0 || v.iter().any(|x| !x.is_finite()) {
        return Err(PyValueError::new_err("Invalid Bone Heat coordinate buffer"));
    }
    Ok(v.chunks_exact(3).map(|v| [v[0], v[1], v[2]]).collect())
}

#[pyfunction]
#[pyo3(signature = (vertices, triangles, heads, tails, group_ids=None, split_edges=None))]
pub fn bone_heat(
    py: Python<'_>,
    vertices: Vec<f32>,
    triangles: Vec<usize>,
    heads: Vec<f32>,
    tails: Vec<f32>,
    group_ids: Option<Vec<usize>>,
    split_edges: Option<Vec<usize>>,
) -> PyResult<Vec<Vec<f32>>> {
    let v = vectors(vertices)?;
    let h = vectors(heads)?;
    let end = vectors(tails)?;
    if v.is_empty()
        || h.is_empty()
        || h.len() != end.len()
        || triangles.len() % 3 != 0
        || triangles.iter().any(|&i| i >= v.len())
    {
        return Err(PyValueError::new_err("Invalid Bone Heat mesh or bones"));
    }
    let t: Vec<[usize; 3]> = triangles
        .chunks_exact(3)
        .map(|t| [t[0], t[1], t[2]])
        .collect();
    let groups = group_ids.unwrap_or_else(|| (0..h.len()).collect());
    if groups.len() != h.len()
        || groups.first() != Some(&0)
        || groups.windows(2).any(|p| p[1] < p[0] || p[1] > p[0] + 1)
    {
        return Err(PyValueError::new_err("Invalid B-Bone segment groups"));
    }
    let parts = split_edges
        .map(|edges| crate::weight_ops::loose_components(v.len(), edges))
        .transpose()?;
    py.detach(move || {
        if let Some(parts) = parts {
            solve_parts(v, t, h, end, groups, parts)
        } else {
            solve(v, t, h, end, groups)
        }
    })
    .map_err(PyValueError::new_err)
}

/// `mesh.separate(type='LOOSE')` (editmesh_tools.cc 4627) on a mesh fresh from object mode, as
/// ARP runs it on its `_arp_copy`: the parts, and the vertex and face order each one ends up with.
///
/// Groups come from `BM_mesh_calc_edge_groups_as_arrays` (bmesh_query.cc 2387): seeds in vertex
/// order, a stack walk over each vertex's disk cycle, faces gathered from each new edge's radial
/// cycle. `BM_mesh_bm_from_me` builds edges in mesh order and `bmesh_disk_edge_append` keeps a
/// vertex's first edge first, so a disk cycle is the vertex's edges in index order.
/// `bmesh_radial_loop_append` makes the newest loop `e->l` and inserts it after the old one, so a
/// radial cycle of loops created l0..lk-1 reads lk-1, l0, l1, ... lk-2.
/// Group 0 stays in the original object, which keeps the original relative order; every other
/// group becomes a new object built by `BM_mesh_copy_arrays` in walk order. Join then puts the
/// original (active) object first and the parts after it in creation order.
#[pyfunction]
pub fn separate_loose(
    vertex_count: usize,
    edges: Vec<usize>,
    faces: Vec<Vec<usize>>,
) -> PyResult<Vec<(Vec<usize>, Vec<usize>)>> {
    if edges.len() % 2 != 0
        || edges.iter().any(|&i| i >= vertex_count)
        || faces.iter().flatten().any(|&i| i >= vertex_count)
    {
        return Err(PyValueError::new_err("Invalid separate geometry"));
    }
    let edge_count = edges.len() / 2;
    let ends: Vec<[usize; 2]> = edges.chunks_exact(2).map(|e| [e[0], e[1]]).collect();
    let mut disk = vec![Vec::new(); vertex_count];
    let mut lookup = HashMap::new();
    for (index, &[a, b]) in ends.iter().enumerate() {
        disk[a].push(index);
        if b != a {
            disk[b].push(index);
        }
        lookup.entry((a.min(b), a.max(b))).or_insert(index);
    }
    let mut loops = vec![Vec::new(); edge_count];
    for (face, verts) in faces.iter().enumerate() {
        for k in 0..verts.len() {
            let (a, b) = (verts[k], verts[(k + 1) % verts.len()]);
            match lookup.get(&(a.min(b), a.max(b))) {
                Some(&edge) => loops[edge].push(face),
                None => {
                    return Err(PyValueError::new_err(
                        "A face edge is missing from the edge list",
                    ))
                }
            }
        }
    }
    let radial: Vec<Vec<usize>> = loops
        .into_iter()
        .map(|l| match l.len() {
            0 | 1 => l,
            k => std::iter::once(l[k - 1])
                .chain(l[..k - 1].iter().copied())
                .collect(),
        })
        .collect();
    let mut vertex_tag = vec![false; vertex_count];
    let mut edge_tag = vec![false; edge_count];
    let mut face_tag = vec![false; faces.len()];
    let mut groups = Vec::new();
    for seed in 0..vertex_count {
        if vertex_tag[seed] {
            continue;
        }
        vertex_tag[seed] = true;
        let mut verts = vec![seed];
        let mut group_faces = Vec::new();
        let mut stack = Vec::new();
        let mut current = Some(seed);
        while let Some(v) = current {
            for &edge in &disk[v] {
                if edge_tag[edge] {
                    continue;
                }
                edge_tag[edge] = true;
                for &face in &radial[edge] {
                    if !face_tag[face] {
                        face_tag[face] = true;
                        group_faces.push(face);
                    }
                }
                let [a, b] = ends[edge];
                let other = if a == v { b } else { a };
                if !vertex_tag[other] {
                    vertex_tag[other] = true;
                    verts.push(other);
                    stack.push(other);
                }
            }
            current = stack.pop();
        }
        groups.push((verts, group_faces));
    }
    if let Some(first) = groups.first_mut() {
        first.0.sort_unstable();
        first.1.sort_unstable();
    }
    Ok(groups)
}

/// Bone heat on each part as its own object, the way ARP's Split Parts calls
/// `parent_set(type='ARMATURE_AUTO')` once per separated part. `parts` gives each part's
/// vertices and triangles (global indices) in that object's own order.
///
/// A part whose factorization fails is reported, not fatal: Blender breaks out of that part's
/// bone loop before writing anything (meshlaplacian.cc 801, linear_solver.cc 290) while the other
/// parts are still weighted. The failed parts' rows come back as 0 and the caller leaves those
/// vertices as they were.
#[pyfunction]
#[pyo3(signature = (vertices, triangles, heads, tails, group_ids, parts))]
pub fn bone_heat_parts(
    py: Python<'_>,
    vertices: Vec<f32>,
    triangles: Vec<usize>,
    heads: Vec<f32>,
    tails: Vec<f32>,
    group_ids: Option<Vec<usize>>,
    parts: Vec<(Vec<usize>, Vec<usize>)>,
) -> PyResult<(Vec<Vec<f32>>, Vec<usize>)> {
    let v = vectors(vertices)?;
    let h = vectors(heads)?;
    let end = vectors(tails)?;
    if v.is_empty()
        || h.is_empty()
        || h.len() != end.len()
        || triangles.len() % 3 != 0
        || triangles.iter().any(|&i| i >= v.len())
    {
        return Err(PyValueError::new_err("Invalid Bone Heat mesh or bones"));
    }
    let t: Vec<[usize; 3]> = triangles
        .chunks_exact(3)
        .map(|t| [t[0], t[1], t[2]])
        .collect();
    let groups = group_ids.unwrap_or_else(|| (0..h.len()).collect());
    if groups.len() != h.len()
        || groups.first() != Some(&0)
        || groups.windows(2).any(|p| p[1] < p[0] || p[1] > p[0] + 1)
    {
        return Err(PyValueError::new_err("Invalid B-Bone segment groups"));
    }
    let mut owner = vec![usize::MAX; v.len()];
    for (index, (verts, tris)) in parts.iter().enumerate() {
        for &vertex in verts {
            if vertex >= v.len() || owner[vertex] != usize::MAX {
                return Err(PyValueError::new_err("Invalid or repeated part vertex"));
            }
            owner[vertex] = index;
        }
        if tris
            .iter()
            .any(|&k| k >= t.len() || t[k].iter().any(|&i| owner[i] != index))
        {
            return Err(PyValueError::new_err("A part triangle leaves its part"));
        }
    }
    let result = py.detach(move || {
        let mut local = vec![0usize; v.len()];
        for (verts, _) in &parts {
            for (i, &vertex) in verts.iter().enumerate() {
                local[vertex] = i;
            }
        }
        // Parts are independent systems: solved in parallel, biggest first for balance,
        // results written back in part order.
        // Big parts one after another, each using every core inside (rays, bones); the many
        // small ones spread across the cores. Either way each part is the same computation.
        const BIG: usize = 2000;
        let solve_part = |index: usize| {
            let (verts, tris) = &parts[index];
            let coords = verts.iter().map(|&i| v[i]).collect();
            let part_tris = tris.iter().map(|&k| t[k].map(|i| local[i])).collect();
            solve(coords, part_tris, h.clone(), end.clone(), groups.clone())
        };
        let mut results: Vec<Option<Result<Vec<Vec<f32>>, String>>> = (0..parts.len()).map(|_| None).collect();
        let small: Vec<usize> = (0..parts.len()).filter(|&i| parts[i].0.len() < BIG).collect();
        for index in (0..parts.len()).filter(|&i| parts[i].0.len() >= BIG) {
            results[index] = Some(solve_part(index));
        }
        let mut order = small;
        order.sort_by_key(|&i| std::cmp::Reverse(parts[i].0.len()));
        let solved = crate::par::map(order.len(), |k| solve_part(order[k]));
        for (k, r) in solved.into_iter().enumerate() {
            results[order[k]] = Some(r);
        }
        let mut out = vec![vec![0.0f32; v.len()]; groups.last().copied().unwrap_or(0) + 1];
        let mut failed = Vec::new();
        for (index, r) in results.into_iter().enumerate() {
            match r.unwrap() {
                Ok(weights) => {
                    for (group, row) in weights.into_iter().enumerate() {
                        for (&vertex, w) in parts[index].0.iter().zip(row) {
                            out[group][vertex] = w;
                        }
                    }
                }
                Err(_) => failed.push(index),
            }
        }
        (out, failed)
    });
    Ok(result)
}

/// Test oracle support: the exact linear system `solve` factorizes, so an Eigen build can be fed
/// the same numbers. Returns (triplets in insertion order, per-bone right-hand side b).
#[pyfunction]
pub fn heat_linear_system(
    vertices: Vec<f32>,
    triangles: Vec<usize>,
    heads: Vec<f32>,
    tails: Vec<f32>,
) -> PyResult<(Vec<(usize, usize, f64)>, Vec<Vec<f64>>)> {
    let v = vectors(vertices)?;
    let h = vectors(heads)?;
    let end = vectors(tails)?;
    if triangles.len() % 3 != 0 || triangles.iter().any(|&i| i >= v.len()) || h.len() != end.len() {
        return Err(PyValueError::new_err("Invalid Bone Heat mesh or bones"));
    }
    let t: Vec<[usize; 3]> = triangles.chunks_exact(3).map(|t| [t[0], t[1], t[2]]).collect();
    let sys = system(&v, &t, &h, &end);
    let rhs = (0..h.len())
        .map(|bone| {
            (0..v.len())
                .map(|i| {
                    if sys.closest_bones[i].contains(&bone) {
                        (sys.h[i] * sys.p[i]) as f64
                    } else {
                        0.
                    }
                })
                .collect()
        })
        .collect();
    Ok((sys.triplets, rhs))
}

fn solve_parts(
    v: Vec<V>,
    t: Vec<[usize; 3]>,
    heads: Vec<V>,
    tails: Vec<V>,
    groups: Vec<usize>,
    parts: Vec<Vec<usize>>,
) -> Result<Vec<Vec<f32>>, String> {
    if parts.len() == 1 {
        return solve(v, t, heads, tails, groups);
    }
    let mut out = vec![vec![0.0; v.len()]; groups.last().copied().unwrap_or(0) + 1];
    let mut owner = vec![0; v.len()];
    let mut local = vec![0; v.len()];
    for (part, vertices) in parts.iter().enumerate() {
        for (i, &vertex) in vertices.iter().enumerate() {
            owner[vertex] = part;
            local[vertex] = i;
        }
    }
    let mut triangles = vec![Vec::new(); parts.len()];
    for tri in t {
        let part = owner[tri[0]];
        if owner[tri[1]] != part || owner[tri[2]] != part {
            return Err("Split edges omit a triangle connection".into());
        }
        triangles[part].push(tri.map(|i| local[i]));
    }
    for (vertices, tris) in parts.into_iter().zip(triangles) {
        let coords = vertices.iter().map(|&i| v[i]).collect();
        let weights = solve(coords, tris, heads.clone(), tails.clone(), groups.clone())?;
        for (group, row) in weights.into_iter().enumerate() {
            for (&vertex, w) in vertices.iter().zip(row) {
                out[group][vertex] = w;
            }
        }
    }
    Ok(out)
}

/// The heat system as Blender builds it: the matrix triplets in `EIG_linear_solver_matrix_add`
/// call order (H diagonal first, then per triangle the nine `laplacian_triangle_weights` entries),
/// and per vertex its H, p and closest bones.
struct System {
    triplets: Vec<(usize, usize, f64)>,
    h: Vec<f32>,
    p: Vec<f32>,
    closest_bones: Vec<Vec<usize>>,
}

fn system(v: &[V], t: &[[usize; 3]], heads: &[V], tails: &[V]) -> System {
    let n = v.len();
    let nb = heads.len();
    let mut normals = vec![[0.; 3]; n];
    let mut attached = vec![false; n];
    let mut edges = HashMap::new();
    let key = |a: usize, b: usize| (a.min(b), a.max(b));
    for &[a, b, c] in t {
        let normal = normal(v[a], v[b], v[c]);
        for i in [a, b, c] {
            normals[i] = add(normals[i], normal);
            attached[i] = true;
        }
        for (i, j) in [(a, b), (b, c), (c, a)] {
            *edges.entry(key(i, j)).or_insert(0u32) += 1;
        }
    }
    for normal in &mut normals {
        *normal = norm(*normal).0;
    }
    let tree = Tree::new(v, t, (0..t.len()).collect());
    // heat_set_H per vertex: independent, so computed in parallel and collected in order.
    let per_vertex = crate::par::map_chunked(n, 256, |i| {
        let mut found = Vec::new();
        let mut h = 0.0f32;
        let mut p = 0.0f32;
        let mut distances = vec![0.; nb];
        let mut min = 1e10f32;
        for b in 0..nb {
            let (d, dist) = norm(sub(v[i], closest(v[i], heads[b], tails[b])));
            distances[b] = dist / (0.5 * (dot(d, normals[i]) + 1.001));
            if distances[b] < min {
                min = distances[b];
            }
        }
        for b in 0..nb {
            if distances[b] <= min * (1. + 1e-4) {
                let vec = sub(closest(v[i], heads[b], tails[b]), v[i]);
                let start = add(v[i], mul(vec, 1e-5));
                let (dir, dist) = norm(mul(vec, 1. - 2e-5));
                let ray_data = RayData::new(dir);
                if !attached[i] || !tree.blocked(v, t, &ray_data, start, dir, dist) {
                    found.push(b);
                }
            }
        }
        let count = found.len();
        if count > 0 {
            p = 1. / count as f32;
            min = min.max(1e-4);
            h = count as f32 / (min * min);
        }
        (found, h, p)
    });
    let mut closest_bones = Vec::with_capacity(n);
    let mut h = Vec::with_capacity(n);
    let mut p = Vec::with_capacity(n);
    for (found, hv, pv) in per_vertex {
        closest_bones.push(found);
        h.push(hv);
        p.push(pv);
    }
    let mut area = vec![0.0f32; n];
    for &[a, b, c] in t {
        let mut w = [
            cot(v[a], v[b], v[c]),
            cot(v[b], v[c], v[a]),
            cot(v[c], v[a], v[b]),
        ];
        let obtuse = if angle(sub(v[b], v[a]), sub(v[c], v[a])) > std::f32::consts::FRAC_PI_2 {
            Some(a)
        } else if angle(sub(v[a], v[b]), sub(v[c], v[b])) > std::f32::consts::FRAC_PI_2 {
            Some(b)
        } else if angle(sub(v[a], v[c]), sub(v[b], v[c])) > std::f32::consts::FRAC_PI_2 {
            Some(c)
        } else {
            None
        };
        if let Some(obtuse) = obtuse {
            let q = cross(sub(v[a], v[b]), sub(v[b], v[c]));
            let ar = dot(q, q).sqrt() * 0.5;
            // By corner slot, like `obtuse == 1/2/3`, so a repeated vertex is not double counted.
            let slot = if obtuse == a { 0 } else if obtuse == b { 1 } else { 2 };
            for (k, i) in [a, b, c].into_iter().enumerate() {
                area[i] += if k == slot { ar } else { ar * 0.5 };
            }
        } else {
            for (j, (i, k)) in [(b, c), (a, c), (a, b)].into_iter().enumerate() {
                let d = sub(v[i], v[k]);
                let l = dot(d, d).sqrt();
                w[j] *= l * l;
            }
            area[a] += (w[1] + w[2]) * 0.25;
            area[b] += (w[0] + w[2]) * 0.25;
            area[c] += (w[0] + w[1]) * 0.25;
        }
    }
    for a in &mut area {
        if *a != 0. {
            *a = 0.5 / *a;
        }
    }
    let mut triplets = Vec::with_capacity(n + 9 * t.len());
    for i in 0..n {
        triplets.push((i, i, h[i] as f64));
    }
    for &[a, b, c] in t {
        let t1 = cot(v[a], v[b], v[c]) / edges[&key(b, c)] as f32;
        let t2 = cot(v[b], v[c], v[a]) / edges[&key(c, a)] as f32;
        let t3 = cot(v[c], v[a], v[b]) / edges[&key(a, b)] as f32;
        for (i, j, value) in [
            (a, a, (t2 + t3) * area[a]),
            (b, b, (t1 + t3) * area[b]),
            (c, c, (t1 + t2) * area[c]),
            (a, b, -t3 * area[a]),
            (b, a, -t3 * area[b]),
            (b, c, -t1 * area[b]),
            (c, b, -t1 * area[c]),
            (c, a, -t2 * area[c]),
            (a, c, -t2 * area[a]),
        ] {
            triplets.push((i, j, value as f64));
        }
    }
    System { triplets, h, p, closest_bones }
}

fn solve(
    v: Vec<V>,
    t: Vec<[usize; 3]>,
    heads: Vec<V>,
    tails: Vec<V>,
    groups: Vec<usize>,
) -> Result<Vec<Vec<f32>>, String> {
    let n = v.len();
    let nb = heads.len();
    let prof = std::env::var_os("DAZA_PROFILE").is_some();
    let t0 = std::time::Instant::now();
    let System { triplets: assembled, h, p, closest_bones } = system(&v, &t, &heads, &tails);
    if prof { eprintln!("PROFILE n={} system {:?}", n, t0.elapsed()); }
    let failure =
        || "Bone Heat Weighting: failed to find solution for one or more bones".to_string();
    // linear_solver.cc least squares: setFromTriplets, M^T M, Eigen SparseLU (bit-exact port,
    // eigen_sparselu.rs). Fails exactly where Eigen reports a zero pivot.
    let t1 = std::time::Instant::now();
    let solver = crate::eigen_sparselu::LeastSquares::new(n, &assembled).ok_or_else(failure)?;
    if prof { eprintln!("PROFILE factor {:?}", t1.elapsed()); }
    let t2 = std::time::Instant::now();
    let group_count = groups[nb - 1] + 1;
    let mut sizes = vec![0; group_count];
    for &g in &groups {
        sizes[g] += 1;
    }
    let mut out = vec![vec![0.0f32; n]; group_count];
    // One right-hand side per bone against the shared factorization: solved in parallel,
    // then applied strictly in bone order (B-Bone segments add onto each other).
    let solutions = crate::par::map(nb, |bone| {
        // laplacian_begin_solve zeroes b; heat_source_closest vertices get H*p.
        let b: Vec<f64> = (0..n)
            .map(|i| if closest_bones[i].contains(&bone) { (h[i] * p[i]) as f64 } else { 0.0 })
            .collect();
        solver.solve(&b)
    });
    if prof { eprintln!("PROFILE solves {:?}", t2.elapsed()); }
    for (bone, solution) in solutions.into_iter().enumerate() {
        let group = groups[bone];
        for i in 0..n {
            let w = solution[i] as f32;
            // meshlaplacian.cc 772: heat_limit_weight(NaN) is NaN and `NaN > 0` is false, so
            // Blender removes the vertex from the group (B-Bone segments: does not add it).
            if w.is_nan() {
                continue;
            }
            // object_vgroup.cc 808 `vgroup_nr_vert_add`: REPLACE stores the weight as is, even
            // above 1. ADD (B-Bone segments) stores a new entry as is too, and only clamps to 1
            // when it adds onto an existing one.
            if sizes[group] > 1 {
                if w > 0. {
                    let current = out[group][i];
                    out[group][i] = if current > 0. {
                        let sum = current + w;
                        if sum >= 1. {
                            1.
                        } else {
                            sum
                        }
                    } else {
                        w
                    };
                }
            } else {
                out[group][i] = if w < 0.025 {
                    0.
                } else if w < 0.05 {
                    (w - 0.025) / (0.05 - 0.025) * 0.05
                } else {
                    w
                };
            }
        }
    }
    for (group, weights) in out.iter_mut().enumerate() {
        if sizes[group] > 1 {
            for w in weights {
                if *w <= 0.025 {
                    *w = 0.;
                }
            }
        }
    }
    Ok(out)
}
