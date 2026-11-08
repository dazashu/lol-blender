#![forbid(unsafe_code)]
/*!
rs_math is the shared primitive layer the format crates build on: it re-exports the vector,
quaternion, and matrix types from glam under the names the on-disk formats use, and adds the
small value types those formats need but glam does not provide, namely the byte and float color
structs and the axis-aligned box and bounding sphere. It holds no parsing logic and depends on
nothing else in the workspace, so every other crate can rely on these types agreeing.
*/

pub use glam::{Mat4, Quat, Vec2, Vec3, Vec4};

/// 4x4 transform matrix, matching the `.bin` `MTX44` value type.
pub type Mtx44 = Mat4;

mod bounds;
mod color;

pub use bounds::{Aabb, Sphere};
pub use color::{Color, Rgba};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rgba_array_round_trip() {
        let c = Rgba::new(10, 20, 30, 40);
        assert_eq!(c.to_array(), [10, 20, 30, 40]);
        assert_eq!(Rgba::from_array(c.to_array()), c);
    }

    #[test]
    fn aabb_center_and_contains() {
        let b = Aabb::new(Vec3::new(-1.0, -1.0, -1.0), Vec3::new(1.0, 3.0, 1.0));
        assert_eq!(b.center(), Vec3::new(0.0, 1.0, 0.0));
        assert_eq!(b.size(), Vec3::new(2.0, 4.0, 2.0));
        assert!(b.contains(Vec3::new(0.0, 2.0, 0.0)));
        assert!(b.contains(b.min));
        assert!(b.contains(b.max));
        assert!(!b.contains(Vec3::new(0.0, 4.0, 0.0)));
    }
}
