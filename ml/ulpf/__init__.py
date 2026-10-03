"""CausalOps ULPF: Universal Log Pre-processing Framework.

Ingests logs from any device in any format, preserves every raw byte in a hash-chained vault,
parses and normalizes events into OCSF, and proves for each event that the original can be
rebuilt from the normalized record (lossless). The CausalOps engine then watches parsing
quality, sources and the pipeline itself.
"""

__version__ = "1.0.0"
