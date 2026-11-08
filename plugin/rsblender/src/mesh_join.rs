// SPDX-FileCopyrightText: 2023 Blender Authors
// SPDX-License-Identifier: GPL-2.0-or-later
// Geometry path of meshtools.cc join_mesh_single and safe matrix inversion.
use pyo3::prelude::*;
use crate::eigen_inverse4::{M,inverse};
use crate::decimate_mesh::{V,dot,cross,normalize};
fn identity()->M{std::array::from_fn(|i|std::array::from_fn(|j|if i==j{1.}else{0.}))}
fn ortho(v:V)->V{
    let [x,y,z]=v.map(f32::abs);
    let axis=if x>y {if x>z{0}else{2}}else{if y>z{1}else{2}};
    match axis{0=>[-v[1]-v[2],v[0],v[0]],1=>[v[1],-v[0]-v[2],v[1]],_=>[v[2],v[2],-v[0]-v[1]]}
}
fn safe_inverse(m:M)->M{
    if let Some(inverted)=inverse(m){return inverted;}
    let mut v:[V;3]=std::array::from_fn(|i|[m[i][0],m[i][1],m[i][2]]);
    let flag=(0..3).fold(0,|f,i|f|if dot(v[i],v[i])==0.{1<<i}else{0});
    if flag==0||flag==7{return identity();}
    match flag{
        3=>{v[1]=ortho(v[2]);v[0]=cross(v[1],v[2]);},1=>v[0]=cross(v[1],v[2]),
        6=>{v[2]=ortho(v[0]);v[1]=cross(v[0],v[2]);},2=>v[1]=cross(v[0],v[2]),
        5=>{v[0]=ortho(v[1]);v[2]=cross(v[0],v[1]);},4=>v[2]=cross(v[0],v[1]),_=>{}
    }
    let mut matrix=m;
    for i in 0..3{if flag&(1<<i)!=0 {v[i]=normalize(v[i]);if v[i]==[0.;3]{v[i][i]=1.;}}
        matrix[i][..3].copy_from_slice(&v[i]);}
    inverse(matrix).unwrap_or_else(identity)
}
fn multiply(a:M,b:M)->M{
    std::array::from_fn(|j|std::array::from_fn(|k|(b[j][0]*a[0][k]+b[j][1]*a[1][k])+(b[j][2]*a[2][k]+b[j][3]*a[3][k])))
}
type Input=(Vec<f32>,Vec<[usize;2]>,Vec<Vec<usize>>,[f32;16]);
#[pyfunction]
pub fn join_geometry(py:Python<'_>,meshes:Vec<Input>)->PyResult<(Vec<f32>,Vec<[usize;2]>,Vec<Vec<usize>>)> {
    py.detach(move|| -> Result<_,String>{
        if meshes.is_empty(){return Err("No meshes to join".into());}
        let matrix=|m:[f32;16]|std::array::from_fn(|i|std::array::from_fn(|j|m[j*4+i]));
        let inverse=safe_inverse(matrix(meshes[0].3));
        let mut vertices=Vec::new();let mut edges=Vec::new();let mut faces=Vec::new();
        for (index,(coords,source_edges,source_faces,world)) in meshes.into_iter().enumerate(){
            let count=coords.len()/3;
            if coords.len()%3!=0||coords.iter().any(|x|!x.is_finite())||world.iter().any(|x|!x.is_finite())
                ||source_edges.iter().flatten().any(|&i|i>=count)||source_faces.iter().flatten().any(|&i|i>=count){return Err("Invalid mesh join geometry or transform".into());}
            let offset=vertices.len()/3;
            if index==0 {vertices.extend(coords);}else{
                let m=multiply(inverse,matrix(world));
                for p in coords.chunks_exact(3){for j in 0..3 {vertices.push(p[0]*m[0][j]+p[1]*m[1][j]+m[2][j]*p[2]+m[3][j]);}}
            }
            edges.extend(source_edges.into_iter().map(|e|e.map(|i|i+offset)));
            faces.extend(source_faces.into_iter().map(|f|f.into_iter().map(|i|i+offset).collect::<Vec<_>>()));
        }
        Ok((vertices,edges,faces))
    }).map_err(pyo3::exceptions::PyValueError::new_err)
}
