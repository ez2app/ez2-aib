//! The Tokio runtime (ADR 0005): aibd mostly waits on I/O, so two worker
//! threads and a bounded blocking pool. CPU-heavy work, if it ever appears,
//! gets a dedicated thread instead of a larger pool.

pub const WORKER_THREADS: usize = 2;
pub const MAX_BLOCKING_THREADS: usize = 8;

pub fn build() -> std::io::Result<tokio::runtime::Runtime> {
    tokio::runtime::Builder::new_multi_thread()
        .worker_threads(WORKER_THREADS)
        .max_blocking_threads(MAX_BLOCKING_THREADS)
        .thread_name("aibd")
        .enable_all()
        .build()
}

#[cfg(test)]
mod tests {
    #[test]
    fn runtime_has_two_workers() {
        let rt = super::build().unwrap();
        assert_eq!(rt.metrics().num_workers(), super::WORKER_THREADS);
        assert_eq!(super::WORKER_THREADS, 2);
    }
}
