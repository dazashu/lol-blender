//! VHDS 3.5.3 wire protocol and process lifecycle.
//! Port of voxel_heat_diffuse_skinning.py (GPL-3.0). The vendor solver stays external.
use pyo3::{exceptions::PyValueError, prelude::*};
use std::io::Read;
use std::sync::{Arc, Mutex};
use std::{
    fs,
    path::Path,
    process::{Child, Command, Stdio},
};

fn err(e: impl std::fmt::Display) -> PyErr {
    PyValueError::new_err(e.to_string())
}

// CPython repr's exponent spelling and fixed/scientific boundary. Input coordinates
// are Blender's float32 results promoted to doubles, just as in the vendor wrapper.
fn repr(v: f64) -> String {
    if v == 0.0 {
        return if v.is_sign_negative() { "-0.0" } else { "0.0" }.into();
    }
    // std::fmt resolves some shortest-representation ties upward; CPython uses
    // ties-to-even. Ryu supplies the same tie rule (e.g. 866.63311767578125).
    let mut buffer = ryu::Buffer::new();
    let raw = buffer.format_finite(v.abs());
    let (mantissa, power) = raw.split_once('e').unwrap_or((raw, "0"));
    let point = mantissa.find('.').unwrap_or(mantissa.len());
    let mut exponent: i32 = power.parse::<i32>().unwrap() + point as i32 - 1;
    let mut digits = mantissa.replace('.', "");
    while digits.starts_with('0') {
        digits.remove(0);
        exponent -= 1;
    }
    while digits.ends_with('0') && digits.len() > 1 {
        digits.pop();
    }
    let sign = if v.is_sign_negative() { "-" } else { "" };
    if !(-4..16).contains(&exponent) {
        let mantissa = if digits.len() == 1 {
            digits
        } else {
            format!("{}.{}", &digits[..1], &digits[1..])
        };
        format!("{sign}{mantissa}e{exponent:+03}")
    } else {
        let point = exponent + 1;
        if point <= 0 {
            format!("{sign}0.{}{digits}", "0".repeat((-point) as usize))
        } else if point as usize >= digits.len() {
            format!(
                "{sign}{digits}{}.0",
                "0".repeat(point as usize - digits.len())
            )
        } else {
            format!(
                "{sign}{}.{}",
                &digits[..point as usize],
                &digits[point as usize..]
            )
        }
    }
}

#[pyfunction]
pub fn vhds_float(v: f64) -> PyResult<String> {
    if !v.is_finite() {
        return Err(err("VHDS input contains a non-finite coordinate"));
    }
    Ok(repr(v))
}

type Mesh = (Vec<f64>, Vec<Vec<usize>>);

#[pyfunction]
pub fn vhds_encode(
    meshes: Vec<Mesh>,
    names: Vec<String>,
    heads: Vec<f64>,
    tails: Vec<f64>,
) -> PyResult<(String, String)> {
    if heads.len() != names.len() * 3 || tails.len() != heads.len() || names.is_empty() {
        return Err(err(
            "Select at least one deforming bone with valid head and tail coordinates",
        ));
    }
    let mut mesh = String::from("# voxel heat diffuse mesh export.\n");
    let mut offset = 0;
    for (verts, faces) in meshes {
        if verts.len() % 3 != 0 {
            return Err(err("Invalid mesh coordinate buffer"));
        }
        for v in verts.chunks_exact(3) {
            mesh.push_str(&format!(
                "v,{},{},{}\n",
                vhds_float(v[0])?,
                vhds_float(v[1])?,
                vhds_float(v[2])?
            ));
        }
        for face in faces {
            if face.len() < 3 || face.iter().any(|&i| i >= verts.len() / 3) {
                return Err(err("Invalid VHDS polygon"));
            }
            mesh.push('f');
            for i in face {
                mesh.push_str(&format!(",{}", offset + i));
            }
            mesh.push('\n');
        }
        offset += verts.len() / 3;
    }
    let mut bones = String::from("# voxel heat diffuse bone export.\n");
    for (i, name) in names.iter().enumerate() {
        if name.contains(['\n', '\r']) {
            return Err(err("Bone names must not contain newlines"));
        }
        bones.push_str(&format!("b,{}", name.replace(',', "\\;")));
        for &v in heads[i * 3..i * 3 + 3]
            .iter()
            .chain(&tails[i * 3..i * 3 + 3])
        {
            bones.push_str(&format!(",{}", vhds_float(v)?));
        }
        bones.push('\n');
    }
    // Python open(..., 'w') uses CRLF on Windows.
    if cfg!(windows) {
        Ok((mesh.replace('\n', "\r\n"), bones.replace('\n', "\r\n")))
    } else {
        Ok((mesh, bones))
    }
}

type Weight = (usize, usize, usize, f32); // mesh, local vertex, bone, weight

