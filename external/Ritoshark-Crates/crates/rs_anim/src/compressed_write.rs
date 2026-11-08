/*!
Writer for the compressed `r3d2canm` container, a port of lol_maya's `ANM.write_compressed` using
this crate's decoder math ([`crate::compressed`]).

Each channel is decimated to the keys its uniform Catmull-Rom curve needs to stay within tolerance,
then quantised (48-bit quaternions, 16 bits per vector component). Channels at the game default
are not written. Constants come from Riot's shipped files.
*/

use std::io::{Cursor, Seek, SeekFrom};

use rs_io::WriterExt;
use rs_math::{Quat, Vec3};

use crate::animation::Animation;
use crate::compressed::{
    align_shortest_path, compress_time, sample_quat, sample_vec3, QuatKey, VecKey,
};
use crate::quantized::{compress_quat, compress_vec3};
use crate::{Error, Result};

/// Ignored by the decoder. The most common value in Riot's files.
const ERROR_METRIC: [f32; 6] = [2.0, 10.0, 2.0, 10.0, 0.01, 0.2];

/// The most common shipped value without bit 2 (keyframe parametrization): uniform weights.
const FLAGS: u32 = 3;

/// Jump-cache index for an absent channel (the decoder keeps the default).
const ABSENT: u16 = u16::MAX;

/// Joint ids are 14 bits.
const MAX_JOINTS: usize = 1 << 14;

/// Header size. File offsets are stored minus 12.
const HEADER_SIZE: usize = 128;

/// Decimation tolerances: per quaternion component, game units, scale factor.
///
/// The defaults (lol_maya's) give a file about Riot's size with smaller rotation error.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct CompressionTolerances {
    pub rotation: f32,
    pub translation: f32,
    pub scale: f32,
}

impl Default for CompressionTolerances {
    fn default() -> Self {
        Self {
            rotation: 0.002,
            translation: 0.1,
            scale: 0.002,
        }
    }
}

/// One channel's values per frame, padded to four components.
type Values = Vec<[f32; 4]>;

#[derive(Clone, Copy)]
struct Entry {
    time: u16,
    id: u16,
    raw: [u8; 6],
}

fn channel_error(got: [f32; 4], want: [f32; 4], size: usize) -> f32 {
    let diff = |sign: f32| {
        (0..size)
            .map(|c| (got[c] - sign * want[c]).abs())
            .fold(0.0f32, f32::max)
    };
    if size == 4 {
        // a quaternion and its negation are the same rotation
        diff(1.0).min(diff(-1.0))
    } else {
        diff(1.0)
    }
}

/// The value the decoder reconstructs at `time` from the four keys in `window`.
fn sample_window(
    time: u16,
    window: [usize; 4],
    times: &[u16],
    values: &Values,
    size: usize,
) -> [f32; 4] {
    if size == 4 {
        let mut keys = window.map(|i| QuatKey {
            time: times[i],
            value: Quat::from_array(values[i]),
        });
        align_shortest_path(&mut keys);
        sample_quat(time, false, &keys).to_array()
    } else {
        let keys = window.map(|i| VecKey {
            time: times[i],
            value: Vec3::new(values[i][0], values[i][1], values[i][2]),
        });
        let v = sample_vec3(time, false, &keys);
        [v.x, v.y, v.z, 0.0]
    }
}

/// The keyframes whose Catmull-Rom curve stays within `tolerance`. Greedy: start from both ends
/// and add the worst frame until everything is within tolerance.
fn decimate(times: &[u16], values: &Values, size: usize, tolerance: f32) -> Vec<usize> {
    let count = values.len();
    if count <= 1 {
        return (0..count).collect();
    }
    let first = values[0];
    if values
        .iter()
        .all(|&v| channel_error(v, first, size) <= tolerance)
    {
        return vec![0]; // constant channel, one key is the whole curve
    }

    let mut keys = vec![0, count - 1];
    let mut errors = vec![0.0f32; count];

    // re-score only the frames near the new key
    let refresh = |keys: &[usize], errors: &mut [f32], key_lo: isize, key_hi: isize| {
        let last = keys.len() - 1;
        let key_lo = key_lo.max(0) as usize;
        let key_hi = key_hi.clamp(0, last as isize) as usize;
        let start = keys[key_lo];
        let stop = if key_hi >= last {
            count - 1
        } else {
            keys[key_hi]
        };
        let mut k = key_lo;
        for f in start..=stop {
            while k < last && keys[k + 1] <= f {
                k += 1;
            }
            // the decoder's window: first and last keys repeat at the ends
            let window = [
                keys[k.saturating_sub(1)],
                keys[k],
                keys[(k + 1).min(last)],
                keys[(k + 2).min(last)],
            ];
            errors[f] = channel_error(
                sample_window(times[f], window, times, values, size),
                values[f],
                size,
            );
        }
    };

    refresh(&keys, &mut errors, 0, keys.len() as isize - 1);
    while keys.len() < count {
        let mut worst = -1.0f32;
        let mut worst_frame = None;
        for (f, &e) in errors.iter().enumerate() {
            if e > worst {
                worst = e;
                worst_frame = Some(f);
            }
        }
        let Some(worst_frame) = worst_frame else {
            break;
        };
        if worst <= tolerance {
            break;
        }
        let Err(at) = keys.binary_search(&worst_frame) else {
            break; // already a key and still worst: nothing more to gain
        };
        keys.insert(at, worst_frame);
        refresh(&keys, &mut errors, at as isize - 4, at as isize + 4);
    }
    keys
}

