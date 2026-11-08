//! Writing `.skl`, `.skn`, `.anm` and static meshes: Blender's flat buffers into the crate structs.
//!
//! Joints come in as raw T/R/S, so an unedited joint is written back exactly (decomposing a matrix
//! is not exact).

use pyo3::exceptions::{PyIOError, PyValueError};
use pyo3::prelude::*;

use rs_anim::{
    AnimFrame, AnimTrack, Animation as RsAnimation, Joint as RsJoint, Skeleton as RsSkeleton,
};
use rs_io::Serialize;
use rs_math::{Aabb, Quat, Sphere, Vec2, Vec3, Vec4};
use rs_mesh::{
    SkinnedMesh as RsSkinnedMesh, SkinnedMeshRange, SkinnedMeshVertex, SkinnedMeshVertexType,
};

/// `(translation xyz, rotation xyzw, scale xyz)` as Python hands it over.
pub type Trs = (Vec<f64>, Vec<f64>, Vec<f64>);

fn vec3(values: &[f64], what: &str) -> PyResult<Vec3> {
    if values.len() != 3 {
        return Err(PyValueError::new_err(format!(
            "{what} needs 3 values, got {}",
            values.len()
        )));
    }
    Ok(Vec3::new(
        values[0] as f32,
        values[1] as f32,
        values[2] as f32,
    ))
}

fn quat(values: &[f64], what: &str) -> PyResult<Quat> {
    if values.len() != 4 {
        return Err(PyValueError::new_err(format!(
            "{what} needs 4 values (x, y, z, w), got {}",
            values.len()
        )));
    }
    // not normalised, to keep the file's exact values
    Ok(Quat::from_xyzw(
        values[0] as f32,
        values[1] as f32,
        values[2] as f32,
        values[3] as f32,
    ))
}

/// Build and write a `.skl`.
///
/// `joints` is one tuple per joint, in FILE order:
/// `(name, flags, id, parent_id, radius, hash, local_trs, inverse_bind_trs)`.
#[pyfunction]
#[pyo3(signature = (path, joints, influences, name="", asset="", flags=0))]
#[allow(clippy::type_complexity)]
pub fn write_skeleton(
    path: &str,
    joints: Vec<(String, u16, i16, i16, f32, u32, Trs, Trs)>,
    influences: Vec<u16>,
    name: &str,
    asset: &str,
    flags: u16,
) -> PyResult<usize> {
    let mut skeleton = RsSkeleton::new();
    skeleton.flags = flags;
    skeleton.name = name.to_string();
    skeleton.asset = asset.to_string();
    skeleton.influences = influences;

    for (index, (joint_name, joint_flags, id, parent_id, radius, hash, local, ibind)) in
        joints.into_iter().enumerate()
    {
        let what = format!("joint {index} ({joint_name})");
        skeleton.joints.push(RsJoint {
            name: joint_name,
            flags: joint_flags,
            id,
            parent_id,
            radius,
            hash,
            local_translation: vec3(&local.0, &format!("{what} local translation"))?,
            local_rotation: quat(&local.1, &format!("{what} local rotation"))?,
            local_scale: vec3(&local.2, &format!("{what} local scale"))?,
            inverse_bind_translation: vec3(&ibind.0, &format!("{what} inverse-bind translation"))?,
            inverse_bind_rotation: quat(&ibind.1, &format!("{what} inverse-bind rotation"))?,
            inverse_bind_scale: vec3(&ibind.2, &format!("{what} inverse-bind scale"))?,
        });
    }

    let bytes = skeleton
        .to_bytes()
        .map_err(|e| PyValueError::new_err(format!("{path}: {e}")))?;
    let written = bytes.len();
    std::fs::write(path, bytes).map_err(|e| PyIOError::new_err(format!("{path}: {e}")))?;
    Ok(written)
}

