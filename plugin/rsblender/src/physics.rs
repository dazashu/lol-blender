//! The Wiggle 2 solver (Steve Miller, GPL-3.0), ported line for line from the configuration the
//! old Daza Physics addon used: chained tail particles, no head simulation, wind or pins.
//!
//! Topology and settings are uploaded once; each frame takes the animated world matrices and
//! returns the local deltas. Collider spheres are solved analytically (the original used
//! icospheres); push-out, sticky, friction and bounce are the original's arithmetic.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use glam::{DMat4, DQuat, DVec3};

use crate::mat;

/// Blender's `Vector.to_track_quat('Y', 'Z')`, in f32 like `mathutils`, so the result matches
/// it exactly, including along the singular direction.
pub fn track_quat_y_z(v: DVec3) -> DQuat {
    let vx = v.x as f32;
    let vy = v.y as f32;
    let vz = v.z as f32;
    let len = (vx * vx + vy * vy + vz * vz).sqrt();
    if len == 0.0 {
        return DQuat::IDENTITY;
    }

    let (tx, ty, tz) = (-vx, -vy, -vz);
    let mut nor = if tx.abs() + tz.abs() < 1e-4 {
        [1.0f32, 0.0, 0.0]
    } else {
        [-tz, 0.0, tx]
    };
    let n = (nor[0] * nor[0] + nor[1] * nor[1] + nor[2] * nor[2]).sqrt();
    nor = [nor[0] / n, nor[1] / n, nor[2] / n];

    let co = (ty / len).clamp(-1.0, 1.0);
    let angle = std::f32::consts::PI - co.acos();
    let half = angle * 0.5;
    let s = half.sin();
    let (qw, qx, qy, qz) = (half.cos(), nor[0] * s, nor[1] * s, nor[2] * s);

    // the Z axis of that rotation, computed like Blender's `quat_to_mat3` (f64, pre-scaled by
    // sqrt 2, rounded to f32): this decides the roll along the singular direction
    let s2 = std::f64::consts::SQRT_2;
    let (d0, d1, d2, d3) = (
        s2 * qw as f64,
        s2 * qx as f64,
        s2 * qy as f64,
        s2 * qz as f64,
    );
    let fpx = (d0 * d2 + d1 * d3) as f32;
    let fpz = (1.0 - d1 * d1 - d2 * d2) as f32;
    let a2 = -0.5 * fpx.atan2(fpz);
    let si = a2.sin() / len;
    let (rw, rx, ry, rz) = (a2.cos(), tx * si, ty * si, tz * si);

    // q2 * q, in Blender's (w, x, y, z) order.
    DQuat::from_xyzw(
        (rw * qx + rx * qw + ry * qz - rz * qy) as f64,
        (rw * qy + ry * qw + rz * qx - rx * qz) as f64,
        (rw * qz + rz * qw + rx * qy - ry * qx) as f64,
        (rw * qw - rx * qx - ry * qy - rz * qz) as f64,
    )
}

/// `mathutils`' `Vector.rotation_difference`: the shortest arc taking `a` to `b`.
fn rotation_difference(a: DVec3, b: DVec3) -> DQuat {
    let a = a.normalize_or_zero();
    let b = b.normalize_or_zero();
    if a.length_squared() < 0.5 || b.length_squared() < 0.5 {
        return DQuat::IDENTITY;
    }
    let axis = a.cross(b);
    let angle = a.dot(b).clamp(-1.0, 1.0).acos();
    if axis.length_squared() < 1e-24 {
        if angle < 1.0 {
            return DQuat::IDENTITY;
        }
        // antiparallel: any perpendicular axis
        let perp = if a.x.abs() < 0.9 { DVec3::X } else { DVec3::Y };
        return DQuat::from_axis_angle(a.cross(perp).normalize(), std::f64::consts::PI);
    }
    DQuat::from_axis_angle(axis.normalize(), angle)
}

/// `mathutils`' `Vector.reflect`: `2*(v.n)n - v` (the negated usual formula), as ported.
fn reflect(v: DVec3, normal: DVec3) -> DVec3 {
    let n = normal.normalize_or_zero();
    n * (2.0 * v.dot(n)) - v
}

fn project(v: DVec3, onto: DVec3) -> DVec3 {
    let d = onto.dot(onto);
    if d < 1e-24 {
        DVec3::ZERO
    } else {
        onto * (v.dot(onto) / d)
    }
}

fn is_finite(v: DVec3) -> bool {
    v.x.is_finite() && v.y.is_finite() && v.z.is_finite()
}

/// `Matrix.LocRotScale(m.loc, m.rot, scale)`: replace a matrix's scale.
fn with_scale(m: &DMat4, scale: DVec3) -> DMat4 {
    let (_, rotation, translation) = m.to_scale_rotation_translation();
    DMat4::from_scale_rotation_translation(scale, rotation, translation)
}

/// Slerp a rotation toward identity until within `max_radians` (the old addon's
/// `clamp_local_rotation_from_identity`).
fn clamp_from_identity(q: DQuat, max_radians: f64) -> DQuat {
    if q.length_squared() < 1e-16 {
        return DQuat::IDENTITY;
    }
    let q = q.normalize();
    let angle = 2.0 * q.w.abs().min(1.0).acos();
    if angle <= max_radians {
        return q;
    }
    let q = if q.w < 0.0 { -q } else { q };
    DQuat::IDENTITY.slerp(q, max_radians / angle)
}

