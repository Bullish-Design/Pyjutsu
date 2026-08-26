//! Small bindings for jj-lib's domain-specific language utilities.

use pyo3::prelude::*;

/// Quote a string as a jj domain-specific language literal.
#[pyfunction]
pub(crate) fn escape_string(s: &str) -> String {
    jj_lib::dsl_util::escape_string(s)
}
