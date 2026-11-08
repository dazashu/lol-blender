/*!
Faithful decoder for the compressed `r3d2canm` container. The format stores each joint's
translation, rotation, and scale curves as a single time-sorted stream of sparse keyframes. A
stateful evaluator walks that stream with a four-keyframe hot window per joint per channel and fills
the gaps with Catmull-Rom interpolation, choosing uniform or timing-parametrized weights from the
clip's `UseKeyframeParametrization` flag. The optional jump-cache table seeds the hot window for any
time without scanning from the first frame, so seeking is constant-time per joint. Quaternion
keyframes are negated to the shortest path before interpolation. Decoding resamples the curves at the
clip's fps into the same explicit per-joint keyframes the uncompressed path produces; the byte-exact
contract is upheld by the retained source bytes, not by this decode.
*/

use std::io::{Read, Seek, SeekFrom};

use rs_io::ReaderExt;
use rs_math::{Quat, Vec3};

use crate::animation::{AnimFrame, AnimTrack};
use crate::quantized::{decompress_quat, decompress_vec3};
use crate::{Error, Result};

const FLAG_USE_KEYFRAME_PARAMETRIZATION: u32 = 1 << 2;
const SLERP_EPSILON: f32 = 1e-6;

#[derive(Clone, Copy)]
struct Frame {
    time: u16,
    joint_id_raw: u16,
    value: [u8; 6],
}

impl Frame {
    fn joint_id(self) -> usize {
        (self.joint_id_raw & 0x3FFF) as usize
    }

    fn transform_type(self) -> u16 {
        self.joint_id_raw >> 14
    }
}

pub(crate) struct Compressed {
    parametrized: bool,
    pub fps: f32,
    duration: f32,
    joint_count: usize,
    frame_count: usize,
    jump_cache_count: usize,
    translation_min: Vec3,
    translation_max: Vec3,
    scale_min: Vec3,
    scale_max: Vec3,
    joints: Vec<u32>,
    frames: Vec<Frame>,
    jump_caches: Vec<u8>,
}

impl Compressed {
    /** Reads the compressed header, joint-hash table, sparse frame stream, and jump-cache block.
    The reader must be positioned immediately after the 8-byte magic and 4-byte version; all section
    offsets are signed and relative to that point (`+12`), matching the uncompressed path. */
    pub(crate) fn from_reader<R: Read + Seek>(reader: &mut R) -> Result<Self> {
        let _resource_size = reader.read_u32()?;
        let _format_token = reader.read_u32()?;
        let flags = reader.read_u32()?;

        let joint_count = reader.read_i32()?;
        let frame_count = reader.read_i32()?;
        let jump_cache_count = reader.read_i32()?.max(0) as usize;

        let duration = reader.read_f32()?;
        let fps = reader.read_f32()?;

        for _ in 0..6 {
            let _error_metric = reader.read_f32()?;
        }

        let translation_min = reader.read_vec3()?;
        let translation_max = reader.read_vec3()?;
        let scale_min = reader.read_vec3()?;
        let scale_max = reader.read_vec3()?;

        let frames_offset = reader.read_i32()?;
        let jump_caches_offset = reader.read_i32()?;
        let joint_hashes_offset = reader.read_i32()?;

        if joint_count <= 0 || frame_count <= 0 {
            return Err(Error::Unsupported("compressed anm has no joints or frames"));
        }
        if frames_offset <= 0 || joint_hashes_offset <= 0 {
            return Err(Error::Unsupported("compressed anm missing data section"));
        }
        let joint_count = joint_count as usize;
        let frame_count = frame_count as usize;

        reader
            .seek(SeekFrom::Start(joint_hashes_offset as u64 + 12))
            .map_err(rs_io::Error::from)?;
        let mut joints = Vec::with_capacity(joint_count);
        for _ in 0..joint_count {
            joints.push(reader.read_u32()?);
        }

        reader
            .seek(SeekFrom::Start(frames_offset as u64 + 12))
            .map_err(rs_io::Error::from)?;
        let mut frames = Vec::with_capacity(frame_count);
        for _ in 0..frame_count {
            let time = reader.read_u16()?;
            let joint_id_raw = reader.read_u16()?;
            let value = reader.read_byte_array::<6>()?;
            frames.push(Frame {
                time,
                joint_id_raw,
                value,
            });
        }

        let jump_caches = if jump_caches_offset > 0 && jump_cache_count > 0 {
            let jump_frame_size = if frame_count < 0x1_0001 { 24 } else { 48 };
            let total = jump_cache_count
                .checked_mul(joint_count)
                .and_then(|n| n.checked_mul(jump_frame_size))
                .ok_or(Error::Unsupported(
                    "compressed anm jump cache size overflow",
                ))?;
            reader
                .seek(SeekFrom::Start(jump_caches_offset as u64 + 12))
                .map_err(rs_io::Error::from)?;
            reader.read_bytes(total)?
        } else {
            Vec::new()
        };

        Ok(Self {
            parametrized: flags & FLAG_USE_KEYFRAME_PARAMETRIZATION != 0,
            fps,
            duration,
            joint_count,
            frame_count,
            jump_cache_count,
            translation_min,
            translation_max,
            scale_min,
            scale_max,
            joints,
            frames,
            jump_caches,
        })
    }

