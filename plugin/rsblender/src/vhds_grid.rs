//! VHDS 3.5.3 grid computation recovered from the supplied unstripped Linux engine.
//! Cache input is a development oracle, not a replacement for grid construction.
use crate::vhds_geometry::{self as geometry, Mt19937, Point, Walk};
use std::io::{self, ErrorKind};

#[derive(Clone, Debug)]
pub struct Voxel {
    pub darkness: f32,
    pub conductivity: f32,
    pub triangles: Vec<u32>,
    pub bones: Vec<u32>,
    pub heat: [Vec<f32>; 2],
}

#[derive(Clone, Debug)]
pub struct Grid {
    pub resolution: usize,
    pub loops: usize,
    pub samples: usize,
    pub falloff: f32,
    pub solidify: bool,
    pub size: [usize; 3],
    pub origin: [f32; 3],
    pub spacing: f32,
    /// Next destination. The original toggles this after every completed pass.
    pub destination: usize,
    pub voxels: Vec<Voxel>,
    pub bone_count: usize,
}

fn invalid() -> io::Error {
    io::Error::new(ErrorKind::InvalidData, "invalid VHDS cache")
}
// Used by the standalone native-cache oracle, not by the production worker.
#[allow(dead_code)]
struct Reader<'a>(&'a [u8]);
#[allow(dead_code)]
impl Reader<'_> {
    fn take(&mut self, n: usize) -> io::Result<&[u8]> {
        if n > self.0.len() {
            return Err(invalid());
        }
        let (a, b) = self.0.split_at(n);
        self.0 = b;
        Ok(a)
    }
    fn u32(&mut self) -> io::Result<u32> {
        Ok(u32::from_le_bytes(self.take(4)?.try_into().unwrap()))
    }
    fn f32(&mut self) -> io::Result<f32> {
        let x = f32::from_bits(self.u32()?);
        if x.is_finite() {
            Ok(x)
        } else {
            Err(invalid())
        }
    }
    fn boolean(&mut self) -> io::Result<bool> {
        match self.take(1)?[0] {
            0 => Ok(false),
            1 => Ok(true),
            _ => Err(invalid()),
        }
    }
    fn indices(&mut self) -> io::Result<Vec<u32>> {
        let n = self.u32()? as usize;
        if n > self.0.len() / 4 {
            return Err(invalid());
        }
        (0..n).map(|_| self.u32()).collect()
    }
}

