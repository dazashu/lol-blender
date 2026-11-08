use std::io::{Read, Write};

use rs_math::{Quat, Rgba, Vec2, Vec3, Vec4};

use crate::Result;

/// Reading helpers layered over any [`std::io::Read`]; every integer is little-endian.
pub trait ReaderExt: Read {
    fn read_u8(&mut self) -> Result<u8> {
        Ok(self.read_byte_array::<1>()?[0])
    }

    fn read_i8(&mut self) -> Result<i8> {
        Ok(self.read_byte_array::<1>()?[0] as i8)
    }

    fn read_u16(&mut self) -> Result<u16> {
        Ok(u16::from_le_bytes(self.read_byte_array()?))
    }

    fn read_i16(&mut self) -> Result<i16> {
        Ok(i16::from_le_bytes(self.read_byte_array()?))
    }

    fn read_u32(&mut self) -> Result<u32> {
        Ok(u32::from_le_bytes(self.read_byte_array()?))
    }

    fn read_i32(&mut self) -> Result<i32> {
        Ok(i32::from_le_bytes(self.read_byte_array()?))
    }

    fn read_u64(&mut self) -> Result<u64> {
        Ok(u64::from_le_bytes(self.read_byte_array()?))
    }

    fn read_i64(&mut self) -> Result<i64> {
        Ok(i64::from_le_bytes(self.read_byte_array()?))
    }

    fn read_f32(&mut self) -> Result<f32> {
        Ok(f32::from_le_bytes(self.read_byte_array()?))
    }

    fn read_f64(&mut self) -> Result<f64> {
        Ok(f64::from_le_bytes(self.read_byte_array()?))
    }

    fn read_bool(&mut self) -> Result<bool> {
        Ok(self.read_u8()? != 0)
    }

    fn read_byte_array<const N: usize>(&mut self) -> Result<[u8; N]> {
        let mut buf = [0u8; N];
        self.read_exact(&mut buf)?;
        Ok(buf)
    }

    fn read_bytes(&mut self, n: usize) -> Result<Vec<u8>> {
        let mut buf = vec![0u8; n];
        self.read_exact(&mut buf)?;
        Ok(buf)
    }

    fn read_string_u16(&mut self) -> Result<String> {
        let len = self.read_u16()? as usize;
        let bytes = self.read_bytes(len)?;
        Ok(String::from_utf8(bytes)?)
    }

    fn read_string_u32(&mut self) -> Result<String> {
        let len = self.read_u32()? as usize;
        let bytes = self.read_bytes(len)?;
        Ok(String::from_utf8(bytes)?)
    }

    fn read_cstring(&mut self) -> Result<String> {
        let mut bytes = Vec::new();
        loop {
            let b = self.read_u8()?;
            if b == 0 {
                break;
            }
            bytes.push(b);
        }
        Ok(String::from_utf8(bytes)?)
    }

    fn read_fixed_string<const N: usize>(&mut self) -> Result<String> {
        let buf = self.read_byte_array::<N>()?;
        let end = buf.iter().position(|&b| b == 0).unwrap_or(N);
        Ok(String::from_utf8(buf[..end].to_vec())?)
    }

    fn read_vec2(&mut self) -> Result<Vec2> {
        Ok(Vec2::new(self.read_f32()?, self.read_f32()?))
    }

    fn read_vec3(&mut self) -> Result<Vec3> {
        Ok(Vec3::new(
            self.read_f32()?,
            self.read_f32()?,
            self.read_f32()?,
        ))
    }

    fn read_vec4(&mut self) -> Result<Vec4> {
        Ok(Vec4::new(
            self.read_f32()?,
            self.read_f32()?,
            self.read_f32()?,
            self.read_f32()?,
        ))
    }

    fn read_quat(&mut self) -> Result<Quat> {
        Ok(Quat::from_array([
            self.read_f32()?,
            self.read_f32()?,
            self.read_f32()?,
            self.read_f32()?,
        ]))
    }

    fn read_mtx44(&mut self) -> Result<[f32; 16]> {
        let mut m = [0.0f32; 16];
        for slot in &mut m {
            *slot = self.read_f32()?;
        }
        Ok(m)
    }

    fn read_rgba(&mut self) -> Result<Rgba> {
        Ok(Rgba::new(
            self.read_u8()?,
            self.read_u8()?,
            self.read_u8()?,
            self.read_u8()?,
        ))
    }
}

impl<R: Read + ?Sized> ReaderExt for R {}