    /** Resamples the compressed curves at `fps` over `duration`, producing one explicit keyframe per
    output frame for every joint. */
    pub(crate) fn bake(&self) -> Vec<AnimTrack> {
        let frame_duration = if self.fps != 0.0 { 1.0 / self.fps } else { 0.0 };
        let out_frames = ((self.duration * self.fps).round() as usize + 1).max(1);

        let mut tracks: Vec<AnimTrack> = self
            .joints
            .iter()
            .map(|&joint_hash| AnimTrack {
                joint_hash,
                frames: Vec::with_capacity(out_frames),
            })
            .collect();

        let mut evaluator = Evaluator::new(self);
        let clamp_to = self.duration.max(0.0);
        for f in 0..out_frames {
            let label_time = f as f32 * frame_duration;
            evaluator.seek(label_time.min(clamp_to));
            let compressed_time = compress_time(label_time.min(clamp_to), self.duration);
            for (joint_idx, track) in tracks.iter_mut().enumerate() {
                let (rotation, translation, scale) =
                    evaluator.hot[joint_idx].sample(compressed_time, self.parametrized);
                track.frames.push(AnimFrame {
                    time: label_time,
                    rotation,
                    translation,
                    scale,
                });
            }
        }

        tracks
    }
}

#[derive(Clone, Copy)]
pub(crate) struct QuatKey {
    pub(crate) time: u16,
    pub(crate) value: Quat,
}

impl Default for QuatKey {
    fn default() -> Self {
        Self {
            time: 0,
            value: Quat::IDENTITY,
        }
    }
}

#[derive(Clone, Copy)]
pub(crate) struct VecKey {
    pub(crate) time: u16,
    pub(crate) value: Vec3,
}

#[derive(Clone, Copy)]
struct JointHot {
    rotation: [QuatKey; 4],
    translation: [VecKey; 4],
    scale: [VecKey; 4],
}

impl JointHot {
    fn new() -> Self {
        Self {
            rotation: [QuatKey::default(); 4],
            translation: [VecKey {
                time: 0,
                value: Vec3::ZERO,
            }; 4],
            scale: [VecKey {
                time: 0,
                value: Vec3::ONE,
            }; 4],
        }
    }

    fn sample(&self, time: u16, parametrized: bool) -> (Quat, Vec3, Vec3) {
        let rotation = sample_quat(time, parametrized, &self.rotation);
        let translation = sample_vec3(time, parametrized, &self.translation);
        let scale = sample_vec3(time, parametrized, &self.scale);
        (rotation, translation, scale)
    }
}

struct Evaluator<'a> {
    anim: &'a Compressed,
    last_time: f32,
    cursor: usize,
    hot: Vec<JointHot>,
}

impl<'a> Evaluator<'a> {
    fn new(anim: &'a Compressed) -> Self {
        Self {
            anim,
            last_time: -1.0,
            cursor: 0,
            hot: vec![JointHot::new(); anim.joint_count],
        }
    }