#[derive(Clone, Copy, Default)]
struct Bone {
    /// The bone this one pulls on. -1 for a root or a detached bone.
    parent: i32,
    /// The bone whose simulated matrix places this bone's head. Same as `parent`, except a
    /// detached bone still follows the bone above it without pulling on it.
    follow: i32,
    /// True when the parent is the direct Blender parent and the head sits on its tail: Wiggle
    /// then translates the parent's particle instead of swinging it (the stable path).
    direct_connected: bool,
    /// The rest length, in armature space.
    rest_length: f64,
    stiff: f64,
    damp: f64,
    gravity: f64,
    mass: f64,
    stretch: f64,
    chain: bool,
    radius: f64,
    friction: f64,
    bounce: f64,
    sticky: f64,
}

/// A rounded box collider. A sphere is the same box with zero half-extents. The matrix is rigid,
/// so `half` and `round` are in world units.
#[derive(Clone)]
struct Collider {
    matrix: DMat4,
    inverse: DMat4,
    /// The inverse from the previous frame, for `sweep`.
    previous: DMat4,
    /// Half the box, in the collider's own space. Zero for a sphere.
    half: DVec3,
    /// Corner rounding (the whole radius when `half` is zero). `half` is shrunk by it, so the
    /// outer size is the one asked for.
    round: f64,
    /// Largest centre-to-surface distance, for quick rejects.
    reach: f64,
    /// The centre in world space, for the broad phase.
    centre: DVec3,
}

impl Collider {
    /// A collider from a rigid matrix, the full half-extents and a rounding radius, or `None`
    /// when the matrix has no inverse. Used for both per-frame and rest-pose colliders.
    fn build(m: DMat4, full_half: DVec3, radius: f64) -> Option<Collider> {
        if !mat::is_usable(&m) {
            return None;
        }
        let round = radius.max(0.0);
        // the rounding grows the shape, so take it off the half-extents
        let half = (full_half - DVec3::splat(round)).max(DVec3::ZERO);
        let inverse = m.inverse();
        Some(Collider {
            previous: inverse,
            inverse,
            half,
            round,
            reach: half.length() + round,
            centre: m.w_axis.truncate(),
            matrix: m,
        })
    }

    /// Signed distance from a point in THIS collider's space to its surface. Negative inside.
    fn distance(&self, local: DVec3) -> f64 {
        let q = local.abs() - self.half;
        let outside = q.max(DVec3::ZERO).length();
        let inside = q.x.max(q.y).max(q.z).min(0.0);
        outside + inside - self.round
    }

    /// The closest point on the surface, and the outward normal there.
    fn closest(&self, local: DVec3) -> (DVec3, DVec3) {
        let clamped = local.clamp(-self.half, self.half);
        let delta = local - clamped;
        let length = delta.length();
        if length > 1e-12 {
            let normal = delta / length;
            return (clamped + normal * self.round, normal);
        }
        // inside the inner box: leave by the nearest face
        let gap = self.half - local.abs();
        let (axis, room) = if gap.x <= gap.y && gap.x <= gap.z {
            (DVec3::X, gap.x)
        } else if gap.y <= gap.z {
            (DVec3::Y, gap.y)
        } else {
            (DVec3::Z, gap.z)
        };
        let sign = if axis.dot(local) < 0.0 { -1.0 } else { 1.0 };
        let normal = axis * sign;
        (local + normal * (room + self.round), normal)
    }
}

/// A simulated bone's own body shape, when one is placed. Without it the bone collides as a
/// point, like the old addon.
#[derive(Clone, Copy, Default)]
struct Body {
    present: bool,
    /// The centre in bone space (head at the origin, +Y along the bone).
    centre: DVec3,
    /// Half the box, already shrunk by `round`. Zero for a sphere.
    half: DVec3,
    round: f64,
    /// Largest distance from the centre to the surface. The broad phase rejects on it.
    reach: f64,
    /// The shape's rotation in bone space.
    turn: DQuat,
}

impl Body {
    /// How far the shape reaches from its centre along a unit direction (same for `-d`).
    fn support(&self, rot: DQuat, d: DVec3) -> f64 {
        let l = (rot * self.turn).inverse() * d;
        self.half.x * l.x.abs() + self.half.y * l.y.abs() + self.half.z * l.z.abs() + self.round
    }
}

#[pyclass]
pub struct Wiggle {
    bones: Vec<Bone>,
    colliders: Vec<Collider>,
    /// The submitted index of each kept collider (singular ones are dropped), and the count.
    collider_slots: Vec<usize>,
    collider_submitted: usize,

    /// Simulated tail point, world space.
    position: Vec<DVec3>,
    position_last: Vec<DVec3>,
    /// One per bone. `present` is false unless the user placed a shape on it.
    bodies: Vec<Body>,
    /// How deep bone `i`'s shape already overlaps collider `j` at the start pose, at
    /// `i * collider_count + j`. Subtracted, so neighbours only collide when they close further.
    allowance: Vec<f64>,
    velocity: Vec<DVec3>,
    /// The simulated world matrix (Wiggle's `bone.wiggle.matrix`).
    matrix: Vec<DMat4>,
    collision_normal: Vec<DVec3>,
    /// Contact point in the collider's own space, and which collider it belongs to.
    collision_point: Vec<DVec3>,
    collision_ob: Vec<i32>,

    /// Movement limit in radians, 0 = off. Not Wiggle 2: the old addon's bake cap, applied live.
    max_angle: f64,



    /// Per frame: animated world matrix, scale and posed length of each bone.
    anim: Vec<DMat4>,
    anim_scale: Vec<DVec3>,
    world_length: Vec<f64>,
}

impl Wiggle {
    fn count(&self) -> usize {
        self.bones.len()
    }

