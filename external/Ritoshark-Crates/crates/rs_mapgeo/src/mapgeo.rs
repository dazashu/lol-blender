use rs_math::{Aabb, Mat4, Vec2, Vec3};

/// Named vertex attribute, matching the OEGM `MAPGEOVertexElementName` enum.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u32)]
pub enum ElementName {
    Position = 0,
    BlendWeight = 1,
    Normal = 2,
    FogCoordinate = 3,
    PrimaryColor = 4,
    SecondaryColor = 5,
    BlendIndex = 6,
    Texcoord0 = 7,
    Texcoord1 = 8,
    Texcoord2 = 9,
    Texcoord3 = 10,
    Texcoord4 = 11,
    Texcoord5 = 12,
    Texcoord6 = 13,
    Texcoord7 = 14,
    Tangent = 15,
}

impl ElementName {
    pub fn from_u32(value: u32) -> Option<Self> {
        Some(match value {
            0 => Self::Position,
            1 => Self::BlendWeight,
            2 => Self::Normal,
            3 => Self::FogCoordinate,
            4 => Self::PrimaryColor,
            5 => Self::SecondaryColor,
            6 => Self::BlendIndex,
            7 => Self::Texcoord0,
            8 => Self::Texcoord1,
            9 => Self::Texcoord2,
            10 => Self::Texcoord3,
            11 => Self::Texcoord4,
            12 => Self::Texcoord5,
            13 => Self::Texcoord6,
            14 => Self::Texcoord7,
            15 => Self::Tangent,
            _ => return None,
        })
    }
}

/// On-disk byte layout of a single vertex attribute, matching `MAPGEOVertexElementFormat`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u32)]
pub enum ElementFormat {
    XFloat32 = 0,
    XyFloat32 = 1,
    XyzFloat32 = 2,
    XyzwFloat32 = 3,
    BgraPacked8888 = 4,
    ZyxwPacked8888 = 5,
    RgbaPacked8888 = 6,
    XyPacked1616 = 7,
    XyzPacked161616 = 8,
    XyzwPacked16161616 = 9,
}

impl ElementFormat {
    pub fn from_u32(value: u32) -> Option<Self> {
        Some(match value {
            0 => Self::XFloat32,
            1 => Self::XyFloat32,
            2 => Self::XyzFloat32,
            3 => Self::XyzwFloat32,
            4 => Self::BgraPacked8888,
            5 => Self::ZyxwPacked8888,
            6 => Self::RgbaPacked8888,
            7 => Self::XyPacked1616,
            8 => Self::XyzPacked161616,
            9 => Self::XyzwPacked16161616,
            _ => return None,
        })
    }

    /// Number of bytes this attribute occupies inside a packed vertex.
    pub fn byte_size(self) -> usize {
        match self {
            Self::XFloat32 => 4,
            Self::XyFloat32 => 8,
            Self::XyzFloat32 => 12,
            Self::XyzwFloat32 => 16,
            Self::BgraPacked8888 | Self::ZyxwPacked8888 | Self::RgbaPacked8888 => 4,
            Self::XyPacked1616 => 4,
            Self::XyzPacked161616 | Self::XyzwPacked16161616 => 8,
        }
    }
}

/// How a vertex buffer is meant to be uploaded, matching `MAPGEOVertexUsage`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u32)]
pub enum VertexUsage {
    Static = 0,
    Dynamic = 1,
    Stream = 2,
}

impl VertexUsage {
    pub fn from_u32(value: u32) -> Self {
        match value {
            1 => Self::Dynamic,
            2 => Self::Stream,
            _ => Self::Static,
        }
    }
}

/// One vertex attribute: its semantic name and packed byte layout.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct VertexElement {
    pub name: ElementName,
    pub format: ElementFormat,
}

/// A vertex layout: the usage hint plus the ordered list of attributes that make up one vertex.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VertexDescription {
    pub usage: VertexUsage,
    pub elements: Vec<VertexElement>,
}

impl VertexDescription {
    /// Total byte stride of one vertex described by this layout.
    pub fn vertex_size(&self) -> usize {
        self.elements.iter().map(|e| e.format.byte_size()).sum()
    }
}

/// A description plus the raw bytes of one vertex buffer; decode using [`VertexDescription`].
#[derive(Debug, Clone, PartialEq)]
pub struct VertexBuffer {
    pub layer: u8,
    pub data: Vec<u8>,
}

/// A raw `u16` index buffer with its visibility layer byte.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IndexBuffer {
    pub layer: u8,
    pub indices: Vec<u16>,
}

/// A draw range into a model's index buffer, with its inclusive vertex bounds.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Submesh {
    pub hash: u32,
    pub name: String,
    pub index_start: u32,
    pub index_count: u32,
    pub min_vertex: u32,
    pub max_vertex: u32,
}