    fn seek(&mut self, time: f32) {
        let time = time.clamp(0.0, self.anim.duration.max(0.0));
        let needs_reinit = self.last_time < 0.0
            || self.last_time > time
            || (self.anim.jump_cache_count > 0
                && self.anim.duration > 0.0
                && (time - self.last_time)
                    > self.anim.duration / self.anim.jump_cache_count as f32);

        if needs_reinit {
            self.init_from_jump_cache(time);
        }

        let compressed_time = compress_time(time, self.anim.duration);
        self.advance_cursor(compressed_time);
        self.last_time = time;
    }

    fn init_from_jump_cache(&mut self, time: f32) {
        if self.anim.jump_cache_count == 0 || self.anim.duration <= 0.0 {
            return;
        }

        let jump_cache_id = ((self.anim.jump_cache_count as f32 * (time / self.anim.duration))
            as usize)
            .min(self.anim.jump_cache_count - 1);

        self.cursor = 0;
        let frame_size = if self.anim.frame_count < 0x1_0001 {
            24
        } else {
            48
        };
        let cache_start = jump_cache_id * frame_size * self.anim.joint_count;
        for joint_idx in 0..self.anim.joint_count {
            let off = cache_start + joint_idx * frame_size;
            let Some(slice) = self.anim.jump_caches.get(off..off + frame_size) else {
                continue;
            };
            let (rotation_keys, translation_keys, scale_keys) = if frame_size == 24 {
                (
                    read_keys_u16(&slice[0..8]),
                    read_keys_u16(&slice[8..16]),
                    read_keys_u16(&slice[16..24]),
                )
            } else {
                (
                    read_keys_u32(&slice[0..16]),
                    read_keys_u32(&slice[16..32]),
                    read_keys_u32(&slice[32..48]),
                )
            };
            self.init_joint_hot(joint_idx, rotation_keys, translation_keys, scale_keys);
        }
        self.cursor += 1;
    }

    fn init_joint_hot(
        &mut self,
        joint_idx: usize,
        rotation_keys: [usize; 4],
        translation_keys: [usize; 4],
        scale_keys: [usize; 4],
    ) {
        let mut hot = JointHot::new();
        for (i, &frame_idx) in rotation_keys.iter().enumerate() {
            if let Some(frame) = self.anim.frames.get(frame_idx) {
                self.cursor = self.cursor.max(frame_idx);
                hot.rotation[i] = QuatKey {
                    time: frame.time,
                    value: decompress_quat(&frame.value),
                };
            }
        }
        for (i, &frame_idx) in translation_keys.iter().enumerate() {
            if let Some(frame) = self.anim.frames.get(frame_idx) {
                self.cursor = self.cursor.max(frame_idx);
                hot.translation[i] = VecKey {
                    time: frame.time,
                    value: decompress_vec3(
                        self.anim.translation_min,
                        self.anim.translation_max,
                        &frame.value,
                    ),
                };
            }
        }
        for (i, &frame_idx) in scale_keys.iter().enumerate() {
            if let Some(frame) = self.anim.frames.get(frame_idx) {
                self.cursor = self.cursor.max(frame_idx);
                hot.scale[i] = VecKey {
                    time: frame.time,
                    value: decompress_vec3(self.anim.scale_min, self.anim.scale_max, &frame.value),
                };
            }
        }
        align_shortest_path(&mut hot.rotation);
        self.hot[joint_idx] = hot;
    }

    fn advance_cursor(&mut self, compressed_time: u16) {
        while self.cursor < self.anim.frames.len() {
            let frame = self.anim.frames[self.cursor];
            let joint_idx = frame.joint_id();
            if joint_idx >= self.anim.joint_count {
                self.cursor += 1;
                continue;
            }
            let hot = &self.hot[joint_idx];
            let needs_update = match frame.transform_type() {
                0 => compressed_time >= hot.rotation[2].time,
                1 => compressed_time >= hot.translation[2].time,
                2 => compressed_time >= hot.scale[2].time,
                _ => {
                    self.cursor += 1;
                    continue;
                }
            };
            if !needs_update {
                break;
            }

            let hot = &mut self.hot[joint_idx];
            match frame.transform_type() {
                0 => {
                    hot.rotation[0] = hot.rotation[1];
                    hot.rotation[1] = hot.rotation[2];
                    hot.rotation[2] = hot.rotation[3];
                    hot.rotation[3] = QuatKey {
                        time: frame.time,
                        value: decompress_quat(&frame.value),
                    };
                    align_shortest_path(&mut hot.rotation);
                }
                1 => {
                    hot.translation[0] = hot.translation[1];
                    hot.translation[1] = hot.translation[2];
                    hot.translation[2] = hot.translation[3];
                    hot.translation[3] = VecKey {
                        time: frame.time,
                        value: decompress_vec3(
                            self.anim.translation_min,
                            self.anim.translation_max,
                            &frame.value,
                        ),
                    };
                }
                _ => {
                    hot.scale[0] = hot.scale[1];
                    hot.scale[1] = hot.scale[2];
                    hot.scale[2] = hot.scale[3];
                    hot.scale[3] = VecKey {
                        time: frame.time,
                        value: decompress_vec3(
                            self.anim.scale_min,
                            self.anim.scale_max,
                            &frame.value,
                        ),
                    };
                }
            }
            self.cursor += 1;
        }
    }
}

