//! In-process Rust VHDS worker. The vendor executable is used only by oracle tests.
use crate::vhds_grid::Grid;
use pyo3::{exceptions::PyValueError, prelude::*};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc, Mutex,
};
type Entry = (usize, usize, usize, f32);
type Output = Result<Vec<Entry>, String>;

#[pyclass]
pub struct VhdsNativeJob {
    names: Vec<String>,
    cancelled: Arc<AtomicBool>,
    output: Arc<Mutex<Option<Output>>>,
    progress: Arc<Mutex<String>>,
}

#[pymethods]
impl VhdsNativeJob {
    #[new]
    #[pyo3(signature=(meshes,names,heads,tails,resolution=128,loops=5,samples=64,influences=8,falloff=0.2,solidify=false,half_cpu=false))]
    fn new(
        meshes: Vec<(Vec<f32>, Vec<Vec<usize>>)>,
        names: Vec<String>,
        heads: Vec<f32>,
        tails: Vec<f32>,
        resolution: usize,
        loops: usize,
        samples: usize,
        influences: usize,
        falloff: f32,
        solidify: bool,
        half_cpu: bool,
    ) -> PyResult<Self> {
        if heads.len() != names.len() * 3
            || tails.len() != heads.len()
            || names.is_empty()
            || influences == 0
        {
            return Err(PyValueError::new_err(
                "Invalid VHDS bones or influence count",
            ));
        }
        let mut vertices = Vec::new();
        let mut triangles = Vec::new();
        let mut counts = Vec::new();
        for (coords, faces) in meshes {
            if coords.len() % 3 != 0 {
                return Err(PyValueError::new_err("Invalid VHDS coordinates"));
            }
            let offset = vertices.len();
            let count = coords.len() / 3;
            counts.push(count);
            vertices.extend(coords.chunks_exact(3).map(|v| [v[0], v[1], v[2]]));
            for face in faces {
                if face.len() < 3 || face.iter().any(|&v| v >= count) {
                    return Err(PyValueError::new_err("Invalid VHDS polygon"));
                }
                for j in 1..face.len() - 1 {
                    let t = [face[0] + offset, face[j] + offset, face[j + 1] + offset];
                    if !(close(vertices[t[0]], vertices[t[1]])
                        || close(vertices[t[1]], vertices[t[2]])
                        || close(vertices[t[2]], vertices[t[0]]))
                    {
                        triangles.push(t);
                    }
                }
            }
        }
        let bones: Vec<_> = heads
            .chunks_exact(3)
            .zip(tails.chunks_exact(3))
            .map(|(h, t)| [[h[0], h[1], h[2]], [t[0], t[1], t[2]]])
            .collect();
        let cancelled = Arc::new(AtomicBool::new(false));
        let output = Arc::new(Mutex::new(None));
        let progress = Arc::new(Mutex::new("Constructing VHDS grid".into()));
        let stop = cancelled.clone();
        let result = output.clone();
        let status = progress.clone();
        std::thread::Builder::new()
            .name("vhds-rust".into())
            .spawn(move || {
                let work = std::panic::catch_unwind(|| -> Output {
                    let mut grid = Grid::build_cancelable(
                        &vertices,
                        &triangles,
                        &bones,
                        resolution,
                        loops,
                        samples,
                        falloff,
                        solidify,
                        || stop.load(Ordering::Relaxed),
                    )
                    .map_err(|e| e.to_string())?;
                    let cores = std::thread::available_parallelism()
                        .map(|n| n.get())
                        .unwrap_or(1);
                    let cores = if half_cpu { (cores / 2).max(1) } else { cores };
                    for pass in 0..grid.resolution * grid.loops {
                        if stop.load(Ordering::Relaxed) {
                            return Err("VHDS cancelled".into());
                        }
                        *status.lock().unwrap() = format!("Diffuse pass: {pass}");
                        if (grid.diffuse_pass_threads(influences, cores) as f64) < 1e-5 {
                            break;
                        }
                    }
                    *status.lock().unwrap() = "Generating vertex weights".into();
                    let mut entries = Vec::new();
                    let mut offset = 0;
                    for (mesh, count) in counts.iter().enumerate() {
                        for vertex in 0..*count {
                            if stop.load(Ordering::Relaxed) {
                                return Err("VHDS cancelled".into());
                            }
                            for (bone, weight) in
                                grid.vertex_weights(vertices[offset + vertex], influences)
                            {
                                if weight > 0.0 {
                                    entries.push((mesh, vertex, bone, weight));
                                }
                            }
                        }
                        offset += count;
                    }
                    Ok(entries)
                })
                .unwrap_or_else(|_| Err("VHDS Rust worker failed".into()));
                if let Ok(mut slot) = result.lock() {
                    *slot = Some(work);
                }
            })
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        Ok(Self {
            names,
            cancelled,
            output,
            progress,
        })
    }
    fn poll(&self) -> Option<i32> {
        self.output
            .lock()
            .ok()
            .and_then(|o| o.as_ref().map(|r| if r.is_ok() { 0 } else { -1 }))
    }
    fn progress(&self) -> String {
        self.progress.lock().map(|p| p.clone()).unwrap_or_default()
    }
    fn result(&self) -> PyResult<(Vec<String>, Vec<Entry>)> {
        let output = self
            .output
            .lock()
            .map_err(|_| PyValueError::new_err("VHDS worker unavailable"))?;
        match output.as_ref() {
            Some(Ok(entries)) => Ok((self.names.clone(), entries.clone())),
            Some(Err(e)) => Err(PyValueError::new_err(e.clone())),
            None => Err(PyValueError::new_err("VHDS is still running")),
        }
    }
    fn cancel(&self) {
        self.cancelled.store(true, Ordering::Relaxed);
    }
}
impl Drop for VhdsNativeJob {
    fn drop(&mut self) {
        self.cancelled.store(true, Ordering::Relaxed);
    }
}

fn close(a: [f32; 3], b: [f32; 3]) -> bool {
    fn ordered(x: f32) -> i64 {
        let i = x.to_bits() as i32;
        if i < 0 {
            i32::MIN as i64 - i as i64
        } else {
            i as i64
        }
    }
    (0..3).all(|j| (ordered(a[j]) - ordered(b[j])).abs() <= 127)
}
