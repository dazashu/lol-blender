// Copyright (C) 2001 Intel Corporation
// Copyright (C) 2010 Gael Guennebaud
// Copyright (C) 2009 Benoit Jacob
// SPDX-License-Identifier: MPL-2.0
// Port of Eigen InverseSize4.h. Intel's original notice also applies:
// Permission is granted to use, copy, distribute and prepare derivative works
// of this library for any purpose and without fee, provided that the above
// copyright notice and this statement appear in all copies. Intel makes no
// representations about suitability for any purpose and disclaims all warranties.
pub type M=[[f32;4];4]; // columns, like Blender's float[4][4]
type P=[f32;4];
fn mul(a:P,b:P)->P{std::array::from_fn(|i|a[i]*b[i])}
fn add(a:P,b:P)->P{std::array::from_fn(|i|a[i]+b[i])}
fn sub(a:P,b:P)->P{std::array::from_fn(|i|a[i]-b[i])}
fn s(a:P,indices:[usize;4])->P{indices.map(|i|a[i])}
fn sw(a:P,b:P,indices:[usize;4])->P{[a[indices[0]],a[indices[1]],b[indices[2]],b[indices[3]]]}
fn low(a:P,b:P)->P{[a[0],a[1],b[0],b[1]]}
fn high(a:P,b:P)->P{[b[2],b[3],a[2],a[3]]}
fn det(m:M)->f32{
    let d2=|i:usize,j:usize|m[0][i]*m[1][j]-m[0][j]*m[1][i];
    let d3=|i:usize,a:f32,j:usize,b:f32,k:usize,c:f32|m[2][i]*a+(-m[2][j]*b+m[2][k]*c);
    let a=d2(0,1);let b=d2(0,2);let c=d2(0,3);let d=d2(1,2);let e=d2(1,3);let f=d2(2,3);
    let p=d3(1,f,2,e,3,d);let q=d3(0,f,2,c,3,b);let r=d3(0,e,1,c,3,a);let t=d3(0,d,1,b,2,a);
    (-m[3][0]*p+m[3][1]*q)+(-m[3][2]*r+m[3][3]*t)
}
pub fn inverse(m:M)->Option<M>{
    if !(det(m).abs()>0.){return None;}
    let a=low(m[0],m[1]);let b=high(m[1],m[0]);let c=low(m[2],m[3]);let d=high(m[3],m[2]);
    let ab=sub(mul(s(a,[3,3,0,0]),b),mul(s(a,[1,1,2,2]),s(b,[2,3,0,1])));
    let dc=sub(mul(s(d,[3,3,0,0]),c),mul(s(d,[1,1,2,2]),s(c,[2,3,0,1])));
    let determinant=|p:P|{let t=mul(s(p,[3,3,1,1]),p);sub(t,high(t,t))};
    let da=determinant(a);let db=determinant(b);let dd=determinant(d);let d_c=determinant(c);
    let mut trace=mul(s(dc,[0,2,1,3]),ab);trace=add(trace,high(trace,trace));trace=add(trace,s(trace,[1,0,0,0]));
    let det=sub(add(mul(da,dd),mul(db,d_c)),trace)[0];let rd=[1./det,-(1./det),-(1./det),1./det];
    let id=sub(mul(d,[da[0];4]),add(mul(s(c,[0,0,2,2]),low(ab,ab)),mul(s(c,[1,1,3,3]),high(ab,ab))));
    let ia=sub(mul(a,[dd[0];4]),add(mul(s(b,[0,0,2,2]),low(dc,dc)),mul(s(b,[1,1,3,3]),high(dc,dc))));
    let ib=sub(mul(c,[db[0];4]),sub(mul(d,s(ab,[3,0,3,0])),mul(s(d,[1,0,3,2]),s(ab,[2,1,2,1]))));
    let ic=sub(mul(b,[d_c[0];4]),sub(mul(a,s(dc,[3,0,3,0])),mul(s(a,[1,0,3,2]),s(dc,[2,1,2,1]))));
    let ia=mul(ia,rd);let ib=mul(ib,rd);let ic=mul(ic,rd);let id=mul(id,rd);
    Some([sw(ia,ib,[3,1,3,1]),sw(ia,ib,[2,0,2,0]),sw(ic,id,[3,1,3,1]),sw(ic,id,[2,0,2,0])])
}