/// A baked-lighting / texture-override channel: a texture path plus a scale and bias.
#[derive(Debug, Clone, PartialEq)]
pub struct AssetChannel {
    pub path: String,
    pub scale: Vec2,
    pub offset: Vec2,
}

impl AssetChannel {
    pub fn empty() -> Self {
        Self {
            path: String::new(),
            scale: Vec2::ZERO,
            offset: Vec2::ZERO,
        }
    }
}

/// A per-model texture path override bound to a sampler index.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TextureOverride {
    pub index: u32,
    pub path: String,
}

/// One placed environment model: its buffer references, transform, bounds, and lighting data.
#[derive(Debug, Clone, PartialEq)]
pub struct MapModel {
    /// Mesh name. Embedded in the file as a sized string for versions < 12; for later versions it
    /// is the generated `MapGeo_Instance_{id}` and is not stored on disk.
    pub name: String,
    pub vertex_count: u32,
    pub vertex_description_id: u32,
    pub vertex_buffer_ids: Vec<i32>,
    pub index_count: u32,
    pub index_buffer_id: i32,
    pub layer: u8,
    /// `UnknownVersion18Int`, present only on version 18; ignored on write for other versions.
    pub unknown_v18: u32,
    /// Scene-graph path hash (`VisibilityControllerPathHash`); present only on version >= 15.
    pub bucket_grid_hash: u32,
    pub submeshes: Vec<Submesh>,
    pub disable_backface_culling: bool,
    pub bounds: Aabb,
    pub transform: Mat4,
    pub quality: u8,
    pub layer_transition: u8,
    pub render_flags: u16,
    /// Separate per-mesh point light, present only on versions 5/6 when the file-level
    /// `separate_point_lights` flag is set. `None` otherwise.
    pub point_light: Option<Vec3>,
    /// The nine spherical-harmonics light-probe coefficients carried by versions < 9 in place of
    /// the stationary-light channel and baked-paint data. `None` for versions >= 9.
    pub spherical_harmonics: Option<[Vec3; 9]>,
    pub baked_light: AssetChannel,
    /// Stationary-light channel; present only on versions >= 9 (versions < 9 use
    /// [`Self::spherical_harmonics`] instead and carry only the baked-light channel).
    pub stationary_light: AssetChannel,
    pub texture_overrides: Vec<TextureOverride>,
    pub baked_paint_scale_offset: [f32; 4],
    /// The single baked-paint channel used by versions 12..=16 in place of the counted override
    /// list. `None` for versions that use [`Self::texture_overrides`] (>= 17).
    pub baked_paint: Option<AssetChannel>,
}

/// One bucket of a [`SceneGraph`] quad-tree leaf.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct GeometryBucket {
    pub max_stick_out_x: f32,
    pub max_stick_out_z: f32,
    pub start_index: u32,
    pub base_vertex: u32,
    pub inside_face_count: u16,
    pub sticking_out_face_count: u16,
}

/// The bucketed-geometry scene graph (`BucketedGeometry`) that trails the model list.
#[derive(Debug, Clone, PartialEq)]
pub struct SceneGraph {
    pub controller_hash: u32,
    /// An unknown leading `f32` present only on version 18; ignored for other versions.
    pub unknown_v18: f32,
    pub min_x: f32,
    pub min_z: f32,
    pub max_x: f32,
    pub max_z: f32,
    pub max_stick_out_x: f32,
    pub max_stick_out_z: f32,
    pub bucket_size_x: f32,
    pub bucket_size_z: f32,
    pub buckets_per_side: u16,
    pub is_disabled: bool,
    pub flags: u8,
    pub vertices: Vec<Vec3>,
    pub indices: Vec<u16>,
    pub buckets: Vec<GeometryBucket>,
    pub face_visibility_flags: Vec<u8>,
}

/// A planar reflector plane (`PlanarReflector`), present from version 13 onward.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct PlanarReflector {
    pub transform: Mat4,
    pub plane: Aabb,
    pub normal: Vec3,
}

/// The top level of a parsed `.mapgeo` (OEGM) file.
#[derive(Debug, Clone, PartialEq)]
pub struct MapGeometry {
    pub version: u32,
    /// File-level flag read only on versions < 7 (a leading `bool`). When set, versions 5/6 store
    /// a separate per-mesh point light. Always `false` for versions >= 7, where the byte is absent.
    pub separate_point_lights: bool,
    pub texture_overrides: Vec<TextureOverride>,
    pub vertex_descriptions: Vec<VertexDescription>,
    pub vertex_buffers: Vec<VertexBuffer>,
    pub index_buffers: Vec<IndexBuffer>,
    pub models: Vec<MapModel>,
    pub scene_graphs: Vec<SceneGraph>,
    pub planar_reflectors: Vec<PlanarReflector>,
}

impl MapGeometry {
    /// Returns the four-byte file magic for OEGM map geometry.
    pub const fn magic() -> &'static [u8; 4] {
        b"OEGM"
    }
}