#[pyfunction]
pub fn vhds_decode(data: &str, counts: Vec<usize>) -> PyResult<(Vec<String>, Vec<Weight>)> {
    let mut bones = Vec::new();
    let mut weights = Vec::new();
    let total: usize = counts.iter().sum();
    for (line_no, line) in data.lines().enumerate() {
        let tokens: Vec<_> = line.split(',').collect();
        let bad = || {
            err(format!(
                "Invalid VHDS output at line {}. Check the VHDS executable and bind again",
                line_no + 1
            ))
        };
        match tokens[0] {
            "b" => {
                if tokens.len() != 2 || tokens[1].is_empty() {
                    return Err(bad());
                }
                let name = tokens[1].replace("\\;", ",");
                if bones.contains(&name) {
                    return Err(bad());
                }
                bones.push(name);
            }
            "w" => {
                if tokens.len() != 4 {
                    return Err(bad());
                }
                let mut vertex: usize = tokens[1].parse().map_err(|_| bad())?;
                let bone: usize = tokens[2].parse().map_err(|_| bad())?;
                let weight: f32 = tokens[3].parse().map_err(|_| bad())?;
                if vertex >= total
                    || bone >= bones.len()
                    || !weight.is_finite()
                    || !(0.0..=1.0).contains(&weight)
                {
                    return Err(bad());
                }
                let mut mesh = 0;
                while vertex >= counts[mesh] {
                    vertex -= counts[mesh];
                    mesh += 1;
                }
                weights.push((mesh, vertex, bone, weight));
            }
            "" => (),
            s if s.starts_with('#') => (),
            _ => return Err(bad()),
        }
    }
    if bones.is_empty() || weights.is_empty() {
        return Err(err(
            "VHDS returned no weights. Check that the bones are inside the mesh and bind again",
        ));
    }
    Ok((bones, weights))
}

#[pyclass]
pub struct VhdsJob {
    child: Option<Child>,
    log: Arc<Mutex<String>>,
    reader: Option<std::thread::JoinHandle<()>>,
    directory: String,
    counts: Vec<usize>,
}

#[pymethods]
impl VhdsJob {
    #[new]
    #[allow(clippy::too_many_arguments)]
    fn new(
        executable: String,
        directory: String,
        mesh: String,
        bones: String,
        counts: Vec<usize>,
        resolution: u32,
        loops: u32,
        samples: u32,
        influence: u32,
        falloff: f64,
        solidify: bool,
        half_cpu: bool,
    ) -> PyResult<Self> {
        if !(32..=1024).contains(&resolution)
            || !(1..=9).contains(&loops)
            || !(32..=128).contains(&samples)
            || !(1..=128).contains(&influence)
            || !(0.01..=0.99).contains(&falloff)
        {
            return Err(err(
                "VHDS settings are outside the vendor's supported ranges",
            ));
        }
        let path = Path::new(&directory);
        fs::create_dir_all(path).map_err(err)?;
        // Each job has an exclusive temporary directory supplied by the bpy adapter.
        if path.join("untitled-weight.txt").exists() {
            return Err(err("VHDS job directory contains stale weights"));
        }
        fs::write(path.join("untitled-mesh.txt"), mesh).map_err(err)?;
        fs::write(path.join("untitled-bone.txt"), bones).map_err(err)?;
        let mut command = Command::new(executable);
        command
            .args([
                "untitled-mesh.txt",
                "untitled-bone.txt",
                "untitled-weight.txt",
            ])
            .args([
                resolution.to_string(),
                loops.to_string(),
                samples.to_string(),
                influence.to_string(),
                repr(falloff),
                if solidify { "y" } else { "n" }.into(),
                if half_cpu { "y" } else { "n" }.into(),
            ])
            .current_dir(path)
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000); // CREATE_NO_WINDOW
        }
        let mut child = command.spawn().map_err(err)?;
        let mut stdout = child.stdout.take().unwrap();
        let log = Arc::new(Mutex::new(String::new()));
        let thread_log = log.clone();
        let reader = std::thread::spawn(move || {
            let mut buffer = [0u8; 4096];
            while let Ok(n) = stdout.read(&mut buffer) {
                if n == 0 {
                    break;
                }
                if let Ok(mut log) = thread_log.lock() {
                    log.push_str(&String::from_utf8_lossy(&buffer[..n]));
                    if log.len() > 65536 {
                        let cut = log
                            .char_indices()
                            .find(|(i, _)| *i >= 32768)
                            .map(|(i, _)| i)
                            .unwrap_or(0);
                        log.drain(..cut);
                    }
                }
            }
        });
        Ok(Self {
            child: Some(child),
            log,
            reader: Some(reader),
            directory,
            counts,
        })
    }
    fn poll(&mut self) -> PyResult<Option<i32>> {
        match self.child.as_mut() {
            Some(child) => Ok(child
                .try_wait()
                .map_err(err)?
                .map(|s| s.code().unwrap_or(-1))),
            None => Ok(Some(-1)),
        }
    }
    fn progress(&self) -> String {
        self.log.lock().map(|s| s.clone()).unwrap_or_default()
    }
    fn result(&mut self) -> PyResult<(Vec<String>, Vec<Weight>)> {
        match self.poll()? {
            Some(0) => (),
            Some(code) => {
                return Err(err(format!(
                    "VHDS exited with code {code}. {}",
                    self.progress()
                )))
            }
            None => return Err(err("VHDS is still running")),
        }
        if let Some(reader) = self.reader.take() {
            let _ = reader.join();
        }
        let text = fs::read_to_string(Path::new(&self.directory).join("untitled-weight.txt"))
            .map_err(err)?;
        vhds_decode(&text, self.counts.clone())
    }
    fn cancel(&mut self) {
        if let Some(mut child) = self.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
        if let Some(reader) = self.reader.take() {
            let _ = reader.join();
        }
    }
}
impl Drop for VhdsJob {
    fn drop(&mut self) {
        self.cancel();
    }
}
