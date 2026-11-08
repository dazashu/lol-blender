//! Native voxel remeshing with the OpenVDB 11 uniform level-set algorithms.
use pyo3::prelude::*;

#[pyfunction]
pub fn remesh_voxel(py: Python<'_>, vertices: Vec<f32>, triangles: Vec<usize>, voxel_size: f32)
    -> PyResult<(Vec<f32>, Vec<[usize; 4]>)>
{
    if vertices.len() % 3 != 0 || triangles.len() % 3 != 0 {
        return Err(pyo3::exceptions::PyValueError::new_err("Invalid voxel remeshing input"));
    }
    let vertices: Vec<_> = vertices.chunks_exact(3).map(|p| [p[0],p[1],p[2]]).collect();
    let triangles: Vec<_> = triangles.chunks_exact(3).map(|p| [p[0],p[1],p[2]]).collect();
    py.detach(move || {
        let grid = crate::vdb_levelset::Grid::from_mesh(&vertices, &triangles, voxel_size)?;
        let mesh = crate::vdb_mesh::mesh(&grid)?;
        if mesh.vertices.iter().flatten().any(|v| !v.is_finite()) {
            return Err("Voxel remeshing produced non-finite coordinates".to_string());
        }
        // Blender mesh_remesh_voxel.cc reverses OpenVDB's winding.
        Ok((mesh.vertices.into_iter().flatten().collect(),
            mesh.quads.into_iter().map(|q| [q[0],q[3],q[2],q[1]]).collect()))
    }).map_err(pyo3::exceptions::PyValueError::new_err)
}
