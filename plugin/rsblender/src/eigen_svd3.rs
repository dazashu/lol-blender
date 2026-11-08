// Copyright (C) 2009-2010 Benoit Jacob
// Copyright (C) 2009-2016 Gael Guennebaud
// SPDX-License-Identifier: MPL-2.0
// Fixed real 3x3 port of Eigen's JacobiSVD, JacobiRotation and RealSvd2x2.
type Matrix = [[f32; 3]; 3];
#[derive(Clone, Copy)]
struct Rotation {
    c: f32,
    s: f32,
}
fn left(a: &mut Matrix, p: usize, q: usize, r: Rotation) {
    for i in 0..3 {
        let x = a[p][i];
        let y = a[q][i];
        a[p][i] = r.c * x + r.s * y;
        a[q][i] = -r.s * x + r.c * y;
    }
}
fn right(a: &mut Matrix, p: usize, q: usize, r: Rotation) {
    for row in a {
        let x = row[p];
        let y = row[q];
        row[p] = r.c * x - r.s * y;
        row[q] = r.s * x + r.c * y;
    }
}
fn jacobi(x: f32, y: f32, z: f32) -> Rotation {
    let denominator = 2. * y.abs();
    if denominator < f32::MIN_POSITIVE {
        return Rotation { c: 1., s: 0. };
    }
    let tau = (x - z) / denominator;
    let w = (tau * tau + 1.).sqrt();
    let t = if tau > 0. {
        1. / (tau + w)
    } else {
        1. / (tau - w)
    };
    let sign = if t > 0. { 1. } else { -1. };
    let n = 1. / (t * t + 1.).sqrt();
    Rotation {
        c: n,
        s: -sign * (y / y.abs()) * t.abs() * n,
    }
}
pub fn pseudoinverse(a: Matrix) -> Matrix {
    let mut scale = a.iter().flatten().fold(0.0f32, |m, v| m.max(v.abs()));
    if scale == 0. {
        scale = 1.;
    }
    let mut work = a.map(|r| r.map(|v| v / scale));
    let mut u = [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]];
    let mut v = u;
    let mut max_diag = work[0][0].abs().max(work[1][1].abs()).max(work[2][2].abs());
    loop {
        let mut finished = true;
        for p in 1..3 {
            for q in 0..p {
                let threshold = f32::MIN_POSITIVE.max(2. * f32::EPSILON * max_diag);
                if !(work[p][q].abs() > threshold || work[q][p].abs() > threshold) {
                    continue;
                }
                finished = false;
                let t = work[p][p] + work[q][q];
                let d = work[q][p] - work[p][q];
                let rot = if d.abs() < f32::MIN_POSITIVE {
                    Rotation { c: 1., s: 0. }
                } else {
                    let ratio = t / d;
                    let tmp = (1. + ratio * ratio).sqrt();
                    Rotation {
                        s: 1. / tmp,
                        c: ratio / tmp,
                    }
                };
                let jr = jacobi(
                    rot.c * work[p][p] + rot.s * work[q][p],
                    rot.c * work[p][q] + rot.s * work[q][q],
                    -rot.s * work[p][q] + rot.c * work[q][q],
                );
                let jl = Rotation {
                    c: rot.c * jr.c - rot.s * (-jr.s),
                    s: rot.c * (-jr.s) + rot.s * jr.c,
                };
                left(&mut work, p, q, jl);
                right(&mut u, p, q, Rotation { c: jl.c, s: -jl.s });
                right(&mut work, p, q, jr);
                right(&mut v, p, q, jr);
                max_diag = max_diag.max(work[p][p].abs().max(work[q][q].abs()));
            }
        }
        if finished {
            break;
        }
    }
    let mut singular = std::array::from_fn::<_, 3, _>(|i| work[i][i].abs() * scale);
    for i in 0..3 {
        if work[i][i] < 0. {
            for row in &mut u {
                row[i] = -row[i];
            }
        }
    }
    for i in 0..3 {
        let mut pos = i;
        for j in i + 1..3 {
            if singular[j] > singular[pos] {
                pos = j;
            }
        }
        if singular[pos] == 0. {
            break;
        }
        if pos != i {
            singular.swap(i, pos);
            for row in &mut u {
                row.swap(i, pos);
            }
            for row in &mut v {
                row.swap(i, pos);
            }
        }
    }
    let scaled = v.map(|r| {
        std::array::from_fn::<_, 3, _>(|i| {
            r[i] * if singular[i].abs() > 0.1 {
                1. / singular[i]
            } else {
                0.
            }
        })
    });
    std::array::from_fn(|i| {
        std::array::from_fn(|j| {
            scaled[i][0] * u[j][0] + (scaled[i][1] * u[j][1] + scaled[i][2] * u[j][2])
        })
    })
}