    /// `reset_bone`: the particle back on the animated tail, at rest.
    fn reset_bone(&mut self, i: usize) {
        let tail = self.anim[i].transform_point3(DVec3::new(0.0, self.bones[i].rest_length, 0.0));
        self.position[i] = tail;
        self.position_last[i] = tail;
        self.velocity[i] = DVec3::ZERO;
        self.matrix[i] = self.anim[i];
        self.collision_normal[i] = DVec3::ZERO;
        self.collision_ob[i] = -1;
    }

    /// The bone's matrix as its simulated parent places it (`relative_matrix` in the original).
    fn followed(&self, i: usize) -> DMat4 {
        let p = self.bones[i].follow;
        if p < 0 {
            self.anim[i]
        } else {
            let p = p as usize;
            self.matrix[p] * (self.anim[p].inverse() * self.anim[i])
        }
    }

    /// The entry point of a step that passes straight through a collider (both ends outside),
    /// so fast strands do not tunnel. `None` otherwise, leaving the normal path untouched.
    fn sweep(from: DVec3, to: DVec3, collider: &Collider, skin: f64) -> Option<DVec3> {
        // in collider space, the start through last frame's inverse: motion relative to the
        // collider, so a character travelling with its colliders does not count as tunnelling
        let a = collider.previous.transform_point3(from);
        let b = collider.inverse.transform_point3(to);

        let d = b - a;
        let travel = d.length();
        if travel < 1e-9 {
            return None;
        }
        // an end inside is a normal contact, handled by the static test
        if collider.distance(a) <= skin || collider.distance(b) <= skin {
            return None;
        }
        // quick reject on the closest approach to the centre
        let t_near = (-a.dot(d) / (travel * travel)).clamp(0.0, 1.0);
        let limit = collider.reach + skin;
        if (a + d * t_near).length_squared() > limit * limit {
            return None;
        }

        // find the first inside sample, then bisect
        let steps = 16;
        let mut lo = 0.0f64;
        let mut hi = -1.0f64;
        for step in 1..=steps {
            let t = step as f64 / steps as f64;
            if collider.distance(a + d * t) <= skin {
                hi = t;
                break;
            }
            lo = t;
        }
        if hi < 0.0 {
            return None;
        }
        for _ in 0..12 {
            let mid = 0.5 * (lo + hi);
            if collider.distance(a + d * mid) <= skin {
                hi = mid;
            } else {
                lo = mid;
            }
        }
        Some(collider.matrix.transform_point3(a + d * hi))
    }

    /// The placed shape's centre and rotation. Head plus rotation only: using the stretch scale
    /// would feed back into a deeper contact and more stretch.
    fn body_at(&self, i: usize) -> (DVec3, DQuat) {
        let m = self.matrix[i];
        let (_scale, rotation, translation) = m.to_scale_rotation_translation();
        (translation + rotation * self.bodies[i].centre, rotation)
    }

    /// How deep a placed shape reaches into a collider, and the collider's normal there, before
    /// the rest allowance. `None` when too far apart. The contact, the allowance and the audit all
    /// use this one function.
    fn shape_overlap(body: &Body, centre: DVec3, rotation: DQuat, collider: &Collider)
        -> Option<(f64, DVec3)> {
        let limit = collider.reach + body.reach;
        if (centre - collider.centre).length_squared() > limit * limit {
            return None;
        }
        let local = collider.inverse.transform_point3(centre);
        let (_surface, local_normal) = collider.closest(local);
        let normal = collider.matrix.transform_vector3(local_normal).normalize_or_zero();
        if normal.length_squared() < 0.5 {
            return None;
        }
        Some((body.support(rotation, normal) - collider.distance(local), normal))
    }

    /// Resolve bone `i`'s placed shape against every collider, by turning the bone about its head
    /// only (its length is written as a scale, so a push must not lengthen it).
    fn collide_body(&mut self, i: usize) {
        let body = self.bodies[i];
        let origin = self.matrix[i].w_axis.truncate();
        let radius = (self.position[i] - origin).length();
        let mut hit = DVec3::ZERO;

        // tracked as it is pushed, so two colliders do not both resolve the same overlap
        let (mut centre, mut rotation) = self.body_at(i);

        for index in 0..self.colliders.len() {
            let Some((mut overlap, normal)) =
                Self::shape_overlap(&body, centre, rotation, &self.colliders[index])
            else {
                continue;
            };
            if !self.allowance.is_empty() {
                // indexed by submitted slot (singular colliders were dropped)
                let at = i * self.collider_submitted + self.collider_slots[index];
                if let Some(rest) = self.allowance.get(at) {
                    overlap -= *rest;
                }
            }
            if overlap <= 0.0 {
                continue;
            }

            // The exact turn about the head that clears the overlap. In the plane of the lever
            // r (head to shape centre) and the normal: cos(t - q) = a + overlap/|r|, with a, b the
            // normal's parts along and across r and q = atan2(b, a).
            let lever = centre - origin;
            let lever_length = lever.length();
            if lever_length < 1e-9 {
                // centred on the head: no turn moves it
                continue;
            }
            let e1 = lever / lever_length;
            let along = normal.dot(e1);
            let across = normal - e1 * along;
            let b = across.length();
            if b < 1e-9 {
                // normal along the lever: no turn moves it out
                continue;
            }
            let e2 = across / b;
            let q = b.atan2(along);
            let wanted = along + overlap / lever_length;
            if wanted >= 1.0 {
                // no turn can clear it (the pivot is inside the collider): leave it
                continue;
            }
            let angle = (q - wanted.acos()).max(0.0);
            if angle <= 0.0 {
                continue;
            }
            let turn = DQuat::from_axis_angle(e1.cross(e2).normalize(), angle);
            self.position[i] = origin + turn * (self.position[i] - origin);
            centre = origin + turn * lever;
            rotation = turn * rotation;
            hit = normal;
        }

        if hit.length_squared() > 0.0 {
            // absorb rounding from several rotations
            self.position[i] = self.keep_length(i, radius, origin);
        }
        self.collision_normal[i] = hit;
        self.collision_ob[i] = -1;
    }

