use rs_math::{Aabb, Sphere, Vec2, Vec3, Vec4};

pub(crate) const MAGIC: u32 = 0x0011_2233;

/// Vertex layout variant of a [`SkinnedMesh`], stored in the file header for major version 4.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SkinnedMeshVertexType {
    Basic,
    Color,
    Tangent,
}

impl SkinnedMeshVertexType {
    pub(crate) fn from_u32(v: u32) -> Option<Self> {
        match v {
            0 => Some(Self::Basic),
            1 => Some(Self::Color),
            2 => Some(Self::Tangent),
            _ => None,
        }
    }

    pub(crate) fn to_u32(self) -> u32 {
        match self {
            Self::Basic => 0,
            Self::Color => 1,
            Self::Tangent => 2,
        }
    }

    /// Byte size of one vertex with this layout.
    pub fn vertex_size(self) -> u32 {
        match self {
            Self::Basic => 52,
            Self::Color => 56,
            Self::Tangent => 72,
        }
    }
}

/// A contiguous span of one material's geometry within the shared vertex and index buffers.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SkinnedMeshRange {
    pub name: String,
    pub vertex_start: u32,
    pub vertex_count: u32,
    pub index_start: u32,
    pub index_count: u32,
}

impl SkinnedMeshRange {
    pub fn new(
        name: impl Into<String>,
        vertex_start: u32,
        vertex_count: u32,
        index_start: u32,
        index_count: u32,
    ) -> Self {
        Self {
            name: name.into(),
            vertex_start,
            vertex_count,
            index_start,
            index_count,
        }
    }
}

/// A single skinned vertex. `color` is present for `Color`/`Tangent` layouts, `tangent` only for
/// the `Tangent` layout; both are `None` for the `Basic` layout so the original bytes round-trip.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct SkinnedMeshVertex {
    pub position: Vec3,
    pub blend_indices: [u8; 4],
    pub blend_weights: [f32; 4],
    pub normal: Vec3,
    pub uv: Vec2,
    pub color: Option<[u8; 4]>,
    pub tangent: Option<Vec4>,
}

impl SkinnedMeshVertex {
    pub fn new(
        position: Vec3,
        blend_indices: [u8; 4],
        blend_weights: [f32; 4],
        normal: Vec3,
        uv: Vec2,
    ) -> Self {
        Self {
            position,
            blend_indices,
            blend_weights,
            normal,
            uv,
            color: None,
            tangent: None,
        }
    }
}

/// A `.skn` skinned mesh: a shared vertex buffer and `u16` index buffer carved into per-material
/// [`SkinnedMeshRange`]s. The on-disk version components, flags, vertex type, and bounds are kept
/// verbatim so that `from_reader` followed by `to_writer` reproduces the input bytes exactly.
#[derive(Debug, Clone, PartialEq)]
pub struct SkinnedMesh {
    pub major: u16,
    pub minor: u16,
    pub flags: u32,
    pub vertex_type: SkinnedMeshVertexType,
    pub bounding_box: Aabb,
    pub bounding_sphere: Sphere,
    pub ranges: Vec<SkinnedMeshRange>,
    pub indices: Vec<u16>,
    pub vertices: Vec<SkinnedMeshVertex>,
    /// Opaque bytes that follow the vertex buffer. Real major-4 files written by the game append a
    /// 12-byte zero "end tab" here; keeping the raw bytes lets `from_reader` -> `to_writer` stay
    /// byte-exact regardless of the (unspecified) meaning of that tail.
    pub trailing: Vec<u8>,
    /// Optional v4 flag-1 block, before the triangle buffer.
    pub skinning_block: Vec<u8>,
}

impl SkinnedMesh {
    pub fn ranges(&self) -> &[SkinnedMeshRange] {
        &self.ranges
    }

    pub fn indices(&self) -> &[u16] {
        &self.indices
    }

    pub fn vertices(&self) -> &[SkinnedMeshVertex] {
        &self.vertices
    }

    pub fn version(&self) -> (u16, u16) {
        (self.major, self.minor)
    }
}
