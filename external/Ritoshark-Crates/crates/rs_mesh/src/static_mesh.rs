use rs_math::{Aabb, Vec2, Vec3};

pub(crate) const SCB_MAGIC: &[u8; 8] = b"r3d2Mesh";
pub(crate) const SCO_MAGIC: &str = "[ObjectBegin]";

/// A single triangle of a [`StaticMesh`], carrying its own material and per-corner UVs.
#[derive(Debug, Clone, PartialEq)]
pub struct StaticMeshFace {
    pub material: String,
    pub indices: [u32; 3],
    pub uvs: [Vec2; 3],
}

impl StaticMeshFace {
    pub fn new(material: impl Into<String>, indices: [u32; 3], uvs: [Vec2; 3]) -> Self {
        Self {
            material: material.into(),
            indices,
            uvs,
        }
    }
}

/// A static (non-skinned) mesh shared by the binary `.scb` (`"r3d2Mesh"`) and text `.sco`
/// (`[ObjectBegin]`) formats: a position list plus per-face triangles with materials and UVs,
/// and optional per-vertex colors carried by `.scb` color layouts.
#[derive(Debug, Clone, PartialEq)]
pub struct StaticMesh {
    pub name: String,
    /// `(major, minor)` version of the binary container; `(0, 0)` for the text `.sco` form.
    pub version: (u16, u16),
    /// Raw `r3d2Mesh` flag bits (`bit0` = `HasVcp`, `bit1` = `HasLocalOriginLocatorAndPivot`).
    /// Zero for the text `.sco` form, which has no flag word.
    pub flags: u32,
    /// On-disk axis-aligned bounds (`.scb` only); `min == max == 0` for `.sco`.
    pub bounding_box: Aabb,
    /// Raw `vertexType` word for `.scb` 3.2 files; `None` for older `.scb` and for `.sco`.
    pub vertex_type: Option<u32>,
    pub central: Vec3,
    pub positions: Vec<Vec3>,
    pub colors: Option<Vec<[u8; 4]>>,
    pub faces: Vec<StaticMeshFace>,
    /// Opaque bytes that follow the face list in `.scb` files (the per-face VCP RGB block and the
    /// local-origin/pivot vectors carried when the corresponding flag bits are set). Captured raw
    /// so that `from_scb_reader` -> `to_scb_writer` is byte-exact even though the exact layout of
    /// this tail is not modelled. Always empty for `.sco`.
    pub trailing: Vec<u8>,
}

impl StaticMesh {
    pub fn name(&self) -> &str {
        &self.name
    }

    pub fn positions(&self) -> &[Vec3] {
        &self.positions
    }

    pub fn faces(&self) -> &[StaticMeshFace] {
        &self.faces
    }

    pub fn colors(&self) -> Option<&[[u8; 4]]> {
        self.colors.as_deref()
    }

    pub fn flags(&self) -> u32 {
        self.flags
    }
}
