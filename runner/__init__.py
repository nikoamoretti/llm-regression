"""Longitudinal coding regression runner for Grok 4.6 and GPT-6 Astra."""

__version__ = "3.0.0"

CANONICAL_MODELS = ("grok-4.6", "gpt-6-astra")
CANONICAL_EFFORTS = ("low", "medium", "high", "xhigh")
CANONICAL_TRACKS = ("model_only", "agentic")
AUXILIARY_ASTRA_EFFORT = "max"

# Optional unblended Codex / Sol product series. Never mix with Grok/Astra API series.
REQUEST_MODEL = "gpt-5.6-sol"
OPTIONAL_CODEX_MODEL = "gpt-5.6-sol"
SINGLE_AGENT_EFFORTS = ("low", "medium", "high", "xhigh", "max")
REASONING_EFFORTS = SINGLE_AGENT_EFFORTS
DEFAULT_EFFORT = "xhigh"
MAX_EFFORT = "max"
PRIMARY_TRACK = "codex_product"
CONTROL_TRACK = "responses_control"
ULTRA_TRACK = "codex_ultra"
PRIMARY_PROVIDER = "codex_cli"
CONTROL_PROVIDER = "openai_responses"
API_TRACKS = CANONICAL_TRACKS
CODEX_TRACKS = (PRIMARY_TRACK, CONTROL_TRACK, ULTRA_TRACK)
# Optional unblended Claude Code product series (Claude subscription auth).
CLAUDE_CODE_TRACK = "claude_code_product"
CLAUDE_CODE_PROVIDER = "claude_code_cli"
CLAUDE_CODE_MODEL = "claude-opus-5-5"
# Optional unblended Cursor CLI product series (Cursor plan auth), Grok 4.7 by default.
CURSOR_TRACK = "cursor_product"
CURSOR_PROVIDER = "cursor_cli"
CURSOR_MODEL = "grok-4.7"
PRODUCT_TRACKS = (CLAUDE_CODE_TRACK, CURSOR_TRACK)
TRACKS = (*CANONICAL_TRACKS, PRIMARY_TRACK, CONTROL_TRACK, ULTRA_TRACK, CLAUDE_CODE_TRACK, CURSOR_TRACK)
CLIENT_MODES = ("pinned", "latest")
QUALITY_STATUSES = (
    "quality_pass",
    "quality_fail",
    "infra_fail",
    "harness_fail",
    "invalid_configuration",
)
NON_SCIENTIFIC_SOURCES = frozenset({"gold", "none", "negative", "synthetic", "fake"})
