"""Coarse body-part labels for injured-list stints.

Derives a label like "Elbow" or "Hamstring" from the MLB feed's reason
text at build time, from the local transaction cache -- never a live
call. Only the derived label is ever stored or shown; the feed text
itself is read here and discarded. No side, severity, diagnosis, or
procedure is kept.

Rules follow the injury-body-part groundwork (Braxton's decisions,
Nov 2026): Thumb is its own label, a stint with no stated reason is
always "Undisclosed" (no 7-day -> Head inference), and personal or
sensitive-medical reasons override any body part with "Personal" or
"Illness" -- the specific condition is never stored.

    from injury_labels import apply_labels, display
    apply_labels(stints)            # stints are il_stints.Stint, mutated in place
    display(stint.label)            # "Elbow", "Illness", "Not disclosed", ...
"""

from __future__ import annotations

import re

UNDISCLOSED = "Undisclosed"

# Arm is dropped when a more specific arm part is also named.
ARM_PARTS = frozenset({"Elbow", "Shoulder", "Forearm", "Wrist", "Hand", "Finger", "Thumb"})

_PERSONAL_RE = re.compile(
    r"\b(mental|anxiety|depress\w*|psych\w*|personal|alcohol|substance|emotional|stress[- ]related)\b",
    re.IGNORECASE,
)
_MEDICAL_RE = re.compile(
    r"\b(heart\w*|cardiac|arrhythmia|testic\w*|blood\s+clot|clot|vascular|thrombo\w*|"
    r"embolism|aneurysm|tumou?r|cancer|lymphoma|leukemia|carcinoma)\b",
    re.IGNORECASE,
)
# The club/player/list clause before the reason is never used -- only
# whatever follows "... injured list[ retroactive to Month D, YYYY]."
# A transfer sentence names the list twice ("from the 15-day injured
# list to the 60-day injured list."); the leading ".*" is greedy and
# backtracks to the *last* occurrence, not the first.
_REASON_RE = re.compile(
    r".*(?:injured|disabled) list(?: retroactive to [A-Za-z]+\.? \d+,\s*\d{4})?\.?\s*(.*)$",
    re.IGNORECASE,
)
_SIDES_RE = re.compile(r"\b(right|left|r\.|l\.|bilateral|both)\b", re.IGNORECASE)
_BLISTER_RE = re.compile(r"\bblister\b", re.IGNORECASE)

# Pre-phrases resolve before the general keyword scan, then are blanked
# out (same length, so later keyword positions stay comparable).
_PRE_PHRASES = (
    (re.compile(r"\bhip\b.{0,30}\bflexor\b", re.IGNORECASE), "Hip"),
    (re.compile(r"\bhip\b.{0,30}\blabrum\b|\blabrum\b.{0,30}\bhip\b", re.IGNORECASE), "Hip"),
    (re.compile(r"\bthoracic\s+outlet\b", re.IGNORECASE), "Shoulder"),
    (re.compile(r"\bhand\W+foot\W+(?:and\W+)?mouth\b", re.IGNORECASE), "Illness"),
    (re.compile(r"\bulnar\s+styloid\b", re.IGNORECASE), "Wrist"),
    (re.compile(r"\b(?:lower|low|mid|upper)\s+back\b", re.IGNORECASE), "Back"),
    (re.compile(r"\blat(?:s|issimus)?(?:\s+dorsi)?\b", re.IGNORECASE), "Back"),
)
# Any remaining "flexor" (not already resolved by the hip-flexor pre-phrase).
_FLEXOR_RE = re.compile(r"\bflexor\b", re.IGNORECASE)

# Order within a label's pattern does not matter; only which label wins
# at each position, and first-mentioned across labels. Every alternative
# is matched whole-word -- see the \b wrapping below -- so a short
# keyword like "ear" or "leg" cannot match inside an unrelated word
# ("tear", "allegation").
_KEYWORDS = (
    ("Elbow", r"tommy\s+john|ucl|ulnar\s+coll?ateral|ulnar\s+nerve|ulnar\s+neuritis|unar\s+nerve|"
              r"elbow|elnow|eblow|albow|olecranon|epicondylitis"),
    ("Shoulder", r"rotator\s+cuff|labrum|labral|ac\s+joint|sc\s+joint|ter[ae]s\s+major|deltoid|"
                 r"scapula\w*|subscapularis|shoulder|clavicle|collarbone|thoracic\s+outlet"),
    ("Forearm", r"flexor|pronator|forearm|extensor|radius|ulna|radial\s+nerve|median\s+nerve|"
                r"brachioradialis"),
    ("Arm", r"biceps?|triceps|humerus|upper\s+arm|brachialis|arm|upper\s+extremity"),
    ("Wrist", r"hamate|scaphoid|wrist|styloid"),
    ("Thumb", r"thumb"),
    ("Finger", r"finger|finer|fingernail|knuckle|index\s+finger|middle\s+finger|ring\s+finger|pinky"),
    ("Hand", r"metacarpal|hand|palm"),
    ("Neck", r"neck|cervical|trapezius|trap"),
    ("Head", r"concussion|conussion|convussion|skull|vision|head|face|facial|jaw|eye|orbital|"
             r"nose|nasal|dental|tooth|ear"),
    ("Chest", r"pectoral\w*|pec|chest|lung|sternum|sternoclavicular"),
    ("Ribs/Side", r"\w*oblique|olique|intercostal|serratus|costochondral|ribs?|rib\s*cage|"
                  r"ribcage|side"),
    ("Back", r"lats?|latissimus|back|lumbar|thoracic|spine|spinal|disc|sacroiliac|si\s+joint|"
             r"s\.i\.\s+joint|rhomboid"),
    ("Abdomen", r"abdominal|abdomen|core|hernia|appendicitis|appendix|appendectomy"),
    ("Hip", r"hip|glute\w*|abductor|pelvis|pelvic"),
    ("Groin", r"groin|adductor|aductor|gracilis"),
    ("Hamstring", r"hamstring|hamstrain|hanstring"),
    ("Quad", r"quad|quadriceps?|thigh|femur|femoral"),
    ("Knee", r"knee|patella\w*|meniscus|acl|mcl|pcl|lcl"),
    ("Lower leg", r"shin|tibia\w*|fibula|peroneal|lower\s+leg|leg"),
    ("Calf", r"calf|calif|achilles|achillies|soleus"),
    ("Ankle", r"ankle"),
    ("Foot", r"foot|heel|plantar|metatarsal|lisfranc|navicular|sesamoid|arch"),
    ("Toe", r"toes?"),
    ("Illness", r"illness|ilness|ill|viral|virus|infection|flu|influenza|covid(?:-?19)?|"
                r"stomach|gastro\w*|migraines?|vertigo|mononucleosis|mono|pneumonia|"
                r"allergic\s+reaction"),
)
_KEYWORD_RES = tuple(
    (label, re.compile(r"\b(?:" + pattern + r")\b", re.IGNORECASE)) for label, pattern in _KEYWORDS
)


