# Shared Vectors

This directory is the canonical machine-readable source of truth for the shared
JSON vectors used by the SLMP verification workspace.

These files were previously published from a separate `slmp-shared-spec`
repository. `plc-comm-slmp-cross-verify/specs/shared/` is now the canonical
location.

- `device_spec_vectors.json`: low-level device spec encoding vectors.
- `high_level_address_normalize_vectors.json`: canonical helper-layer address normalization.
- `high_level_address_parse_vectors.json`: shared parser expectations for Python, .NET, and Node.
- `cpp_high_level_address_parse_vectors.json`: C++ high-level parser expectations.
- `frame_golden_vectors.json`: golden request frames for common SLMP operations.
- `unsupported_path_vectors.json`: negative parity cases for command routes that
  the libraries intentionally guard, including long timer/counter and `LZ`
  route restrictions.

Implementation-specific tests should read these files directly where practical.
Generated artifacts, such as C++ test headers, must be derived from these files
rather than hand-edited.
