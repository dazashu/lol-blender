// SPDX-FileCopyrightText: 2001-2002 NaN Holding BV. All rights reserved.
// SPDX-License-Identifier: GPL-2.0-or-later
// Port of Blender 4.2 blenlib/intern/quadric.c for collapse decimation.
#[derive(Clone, Copy, Default)]
pub struct Quadric {
    a2: f64, b2: f64, c2: f64, ab: f64, ac: f64, bc: f64,
    ad: f64, bd: f64, cd: f64, d2: f64,
}
impl Quadric {
    pub fn plane(v: [f64;4]) -> Self {
        Self { a2:v[0]*v[0], b2:v[1]*v[1], c2:v[2]*v[2],
            ab:v[0]*v[1], ac:v[0]*v[2], bc:v[1]*v[2],
            ad:v[0]*v[3], bd:v[1]*v[3], cd:v[2]*v[3], d2:v[3]*v[3] }
    }
    pub fn add(self, q: Self) -> Self {
        Self {a2:self.a2+q.a2,b2:self.b2+q.b2,c2:self.c2+q.c2,
            ab:self.ab+q.ab,ac:self.ac+q.ac,bc:self.bc+q.bc,
            ad:self.ad+q.ad,bd:self.bd+q.bd,cd:self.cd+q.cd,d2:self.d2+q.d2}
    }
    pub fn scale(self, s: f64) -> Self {
        Self {a2:self.a2*s,b2:self.b2*s,c2:self.c2*s,
            ab:self.ab*s,ac:self.ac*s,bc:self.bc*s,
            ad:self.ad*s,bd:self.bd*s,cd:self.cd*s,d2:self.d2*s}
    }
    pub fn evaluate(self, v: [f64;3]) -> f64 {
        let [x,y,z]=v;
        self.a2*(x*x)+self.ab*2.*(x*y)+self.ac*2.*(x*z)+self.ad*2.*x
            +self.b2*(y*y)+self.bc*2.*(y*z)+self.bd*2.*y
            +self.c2*(z*z)+self.cd*2.*z+self.d2
    }
    pub fn optimize(self) -> Option<[f64;3]> {
        let q=self;
        let determinant=q.a2*(q.b2*q.c2-q.bc*q.bc)-q.ab*(q.ab*q.c2-q.ac*q.bc)
            +q.ac*(q.ab*q.bc-q.ac*q.b2);
        if determinant.abs()<=1e-8 {return None;}
        let inverse=1./determinant;
        let m=[
            [(q.b2*q.c2-q.bc*q.bc)*inverse,(q.ac*q.bc-q.ab*q.c2)*inverse,(q.ab*q.bc-q.ac*q.b2)*inverse],
            [(q.bc*q.ac-q.ab*q.c2)*inverse,(q.a2*q.c2-q.ac*q.ac)*inverse,(q.ac*q.ab-q.a2*q.bc)*inverse],
            [(q.ab*q.bc-q.b2*q.ac)*inverse,(q.ab*q.ac-q.a2*q.bc)*inverse,(q.a2*q.b2-q.ab*q.ab)*inverse],
        ];
        Some(std::array::from_fn(|i| -(m[0][i]*q.ad+m[1][i]*q.bd+m[2][i]*q.cd)))
    }
}