/// Build and write a `.skn`.
///
/// Large buffers arrive as `bytes`. Positions and normals are already in League space.
#[pyfunction]
#[pyo3(signature = (
    path, ranges, positions, normals, uvs, blend_indices, blend_weights, indices,
    colors=None, tangents=None, trailing=None, bounds=None, major=4, minor=1, flags=0,
    skinning_block=None
))]
#[allow(clippy::too_many_arguments)]
pub fn write_skinned_mesh(
    path: &str,
    ranges: Vec<(String, u32, u32, u32, u32)>,
    positions: &[u8],
    normals: &[u8],
    uvs: &[u8],
    blend_indices: &[u8],
    blend_weights: &[u8],
    indices: &[u8],
    colors: Option<&[u8]>,
    tangents: Option<&[u8]>,
    trailing: Option<&[u8]>,
    bounds: Option<(f32, f32, f32, f32, f32, f32, f32, f32, f32, f32)>,
    major: u16,
    minor: u16,
    flags: u32,
    skinning_block: Option<&[u8]>,
) -> PyResult<usize> {
    let count = positions.len() / 12;
    let expect = |name: &str, got: usize, want: usize| -> PyResult<()> {
        if got != want {
            return Err(PyValueError::new_err(format!(
                "{name}: {got} bytes for {count} vertices, expected {want}"
            )));
        }
        Ok(())
    };
    expect("positions", positions.len(), count * 12)?;
    expect("normals", normals.len(), count * 12)?;
    expect("uvs", uvs.len(), count * 8)?;
    expect("blend_indices", blend_indices.len(), count * 4)?;
    expect("blend_weights", blend_weights.len(), count * 16)?;
    if let Some(c) = colors {
        expect("colors", c.len(), count * 4)?;
    }
    if let Some(t) = tangents {
        expect("tangents", t.len(), count * 16)?;
    }
    if indices.len() % 6 != 0 {
        return Err(PyValueError::new_err(format!(
            "indices: {} bytes is not a whole number of u16 triangles",
            indices.len()
        )));
    }

    let f32_at =
        |buf: &[u8], i: usize| f32::from_le_bytes(buf[i * 4..i * 4 + 4].try_into().unwrap());

    let mut vertices = Vec::with_capacity(count);
    for v in 0..count {
        vertices.push(SkinnedMeshVertex {
            position: Vec3::new(
                f32_at(positions, v * 3),
                f32_at(positions, v * 3 + 1),
                f32_at(positions, v * 3 + 2),
            ),
            normal: Vec3::new(
                f32_at(normals, v * 3),
                f32_at(normals, v * 3 + 1),
                f32_at(normals, v * 3 + 2),
            ),
            uv: Vec2::new(f32_at(uvs, v * 2), f32_at(uvs, v * 2 + 1)),
            blend_indices: [
                blend_indices[v * 4],
                blend_indices[v * 4 + 1],
                blend_indices[v * 4 + 2],
                blend_indices[v * 4 + 3],
            ],
            blend_weights: [
                f32_at(blend_weights, v * 4),
                f32_at(blend_weights, v * 4 + 1),
                f32_at(blend_weights, v * 4 + 2),
                f32_at(blend_weights, v * 4 + 3),
            ],
            color: colors.map(|c| [c[v * 4], c[v * 4 + 1], c[v * 4 + 2], c[v * 4 + 3]]),
            tangent: tangents.map(|t| {
                Vec4::new(
                    f32_at(t, v * 4),
                    f32_at(t, v * 4 + 1),
                    f32_at(t, v * 4 + 2),
                    f32_at(t, v * 4 + 3),
                )
            }),
        });
    }

    let mut mesh = RsSkinnedMesh {
        skinning_block: skinning_block.unwrap_or_default().to_vec(),
        major,
        minor,
        flags,
        vertex_type: match (colors.is_some(), tangents.is_some()) {
            (_, true) => SkinnedMeshVertexType::Tangent,
            (true, false) => SkinnedMeshVertexType::Color,
            (false, false) => SkinnedMeshVertexType::Basic,
        },
        bounding_box: Aabb::new(Vec3::ZERO, Vec3::ZERO),
        bounding_sphere: Sphere::new(Vec3::ZERO, 0.0),
        ranges: Vec::new(),
        indices: Vec::new(),
        vertices: Vec::new(),
        // the v4 end tab (12 zero bytes in game files), passed through as given
        trailing: trailing.map(|t| t.to_vec()).unwrap_or_default(),
    };
    mesh.vertices = vertices;
    mesh.indices = indices
        .chunks_exact(2)
        .map(|c| u16::from_le_bytes([c[0], c[1]]))
        .collect();
    mesh.ranges = ranges
        .into_iter()
        .map(
            |(name, vertex_start, vertex_count, index_start, index_count)| SkinnedMeshRange {
                name,
                vertex_start,
                vertex_count,
                index_start,
                index_count,
            },
        )
        .collect();

    crate::skn::resolve_indices(&mesh)?;
    // the file's own bounds when unchanged (Riot's sphere cannot be recomputed), else computed
    match bounds {
        Some((min_x, min_y, min_z, max_x, max_y, max_z, cx, cy, cz, radius)) => {
            mesh.bounding_box = Aabb::new(
                Vec3::new(min_x, min_y, min_z),
                Vec3::new(max_x, max_y, max_z),
            );
            mesh.bounding_sphere = Sphere::new(Vec3::new(cx, cy, cz), radius);
        }
        None => recompute_bounds(&mut mesh),
    }

    let bytes = mesh
        .to_bytes()
        .map_err(|e| PyValueError::new_err(format!("{path}: {e}")))?;
    let written = bytes.len();
    std::fs::write(path, bytes).map_err(|e| PyIOError::new_err(format!("{path}: {e}")))?;
    Ok(written)
}

