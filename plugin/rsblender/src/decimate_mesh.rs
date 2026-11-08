// SPDX-FileCopyrightText: 2023 Blender Authors
// SPDX-License-Identifier: GPL-2.0-or-later
// BMesh disk/radial cycles and normals needed by the collapse port.
pub type V=[f32;3];
pub fn sub(a:V,b:V)->V {std::array::from_fn(|i|a[i]-b[i])}
pub fn dot(a:V,b:V)->f32 {a[0]*b[0]+a[1]*b[1]+a[2]*b[2]}
pub fn cross(a:V,b:V)->V {[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]}
pub fn normalize(a:V)->V {let d=dot(a,a);if d>1e-35 {let s=1./d.sqrt();a.map(|x|x*s)}else{[0.;3]}}
fn acos(x:f32)->f32 {
    let f=x.abs();let m=if f<1. {1.-(1.-f)}else{1.};
    let a=(1.-m).sqrt()*(1.5707963267+m*(-0.213300989+m*(0.077980478+m*(-0.02164095))));
    if x<0. {std::f32::consts::PI-a}else{a}
}
pub struct Vertex {pub co:V,pub normal:V,pub edges:Vec<usize>,pub alive:bool}
pub struct Edge {pub vertices:[usize;2],pub loops:Vec<usize>,pub alive:bool}
pub struct Loop {pub vertex:usize,pub edge:usize,pub face:usize,pub next:usize,pub prev:usize}
pub struct Face {pub loops:Vec<usize>,pub normal:V,pub alive:bool}
pub struct Mesh {pub vertices:Vec<Vertex>,pub edges:Vec<Edge>,pub loops:Vec<Loop>,pub faces:Vec<Face>,free_faces:Vec<usize>}
impl Mesh {
    pub fn new(coords:&[V],edges:&[[usize;2]],faces:&[Vec<usize>])->Result<Self,String> {
        if coords.iter().flatten().any(|v|!v.is_finite()) || edges.iter().flatten().any(|&v|v>=coords.len())
            || faces.iter().any(|f|f.len()<3 || f.iter().any(|&v|v>=coords.len())) {return Err("Invalid decimation mesh".into());}
        let mut mesh=Self {vertices:coords.iter().map(|&co|Vertex{co,normal:[0.;3],edges:Vec::new(),alive:true}).collect(),
            edges:Vec::new(),loops:Vec::new(),faces:Vec::new(),free_faces:Vec::new()};
        for &[a,b] in edges {mesh.add_edge(a,b)?;}
        for face in faces {mesh.add_face(face)?;}
        mesh.update_normals();Ok(mesh)
    }
    pub fn add_edge(&mut self,a:usize,b:usize)->Result<usize,String> {
        if a==b {return Err("Decimation mesh has a zero-length topology edge".into());}
        let e=self.edges.len();
        self.edges.push(Edge{vertices:[a,b],loops:Vec::new(),alive:true});
        self.vertices[a].edges.push(e);self.vertices[b].edges.push(e);Ok(e)
    }
    fn append_radial(&mut self,e:usize,l:usize) {
        let radial=&mut self.edges[e].loops;
        if radial.is_empty(){radial.push(l);}else{radial.insert(1,l);radial.rotate_left(1);}
        self.loops[l].edge=e;
    }
    pub fn add_face(&mut self,vs:&[usize])->Result<usize,String> {
        let f=self.free_faces.pop().unwrap_or(self.faces.len());let first=self.loops.len();
        // BM_edges_from_verts_ensure creates the closing edge first.
        for i in 0..vs.len(){let a=vs[(i+vs.len()-1)%vs.len()];let b=vs[i];
            if !self.vertices[a].edges.iter().any(|&e|self.edges[e].vertices.contains(&b)){self.add_edge(a,b)?;}
        }
        for i in 0..vs.len() {
            let a=vs[i];let b=vs[(i+1)%vs.len()];
            let edge=self.vertices[a].edges.iter().copied().find(|&e|self.edges[e].vertices.contains(&b));
            let edge=if let Some(e)=edge {e}else{self.add_edge(a,b)?};
            let l=self.loops.len();self.loops.push(Loop{vertex:a,edge,face:f,next:first+(i+1)%vs.len(),prev:first+(i+vs.len()-1)%vs.len()});
            self.append_radial(edge,l);
        }
        let face=Face{loops:(first..self.loops.len()).collect(),normal:[0.;3],alive:true};
        if f==self.faces.len(){self.faces.push(face);}else{self.faces[f]=face;}Ok(f)
    }
    pub fn face_vertices(&self,f:usize)->Vec<usize>{self.faces[f].loops.iter().map(|&l|self.loops[l].vertex).collect()}
    fn normal_loops(&self,v:usize)->Vec<usize>{
        self.vertices[v].edges.iter().flat_map(|&e|self.edges[e].loops.iter().copied())
            .filter(|&l|self.loops[l].vertex==v).collect()
    }
    pub fn vertex_loops(&self,v:usize)->Vec<usize>{
        let edges=&self.vertices[v].edges;
        let Some(l)=edges.iter().find_map(|&e|self.edges[e].loops.first().copied())else{return Vec::new();};
        let first=if self.loops[l].vertex==v {l}else{self.loops[l].next};
        let start=edges.iter().position(|&e|e==self.loops[first].edge).unwrap();
        let mut result=Vec::new();
        for k in 0..edges.len(){
            let mut group:Vec<_>=self.edges[edges[(start+k)%edges.len()]].loops.iter().copied().filter(|&l|self.loops[l].vertex==v).collect();
            if k==0 {let index=group.iter().position(|&l|l==first).unwrap();group.rotate_left(index);}
            result.extend(group);
        }
        result
    }
    pub fn update_face_normal(&mut self,f:usize){
            let p:Vec<_>=self.face_vertices(f).iter().map(|&v|self.vertices[v].co).collect();
            self.faces[f].normal=normalize(match p.len(){
                3=>cross(sub(p[0],p[1]),sub(p[1],p[2])),
                4=>cross(sub(p[0],p[2]),sub(p[1],p[3])),
                _=>{let mut n=[0.;3];for i in 0..p.len(){let a=p[(i+p.len()-1)%p.len()];let b=p[i];
                    n[0]+=(a[1]-b[1])*(a[2]+b[2]);n[1]+=(a[2]-b[2])*(a[0]+b[0]);n[2]+=(a[0]-b[0])*(a[1]+b[1]);}n}
            });
    }
    pub fn update_normals(&mut self){
        for f in 0..self.faces.len(){if self.faces[f].alive{self.update_face_normal(f);}}
        for v in 0..self.vertices.len(){
            if !self.vertices[v].alive {continue;}
            let mut n=[0.;3];
            for l in self.normal_loops(v){
                let l=&self.loops[l];let prev=&self.loops[l.prev];
                let e1=&self.edges[prev.edge];let e2=&self.edges[l.edge];
                let d1=normalize(sub(self.vertices[e1.vertices[0]].co,self.vertices[e1.vertices[1]].co));
                let d2=normalize(sub(self.vertices[e2.vertices[0]].co,self.vertices[e2.vertices[1]].co));
                let mut d=dot(d1,d2);
                if (e1.vertices[0]==prev.vertex)^(e2.vertices[0]==l.vertex){d=-d;}
                let weight=acos(-d);let fnormal=self.faces[l.face].normal;
                for j in 0..3{n[j]+=fnormal[j]*weight;}
            }
            n=normalize(n);if n==[0.;3]{n=normalize(self.vertices[v].co);}
            self.vertices[v].normal=n;
        }
    }
    pub fn kill_face(&mut self,f:usize){
        for &l in &self.faces[f].loops {let e=self.loops[l].edge;self.edges[e].loops.retain(|&x|x!=l);}
        self.faces[f].alive=false;
        self.free_faces.push(f);
    }
    pub fn kill_edge(&mut self,e:usize){
        while let Some(&l)=self.edges[e].loops.first(){self.kill_face(self.loops[l].face);}
        for v in self.edges[e].vertices {self.vertices[v].edges.retain(|&x|x!=e);}
        self.edges[e].alive=false;
    }
    pub fn splice_vertex(&mut self,dst:usize,src:usize){
        while let Some(&e)=self.vertices[src].edges.first(){
            for &l in &self.edges[e].loops {
                if self.loops[l].vertex==src {self.loops[l].vertex=dst;}
                else {let next=self.loops[l].next;if self.loops[next].vertex==src {self.loops[next].vertex=dst;}}
            }
            self.vertices[src].edges.remove(0);
            for v in &mut self.edges[e].vertices {if *v==src {*v=dst;}}
            self.vertices[dst].edges.push(e);
        }
        self.vertices[src].alive=false;
    }
    pub fn splice_edge(&mut self,dst:usize,src:usize){
        while !self.edges[src].loops.is_empty(){let l=self.edges[src].loops.remove(0);self.append_radial(dst,l);}
        self.kill_edge(src);
    }
}
