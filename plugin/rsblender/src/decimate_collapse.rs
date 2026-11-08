// SPDX-FileCopyrightText: 2023 Blender Authors
// SPDX-License-Identifier: GPL-2.0-or-later
// Development port of bmesh_decimate_collapse.cc, without symmetry or weights.
use crate::decimate_mesh::{Mesh,V,sub,dot,cross,normalize};
use crate::decimate_quadric::Quadric;
use crate::decimate_heap::Heap;
use std::collections::BTreeSet;
fn target(mesh:&Mesh,q:&[Quadric],e:usize)->[f64;3]{
    let [a,b]=mesh.edges[e].vertices;
    q[a].add(q[b]).optimize().unwrap_or_else(||std::array::from_fn(|j|
        0.5*(mesh.vertices[a].co[j] as f64+mesh.vertices[b].co[j] as f64)))
}
fn quadrics(mesh:&Mesh)->Vec<Quadric>{
    let mut result=vec![Quadric::default();mesh.vertices.len()];
    for (f,face) in mesh.faces.iter().enumerate(){
        if !face.alive {continue;}
        let vertices=mesh.face_vertices(f);let mut center=[0.0f32;3];
        for &v in &vertices {for j in 0..3 {center[j]+=mesh.vertices[v].co[j];}}
        let factor=1./vertices.len() as f32;for v in &mut center {*v*=factor;}
        let n=face.normal.map(|v|v as f64);
        let q=Quadric::plane([n[0],n[1],n[2],-(n[0]*center[0] as f64+n[1]*center[1] as f64+n[2]*center[2] as f64)]);
        for v in vertices {result[v]=result[v].add(q);}
    }
    for e in &mesh.edges {
        if !e.alive || e.loops.len()!=1 {continue;}
        let [a,b]=e.vertices;let aco=mesh.vertices[a].co;let bco=mesh.vertices[b].co;
        let n=cross(sub(bco,aco),mesh.faces[mesh.loops[e.loops[0]].face].normal).map(|v|v as f64);
        let d=(n[0]*n[0]+n[1]*n[1]+n[2]*n[2]).sqrt();
        if d<=f32::EPSILON as f64 {continue;}
        let n=n.map(|v|v*(1./d));let center:V=std::array::from_fn(|j|(aco[j]+bco[j])*0.5);
        let q=Quadric::plane([n[0],n[1],n[2],-(n[0]*center[0] as f64+n[1]*center[1] as f64+n[2]*center[2] as f64)]).scale(100.);
        result[a]=result[a].add(q);result[b]=result[b].add(q);
    }
    result
}
fn update_cost(mesh:&Mesh,q:&[Quadric],heap:&mut Heap,e:usize){
    let edge=&mesh.edges[e];
    if !edge.alive || !(1..=2).contains(&edge.loops.len()) || edge.loops.iter().any(|&l|mesh.faces[mesh.loops[l].face].loops.len()!=3){heap.remove(e);return;}
    let [a,b]=edge.vertices;let p=target(mesh,q,e);
    let mut cost=((q[a].evaluate(p)+q[b].evaluate(p)) as f32).abs();
    if cost<1e-12 {
        let d=sub(mesh.vertices[a].co,mesh.vertices[b].co);
        cost=dot(mesh.vertices[a].normal,mesh.vertices[b].normal).abs()/(-dot(d,d)).min(-f32::EPSILON)-cost;
    }
    heap.set(e,cost);
}
fn degenerate_topology(mesh:&Mesh,e:usize)->bool {
    let [a,b]=mesh.edges[e].vertices;
    let mut tagged_vertices=BTreeSet::new();let mut tagged_faces=BTreeSet::new();
    for v in [a,b] {if mesh.vertices[v].edges.iter().any(|&e|!(1..=2).contains(&mesh.edges[e].loops.len())) {return true;}}
    for &edge in &mesh.vertices[a].edges {
        tagged_vertices.extend(mesh.edges[edge].vertices);
        tagged_faces.extend(mesh.edges[edge].loops.iter().map(|&l|mesh.loops[l].face));
    }
    for &l in &mesh.edges[e].loops {let f=mesh.loops[l].face;tagged_faces.remove(&f);for v in mesh.face_vertices(f){tagged_vertices.remove(&v);}}
    mesh.vertices[b].edges.iter().any(|&edge|mesh.edges[edge].vertices.iter().any(|v|tagged_vertices.contains(v))
        ||mesh.edges[edge].loops.iter().any(|&l|tagged_faces.contains(&mesh.loops[l].face)))
}
fn flips(mesh:&Mesh,e:usize,p:V)->bool {
    for v in mesh.edges[e].vertices {
        for l in mesh.vertex_loops(v) {
            let loop_=&mesh.loops[l];let prev=&mesh.loops[loop_.prev];
            if loop_.edge==e || prev.edge==e {continue;}
            let a=mesh.vertices[prev.vertex].co;let b=mesh.vertices[mesh.loops[loop_.next].vertex].co;
            let other=sub(a,b);let before=cross(other,sub(a,mesh.vertices[v].co));let after=cross(other,sub(a,p));
            if dot(before,after)<=(dot(before,before)+dot(after,after))*0.01 {return true;}
        }
    }
    false
}
pub fn collapse(mesh:&mut Mesh,factor:f32)->Result<(),String>{
    if !factor.is_finite() || !(0.0..=1.0).contains(&factor) || mesh.faces.iter().any(|f|f.alive&&f.loops.len()!=3){return Err("Collapse requires triangulated input and a valid ratio".into());}
    let mut q=quadrics(mesh);let mut heap=Heap::new(mesh.edges.len());
    for e in 0..mesh.edges.len(){update_cost(mesh,&q,&mut heap,e);}
    let mut face_count=mesh.faces.iter().filter(|f|f.alive).count();let target_count=(face_count as f32*factor) as usize;
    while face_count>target_count {
        let Some((e,value))=heap.top()else{break;};if value==f32::MAX {break;}
        heap.pop();
        if degenerate_topology(mesh,e){heap.set(e,f32::MAX);continue;}
        let p=target(mesh,&q,e).map(|v|v as f32);
        if flips(mesh,e,p){heap.set(e,f32::MAX);continue;}
        let [keep,remove]=mesh.edges[e].vertices;
        let mut others=Vec::new();
        for &l in &mesh.edges[e].loops {
            let a=mesh.loops[mesh.loops[l].prev].edge;let b=mesh.loops[mesh.loops[l].next].edge;
            others.push(if mesh.edges[a].vertices.contains(&remove){[a,b]}else{[b,a]});
        }
        if others.len()==2 && others[0].iter().any(|e|others[1].contains(e)){heap.set(e,f32::MAX);continue;}
        let old=mesh.vertices[remove].normal;let a=mesh.vertices[keep].co;let b=mesh.vertices[remove].co;
        let difference=sub(b,a);
        let fac=if (0..3).all(|i|(a[i]-b[i]).abs()<=f32::EPSILON){0.5}else{dot(sub(p,a),difference)/dot(difference,difference)};
        face_count-=mesh.edges[e].loops.len();
        mesh.kill_edge(e);mesh.splice_vertex(keep,remove);
        for &[clear,retained] in &others {mesh.splice_edge(retained,clear);}
        mesh.vertices[keep].co=p;
        for &[clear,_] in &others {heap.remove(clear);}
        q[keep]=q[keep].add(q[remove]);
        let before=mesh.vertices[keep].normal;
        mesh.vertices[keep].normal=normalize(std::array::from_fn(|j|(1.-fac)*before[j]+fac*old[j]));
        for &edge in &mesh.vertices[keep].edges {update_cost(mesh,&q,&mut heap,edge);}
        for l in mesh.vertex_loops(keep){
            if mesh.faces[mesh.loops[l].face].loops.len()==3 {
                let prev=mesh.loops[mesh.loops[l].prev].edge;
                let outer=if mesh.edges[prev].vertices.contains(&keep){mesh.loops[mesh.loops[l].next].edge}else{prev};
                update_cost(mesh,&q,&mut heap,outer);
            }
        }
    }
    Ok(())
}