impl Grid {
    #[allow(dead_code)] // Standalone oracle entry point.
    pub fn build(
        vertices: &[Point],
        triangles: &[[usize; 3]],
        bones: &[[Point; 2]],
        resolution: usize,
        loops: usize,
        samples: usize,
        falloff: f32,
        solidify: bool,
    ) -> io::Result<Self> {
        Self::build_cancelable(
            vertices,
            triangles,
            bones,
            resolution,
            loops,
            samples,
            falloff,
            solidify,
            || false,
        )
    }
    pub fn build_cancelable(
        vertices: &[Point],
        triangles: &[[usize; 3]],
        bones: &[[Point; 2]],
        resolution: usize,
        loops: usize,
        samples: usize,
        falloff: f32,
        solidify: bool,
        cancel: impl Fn() -> bool,
    ) -> io::Result<Self> {
        if vertices.is_empty()
            || bones.is_empty()
            || !(2..=512).contains(&resolution)
            || loops == 0
            || samples == 0
            || !falloff.is_finite()
            || !(0.0..=1.0).contains(&falloff)
            || vertices
                .iter()
                .flatten()
                .chain(bones.iter().flatten().flatten())
                .any(|x| !x.is_finite())
            || triangles.iter().flatten().any(|&i| i >= vertices.len())
        {
            return Err(invalid());
        }
        let mut low = [f32::MAX; 3];
        // Preserve the source's positive MIN initialization, including negative-only meshes.
        let mut high = [f32::MIN_POSITIVE; 3];
        for p in vertices {
            for j in 0..3 {
                low[j] = low[j].min(p[j]);
                high[j] = high[j].max(p[j]);
            }
        }
        let extent = geometry::sub(high, low);
        let spacing = extent[0].max(extent[1]).max(extent[2]) / (resolution - 1) as f32;
        if !spacing.is_finite() || spacing <= 0.0 {
            return Err(invalid());
        }
        let inverse = 1.0 / spacing;
        let size = extent.map(|x| ((x * inverse).ceil() + 1.0) as usize);
        let count = size
            .iter()
            .try_fold(1usize, |a, &b| a.checked_mul(b))
            .ok_or_else(invalid)?;
        // Bound allocation before touching memory. A denser storage is needed for large grids.
        if count > 16_777_216 || count.checked_mul(bones.len()).unwrap_or(usize::MAX) > 134_217_728
        {
            return Err(io::Error::new(
                ErrorKind::InvalidInput,
                "VHDS grid exceeds working memory limit",
            ));
        }
        let origin = low.map(|x| x - spacing * 0.5);
        let empty = Voxel {
            darkness: 0.0,
            conductivity: 0.0,
            triangles: vec![],
            bones: vec![],
            heat: [vec![], vec![]],
        };
        let mut grid = Self {
            resolution,
            loops,
            samples,
            falloff,
            solidify,
            size,
            origin,
            spacing,
            destination: 1,
            voxels: vec![empty; count],
            bone_count: bones.len(),
        };
        for (t, indices) in triangles.iter().enumerate() {
            if cancel() {
                return Err(io::Error::new(ErrorKind::Interrupted, "VHDS cancelled"));
            }
            let tri = indices.map(|i| vertices[i]);
            let mut lo = [f32::MAX; 3];
            let mut hi = [f32::MIN_POSITIVE; 3];
            for p in tri {
                for j in 0..3 {
                    lo[j] = lo[j].min(p[j]);
                    hi[j] = hi[j].max(p[j]);
                }
            }
            let a: [usize; 3] = std::array::from_fn(|j| {
                (((lo[j] - origin[j]) * inverse) as usize).min(size[j] - 1)
            });
            let b: [usize; 3] = std::array::from_fn(|j| {
                (((hi[j] - origin[j]) * inverse) as usize).min(size[j] - 1)
            });
            for z in a[2]..=b[2] {
                for y in a[1]..=b[1] {
                    for x in a[0]..=b[0] {
                        let center = std::array::from_fn(|j| {
                            ([x, y, z][j] as f32 + 0.5) * spacing + origin[j]
                        });
                        if geometry::triangle_box(center, spacing * 0.5, tri) {
                            grid.voxels[(z * size[1] + y) * size[0] + x]
                                .triangles
                                .push(t as u32);
                        }
                    }
                }
            }
        }
        for (bone, &[head, tail]) in bones.iter().enumerate() {
            let d = geometry::sub(tail, head);
            let length = d[0].abs().max(d[1].abs()).max(d[2].abs());
            let direction = if length > 0.0 {
                d.map(|v| v / length)
            } else {
                [0.0; 3]
            };
            let a = std::array::from_fn(|j| (head[j] - origin[j]) * inverse);
            let b = std::array::from_fn(|j| (tail[j] - origin[j]) * inverse);
            let mut walk = Walk::new(a, b, direction);
            while let Some(i) = grid.index(walk.cell) {
                grid.voxels[i].bones.push(bone as u32);
                if walk.finished() {
                    break;
                }
                walk.advance();
            }
        }
        for p in vertices {
            let cell = std::array::from_fn(|j| ((p[j] - origin[j]) * inverse) as i32);
            if let Some(i) = grid.index(cell) {
                grid.voxels[i].darkness = 4.0;
            }
        }
        let mut random = Mt19937::new();
        for z in 0..size[2] {
            for y in 0..size[1] {
                if cancel() {
                    return Err(io::Error::new(ErrorKind::Interrupted, "VHDS cancelled"));
                }
                for x in 0..size[0] {
                    let i = (z * size[1] + y) * size[0] + x;
                    if grid.voxels[i].darkness > 1.0 || !grid.voxels[i].triangles.is_empty() {
                        if grid.voxels[i].darkness <= 1.0 {
                            grid.voxels[i].darkness = 4.0;
                        }
                        grid.voxels[i].conductivity = resolution as f32 * 0.0078125;
                    } else {
                        let (dark, conductivity) =
                            grid.sample_darkness([x, y, z], vertices, triangles, &mut random);
                        grid.voxels[i].darkness = dark;
                        grid.voxels[i].conductivity = conductivity;
                    }
                    if grid.voxels[i].darkness > 1.0 {
                        grid.voxels[i].heat = [vec![0.0; bones.len()], vec![0.0; bones.len()]];
                    }
                }
            }
        }
        Ok(grid)
    }

