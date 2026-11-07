from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

ZERO_WIDTH = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff\u00ad]")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
INJECTION_THRESHOLD = 3


def normalize_text(text: str, max_chars: int = 2000) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = ZERO_WIDTH.sub("", text)
    text = CONTROL.sub(" ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:max_chars]


@dataclass(frozen=True)
class InjectionPattern:
    name: str
    regex: re.Pattern
    weight: int
    exclude: re.Pattern | None = None


def _p(name: str, pattern: str, weight: int, exclude: str | None = None, flags: int = re.IGNORECASE) -> InjectionPattern:
    return InjectionPattern(name, re.compile(pattern, flags), weight, re.compile(exclude, re.I) if exclude else None)


_TARGETS = r"(?:instructions?|prompts?|rules|guidelines|guardrails|restrictions|directives|system\s+message|programming)"

INJECTION_PATTERNS: tuple[InjectionPattern, ...] = (
    _p(
        "ignore_instructions",
        r"\b(?:ignore|disregard|forget|override|bypass|skip)\b[^.!?\n]{0,40}?\b(?:previous|prior|above|earlier|"
        r"preceding|all|any|your|these|those|the|system|original)\b[^.!?\n]{0,25}?\b" + _TARGETS + r"\b",
        3,
        exclude=r"\bmy\b",
    ),
    _p("forget_everything", r"\bforget (?:everything|all)\b[^.!?\n]{0,20}\b(?:told|learned|above|before)\b", 3),
    _p(
        "reveal_prompt",
        r"\b(?:reveal|show|print|repeat|output|display|tell me|give me|what (?:is|are|was)|leak|dump|share|copy)\b"
        r"[^.!?\n]{0,30}?\b(?:system|initial|original|hidden|secret|developer|internal|full)\s+"
        r"(?:prompt|message|instructions?|rules|configuration|guidelines)\b",
        3,
    ),
    _p("system_prompt_mention", r"\bsystem\s*prompt\b", 2),
    _p("role_override", r"\byou are (?:now|no longer)\b|\b(?:from now on|henceforth),? you (?:are|will|must|shall)\b", 2),
    _p("mode_switch", r"\b(?:developer|dev|god|admin|debug|jailbreak|unrestricted|sudo|dan)\s+mode\b", 3),
    _p("dan", r"\bdo anything now\b", 3),
    _p("dan_upper", r"\bDAN\b", 2, flags=0),
    _p(
        "roleplay_unrestricted",
        r"\b(?:pretend|act|behave|role-?play|simulate|imagine)\b[^.!?\n]{0,20}?\b(?:as|to be|like|you(?:'re| are))\b"
        r"[^.!?\n]{0,25}?\b(?:unrestricted|unfiltered|uncensored|jailbroken|evil|different (?:ai|assistant|model|bot)|"
        r"an? (?:admin(?:istrator)?|developer|system)|without (?:any )?(?:rules|restrictions|limits|filters))\b",
        3,
    ),
    _p(
        "special_tokens",
        r"<\|?(?:im_start|im_end|system|endoftext|eot_id|start_header_id)\|?>|\[/?(?:INST|SYS)\]|<</?SYS>>|"
        r"</?(?:system|instructions?|admin)>",
        3,
    ),
    _p("fake_role_header", r"(?m)^\s*(?:system|developer|admin(?:istrator)?)\s*(?:message|prompt)?\s*:", 3),
    _p(
        "fake_block",
        r"\b(?:BEGIN|END|START)\s+(?:SYSTEM|ADMIN|DEVELOPER|NEW|OVERRIDE)\s+(?:PROMPT|MESSAGE|INSTRUCTIONS?|OVERRIDE|MODE)\b",
        3,
    ),
    _p("new_instructions", r"\bnew (?:system )?(?:instructions?|rules|directives?|guidelines)\s*:", 3),
    _p(
        "addresses_ai",
        r"\b(?:attention|note|message|instructions?)\s+(?:to|for)\s+(?:all\s+|any\s+)?(?:the\s+)?"
        r"(?:ai|assistants?|chat\s?bots?|llms?|language models?|bots?)\b",
        3,
    ),
    _p(
        "authority_claim",
        r"\b(?:i am|i'm|this is|as)\s+(?:the |an? |your )?(?:store |site |shop )?(?:admin(?:istrator)?|developer|"
        r"system administrator|owner|ceo|openai|anthropic|security team|it department)\b",
        2,
    ),
    _p("encoding_trick", r"\b(?:base64|rot13|hex[- ]encoded|decode this)\b", 1),
    _p("encoded_blob", r"[A-Za-z0-9+/]{80,}={0,2}", 2, flags=0),
)


@dataclass
class InjectionScan:
    score: int = 0
    matches: list[str] = field(default_factory=list)

    @property
    def is_injection(self) -> bool:
        return self.score >= INJECTION_THRESHOLD

    @property
    def is_suspicious(self) -> bool:
        return 0 < self.score < INJECTION_THRESHOLD


def scan_for_injection(text: str) -> InjectionScan:
    text = normalize_text(text, max_chars=10_000)
    scan = InjectionScan()
    for pattern in INJECTION_PATTERNS:
        for match in pattern.regex.finditer(text):
            if pattern.exclude and pattern.exclude.search(match.group(0)):
                continue
            scan.score += pattern.weight
            scan.matches.append(pattern.name)
            break
    return scan


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def neutralize_instructions(text: str, threshold: int = 2) -> tuple[str, bool]:
    if not text:
        return text, False
    kept, flagged = [], False
    for sentence in _SENTENCE_SPLIT.split(text):
        if not sentence.strip():
            continue
        if scan_for_injection(sentence).score >= threshold:
            flagged = True
            continue
        kept.append(sentence.strip())
    return " ".join(kept), flagged