    fn collide(&mut self, i: usize) {
        if self.bodies[i].present {
            // placed shapes are resolved once after the constraints, see `step`
            return;
        }
        self.collide_inner(i, false)
    }

    /// Exact segment/sphere contact. The capsule radius is the particle radius;
    /// the pivot stays on the parent and the bone's current length is preserved.
    /// A head inside the expanded sphere is unsatisfiable by rotation alone.
    fn collide_segment(&mut self, i: usize) {
        if self.bodies[i].present { return; }
        let head = self.matrix[i].w_axis.truncate();
        let length = (self.position[i] - head).length();
        if length == 0.0 { return; }
        for collider in &self.colliders {
            if collider.half != DVec3::ZERO { continue; }
            let toward = collider.centre - head;
            let distance = toward.length();
            let radius = collider.round + self.bones[i].radius;
            if distance <= radius || length <= distance - radius { continue; }
            let axis = toward / distance;
            let direction = (self.position[i] - head) / length;
            let projection = toward.dot(direction).clamp(0.0, length);
            if (head + direction * projection - collider.centre).length_squared() >= radius * radius { continue; }
            let tangent = (distance * distance - radius * radius).sqrt();
            let boundary = if length >= tangent {
                (radius / distance).asin()
            } else {
                ((distance * distance + length * length - radius * radius) / (2.0 * distance * length)).clamp(-1.0, 1.0).acos()
            };
            let mut across = direction - axis * direction.dot(axis);
            if across.length_squared() == 0.0 {
                // collinear: pick the least aligned axis, deterministically
                let a = axis.abs();
                let basis = if a.x <= a.y && a.x <= a.z { DVec3::X } else if a.y <= a.z { DVec3::Y } else { DVec3::Z };
                across = basis - axis * basis.dot(axis);
            }
            across = across.normalize();
            let out = axis * boundary.cos() + across * boundary.sin();
            self.position[i] = head + out * length;
            let contact = head + out * toward.dot(out).clamp(0.0, length);
            self.collision_normal[i] = (contact - collider.centre).normalize_or_zero();
            self.collision_ob[i] = -1;
        }
    }

    /// `swept` asks for the tunnel test as well. See `step`.
    fn collide_inner(&mut self, i: usize, swept: bool) {
        if self.colliders.is_empty() {
            self.collision_ob[i] = -1;
            return;
        }
        let bone = self.bones[i];
        let mut pos = self.position[i];

        // pull the particle back to where it entered anything it jumped through
        if swept {
            let from = self.position_last[i];
            for collider in &self.colliders {
                if let Some(entry) = Self::sweep(from, pos, collider, bone.radius) {
                    pos = entry;
                }
            }
        }

        let mut cp = self.collision_point[i];
        let mut co = self.collision_ob[i];
        let mut cn = self.collision_normal[i];
        let mut hit = false;

        for index in 0..self.colliders.len() {
            let collider = &self.colliders[index];

            // broad phase: nothing below can fire beyond `reach`
            let limit = collider.reach + bone.radius + bone.sticky;
            if (pos - collider.centre).length_squared() > limit * limit {
                continue;
            }

            let local = collider.inverse.transform_point3(pos);
            let (local_surface, local_normal) = collider.closest(local);
            let n = collider.matrix.transform_vector3(local_normal).normalize_or_zero();
            let surface = collider.matrix.transform_point3(local_surface);
            let v = surface - pos;
            let vn = v.normalize_or_zero();
            let length = v.length();

            let inside = n.dot(vn) > 0.01;
            let near = length < bone.radius;
            let sticking = co >= 0 && length < bone.radius + bone.sticky;
            if !(inside || near || sticking) {
                continue;
            }

            let nv = if n.dot(vn) > 0.0 { vn } else { -vn };
            pos = surface + nv * bone.radius;
            if co >= 0 {
                let remembered = self.colliders[co as usize].matrix.transform_point3(cp);
                pos = pos.lerp(remembered, bone.friction);
            }
            hit = true;
            co = index as i32;
            cp = self.colliders[index].inverse.transform_point3(pos);
            cn = nv;
        }
        if !hit {
            co = -1;
        }

        self.position[i] = pos;
        self.collision_point[i] = cp;
        self.collision_ob[i] = co;
        self.collision_normal[i] = cn;
    }

    /// `update_matrix`: stores the simulated world matrix and returns the local delta
    /// (`rot @ scale`, the `matrix_basis` to write).
    fn update_matrix(&mut self, i: usize, last: bool) -> DMat4 {
        let m2 = self.followed(i);
        let vec = m2.inverse().transform_point3(self.position[i]);
        let rotation = track_quat_y_z(vec);

        let l0 = self.bones[i].rest_length;
        let sy = if l0 > 1e-8 { vec.length() / l0 } else { 1.0 };

        let scale = DMat4::from_scale(DVec3::new(1.0, sy, 1.0));
        let delta = DMat4::from_quat(rotation) * scale;

        // the stored matrix is never bounded; the bounds below only shape the written key
        self.matrix[i] = m2 * delta;

        if !last {
            return delta;
        }

        // The written key only. A late contact can lengthen the bone, and the length is written
        // as a scale (it would inflate the mesh), so it is capped at the animated length times
        // (1 + stretch). Shorter is kept.
        let allowed = if l0 > 1e-8 {
            (self.world_length[i] / l0) * (1.0 + self.bones[i].stretch.max(0.0))
        } else {
            f64::INFINITY
        };
        let written = DMat4::from_scale(DVec3::new(1.0, sy.min(allowed), 1.0));

        let turn = if self.max_angle > 0.0 {
            clamp_from_identity(rotation, self.max_angle)
        } else {
            rotation
        };
        DMat4::from_quat(turn) * written
    }

