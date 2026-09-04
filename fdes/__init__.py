"""FDES v1.0.0-draft checks and the evaluation protocol used by the reproduction package.

Spec: https://github.com/mateenali66/failure-detection-evaluation-spec (SPEC.md, v1.0.0-draft).
"""

SPEC_VERSION = "1.0.0-draft"
# The build that produced a report. A report that cannot be traced back to a build cannot
# be argued with. A test asserts this matches CITATION.cff, so the two cannot drift.
TOOL_VERSION = "1.3.8"
SPEC_URL = "https://github.com/mateenali66/failure-detection-evaluation-spec"
