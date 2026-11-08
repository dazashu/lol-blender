// SPDX-FileCopyrightText: 2023 Blender Authors
// SPDX-License-Identifier: GPL-2.0-or-later
// Blender polyfill_2d / polyfill_2d_beautify and BMesh quad beauty.
use crate::decimate_mesh::{V,sub,dot,cross,normalize};
use crate::decimate_heap::Heap;
type P=[f32;2];
fn area(a:P,b:P,c:P)->f32 {(a[0]-b[0])*(b[1]-c[1])+(a[1]-b[1])*(c[0]-b[0])}
fn span(a:P,b:P,c:P)->i8 {
    let d=[b[0]-c[0],b[1]-c[1]];let e=[a[0]-c[0],a[1]-c[1]];
    let x=d[0]*e[1]-e[0]*d[1];if x>0.{1}else if x<0.{-1}else{0}
}
pub fn project(points:&[V],n:V)->Vec<P>{
    let f=n[0]*n[0]+n[1]*n[1];
    let (a,b)=if f>f32::EPSILON {let d=1./f.sqrt();let a=[n[1]*d,-n[0]*d,0.];
        (a,[-n[2]*a[1],n[2]*a[0],n[0]*a[1]-n[1]*a[0]])
    }else{([if n[2]<0.{-1.}else{1.},0.,0.],[0.,1.,0.])};
    points.iter().map(|&p|[dot(a,p),dot(b,p)]).collect()
}
pub fn beauty(p:[P;4],lock:bool)->(f32,f32){
    let [a,b,c,d]=p;let x=area(b,c,d);let y=area(b,d,a);let z=area(a,b,c);let w=area(a,c,d);
    let size=(x.abs()+y.abs()+z.abs()+w.abs())/8.;
    if (z>=0.)!=(w>=0.) || z.abs()<=1e-12 || w.abs()<=1e-12 {return(f32::MAX,size);}
    if (x>=0.)!=(y>=0.) {return(if lock {f32::MAX}else{-f32::MAX},size);}
    if x.abs()<=1e-12 || y.abs()<=1e-12 {return(-f32::MAX,size);}
    let len=|a:P,b:P|{let x=a[0]-b[0];let y=a[1]-b[1];(x*x+y*y).sqrt()};
    let ab=len(a,b);let bc=len(b,c);let cd=len(c,d);let da=len(d,a);let ac=len(a,c);let bd=len(b,d);
    (x.abs()/(bc+cd+bd)+y.abs()/(da+ab+bd)-(z.abs()/(ab+bc+ac)+w.abs()/(cd+da+ac)),size)
}
pub fn quad(points:[V;4])->[[usize;3];2]{
    let [a,b,c,d]=[points[1],points[2],points[3],points[0]];
    let ab=sub(a,b);let bc=sub(b,c);let cd=sub(c,d);let da=sub(d,a);
    let split=if dot(cross(ab,bc),cross(cd,da))<0. {true}
        else if dot(cross(bc,cd),cross(da,ab))<0. {false}
        else {
            let n1=cross(sub(b,c),sub(c,d));let n2=cross(sub(b,d),sub(d,a));
            let n:V=std::array::from_fn(|j|n1[j]+n2[j]);let scale=dot(n,n).sqrt();
            let cost=if scale==0. {f32::MAX}else{
                let xy=project(&[a,b,c,d],normalize(n));
                let sign=|x:f32|if x>1e-5 {1}else if x< -1e-5 {-1}else{0};
                if sign(area(xy[1],xy[2],xy[3])/scale)+sign(area(xy[1],xy[3],xy[0])/scale)==0 {f32::MAX}
                else{beauty([xy[0],xy[1],xy[2],xy[3]],false).0}
            };cost>0.
        };
    if split {[[0,1,2],[0,2,3]]}else{[[1,2,3],[1,3,0]]}
}
pub fn ears(coords:&[P])->Vec<[usize;3]>{
    let n=coords.len();if n<3{return Vec::new();}
    let mut prev:Vec<_>=(0..n).map(|i|(i+n-1)%n).collect();let mut next:Vec<_>=(0..n).map(|i|(i+1)%n).collect();
    let mut signs:Vec<_>=(0..n).map(|i|span(coords[prev[i]],coords[i],coords[next[i]])).collect();
    let mut alive=vec![true;n];let mut first=0;let mut start=0;let mut reverse=false;let mut remaining=n;let mut result=Vec::new();
    while remaining>3 {
        let mut found=None;
        for accept in [1,0] {
            let mut e=start;
            for _ in 0..remaining {
                let all_convex=signs.iter().enumerate().all(|(i,&s)|!alive[i]||s==1);
                let triangle=[e,next[e],prev[e]];
                let valid=all_convex || (signs[e]==accept && !(0..n).any(|i|{
                    if !alive[i] || signs[i]==1 || triangle.contains(&i){return false;}
                    let t=triangle.map(|v|coords[v]);let p=coords[i];
                    if (0..2).any(|j|p[j]<t.iter().map(|x|x[j]).fold(f32::MAX,f32::min)||p[j]>t.iter().map(|x|x[j]).fold(-f32::MAX,f32::max)){return false;}
                    span(t[0],t[1],p)!=-1 && span(t[1],t[2],p)!=-1 && span(t[2],t[0],p)!=-1
                }));
                if valid{found=Some(e);break;}e=if reverse{prev[e]}else{next[e]};
            }
            if found.is_some(){break;}
        }
        let e=found.unwrap_or_else(||{let mut e=start;for _ in 0..remaining{if signs[e]!=-1{return e;}e=next[e];}e});
        let a=prev[e];let b=next[e];result.push([a,e,b]);next[a]=b;prev[b]=a;alive[e]=false;if first==e{first=b;}remaining-=1;
        for v in [a,b]{if signs[v]!=1 {signs[v]=span(coords[prev[v]],coords[v],coords[next[v]]);}}
        start=if reverse{prev[a]}else{next[b]};
        if signs[start]!=1 {start=if reverse{prev[start]}else{next[start]};reverse=!reverse;}
    }
    result.push([first,next[first],next[next[first]]]);result
}
#[derive(Clone,Copy)]
struct Half {v:usize,next:usize,radial:usize,base:usize}
fn half_cost(coords:&[P],edges:&[Half],e:usize)->(f32,f32){
    let other=edges[e].radial;let a=edges[edges[e].next].next;let b=edges[edges[other].next].next;
    beauty([coords[edges[a].v],coords[edges[e].v],coords[edges[b].v],coords[edges[other].v]],false)
}
pub fn beautify(coords:&[P],tris:&mut Vec<[usize;3]>){
    if tris.len()<2{return;}
    let mut edges=vec![Half{v:usize::MAX,next:usize::MAX,radial:usize::MAX,base:usize::MAX};tris.len()*3];let mut order=Vec::new();
    for (i,t) in tris.iter().enumerate(){for current in 0..3 {
        let previous=(current+2)%3;let e=i*3+previous;edges[e].v=t[previous];edges[e].next=i*3+current;
        let a=t[previous].min(t[current]);let b=t[previous].max(t[current]);
        if !(b-a==1 || (a==0&&b==coords.len()-1)){order.push((a,b,e));}
    }}
    order.sort_unstable();let count=order.len()/2;let mut heap=Heap::new(count);let mut half_for_base=vec![0;count];
    for (base,pair) in order.chunks_exact(2).enumerate(){let a=pair[0].2;let b=pair[1].2;
        edges[a].radial=b;edges[b].radial=a;edges[a].base=base;edges[b].base=base;}
    for e in 0..edges.len(){if edges[e].radial<e{let cost=half_cost(coords,&edges,e).0;if cost<0.{heap.set(edges[e].base,cost);half_for_base[edges[e].base]=e;}}}
    while let Some(base)=heap.pop(){
        let e=half_for_base[base];let a=e;let b=edges[a].next;let c=edges[b].next;let d=edges[a].radial;let f=edges[d].next;let g=edges[f].next;
        edges[a].next=c;edges[b].next=d;edges[c].next=f;edges[d].next=g;edges[f].next=a;edges[g].next=b;edges[a].v=edges[g].v;edges[d].v=edges[c].v;
        let x=edges[e].next;let y=edges[x].next;let other=edges[e].radial;let z=edges[other].next;let w=edges[z].next;
        for edge in [x,y,z,w]{let base=edges[edge].base;if base==usize::MAX{continue;}
            let (cost,area)=half_cost(coords,&edges,edge);if cost< -1e-6*area.max(1.){heap.set(base,cost);half_for_base[base]=edge;}else{heap.remove(base);}}
    }
    tris.clear();for e in 0..edges.len(){if edges[e].v!=usize::MAX{let b=edges[e].next;let c=edges[b].next;
        tris.push([edges[e].v,edges[b].v,edges[c].v]);edges[e].v=usize::MAX;edges[b].v=usize::MAX;edges[c].v=usize::MAX;}}
}
