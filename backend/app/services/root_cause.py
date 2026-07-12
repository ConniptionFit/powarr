"""FI-06: root-cause classifier for stuck/suggested Failed Import rows. Pure
and read-only — tags a row with a plain-language reason and suggested next
action, driving Match Review filter chips. Never a scoring input and never
auto-acts; it only reads signals the scorer/LLM review already computed
(services/import_matcher.py) rather than re-deriving anything, so it can
never disagree with the confidence/badges already shown.

Ordered — first matching cause wins, and this order intentionally mirrors
the existing quality_downgrade/suspicious_files/partial_import badge
priority in MatchReview.tsx so the two never contradict each other."""
from dataclasses import dataclass

from app.services.import_matcher import strip_release_junk


@dataclass
class RootCause:
    code: str
    label: str
    suggested_action: str


def classify_root_cause(item) -> RootCause:
    rationale = (item.match_rationale or "").lower()
    message = (item.message or "").lower()

    if item.status == "orphaned" or "no files" in message or "files are gone" in message:
        return RootCause(
            "missing_files", "Missing files",
            "Files are gone from the download client/disk — reject, or let auto-purge "
            "clear the stale queue entry.",
        )
    if "year mismatch" in rationale:
        return RootCause(
            "year_mismatch", "Year mismatch",
            "The release year doesn't match the library title's year — this is the "
            "wrong release (likely a different edition/remake). Reject.",
        )
    if item.quality_downgrade:
        return RootCause(
            "not_an_upgrade", "Not an upgrade",
            "Every file in this download scores below what's already in the library — "
            "reject, it will never import as-is.",
        )
    if item.suspicious_files:
        return RootCause(
            "suspicious_file", "Suspicious file type",
            "A file extension doesn't match a real media release — reject, and consider "
            "deleting it from the download client.",
        )
    if item.partial_import:
        return RootCause(
            "pack_partial", "Partial pack",
            "Some files are new/upgrades and some are already covered — use the "
            "gap-fill partial accept rather than a full reject.",
        )
    if "no library match found" in rationale:
        # A near-empty title after stripping known release-junk tokens (scene
        # groups, quality/codec tags, container extensions) usually means the
        # filename itself carried little real title signal to match against,
        # rather than a genuinely wrong/missing library entry.
        stripped = strip_release_junk(item.raw_title or "", music=item.source_app in ("lidarr", "readarr"))
        if len(stripped.strip()) < 4:
            return RootCause(
                "scene_name_junk", "Junk release name",
                "The release filename has almost no real title left after stripping "
                "scene tags/quality markers — verify by hand, this may not be worth "
                "matching at all.",
            )
        return RootCause(
            "no_match", "No library match",
            "No confident title match was found in the library — verify manually; "
            "this may be a wrong series/album or a title not yet in your library.",
        )
    if item.llm_agrees is False:
        return RootCause(
            "llm_disagrees", "LLM disagrees",
            "The LLM review flagged a mismatch with the suggested match — check its "
            "rationale before accepting.",
        )
    if "no numeric corroboration" in rationale:
        return RootCause(
            "weak_numeric_match", "Weak episode/track match",
            "The title matched but episode/track numbering didn't corroborate it — "
            "spot-check before accepting.",
        )
    if (item.confidence or 0) < 0.3:
        return RootCause(
            "low_confidence", "Low confidence",
            "Overall match confidence is low without one specific known cause — "
            "review manually.",
        )
    return RootCause(
        "unclassified", "Unclassified",
        "No specific root cause detected — review manually.",
    )
