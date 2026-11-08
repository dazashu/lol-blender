//! Rust Sharp remeshing, including dualcon scan, repair, flood fill and QEF.
use pyo3::prelude::*;
#[pyfunction]
#[pyo3(signature=(vertices,triangles,depth=7,scale=0.95,remove_disconnected=true,threshold=0.82,sharpness=1.0))]
pub fn remesh_sharp(py:Python<'_>,vertices:Vec<f32>,triangles:Vec<usize>,depth:u32,scale:f32,
    remove_disconnected:bool,threshold:f32,sharpness:f32)->PyResult<(Vec<f32>,Vec<[usize;4]>)> {
    if vertices.len()%3!=0 || triangles.len()%3!=0 || !threshold.is_finite() || !(0.0..=1.0).contains(&threshold)
        || !sharpness.is_finite() || !(0.0..=2.0).contains(&sharpness) {
        return Err(pyo3::exceptions::PyValueError::new_err("Invalid Sharp remeshing input"));
    }
    let vertices:Vec<_>=vertices.chunks_exact(3).map(|v|[v[0],v[1],v[2]]).collect();
    let triangles:Vec<_>=triangles.chunks_exact(3).map(|v|[v[0],v[1],v[2]]).collect();
    py.detach(move || {
        let mut scan=crate::dualcon_scan::Scan::new(&vertices,&triangles,scale,depth)?;
        scan.reset_minimal_edges();
        { let mut repair=crate::dualcon_repair::Repair::new(&mut scan); repair.trace()?; repair.trace()?; }
        scan.build_signs();
        if remove_disconnected {scan.flood_fill(threshold)?;scan.build_signs();}
        let mesh=scan.mesh(sharpness);
        if mesh.vertices.iter().flatten().any(|v|!v.is_finite()) || mesh.quads.iter().flatten().any(|&i|i>=mesh.vertices.len()) {
            return Err("Sharp remeshing produced invalid geometry".to_string());
        }
        Ok((mesh.vertices.into_iter().flatten().collect(),mesh.quads))
    }).map_err(pyo3::exceptions::PyValueError::new_err)
}