    fn index(&self, cell: [i32; 3]) -> Option<usize> {
        if (0..3).any(|j| cell[j] < 0 || cell[j] as usize >= self.size[j]) {
            None
        } else {
            Some(
                (cell[2] as usize * self.size[1] + cell[1] as usize) * self.size[0]
                    + cell[0] as usize,
            )
        }
    }

    fn sample_darkness(
        &self,
        xyz: [usize; 3],
        vertices: &[Point],
        triangles: &[[usize; 3]],
        random: &mut Mt19937,
    ) -> (f32, f32) {
        let center: Point =
            std::array::from_fn(|j| (xyz[j] as f32 + 0.5) * self.spacing + self.origin[j]);
        let [nx, ny, nz] = self.size;
        let ray_length = self.spacing * ((nz * nz + nx * nx + ny * ny) as f32).sqrt();
        let inverse = 1.0 / self.spacing;
        let mut darkness = 0.0f32;
        let mut distances = Vec::with_capacity(self.samples);
        let mut used = 0;
        for _ in 0..self.samples {
            let angle = random.unit() * std::f32::consts::TAU;
            let z = random.unit() * 2.0 - 1.0;
            let radial = (1.0 - z * z).sqrt();
            let (s, c) = angle.sin_cos();
            let mut direction = [radial * c, s * radial, z];
            let norm = (direction[0] * direction[0] + direction[1] * direction[1] + z * z).sqrt();
            if norm != 0.0 {
                let scale = 1.0 / norm;
                direction = direction.map(|v| v * scale);
            }
            let end: Point = std::array::from_fn(|j| direction[j] * ray_length + center[j]);
            let delta = geometry::sub(end, center);
            let maximum = delta[0].abs().max(delta[1].abs()).max(delta[2].abs());
            let mut walk = Walk::new(
                std::array::from_fn(|j| (center[j] - self.origin[j]) * inverse),
                std::array::from_fn(|j| (end[j] - self.origin[j]) * inverse),
                delta.map(|v| v / maximum),
            );
            let mut hit = None;
            while let Some(i) = self.index(walk.cell) {
                let mut closest = f32::MAX;
                let mut classification = 0.0;
                for &t in &self.voxels[i].triangles {
                    if let Some((distance, determinant)) = geometry::intersect(
                        center,
                        direction,
                        triangles[t as usize].map(|j| vertices[j]),
                    ) {
                        if distance >= 0.0 && distance < closest {
                            closest = distance;
                            classification = if self.solidify || determinant <= 0.0 {
                                2.0
                            } else {
                                1.0
                            };
                        }
                    }
                }
                if classification != 0.0 {
                    darkness += classification;
                    if closest > 0.0 {
                        hit = Some(closest);
                    }
                    break;
                }
                if walk.finished() {
                    break;
                }
                walk.advance();
            }
            let distance = hit.unwrap_or_else(|| {
                let d: Point = std::array::from_fn(|j| {
                    (walk.cell[j] as f32 + 0.5) * self.spacing + self.origin[j] - center[j]
                });
                (d[2] * d[2] + d[0] * d[0] + d[1] * d[1]).sqrt()
            });
            distances.push(distance);
            used += 1;
            if used == 16 && darkness * 0.0625 < 0.5 {
                break;
            }
        }
        distances.sort_by(f32::total_cmp);
        let keep = (used as f32 * 0.6 + 0.5) as usize;
        distances.resize(keep, 0.0);
        let total = distances.iter().fold(0.0f32, |a, &b| a + b);
        (darkness / used as f32, (total / keep as f32) / self.spacing)
    }

    #[allow(dead_code)] // Full-cache replay oracle.
    pub fn diffuse(&mut self, influences: usize) -> usize {
        for v in &mut self.voxels {
            for h in &mut v.heat {
                h.fill(0.0);
            }
        }
        self.destination = 1;
        for pass in 0..self.resolution * self.loops {
            if (self.diffuse_pass(influences) as f64) < 1e-5 {
                return pass + 1;
            }
        }
        self.resolution * self.loops
    }