pub(crate) fn align_shortest_path(rotation: &mut [QuatKey; 4]) {
    let anchor = rotation[0].value;
    for key in rotation.iter_mut().skip(1) {
        if key.value.dot(anchor) < 0.0 {
            key.value = -key.value;
        }
    }
}

pub(crate) fn sample_quat(time: u16, parametrized: bool, keys: &[QuatKey; 4]) -> Quat {
    // Constant/absent channels use a repeated key. Do not evaluate a cubic
    // with a zero time interval: huge cancelling coefficients produce NaNs.
    if keys[2].time == keys[1].time {
        return keys[1].value;
    }
    let (amount, ease_in, ease_out) = if parametrized {
        keyframe_weights(time, keys[0].time, keys[1].time, keys[2].time, keys[3].time)
    } else {
        let span = keys[2].time.saturating_sub(keys[1].time);
        if span == 0 {
            return keys[1].value;
        }
        (
            time.saturating_sub(keys[1].time) as f32 / span as f32,
            0.5,
            0.5,
        )
    };
    let (m0, m1, m2, m3) = catmull_rom_weights(amount, ease_in, ease_out);
    Quat::from_xyzw(
        m0 * keys[0].value.x + m1 * keys[1].value.x + m2 * keys[2].value.x + m3 * keys[3].value.x,
        m0 * keys[0].value.y + m1 * keys[1].value.y + m2 * keys[2].value.y + m3 * keys[3].value.y,
        m0 * keys[0].value.z + m1 * keys[1].value.z + m2 * keys[2].value.z + m3 * keys[3].value.z,
        m0 * keys[0].value.w + m1 * keys[1].value.w + m2 * keys[2].value.w + m3 * keys[3].value.w,
    )
    .normalize()
}

pub(crate) fn sample_vec3(time: u16, parametrized: bool, keys: &[VecKey; 4]) -> Vec3 {
    if keys[2].time == keys[1].time {
        return keys[1].value;
    }
    let (amount, ease_in, ease_out) = if parametrized {
        keyframe_weights(time, keys[0].time, keys[1].time, keys[2].time, keys[3].time)
    } else {
        let span = keys[2].time.saturating_sub(keys[1].time);
        if span == 0 {
            return keys[1].value;
        }
        (
            time.saturating_sub(keys[1].time) as f32 / span as f32,
            0.5,
            0.5,
        )
    };
    let (m0, m1, m2, m3) = catmull_rom_weights(amount, ease_in, ease_out);
    m0 * keys[0].value + m1 * keys[1].value + m2 * keys[2].value + m3 * keys[3].value
}

fn keyframe_weights(time: u16, t0: u16, t1: u16, t2: u16, t3: u16) -> (f32, f32, f32) {
    let span = t2.saturating_sub(t1) as f32;
    let amount = time.saturating_sub(t1) as f32 / (span + SLERP_EPSILON);
    let ease_in = span / (t2.saturating_sub(t0) as f32 + SLERP_EPSILON);
    let ease_out = span / (t3.saturating_sub(t1) as f32 + SLERP_EPSILON);
    (amount, ease_in, ease_out)
}

