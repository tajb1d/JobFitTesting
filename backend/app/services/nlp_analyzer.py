"""Skill extraction with spaCy PhraseMatcher over app/data/skills.json (plan §5, step 4).

spaCy's NER doesn't know skills, so matching is purely taxonomy-driven. Taxonomy fields beyond
the documented {canonical, category, aliases}:

- "case_sensitive": aliases in this entry that contain a capital letter ("Excel", "Go", "R")
  match exact case only. Its all-lowercase aliases ("microsoft excel") still match any case.
- "ambiguous": the entry's single-word, case-sensitive aliases are also English words or
  names ("Excel at", "Spring 2023", "cross-functional Teams"), so a match only counts with
  supporting context. Multi-word aliases ("spring boot") never need context.
"""

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import spacy
from spacy.language import Language
from spacy.matcher import PhraseMatcher
from spacy.tokens import Span as SpacySpan
from spacy.util import filter_spans

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Sections whose content is mostly a list of skills, so ambiguous words count there.
SKILL_LIST_SECTIONS = {"skills"}
# Soft skills only count where the candidate claims them, not in incidental bullet wording.
SOFT_SKILL_SECTIONS = {"skills", "summary", "header"}

_LIST_PUNCT = set(",/|;()•·")
# "a Go service", "a React dashboard", "Excel spreadsheets": a technical noun right after
# an ambiguous word settles it.
_TECH_NOUN_AFTER = re.compile(
    r"^\s+(?:apps?|applications?|apis?|backends?|frontends?|services?|microservices?|servers?|"
    r"components?|hooks|librar(?:y|ies)|frameworks?|code(?:base)?|sdk|modules?|scripts?|"
    r"programs?|programming|developers?|development|dashboards?|ui|web|native|boot|mvc|"
    r"spreadsheets?|macros|formulas|pivot|vba|workbooks?|charts?|cluster|jobs?|pipelines?|"
    r"projects?|game|engine|templates?|plugins?|packages?|channels?|bots?|integrations?)\b",
    re.IGNORECASE,
)
# Context within this many characters of another (non-ambiguous) skill supports a match.
_NEARBY_CHARS = 40

# Matches that are never skills, checked against the text around the match.
_HARD_NEGATIVES: dict[str, list[re.Pattern]] = {
    "Spring": [re.compile(r"^Spring\s*(?:'?\d{2}|\d{4}|semester|break|term)", re.I)],
    "Go": [re.compile(r"^Go\s*-"), re.compile(r"^Go\s+(?:to|out|through|beyond|above)\b")],
    "Microsoft Excel": [re.compile(r"^Excel(?:led|s|ling)?\s+(?:at|in)\b")],
    "Express": [re.compile(r"^Express(?:ed|ing)?\b(?!\.js|js)(?=\s+(?:my|our|interest|concern))")],
}
_NEGATIVE_BEFORE: dict[str, re.Pattern] = {
    "Express": re.compile(r"American\s+$"),
    "Microsoft Teams": re.compile(r"cross[- ]functional\s+$", re.I),
}


@dataclass(frozen=True)
class SkillEntry:
    canonical: str
    category: str
    aliases: tuple[str, ...]
    case_sensitive: bool = False
    ambiguous: bool = False


@dataclass
class SkillMatch:
    canonical: str
    category: str
    count: int
    in_skills_section: bool

    def as_dict(self) -> dict:
        return {
            "canonical": self.canonical,
            "category": self.category,
            "count": self.count,
            "in_skills_section": self.in_skills_section,
        }


@lru_cache
def load_taxonomy() -> tuple[SkillEntry, ...]:
    raw = json.loads((DATA_DIR / "skills.json").read_text())
    return tuple(
        SkillEntry(
            canonical=e["canonical"],
            category=e["category"],
            aliases=tuple(e["aliases"]),
            case_sensitive=e.get("case_sensitive", False),
            ambiguous=e.get("ambiguous", False),
        )
        for e in raw
    )


@lru_cache
def get_nlp() -> Language:
    """en_core_web_sm with every pipe excluded: matching only needs the tokenizer, and this
    keeps memory low on Render's 512 MB. Load once; call at startup to warm."""
    return spacy.load(
        "en_core_web_sm",
        exclude=["tok2vec", "tagger", "parser", "attribute_ruler", "lemmatizer", "ner", "senter"],
    )