    /// Put the particle back at distance `was` from the head. Only for placed shapes.
    fn keep_length(&self, i: usize, was: f64, origin: DVec3) -> DVec3 {
        let out = self.position[i] - origin;
        let length = out.length();
        if was < 1e-9 || length < 1e-9 {
            return self.position[i];
        }
        origin + out * (was / length)
    }

    fn spring(&self, target: DVec3, position: DVec3, stiff: f64, dt: f64, iterations: f64) -> DVec3 {
        let s = target - position;
        let force = s * (stiff / iterations) * dt * dt;
        if force.length() > s.length() {
            s
        } else {
            force
        }
    }

    /// Swing the parent's particle around its head, so a pull bends the parent, not stretches it.
    fn drag_parent(&mut self, p: usize, pivot_from: DVec3, pivot_to: DVec3) {
        let origin = self.matrix[p].w_axis.truncate();
        let v1 = pivot_from - origin;
        let v2 = pivot_to - origin;
        let l1 = v1.length();
        if l1 < 1e-12 {
            return;
        }
        let sc = v2.length() / l1;
        let q = rotation_difference(v1, v2);
        let v3 = q * (self.position[p] - origin);
        self.position[p] = origin + v3 * sc;
    }

    fn constrain(&mut self, i: usize, iteration: usize, dt: f64, iterations: f64) {
        if dt == 0.0 {
            self.update_matrix(i, false);
            return;
        }
        let bone = self.bones[i];
        let coupled = bone.parent >= 0 && bone.chain;
        let p = bone.parent.max(0) as usize;
        let fac = if coupled {
            if iteration == 0 {
                self.bones[p].stretch
            } else if bone.mass == self.bones[p].mass {
                0.5
            } else {
                bone.mass / (bone.mass + self.bones[p].mass)
            }
        } else {
            0.0
        };

        let mut update_p = false;
        let mat = with_scale(&self.followed(i), self.anim_scale[i]);
        let origin = mat.w_axis.truncate();

        // spring toward the animated tail
        let target = mat.transform_point3(DVec3::new(0.0, bone.rest_length, 0.0));
        let s = self.spring(target, self.position[i], bone.stiff, dt, iterations);
        if coupled {
            if bone.direct_connected {
                self.position[p] -= s * fac;
            } else {
                let head = self.matrix[i].w_axis.truncate();
                let tail = self.matrix[i].transform_point3(DVec3::new(0.0, bone.rest_length, 0.0));
                self.drag_parent(p, (head + tail) * 0.5, (head + (tail - s * fac)) * 0.5);
            }
            self.position[i] += s * (1.0 - fac);
            update_p = true;
        } else {
            self.position[i] += s;
        }

        // stretch: hold the posed length from the head
        let direction = (self.position[i] - origin).normalize_or_zero();
        let target = origin + direction * self.world_length[i];
        let s = (target - self.position[i]) * (1.0 - bone.stretch);
        if coupled {
            if bone.direct_connected {
                self.position[p] -= s * fac;
            } else {
                let head = self.matrix[i].w_axis.truncate();
                self.drag_parent(p, head, head - s * fac);
            }
            self.position[i] += s * (1.0 - fac);
            update_p = true;
        } else {
            self.position[i] += s;
        }

        if update_p {
            self.collide(p);
            self.update_matrix(p, false);
        }
        self.collide(i);
        self.update_matrix(i, false);
    }

    fn load_anim(&mut self, anim_world: &[f64], anim_scale: Option<&[f64]>) -> PyResult<()> {
        let n = self.count();
        if anim_world.len() != n * 16 {
            return Err(PyValueError::new_err(format!(
                "expected {} matrix values, got {}",
                n * 16,
                anim_world.len()
            )));
        }
        for i in 0..n {
            let m = mat::from_row_major(&anim_world[i * 16..i * 16 + 16])
                .ok_or_else(|| PyValueError::new_err("bad animated matrix"))?;
            self.anim[i] = m;
            let tail = m.transform_point3(DVec3::new(0.0, self.bones[i].rest_length, 0.0));
            self.world_length[i] = (tail - m.w_axis.truncate()).length();
        }
        if let Some(scale) = anim_scale {
            if scale.len() != n * 3 {
                return Err(PyValueError::new_err(format!(
                    "expected {} scale values, got {}",
                    n * 3,
                    scale.len()
                )));
            }
            for i in 0..n {
                self.anim_scale[i] = DVec3::new(scale[i * 3], scale[i * 3 + 1], scale[i * 3 + 2]);
            }
        }
        Ok(())
    }
}

