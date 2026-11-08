//! Blender collapse decimation used by ARP's high-resolution preparation.
use pyo3::prelude::*;
#[pyfunction]
#[pyo3(signature=(vertices,edges,faces,ratio=0.2))]
pub fn decimate_mesh(py:Python<'_>,vertices:Vec<f32>,edges:Vec<[usize;2]>,faces:Vec<Vec<usize>>,ratio:f32)
    ->PyResult<(Vec<f32>,Vec<[usize;2]>,Vec<Vec<usize>>)>{
    if vertices.len()%3!=0 || !ratio.is_finite() || !(0.0..=1.0).contains(&ratio){
        return Err(pyo3::exceptions::PyValueError::new_err("Invalid collapse decimation input"));
    }
    let coords:Vec<_>=vertices.chunks_exact(3).map(|p|[p[0],p[1],p[2]]).collect();
    py.detach(move|| -> Result<_,String> {
        let mut mesh=crate::decimate_mesh::Mesh::new(&coords,&edges,&faces)?;
        // The modifier returns these meshes without entering BMesh decimation.
        if ratio==1.0 || faces.len()<=3 {return Ok((vertices,edges,faces));}
        let triangulation=crate::decimate_triangulate::begin(&mut mesh)?;
        crate::decimate_collapse::collapse(&mut mesh,ratio)?;
        triangulation.finish(&mut mesh)?;
        let mut map=vec![usize::MAX;mesh.vertices.len()];let mut coords=Vec::new();
        for (i,v) in mesh.vertices.iter().enumerate(){if v.alive{map[i]=coords.len()/3;coords.extend(v.co);}}
        let edges=mesh.edges.iter().filter(|e|e.alive).map(|e|e.vertices.map(|v|map[v])).collect();
        let faces=mesh.faces.iter().enumerate().filter(|(_,f)|f.alive).map(|(f,_)|mesh.face_vertices(f).into_iter().map(|v|map[v]).collect()).collect();
        Ok((coords,edges,faces))
    }).map_err(pyo3::exceptions::PyValueError::new_err)
}