impl Animation {
    /// Encodes this animation as a compressed `r3d2canm` with the default tolerances.
    pub fn to_compressed_bytes(&self) -> Result<Vec<u8>> {
        self.to_compressed_bytes_with(CompressionTolerances::default())
    }

    /// Encodes this animation as a compressed `r3d2canm`.
    ///
    /// Every track holds one key per frame (frame `f` at `f / fps`). Fails with more than 16383
    /// joints or 65535 keys after decimation.
    pub fn to_compressed_bytes_with(&self, tolerances: CompressionTolerances) -> Result<Vec<u8>> {
        self.to_compressed_bytes_per_track(tolerances, &[])
    }

    /// Explicit per-track error bounds for collision-sensitive channels.
    pub fn to_compressed_bytes_per_track(&self, tolerances: CompressionTolerances,
        overrides: &[(u32, CompressionTolerances)]) -> Result<Vec<u8>> {
        let track_count = self.tracks.len();
        if track_count == 0 {
            return Err(Error::Unsupported(
                "compressed anm needs at least one track",
            ));
        }
        if track_count >= MAX_JOINTS {
            return Err(Error::Unsupported(
                "compressed anm holds at most 16383 joints",
            ));
        }
        if !(self.fps > 0.0) {
            return Err(Error::Unsupported("compressed anm needs a positive fps"));
        }
        let frame_count = self.tracks[0].frames.len();
        if frame_count == 0 {
            return Err(Error::Unsupported(
                "compressed anm needs at least one frame",
            ));
        }
        if self.tracks.iter().any(|t| t.frames.len() != frame_count) {
            return Err(Error::Unsupported(
                "compressed anm needs every track to share a frame count",
            ));
        }

        // frame times computed like the decoder's bake
        let frame_duration = 1.0 / self.fps;
        let duration = (frame_count - 1) as f32 * frame_duration;
        let times: Vec<u16> = (0..frame_count)
            .map(|f| compress_time((f as f32 * frame_duration).min(duration), duration))
            .collect();

        // quantisation ranges, component wise over the whole clip
        let mut tmin = Vec3::splat(f32::INFINITY);
        let mut tmax = Vec3::splat(f32::NEG_INFINITY);
        let mut smin = tmin;
        let mut smax = tmax;
        for frame in self.tracks.iter().flat_map(|t| &t.frames) {
            tmin = tmin.min(frame.translation);
            tmax = tmax.max(frame.translation);
            smin = smin.min(frame.scale);
            smax = smax.max(frame.scale);
        }

        // the game's default per channel (JointHot::new); such channels are not written
        let defaults: [([f32; 4], usize, f32); 3] = [
            ([0.0, 0.0, 0.0, 1.0], 4, tolerances.rotation),
            ([0.0, 0.0, 0.0, 0.0], 3, tolerances.translation),
            ([1.0, 1.0, 1.0, 0.0], 3, tolerances.scale),
        ];

        // per joint, per channel (rotation, translation, scale): decimate, then quantise
        let mut channels: Vec<Vec<(u16, [u8; 6])>> = Vec::with_capacity(track_count * 3);
        for track in &self.tracks {
            let streams: [Values; 3] = [
                track.frames.iter().map(|p| p.rotation.to_array()).collect(),
                track
                    .frames
                    .iter()
                    .map(|p| {
                        let v = p.translation;
                        [v.x, v.y, v.z, 0.0]
                    })
                    .collect(),
                track
                    .frames
                    .iter()
                    .map(|p| {
                        let v = p.scale;
                        [v.x, v.y, v.z, 0.0]
                    })
                    .collect(),
            ];
            let bounds=overrides.iter().find(|(hash,_)| *hash==track.joint_hash)
                .map(|(_,bounds)| *bounds).unwrap_or(tolerances);
            for (kind, values) in streams.iter().enumerate() {
                let (default, size, _) = defaults[kind];
                let tolerance=[bounds.rotation,bounds.translation,bounds.scale][kind];
                let frames = decimate(&times, values, size, tolerance);
                if frames.len() == 1 && channel_error(values[frames[0]], default, size) <= tolerance
                {
                    channels.push(Vec::new()); // never moves off the default
                    continue;
                }
                let mut keys: Vec<(u16, [u8; 6])> = frames
                    .iter()
                    .map(|&f| {
                        let v = values[f];
                        let raw = match kind {
                            0 => compress_quat(Quat::from_array(v)),
                            1 => compress_vec3(tmin, tmax, Vec3::new(v[0], v[1], v[2])),
                            _ => compress_vec3(smin, smax, Vec3::new(v[0], v[1], v[2])),
                        };
                        (times[f], raw)
                    })
                    .collect();
                // repeat the last key, like Riot, so the last segment has a key beyond it
                if keys.len() > 1 {
                    keys.push(*keys.last().unwrap());
                }
                channels.push(keys);
            }
        }
        // an empty stream is rejected by the reader: keep one key
        if channels.iter().all(Vec::is_empty) {
            let q = self.tracks[0].frames[0].rotation;
            channels[0].push((times[0], compress_quat(q)));
        }
        // one frame: duration 0, the decoder skips the jump cache, so write each key four times
        if frame_count == 1 {
            for keys in channels.iter_mut().filter(|k| !k.is_empty()) {
                *keys = vec![keys[0]; 4];
            }
        }

        // The stream is in decoder consumption order: one cursor, an entry is taken when its
        // channel needs it. It starts with each channel's first three keys, which the jump cache
        // seeds as (i0, i0, i1, i2); constant channels (i0 x4), absent ones 65535.
        let mut stream: Vec<Entry> = Vec::new();
        let mut seeds = vec![[ABSENT; 4]; track_count * 3];
        let mut later: Vec<(u16, Entry)> = Vec::new();
        for (c, keys) in channels.iter().enumerate() {
            if keys.is_empty() {
                continue;
            }
            let id = (c / 3) as u16 | (((c % 3) as u16) << 14);
            let start = stream.len();
            let head = keys.len().min(3);
            stream.extend(
                keys[..head]
                    .iter()
                    .map(|&(time, raw)| Entry { time, id, raw }),
            );
            let last = start + head - 1;
            let index = [start, start, (start + 1).min(last), (start + 2).min(last)];
            seeds[c] = index.map(|i| i.min(ABSENT as usize) as u16);

            // each later key is needed once time reaches the window's third key
            let mut hot = index.map(|i| keys[i - start].0);
            for &(time, raw) in &keys[head..] {
                later.push((hot[2], Entry { time, id, raw }));
                hot = [hot[1], hot[2], hot[3], time];
            }
        }
        // sorting by that threshold (stable) gives the consumption order at any time
        later.sort_by_key(|&(threshold, _)| threshold);
        stream.extend(later.into_iter().map(|(_, e)| e));

        if stream.len() > ABSENT as usize {
            return Err(Error::Unsupported(
                "compressed anm holds at most 65535 keys after decimation",
            ));
        }

        let mut buf = Cursor::new(Vec::with_capacity(
            HEADER_SIZE + stream.len() * 10 + track_count * 28,
        ));
        buf.write_bytes(b"r3d2canm")?;
        buf.write_u32(1)?; // version
        buf.write_u32(0)?; // resource size, patched last
        buf.write_bytes(b"canm")?; // format token
        buf.write_u32(FLAGS)?;
        buf.write_i32(track_count as i32)?;
        buf.write_i32(stream.len() as i32)?;
        buf.write_i32(1)?; // jump caches
        buf.write_f32(duration)?;
        buf.write_f32(self.fps)?;
        for v in ERROR_METRIC {
            buf.write_f32(v)?;
        }
        for v in [tmin, tmax, smin, smax] {
            buf.write_vec3(v)?;
        }
        let frames_offset = HEADER_SIZE;
        let jump_caches_offset = frames_offset + stream.len() * 10;
        let joint_hashes_offset = jump_caches_offset + track_count * 24;
        buf.write_i32((frames_offset - 12) as i32)?;
        buf.write_i32((jump_caches_offset - 12) as i32)?;
        buf.write_i32((joint_hashes_offset - 12) as i32)?;

        for e in &stream {
            buf.write_u16(e.time)?;
            buf.write_u16(e.id)?;
            buf.write_bytes(&e.raw)?;
        }
        // 4 frame indices per channel, rotation then translation then scale
        for seed in &seeds {
            for i in seed {
                buf.write_u16(*i)?;
            }
        }
        for track in &self.tracks {
            buf.write_u32(track.joint_hash)?;
        }

        let end = buf.seek(SeekFrom::End(0)).map_err(rs_io::Error::from)?;
        buf.seek(SeekFrom::Start(12)).map_err(rs_io::Error::from)?;
        buf.write_u32((end - 12) as u32)?;
        Ok(buf.into_inner())
    }
}
