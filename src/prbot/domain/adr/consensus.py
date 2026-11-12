"""Rules that turn an ADR's consensus table into the emoji its review post should show.

Each reviewer scores the ADR with ``+/- 0, 1, 2`` (fractions like ``+0.5`` are
used too) at the start of their Comments cell. Only rows for the page's
current version count, and only each reviewer's last row for it:

- ``+1`` or higher → approved; ``-1`` or lower → changes requested. Anything
  in between counts for neither.
- Both at once → contested (🥊), shown *instead of* approved and changes
  requested.
- Any text after the score, other than a short sign-off like "LGTM" → commented.

The post also shows the current version as a number emoji.
"""

import re
from collections.abc import Iterable
from decimal import Decimal

from pydantic import BaseModel

from prbot.domain.adr.value_objects import AdrSnapshot, ConsensusRow
from prbot.domain.emoji.value_objects import EmojiConfig

_APPROVAL = Decimal(1)
_CHANGES_REQUESTED = Decimal(-1)

# People type real minus signs and dashes as often as hyphens.
_DASHES = str.maketrans({"\N{MINUS SIGN}": "-", "\N{EN DASH}": "-", "\N{EM DASH}": "-"})
# The sign has to touch the number, so a cell starting "- 2 nits" isn't a -2.
_LEADING_SCORE = re.compile(r"\s*([+-])(\d+(?:\.\d+)?)")

# Short phrases that only restate the score, so text made up of nothing else is
# not a comment ("+1 LGTM"). Longer phrases first, so they match before their
# prefixes do.
SIGN_OFFS: tuple[str, ...] = (
    "looks good to me",
    "looks great",
    "looks good",
    "all good",
    "lgtm",
    "sgtm",
    "ship it",
    "approved",
    "great",
    "nice",
)
_SIGN_OFF = re.compile(r"\b(?:" + "|".join(map(re.escape, SIGN_OFFS)) + r")\b")
# Punctuation, symbols and emoji: everything that isn't a letter or a digit.
_NOT_A_WORD = re.compile(r"[\W_]+")

_KEYCAP = "\N{VARIATION SELECTOR-16}\N{COMBINING ENCLOSING KEYCAP}"
_DIGIT_NAMES = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
# Slack name and Unicode fallback for each version a single reaction can show.
# Slack allows one reaction per name, so 11 and up can't be spelled from digits.
_VERSION_EMOJI: dict[int, tuple[str, str]] = {
    **{n: (name, f"{n}{_KEYCAP}") for n, name in enumerate(_DIGIT_NAMES, start=1)},
    10: ("keycap_ten", "\N{KEYCAP TEN}"),
}


class Vote(BaseModel, frozen=True):
    """What one Comments cell says: its score, if any, and whether it has a comment."""

    score: Decimal | None
    has_comment: bool


class ConsensusVerdict(BaseModel, frozen=True):
    """The review state of an ADR's current version."""

    version: int | None
    approved: bool = False
    changes_requested: bool = False
    commented: bool = False

    @property
    def contested(self) -> bool:
        return self.approved and self.changes_requested


class AdrEmoji(BaseModel, frozen=True):
    """An emoji an ADR post should show, with a Unicode fallback for platforms lacking it."""

    emoji: str
    fallback: str | None


def read_vote(cell: str) -> Vote:
    """Read the leading score and any real comment out of a Comments cell."""
    text = cell.translate(_DASHES)
    score: Decimal | None = None
    if match := _LEADING_SCORE.match(text):
        sign, magnitude = match.groups()
        score = Decimal(magnitude) if sign == "+" else -Decimal(magnitude)
        text = text[match.end() :]
    return Vote(score=score, has_comment=_is_comment(text))


def _is_comment(text: str) -> bool:
    words = _NOT_A_WORD.sub(" ", text.lower())
    return bool(_SIGN_OFF.sub(" ", words).strip())


def evaluate_consensus(snapshot: AdrSnapshot) -> ConsensusVerdict:
    """Work out the review state of the ADR's current version.

    The current version is the page's ``Version`` property. When that is
    unset, the highest version in the table stands in for it.
    """
    version = snapshot.version
    if version is None:
        known = [row.version for row in snapshot.rows if row.version is not None]
        version = max(known, default=None)
    if version is None:
        return ConsensusVerdict(version=None)

    rows = _last_row_per_reviewer(row for row in snapshot.rows if row.version == version)
    votes = [read_vote(row.comment) for row in rows]
    return ConsensusVerdict(
        version=version,
        approved=any(v.score is not None and v.score >= _APPROVAL for v in votes),
        changes_requested=any(v.score is not None and v.score <= _CHANGES_REQUESTED for v in votes),
        commented=any(v.has_comment for v in votes),
    )


def _last_row_per_reviewer(rows: Iterable[ConsensusRow]) -> list[ConsensusRow]:
    """Keep each reviewer's last row, so a later row replaces their earlier vote.

    Rows without a reviewer can't be told apart, so each one is kept.
    """
    named: dict[str, ConsensusRow] = {}
    unnamed: list[ConsensusRow] = []
    for row in rows:
        if row.reviewer:
            named[row.reviewer] = row
        else:
            unnamed.append(row)
    return [*named.values(), *unnamed]


def wanted_emojis(verdict: ConsensusVerdict, config: EmojiConfig) -> tuple[AdrEmoji, ...]:
    """List the emoji an ADR post should show for *verdict*: version, review state, comments."""
    wanted: list[AdrEmoji] = []
    if verdict.version in _VERSION_EMOJI:
        name, fallback = _VERSION_EMOJI[verdict.version]
        wanted.append(AdrEmoji(emoji=name, fallback=fallback))

    if verdict.contested:
        wanted.append(_with_fallback(config.adr_contested, "adr_contested"))
    elif verdict.approved:
        wanted.append(_with_fallback(config.approved, "approved"))
    elif verdict.changes_requested:
        wanted.append(_with_fallback(config.changes_requested, "changes_requested"))

    if verdict.commented:
        wanted.append(_with_fallback(config.commented, "commented"))

    # A workspace may map two of these to the same name; Slack shows it once.
    unique: dict[str, AdrEmoji] = {}
    for emoji in wanted:
        unique.setdefault(emoji.emoji, emoji)
    return tuple(unique.values())


def fallback_for(emoji: str, config: EmojiConfig) -> str | None:
    """Return the Unicode fallback ``wanted_emojis`` gives *emoji*, if it gives it one.

    Removing a reaction needs this too: the fallback may be what landed.
    """
    for name, fallback in _VERSION_EMOJI.values():
        if name == emoji:
            return fallback
    statuses = (
        (config.adr_contested, "adr_contested"),
        (config.approved, "approved"),
        (config.changes_requested, "changes_requested"),
        (config.commented, "commented"),
    )
    return next((_with_fallback(e, key).fallback for e, key in statuses if e == emoji), None)


def _with_fallback(emoji: str, key: str) -> AdrEmoji:
    return AdrEmoji(emoji=emoji, fallback=EmojiConfig.fallback_for_status(key))
