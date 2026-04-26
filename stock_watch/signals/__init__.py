"""Signal calculation helpers for stock watch."""

from .library import SIGNAL_TEMPLATES
from .library import SignalTemplate
from .library import apply_signal_template_labels
from .library import match_signal_templates
from .library import parse_signal_tokens
from .library import summarize_signal_templates
from .library import template_labels
from .detect import SpeculativeRiskProfile
from .detect import build_speculative_risk_profile
from .detect import speculative_risk_subtype

__all__ = [
    "SIGNAL_TEMPLATES",
    "SignalTemplate",
    "SpeculativeRiskProfile",
    "apply_signal_template_labels",
    "build_speculative_risk_profile",
    "match_signal_templates",
    "parse_signal_tokens",
    "speculative_risk_subtype",
    "summarize_signal_templates",
    "template_labels",
]