#[pymethods]
impl Wiggle {
    /// `parents[i]`: the bone `i` pulls on, or -1. `follows[i]`: the bone placing its head, or -1
    /// (defaults to `parents`). Both must come before `i`.
    #[new]
    #[pyo3(signature = (parents, direct_connected, rest_lengths, follows = None))]
    fn new(
        parents: Vec<i32>,
        direct_connected: Vec<bool>,
        rest_lengths: Vec<f64>,
        follows: Option<Vec<i32>>,
    ) -> PyResult<Self> {
        let n = parents.len();
        let follows = follows.unwrap_or_else(|| parents.clone());
        if direct_connected.len() != n || rest_lengths.len() != n || follows.len() != n {
            return Err(PyValueError::new_err(
                "parents, direct_connected, rest_lengths and follows must be the same length",
            ));
        }
        for (name, list) in [("parent", &parents), ("follow", &follows)] {
            for (i, &p) in list.iter().enumerate() {
                if p >= i as i32 {
                    return Err(PyValueError::new_err(format!(
                        "bone {i} has {name} {p}: it must be listed before the bone that names it"
                    )));
                }
            }
        }
        let bones = (0..n)
            .map(|i| Bone {
                parent: parents[i],
                follow: follows[i],
                direct_connected: direct_connected[i],
                rest_length: rest_lengths[i],
                mass: 1.0,
                chain: true,
                ..Default::default()
            })
            .collect();
        Ok(Self {
            bones,
            colliders: Vec::new(),
            collider_slots: Vec::new(),
            collider_submitted: 0,
            position: vec![DVec3::ZERO; n],
            position_last: vec![DVec3::ZERO; n],
            bodies: vec![Body::default(); n],
            allowance: Vec::new(),
            velocity: vec![DVec3::ZERO; n],
            matrix: vec![DMat4::IDENTITY; n],
            collision_normal: vec![DVec3::ZERO; n],
            collision_point: vec![DVec3::ZERO; n],
            collision_ob: vec![-1; n],
            max_angle: 0.0,
            anim: vec![DMat4::IDENTITY; n],
            anim_scale: vec![DVec3::ONE; n],
            world_length: vec![0.0; n],
        })
    }

    #[getter]
    fn bone_count(&self) -> usize {
        self.count()
    }

    #[getter]
    fn collider_count(&self) -> usize {
        self.colliders.len()
    }

    /// The Wiggle 2 and collision values, one per bone. Does not reset the simulation.
    #[allow(clippy::too_many_arguments)]
    fn set_params(
        &mut self,
        stiff: Vec<f64>,
        damp: Vec<f64>,
        gravity: Vec<f64>,
        mass: Vec<f64>,
        stretch: Vec<f64>,
        chain: Vec<bool>,
        radius: Vec<f64>,
        friction: Vec<f64>,
        bounce: Vec<f64>,
        sticky: Vec<f64>,
    ) -> PyResult<()> {
        let n = self.count();
        for (name, len) in [
            ("stiff", stiff.len()),
            ("damp", damp.len()),
            ("gravity", gravity.len()),
            ("mass", mass.len()),
            ("stretch", stretch.len()),
            ("chain", chain.len()),
            ("radius", radius.len()),
            ("friction", friction.len()),
            ("bounce", bounce.len()),
            ("sticky", sticky.len()),
        ] {
            if len != n {
                return Err(PyValueError::new_err(format!(
                    "{name} has {len} entries, expected {n}"
                )));
            }
        }
        for i in 0..n {
            let b = &mut self.bones[i];
            b.stiff = stiff[i];
            b.damp = damp[i];
            b.gravity = gravity[i];
            b.mass = mass[i].max(1e-6);
            b.stretch = stretch[i];
            b.chain = chain[i];
            b.radius = radius[i];
            b.friction = friction[i];
            b.bounce = bounce[i];
            b.sticky = sticky[i];
        }
        Ok(())
    }

    /// The placed shape of each bone in bone space; all zeros means none (a point).
    /// `centres`, `halves`: 3 values; `turns`: (w, x, y, z); `radii`: rounding (sphere radius).
    fn set_bodies(
        &mut self,
        centres: Vec<f64>,
        halves: Vec<f64>,
        radii: Vec<f64>,
        turns: Vec<f64>,
    ) -> PyResult<()> {
        let n = self.count();
        for (name, len, want) in [
            ("centres", centres.len(), n * 3),
            ("halves", halves.len(), n * 3),
            ("radii", radii.len(), n),
            ("turns", turns.len(), n * 4),
        ] {
            if len != want {
                return Err(PyValueError::new_err(format!(
                    "{name} has {len} entries, expected {want}"
                )));
            }
        }
        for i in 0..n {
            let round = radii[i].max(0.0);
            let full = DVec3::new(halves[i * 3], halves[i * 3 + 1], halves[i * 3 + 2]);
            let present = full.max_element() > 1e-9 || round > 1e-9;
            // rounding comes off the half-extents, as in `set_colliders`
            let half = (full - DVec3::splat(round)).max(DVec3::ZERO);
            let centre = DVec3::new(centres[i * 3], centres[i * 3 + 1], centres[i * 3 + 2]);
            let turn = DQuat::from_xyzw(
                turns[i * 4 + 1],
                turns[i * 4 + 2],
                turns[i * 4 + 3],
                turns[i * 4],
            );
            self.bodies[i] = Body {
                present,
                centre,
                half,
                round,
                reach: half.length() + round,
                turn: if turn.length_squared() > 1e-9 {
                    turn.normalize()
                } else {
                    DQuat::IDENTITY
                },
            };
        }
        Ok(())
    }