def _blank(pattern: re.Pattern, text: str) -> str:
    """Replace every match with spaces of the same length, so positions
    found later still line up with the original text."""
    return pattern.sub(lambda m: " " * len(m.group(0)), text)


def _reason_text(text: str) -> str:
    match = _REASON_RE.search(text or "")
    return match.group(1) if match else ""


def sensitive_label(text: str) -> str | None:
    """"Personal" or "Illness" when the row's own text is personal/mental-health
    or a sensitive medical condition (heart, vascular, testicular, tumor...),
    else None. Distinct from the ordinary "Illness" keyword (migraines, flu,
    infection...), which is a ordinary label, not an override -- only this
    function's result makes label_source "sensitive" at the stint level.
    """
    reason = _reason_text(text)
    if _PERSONAL_RE.search(reason):
        return "Personal"
    if _MEDICAL_RE.search(reason):
        return "Illness"
    return None


def row_labels(text: str) -> list[str]:
    """Labels mentioned in one IL row's feed text, first-mentioned first.

    Only the part after "... injured list[ retroactive to ...]." is read.
    A personal or sensitive-medical term returns immediately with just
    that one label; body-part words elsewhere in the same text are
    ignored, per Braxton's decision that those stints never carry a
    body part.
    """
    sensitive = sensitive_label(text)
    if sensitive:
        return [sensitive]
    reason = _SIDES_RE.sub(" ", _reason_text(text))

    hits: list[tuple[int, str]] = []
    remaining = reason
    for pattern, label in _PRE_PHRASES:
        for found in pattern.finditer(remaining):
            hits.append((found.start(), label))
        remaining = _blank(pattern, remaining)
    for found in _FLEXOR_RE.finditer(remaining):
        hits.append((found.start(), "Forearm"))
    remaining = _blank(_FLEXOR_RE, remaining)
    for label, pattern in _KEYWORD_RES:
        for found in pattern.finditer(remaining):
            hits.append((found.start(), label))

    hits.sort(key=lambda item: item[0])
    labels: list[str] = []
    for _pos, label in hits:
        if label not in labels:
            labels.append(label)

    if "Arm" in labels and any(part in labels for part in ARM_PARTS):
        labels.remove("Arm")
    if not labels and _BLISTER_RE.search(remaining):
        labels = ["Finger"]
    if "Illness" in labels and len(labels) > 1:
        labels.remove("Illness")
    return labels


def label_for_stint(reasons: list[tuple]) -> dict:
    """The stint-level label from its ordered (date, text, how) rows.

    `how` is "placement" or "transfer". Any sensitive row makes the
    whole stint Personal (if any row is personal) or else Illness,
    regardless of what other rows say. Otherwise the placement's label
    wins; a later row only fills in when every earlier row was
    Undisclosed, or when the label so far is the vague "Arm" and the
    later row names a specific arm part. A stint with no mappable
    reason stays Undisclosed -- there is no 7-day-list inference.
    """
    sensitive = [sensitive_label(text) for _when, text, _how in reasons]
    sensitive = [found for found in sensitive if found]
    if sensitive:
        final = "Personal" if "Personal" in sensitive else "Illness"
        return {"label": final, "label_source": "sensitive", "label_conflict": False, "multi": False}

    per_row = [(how, row_labels(text)) for _when, text, how in reasons]

    label = UNDISCLOSED
    source = None
    conflict = False
    multi = False
    for how, labels in per_row:
        if not labels:
            continue
        if len(labels) > 1:
            multi = True
        found = labels[0]
        if label == UNDISCLOSED:
            label, source = found, how
        elif label == "Arm" and found in ARM_PARTS:
            label, source = found, how
        elif found != label:
            conflict = True
    return {"label": label, "label_source": source, "label_conflict": conflict, "multi": multi}


def apply_labels(stints) -> None:
    """Set label/label_source/label_conflict/multi on each stint, from its reasons."""
    for stint in stints:
        result = label_for_stint(stint.reasons)
        stint.label = result["label"]
        stint.label_source = result["label_source"]
        stint.label_conflict = result["label_conflict"]
        stint.multi = result["multi"]


def display(label: str | None) -> str:
    """What the page shows: "Not disclosed" for Undisclosed, or an unlabeled
    stint we never fetched text for -- the two look the same to a reader,
    and neither is a guess."""
    if not label or label == UNDISCLOSED:
        return "Not disclosed"
    return label