/// Writing helpers layered over any [`std::io::Write`]; every integer is little-endian.
pub trait WriterExt: Write {
    fn write_u8(&mut self, v: u8) -> Result<()> {
        self.write_bytes(&v.to_le_bytes())
    }

    fn write_i8(&mut self, v: i8) -> Result<()> {
        self.write_bytes(&v.to_le_bytes())
    }

    fn write_u16(&mut self, v: u16) -> Result<()> {
        self.write_bytes(&v.to_le_bytes())
    }

    fn write_i16(&mut self, v: i16) -> Result<()> {
        self.write_bytes(&v.to_le_bytes())
    }

    fn write_u32(&mut self, v: u32) -> Result<()> {
        self.write_bytes(&v.to_le_bytes())
    }

    fn write_i32(&mut self, v: i32) -> Result<()> {
        self.write_bytes(&v.to_le_bytes())
    }

    fn write_u64(&mut self, v: u64) -> Result<()> {
        self.write_bytes(&v.to_le_bytes())
    }

    fn write_i64(&mut self, v: i64) -> Result<()> {
        self.write_bytes(&v.to_le_bytes())
    }

    fn write_f32(&mut self, v: f32) -> Result<()> {
        self.write_bytes(&v.to_le_bytes())
    }

    fn write_f64(&mut self, v: f64) -> Result<()> {
        self.write_bytes(&v.to_le_bytes())
    }

    fn write_bool(&mut self, v: bool) -> Result<()> {
        self.write_u8(v as u8)
    }

    fn write_bytes(&mut self, bytes: &[u8]) -> Result<()> {
        self.write_all(bytes)?;
        Ok(())
    }

    fn write_string_u16(&mut self, s: &str) -> Result<()> {
        self.write_u16(s.len() as u16)?;
        self.write_bytes(s.as_bytes())
    }

    fn write_string_u32(&mut self, s: &str) -> Result<()> {
        self.write_u32(s.len() as u32)?;
        self.write_bytes(s.as_bytes())
    }

    fn write_cstring(&mut self, s: &str) -> Result<()> {
        self.write_bytes(s.as_bytes())?;
        self.write_u8(0)
    }

    fn write_vec2(&mut self, v: Vec2) -> Result<()> {
        let [x, y] = v.to_array();
        self.write_f32(x)?;
        self.write_f32(y)
    }

    fn write_vec3(&mut self, v: Vec3) -> Result<()> {
        let [x, y, z] = v.to_array();
        self.write_f32(x)?;
        self.write_f32(y)?;
        self.write_f32(z)
    }

    fn write_vec4(&mut self, v: Vec4) -> Result<()> {
        let [x, y, z, w] = v.to_array();
        self.write_f32(x)?;
        self.write_f32(y)?;
        self.write_f32(z)?;
        self.write_f32(w)
    }

    fn write_quat(&mut self, q: Quat) -> Result<()> {
        let [x, y, z, w] = q.to_array();
        self.write_f32(x)?;
        self.write_f32(y)?;
        self.write_f32(z)?;
        self.write_f32(w)
    }

    fn write_mtx44(&mut self, m: &[f32; 16]) -> Result<()> {
        for &v in m {
            self.write_f32(v)?;
        }
        Ok(())
    }

    fn write_rgba(&mut self, c: Rgba) -> Result<()> {
        self.write_u8(c.r)?;
        self.write_u8(c.g)?;
        self.write_u8(c.b)?;
        self.write_u8(c.a)
    }
}

impl<W: Write + ?Sized> WriterExt for W {}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    #[test]
    fn round_trip_u32() {
        let mut c = Cursor::new(Vec::new());
        c.write_u32(0xDEAD_BEEF).unwrap();
        c.set_position(0);
        assert_eq!(c.read_u32().unwrap(), 0xDEAD_BEEF);
    }

    #[test]
    fn round_trip_f32() {
        let mut c = Cursor::new(Vec::new());
        c.write_f32(1.5).unwrap();
        c.set_position(0);
        assert_eq!(c.read_f32().unwrap(), 1.5);
    }

    #[test]
    fn round_trip_string_u16() {
        let mut c = Cursor::new(Vec::new());
        c.write_string_u16("ritoshark").unwrap();
        c.set_position(0);
        assert_eq!(c.read_string_u16().unwrap(), "ritoshark");
    }

    #[test]
    fn round_trip_vec3() {
        let v = Vec3::new(1.0, -2.0, 3.5);
        let mut c = Cursor::new(Vec::new());
        c.write_vec3(v).unwrap();
        c.set_position(0);
        assert_eq!(c.read_vec3().unwrap(), v);
    }
}
