//! Geometry primitives recovered from VHDS 3.5.3 (Linux addresses 0x20650, 0x21130).
pub type Point = [f32; 3];
pub fn sub(a: Point, b: Point) -> Point {
    std::array::from_fn(|i| a[i] - b[i])
}
fn cross(a: Point, b: Point) -> Point {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}
fn dot(a: Point, b: Point) -> f32 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

pub fn intersect(origin: Point, direction: Point, triangle: [Point; 3]) -> Option<(f32, f32)> {
    let e1 = sub(triangle[1], triangle[0]);
    let e2 = sub(triangle[2], triangle[0]);
    let p = cross(direction, e2);
    let determinant = dot(e1, p);
    if (determinant as f64) > -1e-6 && (determinant as f64) < 1e-6 {
        return None;
    }
    let inverse = 1.0 / determinant;
    let t = sub(origin, triangle[0]);
    let u = (p[1] * t[1] + p[0] * t[0] + p[2] * t[2]) * inverse;
    if !(0.0..=1.0).contains(&u) {
        return None;
    }
    let q = cross(t, e1);
    let v = dot(direction, q) * inverse;
    if v < 0.0 || v + u > 1.0 {
        return None;
    }
    Some((
        inverse * (e2[2] * q[2] + e2[0] * q[0] + e2[1] * q[1]),
        determinant,
    ))
}

pub fn triangle_box(center: Point, half: f32, triangle: [Point; 3]) -> bool {
    let v = triangle.map(|p| sub(p, center));
    let edges = [sub(v[1], v[0]), sub(v[2], v[1]), sub(v[0], v[2])];
    // The specialized SAT tests use two projections per axis; the third is
    // redundant mathematically but can change a floating-point boundary decision.
    for (i, e) in edges.into_iter().enumerate() {
        let pairs = if i == 0 {
            [(0, 2), (0, 2), (1, 2)]
        } else if i == 1 {
            [(0, 2), (0, 2), (0, 1)]
        } else {
            [(0, 1), (0, 1), (1, 2)]
        };
        for (axis, (a, b)) in pairs.into_iter().enumerate() {
            let project = |p: Point| match axis {
                0 => p[1] * e[2] - p[2] * e[1],
                1 => -e[2] * p[0] + e[0] * p[2],
                _ => e[1] * p[0] - e[0] * p[1],
            };
            let p = project(v[a]);
            let q = project(v[b]);
            let radius = match axis {
                0 => half * e[2].abs() + half * e[1].abs(),
                1 => half * e[2].abs() + half * e[0].abs(),
                _ => half * e[1].abs() + half * e[0].abs(),
            };
            if p.min(q) > radius || p.max(q) < -radius {
                return false;
            }
        }
    }
    for j in 0..3 {
        if v[0][j].min(v[1][j]).min(v[2][j]) > half || v[0][j].max(v[1][j]).max(v[2][j]) < -half {
            return false;
        }
    }
    let normal = cross(edges[0], edges[1]);
    let near = std::array::from_fn(|j| {
        if normal[j] > 0.0 {
            -half - v[0][j]
        } else {
            half - v[0][j]
        }
    });
    let far = std::array::from_fn(|j| {
        if normal[j] > 0.0 {
            half - v[0][j]
        } else {
            -half - v[0][j]
        }
    });
    dot(normal, near) <= 0.0 && dot(normal, far) >= 0.0
}

/// Shared DDA traversal; ties select Z, then Y, then X as in the native engine.
pub struct Walk {
    pub cell: [i32; 3],
    end: [i32; 3],
    start: [i32; 3],
    step: [i32; 3],
    next: Point,
    delta: Point,
}
impl Walk {
    pub fn new(origin: Point, end: Point, direction: Point) -> Self {
        let cell = origin.map(|x| x.floor() as i32);
        let step = direction.map(|d| {
            if d > 0.0 {
                1
            } else if d < 0.0 {
                -1
            } else {
                0
            }
        });
        let delta = direction.map(|d| if d == 0.0 { f32::MAX } else { 1.0 / d.abs() });
        let next = std::array::from_fn(|i| {
            if direction[i] > 0.0 {
                (1.0 - (origin[i] - cell[i] as f32)) / direction[i]
            } else if direction[i] < 0.0 {
                -(origin[i] - cell[i] as f32) / direction[i]
            } else {
                f32::MAX
            }
        });
        Self {
            cell,
            end: end.map(|x| x.floor() as i32),
            start: cell,
            step,
            next,
            delta,
        }
    }
    pub fn finished(&self) -> bool {
        self.cell == self.end
            || (0..3)
                .map(|i| (self.cell[i] as i64 - self.start[i] as i64).pow(2))
                .sum::<i64>()
                > (0..3)
                    .map(|i| (self.end[i] as i64 - self.start[i] as i64).pow(2))
                    .sum::<i64>()
    }
    pub fn advance(&mut self) {
        let axis = if self.next[0] < self.next[1] {
            if self.next[2] <= self.next[0] {
                2
            } else {
                0
            }
        } else if self.next[2] <= self.next[1] {
            2
        } else {
            1
        };
        self.cell[axis] += self.step[axis];
        self.next[axis] += self.delta[axis];
    }
}

/// Native mt19937 starts with the standard seed 5489. The supplied binary shares
/// this state across workers without a lock. Rust owns it and avoids that race.
pub struct Mt19937 {
    state: [u32; 624],
    index: usize,
}
impl Mt19937 {
    pub fn new() -> Self {
        let mut state = [0u32; 624];
        state[0] = 5489;
        for i in 1..624 {
            state[i] = 1812433253u32
                .wrapping_mul(state[i - 1] ^ (state[i - 1] >> 30))
                .wrapping_add(i as u32);
        }
        Self { state, index: 624 }
    }
    pub fn unit(&mut self) -> f32 {
        if self.index == 624 {
            for i in 0..624 {
                let x = (self.state[i] & 0x80000000) | (self.state[(i + 1) % 624] & 0x7fffffff);
                self.state[i] = self.state[(i + 397) % 624]
                    ^ (x >> 1)
                    ^ if x & 1 != 0 { 0x9908b0df } else { 0 };
            }
            self.index = 0;
        }
        let mut y = self.state[self.index];
        self.index += 1;
        y ^= y >> 11;
        y ^= (y << 7) & 0x9d2c5680;
        y ^= (y << 15) & 0xefc60000;
        y ^= y >> 18;
        let x = y as f32 * 2.3283064365386963e-10f32;
        x.min(f32::from_bits(1.0f32.to_bits() - 1))
    }
}