    /// Recovered generate_weight_for_vertices: Gaussian accumulation over surface
    /// cells within two grid indices, descending influence selection and normalization.
    pub fn vertex_weights(&self, position: [f32; 3], influences: usize) -> Vec<(usize, f32)> {
        let nb = self.bone_count;
        let mut weights = vec![0.0f32; nb];
        let inverse = 1.0 / self.spacing;
        let center: [i32; 3] =
            std::array::from_fn(|j| ((position[j] - self.origin[j]) * inverse) as i32);
        let mut count = 0;
        for z in (center[2] - 2).max(0)..=(center[2] + 2).min(self.size[2] as i32 - 1) {
            for y in (center[1] - 2).max(0)..=(center[1] + 2).min(self.size[1] as i32 - 1) {
                for x in (center[0] - 2).max(0)..=(center[0] + 2).min(self.size[0] as i32 - 1) {
                    let v = &self.voxels
                        [(z as usize * self.size[1] + y as usize) * self.size[0] + x as usize];
                    if v.darkness <= 1.0 || v.triangles.is_empty() {
                        continue;
                    }
                    let delta: [f32; 3] = std::array::from_fn(|j| {
                        position[j] - (([x, y, z][j] as f32 + 0.5) * self.spacing + self.origin[j])
                    });
                    let distance =
                        (delta[1] * delta[1] + delta[0] * delta[0] + delta[2] * delta[2]).sqrt();
                    let negative_square = -(inverse * distance * inverse * distance);
                    let gaussian = (negative_square + negative_square).exp();
                    for (a, &b) in weights.iter_mut().zip(&v.heat[1 - self.destination]) {
                        *a += b * gaussian;
                    }
                    count += 1;
                }
            }
        }
        if count != 0 {
            let inv = 1.0 / count as f32;
            for w in &mut weights {
                *w *= inv;
            }
        }
        let mut ranked: Vec<_> = weights.into_iter().enumerate().collect();
        crate::vhds_sort::sort(&mut ranked);
        ranked.truncate(influences);
        let total = ranked.iter().fold(0.0f32, |a, b| a + b.1);
        if total > 0.0 {
            for w in &mut ranked {
                w.1 /= total;
            }
            return ranked;
        }
        let mut best = f32::MAX;
        let mut bone = 0;
        for (i, v) in self.voxels.iter().enumerate() {
            if v.bones.is_empty() {
                continue;
            }
            let xyz = [
                i % self.size[0],
                (i / self.size[0]) % self.size[1],
                i / (self.size[0] * self.size[1]),
            ];
            let d: [f32; 3] = std::array::from_fn(|j| {
                (xyz[j] as f32 + 0.5) * self.spacing + self.origin[j] - position[j]
            });
            let square = d[2] * d[2] + d[0] * d[0] + d[1] * d[1];
            if square < best {
                best = square;
                bone = v.bones[0] as usize;
            }
        }
        vec![(bone, 1.0)]
    }

    #[allow(dead_code)] // Purchased-engine cache reader for independent comparisons.
    pub fn from_cache(bytes: &[u8]) -> io::Result<Self> {
        let mut r = Reader(bytes);
        for _ in 0..2 {
            let n = r.u32()? as usize;
            r.take(n)?;
        }
        let resolution = r.u32()? as usize;
        let loops = r.u32()? as usize;
        let samples = r.u32()? as usize;
        let falloff = r.f32()?;
        let solidify = r.boolean()?;
        let size = [r.u32()? as usize, r.u32()? as usize, r.u32()? as usize];
        let origin = [r.f32()?, r.f32()?, r.f32()?];
        let spacing = r.f32()?;
        let destination = usize::from(r.boolean()?);
        let n = r.u32()? as usize;
        let count = size
            .iter()
            .try_fold(1usize, |a, &b| a.checked_mul(b))
            .ok_or_else(invalid)?;
        if n != count
            || n == 0
            || n > r.0.len() / 20
            || spacing <= 0.0
            || !(0.0..=1.0).contains(&falloff)
        {
            return Err(invalid());
        }
        let mut voxels = Vec::with_capacity(n);
        for _ in 0..n {
            let darkness = r.f32()?;
            let conductivity = r.f32()?;
            let triangles = r.indices()?;
            let bones = r.indices()?;
            let nb = r.u32()? as usize;
            if nb > r.0.len() / 8 {
                return Err(invalid());
            }
            let a: Vec<f32> = (0..nb).map(|_| r.f32()).collect::<io::Result<_>>()?;
            let b = (0..nb).map(|_| r.f32()).collect::<io::Result<_>>()?;
            if (nb > 0 && bones.iter().any(|&i| i as usize >= nb))
                || (darkness > 1.0 && conductivity <= 0.0)
            {
                return Err(invalid());
            }
            voxels.push(Voxel {
                darkness,
                conductivity,
                triangles,
                bones,
                heat: [a, b],
            });
        }
        if !r.0.is_empty() {
            return Err(invalid());
        }
        let nb = voxels.iter().map(|v| v.heat[0].len()).max().unwrap_or(0);
        if voxels
            .iter()
            .any(|v| v.darkness > 1.0 && v.heat[0].len() != nb)
        {
            return Err(invalid());
        }
        Ok(Self {
            resolution,
            loops,
            samples,
            falloff,
            solidify,
            size,
            origin,
            spacing,
            destination,
            voxels,
            bone_count: nb,
        })
    }

