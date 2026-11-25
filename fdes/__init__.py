"""FDES v1.0.0-draft checks and the evaluation protocol used by the reproduction package.

Spec: https://github.com/mateenali66/failure-detection-evaluation-spec (SPEC.md, v1.0.0-draft).
"""

SPEC_VERSION = "1.0.0-draft"
# The build that produced a report, so every report can be traced to it. A test asserts this
# matches CITATION.cff, so the two cannot drift.
TOOL_VERSION = "1.5.0"
SPEC_URL = "https://github.com/mateenali66/failure-detection-evaluation-spec"