    /// Measure the rest allowances with `shape_overlap`. `rest_bones`: 16 row-major values per
    /// bone at the start pose; the colliders at that pose, in the same order as `set_colliders`.
    fn set_rest_allowances(
        &mut self,
        rest_bones: Vec<f64>,
        matrices: Vec<f64>,
        halves: Vec<f64>,
        radii: Vec<f64>,
    ) -> PyResult<()> {
        let n = self.count();
        let columns = radii.len();
        if rest_bones.len() != n * 16 {
            return Err(PyValueError::new_err(format!(
                "expected {} rest matrix values for {n} bones, got {}",
                n * 16,
                rest_bones.len()
            )));
        }
        if matrices.len() != columns * 16 || halves.len() != columns * 3 {
            return Err(PyValueError::new_err(
                "rest colliders need 16 matrix values, 3 half-extents and 1 radius each",
            ));
        }
        if columns != self.collider_submitted {
            return Err(PyValueError::new_err(format!(
                "{columns} rest colliders for {} submitted ones - they must be the same list",
                self.collider_submitted
            )));
        }
        let rest: Vec<Option<Collider>> = (0..columns)
            .map(|c| {
                // negative radius: a fallback sphere, no allowance
                if radii[c] < 0.0 {
                    return None;
                }
                let m = mat::from_row_major(&matrices[c * 16..c * 16 + 16])?;
                Collider::build(m, DVec3::new(halves[c * 3], halves[c * 3 + 1], halves[c * 3 + 2]),
                                radii[c])
            })
            .collect();

        let mut allowance = vec![0.0f64; n * columns];
        for i in 0..n {
            if !self.bodies[i].present {
                continue;
            }
            let Some(m) = mat::from_row_major(&rest_bones[i * 16..i * 16 + 16]) else {
                continue;
            };
            let (_scale, rotation, translation) = m.to_scale_rotation_translation();
            let body = self.bodies[i];
            let centre = translation + rotation * body.centre;
            for (slot, collider) in rest.iter().enumerate() {
                if let Some(collider) = collider {
                    if let Some((overlap, _)) = Self::shape_overlap(&body, centre, rotation, collider) {
                        allowance[i * columns + slot] = overlap.max(0.0);
                    }
                }
            }
        }
        self.allowance = allowance;
        Ok(())
    }

    /// Set the rest allowances directly: bone-major, `set_colliders` order. Empty clears them.
    fn set_body_allowances(&mut self, allowance: Vec<f64>) -> PyResult<()> {
        if allowance.is_empty() {
            self.allowance.clear();
            return Ok(());
        }
        // sized by the submitted count (the caller does not know which were dropped)
        let want = self.count() * self.collider_submitted;
        if allowance.len() != want {
            return Err(PyValueError::new_err(format!(
                "expected {want} allowances for {} bones and {} colliders, got {}",
                self.count(),
                self.collider_submitted,
                allowance.len()
            )));
        }
        self.allowance = allowance.into_iter().map(|v| v.max(0.0)).collect();
        Ok(())
    }

    /// Degrees a bone may turn away from the pose the animation put it in. Zero turns it off.
    fn set_max_angle(&mut self, degrees: f64) {
        self.max_angle = if degrees > 0.0 {
            degrees.to_radians()
        } else {
            0.0
        };
    }

    /// The colliders: rigid world matrix (16 row-major), half-extents (3), rounding radius (1).
    /// A sphere has zero half-extents.
    fn set_colliders(&mut self, matrices: Vec<f64>, halves: Vec<f64>, radii: Vec<f64>)
        -> PyResult<()> {
        if matrices.len() % 16 != 0 {
            return Err(PyValueError::new_err(
                "collider matrices must be a multiple of 16 values",
            ));
        }
        let count = matrices.len() / 16;
        if halves.len() != count * 3 || radii.len() != count {
            return Err(PyValueError::new_err(format!(
                "expected {} half-extents and {} radii for {} colliders, got {} and {}",
                count * 3,
                count,
                count,
                halves.len(),
                radii.len()
            )));
        }

        // keep last frame's inverses by submitted index (the same list every frame)
        let mut previous: Vec<Option<DMat4>> = vec![None; self.collider_submitted];
        for (at, collider) in self.colliders.iter().enumerate() {
            if let Some(slot) = self.collider_slots.get(at) {
                if let Some(entry) = previous.get_mut(*slot) {
                    *entry = Some(collider.inverse);
                }
            }
        }
        let reuse = self.collider_submitted == count;

        self.colliders.clear();
        self.collider_slots.clear();
        self.collider_submitted = count;
        for index in 0..count {
            let chunk = &matrices[index * 16..index * 16 + 16];
            let m = mat::from_row_major(chunk)
                .ok_or_else(|| PyValueError::new_err("bad collider matrix"))?;
            let full = DVec3::new(halves[index * 3], halves[index * 3 + 1], halves[index * 3 + 2]);
            // skip a singular collider (zero-scale bone)
            let Some(mut collider) = Collider::build(m, full, radii[index]) else {
                continue;
            };
            if reuse {
                if let Some(Some(remembered)) = previous.get(index) {
                    collider.previous = *remembered;
                }
            }
            self.collider_slots.push(index);
            self.colliders.push(collider);
        }
        Ok(())
    }

    /// Put every particle back on the animated pose and clear its velocity.
    fn reset(&mut self, anim_world: Vec<f64>) -> PyResult<()> {
        self.load_anim(&anim_world, None)?;
        for i in 0..self.count() {
            self.reset_bone(i);
        }
        Ok(())
    }

