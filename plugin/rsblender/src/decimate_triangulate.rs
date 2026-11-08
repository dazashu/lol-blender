// SPDX-FileCopyrightText: 2023 Blender Authors
// SPDX-License-Identifier: GPL-2.0-or-later
use crate::decimate_mesh::{Mesh,V,sub,dot,cross};
use crate::decimate_polyfill::{quad,project,ears,beautify};
pub struct Triangulation {pub tags:Vec<Option<usize>>,pub count:usize}
pub fn begin(mesh:&mut Mesh)->Result<Triangulation,String>{
    let mut result=Triangulation{tags:vec![None;mesh.loops.len()],count:0};
    let original=mesh.faces.len();
    for f in 0..original {
        if !mesh.faces[f].alive || mesh.faces[f].loops.len()<=3 {continue;}
        let vertices=mesh.face_vertices(f);let points:Vec<_>=vertices.iter().map(|&v|mesh.vertices[v].co).collect();
        let normal=mesh.faces[f].normal;
        let tris=if vertices.len()==4 {quad(points.as_slice().try_into().unwrap()).to_vec()}
        else{let xy=project(&points,normal.map(|v|-v));let mut tris=ears(&xy);beautify(&xy,&mut tris);tris};
        let first_edge=mesh.edges.len();let mut created=Vec::new();
        for t in tris {
            let new=mesh.add_face(&t.map(|i|vertices[i]))?;mesh.faces[new].normal=normal;created.push(new);
        }
        result.tags.resize(mesh.loops.len(),None);
        for e in first_edge..mesh.edges.len(){for &l in &mesh.edges[e].loops{result.tags[l]=Some(f);}}
        result.count+=mesh.edges.len()-first_edge;
        let last=*created.last().ok_or("Triangulation produced no faces")?;
        mesh.faces.swap(f,last);
        for &l in &mesh.faces[f].loops {mesh.loops[l].face=f;}
        for &l in &mesh.faces[last].loops {mesh.loops[l].face=last;}
        mesh.kill_face(last);
        for &new in &created[..created.len()-1]{mesh.update_face_normal(new);}
    }
    Ok(result)
}
fn convex(p:[V;4])->bool{
    let plane=cross(sub(p[0],p[2]),sub(p[1],p[3]));let len=dot(plane,plane);
    if len<f32::EPSILON{return false;}
    let projected=p.map(|v|{let scale=dot(v,plane)/len;std::array::from_fn(|j|v[j]-plane[j]*scale)});
    let dirs:[V;4]=std::array::from_fn(|j|sub(projected[j],projected[(j+3)%4]));
    (0..4).all(|j|dot(plane,cross(dirs[j],dirs[(j+1)%4]))>0.)
}
impl Triangulation {
    pub fn finish(&self,mesh:&mut Mesh)->Result<(),String>{
        if self.count==0{return Ok(());}
        let can_merge=|l:usize,tag:Option<usize>|{
            let edge=&mesh.edges[mesh.loops[l].edge];
            edge.alive&&edge.loops.len()==2&&mesh.loops[edge.loops[0]].vertex!=mesh.loops[edge.loops[1]].vertex
                &&edge.loops.iter().all(|&l|self.tags.get(l).copied().flatten()==tag)
        };
        let mut candidates=Vec::new();
        for (e,edge) in mesh.edges.iter().enumerate(){
            if !edge.alive || edge.loops.len()!=2 {continue;}
            let [a,b]=[edge.loops[0],edge.loops[1]];let tag=self.tags[a];
            if tag.is_none() || self.tags[b]!=tag || mesh.loops[a].vertex==mesh.loops[b].vertex{continue;}
            let fa=&mesh.faces[mesh.loops[a].face];let fb=&mesh.faces[mesh.loops[b].face];
            if fa.loops.len()==3&&fb.loops.len()==3&&[mesh.loops[a].next,mesh.loops[a].prev,mesh.loops[b].next,mesh.loops[b].prev].iter().all(|&l|!can_merge(l,tag)){
                let opposite=|l:usize|{let next=mesh.loops[mesh.loops[l].next].vertex;
                    if edge.vertices.contains(&next){mesh.loops[mesh.loops[l].prev].vertex}else{next}};
                let quad=[edge.vertices[0],opposite(a),edge.vertices[1],opposite(b)].map(|v|mesh.vertices[v].co);
                if !convex(quad){continue;}
            }
            candidates.push(e);
        }
        for e in candidates{
            if !mesh.edges[e].alive || mesh.edges[e].loops.len()!=2{continue;}
            let a=mesh.loops[mesh.edges[e].loops[0]].face;let b=mesh.loops[mesh.edges[e].loops[1]].face;
            if a==b{continue;}
            let mut boundary=Vec::new();let mut first=None;
            for f in [a,b]{for &l in &mesh.faces[f].loops {
                let edge=mesh.loops[l].edge;
                let count=mesh.edges[edge].loops.iter().filter(|&&x|[a,b].contains(&mesh.loops[x].face)).count();
                if count==1 {boundary.push(edge);if first.is_none(){first=Some((mesh.loops[l].vertex,edge));}}
            }}
            let Some((start,first_edge))=first else{continue;};
            let mut vs=vec![start];let mut v=start;let mut current=first_edge;let mut used=std::collections::BTreeSet::new();
            loop {
                if !used.insert(current){break;}
                v=*mesh.edges[current].vertices.iter().find(|&&x|x!=v).ok_or("Invalid joined boundary")?;
                if v==start{break;}vs.push(v);
                let Some(edge)=boundary.iter().copied().find(|&x|x!=current&&mesh.edges[x].vertices.contains(&v))else{break;};current=edge;
            }
            if v!=start||used.len()!=boundary.len()||vs.len()<3{continue;}
            let normal=mesh.faces[a].normal;
            let new=mesh.add_face(&vs)?;mesh.faces[new].normal=normal;
            mesh.kill_face(a);mesh.kill_face(b);
            if mesh.edges[e].loops.is_empty(){mesh.kill_edge(e);}
        }
        Ok(())
    }
}
