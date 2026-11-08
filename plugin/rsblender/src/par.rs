//! Deterministic parallel map over independent work items.
//!
//! Each item is computed on its own, exactly as the sequential loop would, and the results come
//! back in item order, so the output never depends on the number of threads or on scheduling.
//! A call made from inside a worker runs sequentially: the outer level already uses every core.

use std::cell::Cell;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Mutex;

thread_local! {
    static IN_WORKER: Cell<bool> = const { Cell::new(false) };
}

fn threads() -> usize {
    std::thread::available_parallelism().map(|n| n.get()).unwrap_or(1)
}

/// `(0..count).map(f).collect()`, spread over the cores when `count` is worth it.
pub fn map<T: Send, F: Fn(usize) -> T + Sync>(count: usize, f: F) -> Vec<T> {
    let workers = threads().min(count);
    if workers <= 1 || IN_WORKER.with(|w| w.get()) {
        return (0..count).map(f).collect();
    }
    let next = AtomicUsize::new(0);
    let slots: Vec<Mutex<Option<T>>> = (0..count).map(|_| Mutex::new(None)).collect();
    std::thread::scope(|scope| {
        for _ in 0..workers {
            scope.spawn(|| {
                IN_WORKER.with(|w| w.set(true));
                loop {
                    let i = next.fetch_add(1, Ordering::Relaxed);
                    if i >= count {
                        break;
                    }
                    let value = f(i);
                    *slots[i].lock().unwrap() = Some(value);
                }
            });
        }
    });
    slots.into_iter().map(|slot| slot.into_inner().unwrap().unwrap()).collect()
}

/// `map` over contiguous chunks of `count` items, for cheap per-item work.
pub fn map_chunked<T: Send, F: Fn(usize) -> T + Sync>(count: usize, chunk: usize, f: F) -> Vec<T> {
    let chunks = count.div_ceil(chunk.max(1));
    map(chunks, |c| {
        let start = c * chunk;
        let end = (start + chunk).min(count);
        (start..end).map(&f).collect::<Vec<T>>()
    })
    .into_iter()
    .flatten()
    .collect()
}
