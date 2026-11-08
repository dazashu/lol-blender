// SPDX-License-Identifier: GPL-2.0-or-later
// Port of Blender 4.2 CCGSubSurf_legacy.cc and subsurf_ccg.cc limit positions.
// Copyright Blender Authors. Preserves the source's boundary correction.
use pyo3::{exceptions::PyValueError, prelude::*};
use std::collections::HashMap;
type V = [f32; 3];
fn add(a: V, b: V) -> V {
    [a[0] + b[0], a[1] + b[1], a[2] + b[2]]
}
fn sub(a: V, b: V) -> V {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}
fn mul(a: V, s: f32) -> V {
    [a[0] * s, a[1] * s, a[2] * s]
}
fn key(a: usize, b: usize) -> (usize, usize) {
    (a.min(b), a.max(b))
}

#[pyfunction]
pub fn subsurf_limit(
    vertices: Vec<f32>,
    edges: Vec<(usize, usize)>,
    faces: Vec<Vec<usize>>,
    creases: Vec<f32>,
) -> PyResult<Vec<f32>> {
    let invalid = || PyValueError::new_err("Invalid subdivision topology or coordinate buffer");
    if vertices.len() % 3 != 0
        || vertices.iter().chain(&creases).any(|v| !v.is_finite())
        || creases.len() != edges.len()
    {
        return Err(invalid());
    }
    let v: Vec<V> = vertices
        .chunks_exact(3)
        .map(|p| [p[0], p[1], p[2]])
        .collect();
    let mut ve = vec![Vec::new(); v.len()];
    let mut vf = vec![Vec::new(); v.len()];
    let mut ef = vec![Vec::new(); edges.len()];
    let mut lookup = HashMap::new();
    for (i, &(a, b)) in edges.iter().enumerate() {
        if a >= v.len() || b >= v.len() || a == b || lookup.insert(key(a, b), i).is_some() {
            return Err(invalid());
        }
        ve[a].push(i);
        ve[b].push(i);
    }
    let mut centers = Vec::with_capacity(faces.len());
    for (i, face) in faces.iter().enumerate() {
        if face.len() < 3 || face.iter().any(|&a| a >= v.len()) {
            return Err(invalid());
        }
        let mut center = [0.; 3];
        for (j, &a) in face.iter().enumerate() {
            let b = face[(j + 1) % face.len()];
            let &e = lookup.get(&key(a, b)).ok_or_else(invalid)?;
            vf[a].push(i);
            ef[e].push(i);
            center = add(center, v[a]);
        }
        centers.push(mul(center, 1. / face.len() as f32));
    }
    let boundary = |e: usize| ef[e].len() < 2;
    let mut points = Vec::with_capacity(edges.len());
    for (i, &(a, b)) in edges.iter().enumerate() {
        let sum = add(v[a], v[b]);
        let r = mul(sum, 0.5);
        if boundary(i) || creases[i] >= 1. {
            points.push(r);
        } else {
            let mut q = sum;
            for &f in &ef[i] {
                q = add(q, centers[f]);
            }
            q = mul(q, 1. / (2. + ef[i].len() as f32));
            points.push(add(q, mul(sub(r, q), creases[i])));
        }
    }
    let mut output = Vec::with_capacity(vertices.len());
    for (i, &co) in v.iter().enumerate() {
        let other = |e: usize| {
            let (a, b) = edges[e];
            v[if a == i { b } else { a }]
        };
        let n = ve[i].len();
        // The original divides by zero for isolated vertices. Report that failure.
        if n == 0 {
            return Err(PyValueError::new_err(
                "Subdivision limit is undefined for an isolated vertex",
            ));
        }
        let mut sharp_count = 0;
        let mut all_sharp = true;
        let mut avg = 0.;
        for &e in &ve[i] {
            if creases[e] != 0. {
                sharp_count += 1;
                avg += creases[e];
            } else {
                all_sharp = false;
            }
        }
        if sharp_count > 0 {
            avg = (avg / sharp_count as f32).min(1.);
        }
        let mut q = [0.; 3];
        let mut r = [0.; 3];
        let mut nco;
        if ve[i].iter().any(|&e| boundary(e)) {
            let mut count = 0;
            for &e in &ve[i] {
                if boundary(e) {
                    r = add(r, other(e));
                    count += 1;
                }
            }
            nco = add(mul(co, 0.75), mul(r, 0.25 / count as f32));
        } else {
            for &f in &vf[i] {
                q = add(q, centers[f]);
            }
            q = mul(q, 1. / vf[i].len() as f32);
            for &e in &ve[i] {
                r = add(r, other(e));
            }
            r = mul(r, 1. / n as f32);
            nco = mul(add(add(mul(co, n as f32 - 2.), q), r), 1. / n as f32);
        }
        if sharp_count > 1 {
            q = [0.; 3];
            for &e in &ve[i] {
                if creases[e] != 0. {
                    q = add(q, other(e));
                }
            }
            q = mul(q, 1. / sharp_count as f32);
            if sharp_count != 2 || all_sharp {
                q = add(q, mul(sub(co, q), avg));
            }
            r = add(mul(co, 0.75), mul(q, 0.25));
            nco = add(nco, mul(sub(r, nco), avg));
        }
        let mut edge_sum = [0.; 3];
        let mut face_sum = [0.; 3];
        for &e in &ve[i] {
            edge_sum = add(edge_sum, points[e]);
        }
        for &f in &vf[i] {
            face_sum = add(face_sum, centers[f]);
        }
        if !vf[i].is_empty() && vf[i].len() != n {
            face_sum = mul(face_sum, n as f32 / vf[i].len() as f32);
        }
        for k in 0..3 {
            output.push(
                (nco[k] * n as f32 * n as f32 + edge_sum[k] * 4. + face_sum[k])
                    / (n * (n + 5)) as f32,
            );
        }
    }
    if output.iter().any(|v| !v.is_finite()) {
        return Err(invalid());
    }
    Ok(output)
}