fn catmull_rom_weights(amount: f32, ease_in: f32, ease_out: f32) -> (f32, f32, f32, f32) {
    let m0 = (((2.0 - amount) * amount) - 1.0) * (amount * ease_in);
    let m1 = ((((2.0 - ease_out) * amount) + (ease_out - 3.0)) * (amount * amount)) + 1.0;
    let m2 = ((((3.0 - ease_in * 2.0) + ((ease_in - 2.0) * amount)) * amount) + ease_in) * amount;
    let m3 = ((amount - 1.0) * amount) * (amount * ease_out);
    (m0, m1, m2, m3)
}

pub(crate) fn compress_time(time: f32, duration: f32) -> u16 {
    if duration <= 0.0 {
        return 0;
    }
    let scaled = (time / duration) * u16::MAX as f32;
    if scaled <= 0.0 {
        0
    } else if scaled >= u16::MAX as f32 {
        u16::MAX
    } else {
        scaled as u16
    }
}

fn read_keys_u16(b: &[u8]) -> [usize; 4] {
    [
        u16::from_le_bytes([b[0], b[1]]) as usize,
        u16::from_le_bytes([b[2], b[3]]) as usize,
        u16::from_le_bytes([b[4], b[5]]) as usize,
        u16::from_le_bytes([b[6], b[7]]) as usize,
    ]
}

fn read_keys_u32(b: &[u8]) -> [usize; 4] {
    [
        u32::from_le_bytes([b[0], b[1], b[2], b[3]]) as usize,
        u32::from_le_bytes([b[4], b[5], b[6], b[7]]) as usize,
        u32::from_le_bytes([b[8], b[9], b[10], b[11]]) as usize,
        u32::from_le_bytes([b[12], b[13], b[14], b[15]]) as usize,
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compress_time_clamps() {
        assert_eq!(compress_time(0.0, 2.0), 0);
        assert_eq!(compress_time(2.0, 2.0), u16::MAX);
        assert_eq!(compress_time(1.0, 0.0), 0);
        assert_eq!(compress_time(-1.0, 2.0), 0);
    }

    #[test]
    fn uniform_catmull_hits_linear_midpoint() {
        let keys = [
            VecKey {
                time: 0,
                value: Vec3::new(0.0, 0.0, 0.0),
            },
            VecKey {
                time: 100,
                value: Vec3::new(1.0, 0.0, 0.0),
            },
            VecKey {
                time: 200,
                value: Vec3::new(2.0, 0.0, 0.0),
            },
            VecKey {
                time: 300,
                value: Vec3::new(3.0, 0.0, 0.0),
            },
        ];
        let mid = sample_vec3(150, false, &keys);
        assert!((mid.x - 1.5).abs() < 1e-4, "mid={mid:?}");
    }

    #[test]
    fn uniform_catmull_returns_p1_on_zero_span() {
        let keys = [VecKey {
            time: 0,
            value: Vec3::splat(7.0),
        }; 4];
        assert_eq!(sample_vec3(0, false, &keys), Vec3::splat(7.0));
    }
}

#[cfg(test)]
mod regression_tests {
    use super::*;

    #[test]
    fn constant_parametrized_channels_remain_constant() {
        let q = [QuatKey {time: 0,value: Quat::IDENTITY}; 4];
        let v = [VecKey {time: 0,value: Vec3::new(1.0,2.0,3.0)};4];
        for t in [0,1,655,32767,65535] {
            assert_eq!(sample_quat(t,true,&q),Quat::IDENTITY);
            assert_eq!(sample_vec3(t,true,&v),v[0].value);
        }
    }

    #[test]
    fn missing_channel_sentinel_does_not_jump_cursor_past_stream() {
        let anim = Compressed {
            parametrized:true,fps:30.0,duration:1.0,joint_count:1,frame_count:1,jump_cache_count:0,
            translation_min:Vec3::ZERO,translation_max:Vec3::ONE,scale_min:Vec3::ONE,scale_max:Vec3::ONE,
            joints:vec![1],frames:vec![Frame{time:0,joint_id_raw:0,value:[0;6]}],jump_caches:vec![],
        };
        let mut evaluator=Evaluator::new(&anim);
        evaluator.init_joint_hot(0,[0;4],[65535;4],[65535;4]);
        assert_eq!(evaluator.cursor,0);
        assert_eq!(evaluator.hot[0].sample(100,true).2,Vec3::ONE);
    }
}
