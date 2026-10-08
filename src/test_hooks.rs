//! Test-only barriers for the guarded publication path. Compiled only with the `test-hooks`
//! Cargo feature, which the release wheel never enables.
//!
//! At each named stage the code reads two environment variables:
//!   `PJ_SIGNAL_<STAGE>=<fifo>`  open the FIFO for writing and write one byte (blocks until a
//!                               reader opens it). It tells a test "the process reached this stage".
//!   `PJ_WAIT_<STAGE>=<fifo>`    open the FIFO for reading and read one byte (blocks until a
//!                               writer writes). It holds the stage until the test says go.
//! Signal runs before wait. A FIFO is a kernel barrier, not a sleep. A test kills the process
//! while it waits to simulate a crash at that stage.
//!
//! `PJ_TEST_SKIP_OPHEADS_CAS=1` removes the operation-heads comparison. A negative-control test
//! uses it to prove that the comparison matters.

use std::io::{Read, Write};

pub(crate) fn hook(stage: &str) {
    if let Ok(path) = std::env::var(format!("PJ_SIGNAL_{stage}"))
        && let Ok(mut f) = std::fs::OpenOptions::new().write(true).open(&path)
    {
        let _ = f.write_all(b"x");
    }
    if let Ok(path) = std::env::var(format!("PJ_WAIT_{stage}"))
        && let Ok(mut f) = std::fs::OpenOptions::new().read(true).open(&path)
    {
        let mut b = [0u8; 1];
        let _ = f.read(&mut b);
    }
}

pub(crate) fn skip_op_heads_comparison() -> bool {
    std::env::var_os("PJ_TEST_SKIP_OPHEADS_CAS").is_some()
}