    /// One frame. Returns 16 values per bone: the local delta to write as `matrix_basis`.
    ///
    /// `frames_elapsed` divides the Verlet velocity, so skipping frames does not speed it up.
    #[allow(clippy::too_many_arguments)]
    fn step(
        &mut self,
        anim_world: Vec<f64>,
        anim_scale: Vec<f64>,
        dt: f64,
        frames_elapsed: f64,
        gravity: Vec<f64>,
        iterations: usize,
    ) -> PyResult<Vec<f64>> {
        if gravity.len() != 3 {
            return Err(PyValueError::new_err("gravity must be 3 values"));
        }
        self.load_anim(&anim_world, Some(&anim_scale))?;
        let gravity = DVec3::new(gravity[0], gravity[1], gravity[2]);
        let n = self.count();
        let iterations = iterations.max(1);
        let iter_f = iterations as f64;

        for i in 0..n {
            self.collision_normal[i] = DVec3::ZERO;
        }

        // move
        if dt != 0.0 {
            for i in 0..n {
                let damp = (1.0 - self.bones[i].damp * dt).clamp(0.0, 1.0);
                self.velocity[i] *= damp;
                let force = gravity * self.bones[i].gravity;
                self.position[i] += self.velocity[i] + force * dt * dt;
                if !is_finite(self.position[i]) {
                    self.reset_bone(i);
                    continue;
                }
                self.collide(i);
                self.update_matrix(i, false);
            }
        }

        // constrain
        for it in 0..iterations {
            for i in 0..n {
                self.constrain(i, iterations - 1 - it, dt, iter_f);
            }
        }

        // placed shapes: resolved once from the settled pose, parents first (inside the
        // iterations the contact and spring fight and the result jitters). Repeated until stable.
        if dt != 0.0 && !self.colliders.is_empty() {
            for i in 0..n {
                // the parent's contact moved this bone's frame
                self.update_matrix(i, false);
                if !self.bodies[i].present {
                    continue;
                }
                for _ in 0..iterations {
                    let before = self.position[i];
                    self.collide_body(i);
                    self.update_matrix(i, false);
                    if (self.position[i] - before).length_squared() < 1e-18 {
                        break;
                    }
                }
            }
        }

        // tunnel pass: one swept test per bone over the whole frame's movement
        if dt != 0.0 && !self.colliders.is_empty() {
            for i in 0..n {
                // not for placed shapes (the test uses the point radius)
                if self.bodies[i].present {
                    continue;
                }
                self.collide_inner(i, true);
                self.update_matrix(i, false);
            }
        }

        // final write
        let mut out = Vec::with_capacity(n * 16);
        for i in 0..n {
            if dt != 0.0 {
                // the parent's last collision may have moved the pivot
                self.update_matrix(i, false);
                self.collide_segment(i);
            }
            let delta = self.update_matrix(i, true);
            out.extend_from_slice(&mat::to_row_major(&delta));
        }

        if frames_elapsed != 0.0 {
            let divisor = frames_elapsed.max(1.0);
            for i in 0..n {
                let mut vb = DVec3::ZERO;
                let cn = self.collision_normal[i];
                if cn.length() > 0.0 {
                    vb = project(reflect(self.velocity[i], cn), cn) * self.bones[i].bounce;
                }
                self.velocity[i] = (self.position[i] - self.position_last[i]) / divisor + vb;
                self.position_last[i] = self.position[i];
            }
        }
        Ok(out)
    }

    /// Each particle's world position, three values per bone. For tests.
    fn particles(&self) -> Vec<f64> {
        let mut out = Vec::with_capacity(self.count() * 3);
        for p in &self.position {
            out.extend_from_slice(&[p.x, p.y, p.z]);
        }
        out
    }

    /// Each bone's head in world space, three values per bone. With `particles`, the segments.
    fn heads(&self) -> Vec<f64> {
        let mut out = Vec::with_capacity(self.count() * 3);
        for m in &self.matrix {
            let h = m.w_axis;
            out.extend_from_slice(&[h.x, h.y, h.z]);
        }
        out
    }

    /// Each bone's particle radius. For tests.
    fn radii(&self) -> Vec<f64> {
        self.bones.iter().map(|b| b.radius).collect()
    }

    /// Per bone, the current overlap of its placed shape beyond the rest allowance (<= 0 means
    /// resolved). `NaN` for a bone without a shape. For tests.
    fn body_overlaps(&self) -> Vec<f64> {
        (0..self.count())
            .map(|i| {
                if !self.bodies[i].present {
                    return f64::NAN;
                }
                let body = self.bodies[i];
                let (centre, rotation) = self.body_at(i);
                let mut worst = f64::NEG_INFINITY;
                for (index, collider) in self.colliders.iter().enumerate() {
                    let Some((mut overlap, _normal)) =
                        Self::shape_overlap(&body, centre, rotation, collider)
                    else {
                        continue;
                    };
                    if !self.allowance.is_empty() {
                        let at = i * self.collider_submitted + self.collider_slots[index];
                        if let Some(rest) = self.allowance.get(at) {
                            overlap -= *rest;
                        }
                    }
                    worst = worst.max(overlap);
                }
                if worst.is_finite() { worst } else { 0.0 }
            })
            .collect()
    }
}

/// `Vector.to_track_quat('Y', 'Z')`, for a test against `mathutils`.
#[pyfunction]
pub fn track_quat(vector: Vec<f64>) -> PyResult<Vec<f64>> {
    if vector.len() != 3 {
        return Err(PyValueError::new_err("expected 3 values"));
    }
    let q = track_quat_y_z(DVec3::new(vector[0], vector[1], vector[2]));
    Ok(vec![q.w, q.x, q.y, q.z])
}

/// `clamp_local_rotation_from_identity` on one (w, x, y, z) quaternion. For a test.
#[pyfunction]
pub fn clamp_rotation(quaternion: Vec<f64>, max_degrees: f64) -> PyResult<Vec<f64>> {
    if quaternion.len() != 4 {
        return Err(PyValueError::new_err("expected 4 values"));
    }
    let q = DQuat::from_xyzw(quaternion[1], quaternion[2], quaternion[3], quaternion[0]);
    let out = clamp_from_identity(q, max_degrees.to_radians());
    Ok(vec![out.w, out.x, out.y, out.z])
}
