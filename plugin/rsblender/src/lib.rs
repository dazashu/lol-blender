//! `_rsblender`, the Rust half of the LoL Blender plugin.
//!
//! All League file parsing and writing happens here, through the Ritoshark crates. Python never
//! parses a byte.

#![forbid(unsafe_code)]

use pyo3::prelude::*;

mod anm;
mod mapgeo;
mod mat;
mod shape_mirror;
mod physics;
mod skin;
mod skl;
mod skn;
mod statics;
mod write;
mod vhds;
mod vhds_grid;
mod vhds_geometry;
mod vhds_native;
mod vhds_sort;
mod bone_heat;
mod eigen_dense;
mod eigen_sparselu;
mod eigen_colamd_exact;
mod par;
mod subsurf;
mod weight_ops;
mod weight_transfer;
mod dualcon;
mod vdb;
mod vdb_levelset;
mod vdb_mesh;
mod vdb_tables;
mod dualcon_projection;
mod dualcon_scan;
mod dualcon_repair;
mod dualcon_topology;
mod dualcon_mesh;
mod eigen_svd3;
mod mesh_merge;
mod decimate;
mod decimate_mesh;
mod decimate_quadric;
mod decimate_heap;
mod decimate_collapse;
mod decimate_polyfill;
mod decimate_triangulate;
mod eigen_inverse4;
mod mesh_join;

/// Crate version, checked by the addon to refuse a stale .pyd.
#[pyfunction]
fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

/// League's lowercased ELF hash of a joint name (the same function `rs_anim` reads `.skl` with).
#[pyfunction]
fn elf_hash(name: &str) -> u32 {
    rs_hash::elf_lower(name)
}

/// Re-express a League-space 4x4 (row-major) in Blender space. Only used by a test checking that
/// this matches `core.convert.P`.
#[pyfunction]
fn riot_to_blender_matrix(matrix: Vec<f64>) -> PyResult<Vec<f64>> {
    let m = mat::from_row_major(&matrix).ok_or_else(|| {
        pyo3::exceptions::PyValueError::new_err(format!(
            "expected 16 row-major values, got {}",
            matrix.len()
        ))
    })?;
    Ok(mat::to_row_major(&mat::riot_to_blender(&m)))
}

#[pymodule]
fn _rsblender(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(dualcon::remesh_sharp, m)?)?;
    m.add_function(wrap_pyfunction!(vdb::remesh_voxel, m)?)?;
    m.add_function(wrap_pyfunction!(mesh_merge::merge_map, m)?)?;
    m.add_function(wrap_pyfunction!(mesh_merge::merge_mesh, m)?)?;
    m.add_function(wrap_pyfunction!(decimate::decimate_mesh, m)?)?;
    m.add_function(wrap_pyfunction!(mesh_join::join_geometry, m)?)?;
    m.add_function(wrap_pyfunction!(bone_heat::bone_heat, m)?)?;
    m.add_function(wrap_pyfunction!(bone_heat::bone_heat_parts, m)?)?;
    m.add_function(wrap_pyfunction!(bone_heat::separate_loose, m)?)?;
    m.add_function(wrap_pyfunction!(bone_heat::heat_linear_system, m)?)?;
    m.add_function(wrap_pyfunction!(eigen_sparselu::eigen_lu_debug, m)?)?;
    m.add_function(wrap_pyfunction!(eigen_dense::trsm_debug, m)?)?;
    m.add_function(wrap_pyfunction!(eigen_dense::eigen_blocking_debug, m)?)?;
    m.add_function(wrap_pyfunction!(subsurf::subsurf_limit, m)?)?;
    m.add_function(wrap_pyfunction!(weight_ops::weight_buckets, m)?)?;
    m.add_function(wrap_pyfunction!(weight_ops::weight_lists, m)?)?;
    m.add_function(wrap_pyfunction!(weight_ops::mathutils_transform, m)?)?;
    m.add_function(wrap_pyfunction!(weight_ops::loose_components, m)?)?;
    m.add_function(wrap_pyfunction!(weight_ops::fill_unweighted, m)?)?;
    m.add_function(wrap_pyfunction!(weight_ops::clean_weight_members, m)?)?;
    m.add_function(wrap_pyfunction!(weight_transfer::transfer_map, m)?)?;
    m.add_function(wrap_pyfunction!(weight_transfer::transfer_values, m)?)?;
    m.add_class::<vhds::VhdsJob>()?;
    m.add_class::<vhds_native::VhdsNativeJob>()?;
    m.add_function(wrap_pyfunction!(vhds::vhds_encode, m)?)?;
    m.add_function(wrap_pyfunction!(vhds::vhds_decode, m)?)?;
    m.add_function(wrap_pyfunction!(vhds::vhds_float, m)?)?;
    m.add_function(wrap_pyfunction!(version, m)?)?;
    m.add_function(wrap_pyfunction!(elf_hash, m)?)?;
    m.add_function(wrap_pyfunction!(riot_to_blender_matrix, m)?)?;
    m.add_function(wrap_pyfunction!(write::write_skeleton, m)?)?;
    m.add_function(wrap_pyfunction!(write::write_skinned_mesh, m)?)?;
    m.add_function(wrap_pyfunction!(skn::pack_skin_weights, m)?)?;
    m.add_function(wrap_pyfunction!(shape_mirror::mirror_body_shape, m)?)?;
    m.add_function(wrap_pyfunction!(write::write_animation, m)?)?;
    m.add_function(wrap_pyfunction!(physics::track_quat, m)?)?;
    m.add_function(wrap_pyfunction!(physics::clamp_rotation, m)?)?;
    m.add_class::<physics::Wiggle>()?;
    m.add_class::<skin::MeshReport>()?;
    m.add_function(wrap_pyfunction!(skin::inspect_mesh, m)?)?;
    m.add_class::<statics::StaticMesh>()?;
    m.add_class::<mapgeo::MapGeo>()?;
    m.add_class::<mapgeo::MapModel>()?;
    m.add_class::<mapgeo::ModelEdit>()?;
    m.add_function(wrap_pyfunction!(mapgeo::write_map_geometry, m)?)?;
    m.add_function(wrap_pyfunction!(statics::write_static_mesh, m)?)?;
    m.add_class::<skl::Joint>()?;
    m.add_class::<skl::Skeleton>()?;
    m.add_class::<skn::Submesh>()?;
    m.add_class::<skn::Mesh>()?;
    m.add_class::<anm::Animation>()?;
    m.add_class::<anm::Baked>()?;
    Ok(())
}