fn recompute_bounds(mesh: &mut RsSkinnedMesh) {
    if mesh.vertices.is_empty() {
        return;
    }
    let mut min = mesh.vertices[0].position;
    let mut max = min;
    for vertex in &mesh.vertices {
        min = min.min(vertex.position);
        max = max.max(vertex.position);
    }
    mesh.bounding_box.min = min;
    mesh.bounding_box.max = max;
    // box centre, radius to the furthest vertex (as lol_maya)
    let centre = (min + max) * 0.5;
    let mut radius = 0.0f32;
    for vertex in &mesh.vertices {
        radius = radius.max((vertex.position - centre).length());
    }
    mesh.bounding_sphere.center = centre;
    mesh.bounding_sphere.radius = radius;
}

/// Build and write a `.anm`. Returns `(bytes written, compressed)`.
///
/// `tracks` is `(joint_hash, times, translations, rotations, scales)`, one key per frame (3, 4 and
/// 3 floats per key).
///
/// `compressed` (default) writes `r3d2canm`; a clip it cannot hold (over 16383 joints or 65535
/// keys) falls back to uncompressed `r3d2anmd` v4. `compressed=False` always writes v4.
#[pyfunction]
#[pyo3(signature = (path, fps, tracks, compressed=true, precise=false, tolerances=None, protected_hashes=None))]
#[allow(clippy::type_complexity)]
pub fn write_animation(
    path: &str,
    fps: f32,
    tracks: Vec<(u32, Vec<f32>, Vec<f32>, Vec<f32>, Vec<f32>)>,
    compressed: bool,
    precise: bool,
    tolerances: Option<(f32, f32, f32)>,
    protected_hashes: Option<Vec<u32>>,
) -> PyResult<(usize, bool)> {
    if let Some((r,t,s)) = tolerances {
        if [r,t,s].iter().any(|v| !v.is_finite() || *v < 0.0) {
            return Err(PyValueError::new_err("compression tolerances must be finite and nonnegative"));
        }
    }
    let mut animation = RsAnimation::new(fps);

    for (index, (joint_hash, times, translations, rotations, scales)) in
        tracks.into_iter().enumerate()
    {
        let keys = times.len();
        if translations.len() != keys * 3 || rotations.len() != keys * 4 || scales.len() != keys * 3
        {
            return Err(PyValueError::new_err(format!(
                "track {index} (joint 0x{joint_hash:08X}): {keys} times but {} translation, {} \
                 rotation and {} scale components",
                translations.len(),
                rotations.len(),
                scales.len()
            )));
        }

        let mut track = AnimTrack::new(joint_hash);
        for k in 0..keys {
            track.frames.push(AnimFrame::new(
                times[k],
                Quat::from_xyzw(
                    rotations[k * 4],
                    rotations[k * 4 + 1],
                    rotations[k * 4 + 2],
                    rotations[k * 4 + 3],
                ),
                Vec3::new(
                    translations[k * 3],
                    translations[k * 3 + 1],
                    translations[k * 3 + 2],
                ),
                Vec3::new(scales[k * 3], scales[k * 3 + 1], scales[k * 3 + 2]),
            ));
        }
        animation.tracks.push(track);
    }

    let mut compression_failure = None;
    let packed = if compressed {
        match if precise || tolerances.is_some() {
            let (rotation,translation,scale) = tolerances.unwrap_or((0.0001, 0.001, 0.0001));
            let bounds=rs_anim::CompressionTolerances { rotation, translation, scale };
            if let Some(hashes)=&protected_hashes {
                let overrides:Vec<_>=hashes.iter().map(|h| (*h,bounds)).collect();
                animation.to_compressed_bytes_per_track(rs_anim::CompressionTolerances::default(), &overrides)
            } else { animation.to_compressed_bytes_with(bounds) }
        } else { animation.to_compressed_bytes() } {
            Ok(bytes) => Some(bytes),
            Err(rs_anim::Error::Unsupported(reason)) => { compression_failure=Some(reason); None },
            Err(e) => return Err(PyValueError::new_err(format!("{path}: {e}"))),
        }
    } else {
        None
    };
    let is_compressed = packed.is_some();
    let bytes = match packed {
        Some(bytes) => bytes,
        None => animation
            .to_bytes()
            .map_err(|e| PyValueError::new_err(match compression_failure {
                Some(reason)=>format!("{path}: compressed export failed ({reason}); uncompressed export failed ({e})"),
                None=>format!("{path}: {e}")
            }))?,
    };
    let written = bytes.len();
    std::fs::write(path, bytes).map_err(|e| PyIOError::new_err(format!("{path}: {e}")))?;
    Ok((written, is_compressed))
}
