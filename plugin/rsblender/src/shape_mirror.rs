//! Mirror an editable collision shape through an armature-local coordinate plane.
use glam::{DMat4,DVec3};
use pyo3::prelude::*;
use pyo3::exceptions::PyValueError;
use crate::mat::{from_row_major,to_row_major};

#[pyfunction]
pub fn mirror_body_shape(source_rest: Vec<f64>, target_rest: Vec<f64>, basis: Vec<f64>, axis: usize) -> PyResult<Vec<f64>> {
    if axis>2 { return Err(PyValueError::new_err("mirror axis must be X, Y or Z")); }
    let read=|v:&[f64]|from_row_major(v).filter(|m|m.is_finite()).ok_or_else(||PyValueError::new_err("expected a finite 4x4 transform"));
    let source=read(&source_rest)?;
    let target=read(&target_rest)?;
    let basis=read(&basis)?;
    if target.determinant()==0.0 {return Err(PyValueError::new_err("opposite bone has a singular rest transform"));}
    let mut signs=DVec3::ONE;signs[axis]=-1.0;
    let reflection=DMat4::from_scale(signs);
    // The second reflection preserves handedness. Boxes and spheres are
    // symmetric in their own coordinates, so it leaves their occupied volume unchanged.
    Ok(to_row_major(&(target.inverse()*reflection*source*basis*reflection)))
}