class SkillMatcher:
    def __init__(self, nlp: Language, taxonomy: Iterable[SkillEntry]):
        self.nlp = nlp
        self.entries: list[SkillEntry] = list(taxonomy)
        # Lowercase matcher runs on lowercased text: "Node.JS" and "node.js" tokenize
        # differently, but "node.js" always tokenizes the same way.
        self.lower = PhraseMatcher(nlp.vocab, attr="ORTH")
        self.exact = PhraseMatcher(nlp.vocab, attr="ORTH")
        # label → (entry index, needs_context)
        self.labels: dict[str, tuple[int, bool]] = {}
        for idx, entry in enumerate(self.entries):
            lower_aliases, exact_aliases = set(), set()
            # Aliases only, not the canonical name: canonicals like "Sales" or "Research"
            # are deliberately left out of their alias lists because they're common words.
            for alias in entry.aliases:
                if entry.case_sensitive and alias != alias.lower():
                    exact_aliases.add(alias)
                else:
                    lower_aliases.add(alias.lower())
            if lower_aliases:
                label = f"{idx}:lower"
                self.labels[label] = (idx, False)
                self.lower.add(label, [nlp.make_doc(a) for a in sorted(lower_aliases)])
            for alias in sorted(exact_aliases):
                needs_context = entry.ambiguous and len(nlp.make_doc(alias)) == 1
                label = f"{idx}:exact:{int(needs_context)}"
                self.labels[label] = (idx, needs_context)
                self.exact.add(label, [nlp.make_doc(alias)])

    def find(self, text: str, section: str) -> list[tuple[SkillEntry, int, int]]:
        """(entry, start_char, end_char) for every skill mention in one section's text."""
        # Lowercase without changing string length, so character offsets line up.
        lowered = "".join(c.lower() if len(c.lower()) == 1 else c for c in text)
        doc_exact = self.nlp.make_doc(text)
        doc_lower = self.nlp.make_doc(lowered)

        candidates: list[tuple[int, int, str]] = []
        for doc, matcher in ((doc_lower, self.lower), (doc_exact, self.exact)):
            for match_id, start, end in matcher(doc):
                span = doc[start:end]
                candidates.append(
                    (span.start_char, span.end_char, self.nlp.vocab.strings[match_id])
                )

        # One longest-match pass across both matchers, so "C++" / "C#" / "Objective-C" beat
        # "C", and "Spring Boot" beats "Spring". filter_spans works on token spans, so map
        # character offsets back onto a single doc.
        spans = []
        for start, end, label in candidates:
            span = doc_exact.char_span(start, end, label=label, alignment_mode="expand")
            if span is not None:
                spans.append(span)

        kept = filter_spans(spans)
        results = []
        for span in kept:
            idx, needs_context = self.labels[span.label_]
            entry = self.entries[idx]
            if entry.category == "soft" and section not in SOFT_SKILL_SECTIONS:
                continue
            if self._rejected(entry, text, span):
                continue
            if needs_context and not self._has_context(text, span, section, kept):
                continue
            results.append((entry, span.start_char, span.end_char))
        return results

    def _rejected(self, entry: SkillEntry, text: str, span: SpacySpan) -> bool:
        after = text[span.start_char : span.start_char + 40]
        before = text[max(0, span.start_char - 40) : span.start_char]
        if any(p.search(after) for p in _HARD_NEGATIVES.get(entry.canonical, [])):
            return True
        pattern = _NEGATIVE_BEFORE.get(entry.canonical)
        return bool(pattern and pattern.search(before))

    def _has_context(
        self, text: str, span: SpacySpan, section: str, all_spans: list[SpacySpan]
    ) -> bool:
        if section in SKILL_LIST_SECTIONS:
            return True
        before = text[: span.start_char].rstrip()[-1:]
        after = text[span.end_char :].lstrip()[:1]
        if before in _LIST_PUNCT or after in _LIST_PUNCT:
            return True
        if _TECH_NOUN_AFTER.match(text[span.end_char : span.end_char + 30]):
            return True
        for other in all_spans:
            if other is span or self.labels[other.label_][1]:
                continue  # only unambiguous skills vouch for an ambiguous one
            gap = max(other.start_char - span.end_char, span.start_char - other.end_char)
            if gap <= _NEARBY_CHARS:
                return True
        return False


@lru_cache
def get_skill_matcher() -> SkillMatcher:
    return SkillMatcher(get_nlp(), load_taxonomy())


def extract_skills(sections: Iterable[tuple[str, str]]) -> list[SkillMatch]:
    """Skills across (section_key, text) pairs, most-mentioned first."""
    matcher = get_skill_matcher()
    found: dict[str, SkillMatch] = {}
    for key, text in sections:
        for entry, _, _ in matcher.find(text, key):
            m = found.setdefault(
                entry.canonical, SkillMatch(entry.canonical, entry.category, 0, False)
            )
            m.count += 1
            m.in_skills_section |= key in SKILL_LIST_SECTIONS
    return sorted(found.values(), key=lambda m: (-m.count, m.canonical.lower()))