    /// Original diffuse_heat_in_range: 27-neighbor average, attenuation, top K,
    /// then add 0.5 per seed bone. Preserve f32 operation and neighbor order.
    #[allow(dead_code)] // Single-thread replay oracle.
    pub fn diffuse_pass(&mut self, influences: usize) -> f32 {
        self.diffuse_pass_threads(influences, 1)
    }

    fn diffuse_cell(&self, i: usize, influences: usize, weights: &mut Vec<f32>) {
        if self.voxels[i].darkness <= 1.0 {
            return;
        }
        let [nx, ny, nz] = self.size;
        let x = i % nx;
        let y = (i / nx) % ny;
        let z = i / (nx * ny);
        let dest = self.destination;
        weights.fill(0.0);
        let attenuation = (1.0 - self.falloff).powf(1.0 / self.voxels[i].conductivity);
        let mut count = 0;
        for zz in z.saturating_sub(1)..=(z + 1).min(nz - 1) {
            for yy in y.saturating_sub(1)..=(y + 1).min(ny - 1) {
                for xx in x.saturating_sub(1)..=(x + 1).min(nx - 1) {
                    let neighbor = &self.voxels[(zz * ny + yy) * nx + xx];
                    if neighbor.darkness > 1.0 {
                        for (a, b) in weights.iter_mut().zip(&neighbor.heat[1 - dest]) {
                            *a += *b;
                        }
                        count += 1;
                    }
                }
            }
        }
        if count != 0 {
            let inverse = 1.0 / count as f32;
            for w in weights.iter_mut() {
                *w = *w * inverse * attenuation;
            }
            if influences >= weights.len() {
                for w in weights.iter_mut() {
                    if *w <= f32::MIN_POSITIVE {
                        *w = 0.0;
                    }
                }
            } else {
                let mut selected = Vec::with_capacity(influences.min(weights.len()));
                for _ in 0..influences.min(weights.len()) {
                    let mut best = None;
                    let mut maximum = f32::MIN_POSITIVE;
                    for (j, &w) in weights.iter().enumerate() {
                        if w > maximum {
                            best = Some(j);
                            maximum = w;
                        }
                    }
                    if let Some(j) = best {
                        selected.push((j, maximum));
                        weights[j] = 0.0;
                    }
                }
                weights.fill(0.0);
                for (j, w) in selected {
                    weights[j] = w;
                }
            }
        }
        for &bone in &self.voxels[i].bones {
            weights[bone as usize] += 0.5;
        }
    }

    pub fn diffuse_pass_threads(&mut self, influences: usize, threads: usize) -> f32 {
        let dest = self.destination;
        let mut outputs: Vec<Vec<f32>> = self
            .voxels
            .iter_mut()
            .map(|v| std::mem::take(&mut v.heat[dest]))
            .collect();
        let threads = threads.max(1).min(self.size[2]);
        let chunk_size = outputs.len().div_ceil(threads);
        let grid = &*self;
        std::thread::scope(|scope| {
            for (chunk, values) in outputs.chunks_mut(chunk_size).enumerate() {
                scope.spawn(move || {
                    for (offset, weights) in values.iter_mut().enumerate() {
                        grid.diffuse_cell(chunk * chunk_size + offset, influences, weights);
                    }
                });
            }
        });
        for (voxel, weights) in self.voxels.iter_mut().zip(outputs) {
            voxel.heat[dest] = weights;
        }
        self.destination ^= 1;
        let mut sum = 0.0f32;
        let mut count = 0usize;
        for v in &self.voxels {
            if v.darkness <= 1.0 {
                continue;
            }
            for (&a, &b) in v.heat[0].iter().zip(&v.heat[1]) {
                if a != 0.0 || b != 0.0 {
                    let d = a - b;
                    sum += d * d;
                    count += 1;
                }
            }
        }
        (sum / count as f32).sqrt()
    }
}
