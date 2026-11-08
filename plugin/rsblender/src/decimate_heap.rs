// SPDX-FileCopyrightText: 2023 Blender Authors
// SPDX-License-Identifier: GPL-2.0-or-later
// BLI_heap.c ordering, with stable mesh edge indices instead of node pointers.
pub struct Heap { tree: Vec<usize>, positions: Vec<usize>, values: Vec<f32> }
impl Heap {
    pub fn new(edges: usize) -> Self {
        Self {tree:Vec::new(),positions:vec![usize::MAX;edges],values:vec![0.;edges]}
    }
    fn swap(&mut self, a:usize,b:usize) {
        self.tree.swap(a,b);
        self.positions[self.tree[a]]=a;
        self.positions[self.tree[b]]=b;
    }
    fn up(&mut self,mut i:usize) {
        while i>0 {
            let p=(i-1)>>1;
            if self.values[self.tree[p]]<self.values[self.tree[i]] {break;}
            self.swap(p,i); i=p;
        }
    }
    fn down(&mut self,mut i:usize) {
        loop {
            let l=i*2+1; let r=l+1; let mut smallest=i;
            if l<self.tree.len() && self.values[self.tree[l]]<self.values[self.tree[smallest]] {smallest=l;}
            if r<self.tree.len() && self.values[self.tree[r]]<self.values[self.tree[smallest]] {smallest=r;}
            if smallest==i {break;}
            self.swap(i,smallest);i=smallest;
        }
    }
    pub fn set(&mut self,edge:usize,value:f32) {
        let i=self.positions[edge];
        let old=self.values[edge];self.values[edge]=value;
        if i==usize::MAX {
            self.positions[edge]=self.tree.len();self.tree.push(edge);self.up(self.tree.len()-1);
        } else if value<old {self.up(i);} else if value>old {self.down(i);}
    }
    pub fn top(&self) -> Option<(usize,f32)> {self.tree.first().map(|&e|(e,self.values[e]))}
    pub fn pop(&mut self) -> Option<usize> {
        let first=*self.tree.first()?;
        let last=self.tree.pop().unwrap();
        self.positions[first]=usize::MAX;
        if !self.tree.is_empty() {self.tree[0]=last;self.positions[last]=0;self.down(0);}
        Some(first)
    }
    pub fn remove(&mut self,edge:usize) {
        let mut i=self.positions[edge];
        if i==usize::MAX {return;}
        while i>0 {let p=(i-1)>>1;self.swap(p,i);i=p;}
        self.pop();
    }
}
