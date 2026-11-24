"""Personal V1 states of the detector output, with the reason in words (ADR-025, ADR-027).

Read-only view of a ``strokes.json``: it adds no measurement and changes no decision.

* ``flagged`` (*colpo segnalato*): an ``accepted`` event.
* ``to_check`` (*da verificare*): an ``uncertain`` event; the reasons say why.
* ``discarded`` (*scartato*): a ``rejected`` event; hidden by default in the app, shown on
  request, never deleted.
* Region visibility (busto, polsi, gambe) per event is shown apart from the state and its
  reasons: it says how well each part of the body was seen, not whether it is a stroke.
* ``not_assessable`` (*non valutabile*): a ``coverage`` stretch: **insufficient evidence to
  detect strokes, not absence of strokes**.

No percentage or score: the state and its reasons are the whole message. Texts are
Italian (the app language); codes stay the stable identifiers.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from tennis_cv.strokes.artifact import (
    CandidateEvent,
    CoverageStretch,
    EventStatus,
    RegionName,
    RegionReason,
    RegionState,
    StrokesFile,
)


class V1State(StrEnum):
    FLAGGED = "flagged"
    TO_CHECK = "to_check"
    DISCARDED = "discarded"
    NOT_ASSESSABLE = "not_assessable"


STATE_LABEL: dict[V1State, str] = {
    V1State.FLAGGED: "Colpo segnalato",
    V1State.TO_CHECK: "Da verificare",
    V1State.DISCARDED: "Scartato",
    V1State.NOT_ASSESSABLE: "Non valutabile",
}

_FROM_STATUS = {
    EventStatus.ACCEPTED: V1State.FLAGGED,
    EventStatus.UNCERTAIN: V1State.TO_CHECK,
    EventStatus.REJECTED: V1State.DISCARDED,
}

REASON_TEXT: dict[str, str] = {
    "reject_low_relative_height": (
        "Movimento del polso molto più lento dei colpi di questo video: probabilmente non è "
        "un colpo"
    ),
    "uncertain_low_relative_height": (
        "Movimento del polso più lento dei colpi tipici di questo video"
    ),
    "uncertain_no_clip_reference": (
        "Video troppo breve o con pochi movimenti: manca un riferimento per confrontare la velocità"
    ),
    "uncertain_wrist_undetermined": (
        "Non si capisce quale polso ha colpito: il polso entra o esce dalla vista durante "
        "il movimento"
    ),
    "uncertain_possible_merge": (
        "Movimento lungo: potrebbero essere due colpi ravvicinati uniti in uno"
    ),
    "uncertain_no_mostly_good_peak": "Polsi visti male durante il movimento",
    "uncertain_irregular_interval": (
        "Intervallo irregolare tra i fotogrammi vicino al colpo: il tempo è meno preciso"
    ),
    "uncertain_region_not_observable": "Una parte del corpo necessaria non è visibile",
    "info_time_basis_proxy": (
        "Tempi del video originale non disponibili: i tempi sono approssimati"
    ),
}

REGION_TEXT: dict[RegionName, str] = {
    RegionName.TORSO: "busto",
    RegionName.LEFT_WRIST: "polso sinistro",
    RegionName.RIGHT_WRIST: "polso destro",
    RegionName.LEGS: "gambe",
}
REGION_REASON_TEXT: dict[RegionReason, str] = {
    RegionReason.NO_POSE: "giocatore non rilevato",
    RegionReason.BELOW_THRESHOLD: "punti del corpo visti male",
    RegionReason.OUT_OF_FRAME: "fuori inquadratura",
    RegionReason.BROKEN_RUN: "visibilità a tratti troppo brevi",
    RegionReason.IRREGULAR_INTERVAL: "fotogrammi irregolari",
}
REGION_STATE_LABEL: dict[RegionState, str] = {
    RegionState.GOOD: "buona",
    RegionState.LOW_BUT_USABLE: "bassa ma usabile",
    RegionState.NOT_OBSERVABLE: "non osservabile",
}
NOT_ASSESSABLE_NOTE = "qui i colpi non si possono rilevare (non vuol dire che non ci siano)"


@dataclass(frozen=True)
class RegionView:
    region: RegionName
    label: str  # e.g. "polso sinistro"
    state: RegionState
    state_label: str  # "buona" / "bassa ma usabile" / "non osservabile"
    used: bool  # in reliability.regions_used
    reasons: tuple[str, ...]  # in words


@dataclass(frozen=True)
class EventView:
    event_id: str
    state: V1State
    t_s: float | None  # reference time; None when the wrist is undetermined
    window_t_s: tuple[float, float]
    reasons: tuple[str, ...]  # in words, rule order
    regions: tuple[RegionView, ...]  # fixed order: torso, left wrist, right wrist, legs


@dataclass(frozen=True)
class StretchView:
    start_t_s: float
    end_t_s: float
    state: V1State
    reason: str  # in words


def region_views(e: CandidateEvent) -> tuple[RegionView, ...]:
    """Visibility of the four regions of an event, as recorded by the detector."""
    used = set(e.reliability.regions_used)
    out = []
    for name in RegionName:
        r = e.regions.get(name)
        out.append(
            RegionView(
                region=name,
                label=REGION_TEXT[name],
                state=r.state,
                state_label=REGION_STATE_LABEL[r.state],
                used=name in used,
                reasons=tuple(REGION_REASON_TEXT[x] for x in r.reasons),
            )
        )
    return tuple(out)


def event_view(e: CandidateEvent) -> EventView:
    return EventView(
        event_id=e.id,
        state=_FROM_STATUS[e.status],
        t_s=e.time.ref_t_s,
        window_t_s=e.time.window_t_s,
        reasons=tuple(REASON_TEXT.get(code, code) for code in e.reasons),
        regions=region_views(e),
    )


def stretch_view(s: CoverageStretch) -> StretchView:
    regions = ", ".join(REGION_TEXT[r] for r in s.regions)
    causes = ", ".join(REGION_REASON_TEXT[r] for r in s.reasons)
    return StretchView(
        start_t_s=s.start_t_s,
        end_t_s=s.end_t_s,
        state=V1State.NOT_ASSESSABLE,
        reason=f"{regions.capitalize()} senza dati utilizzabili ({causes}): {NOT_ASSESSABLE_NOTE}",
    )


def v1_views(doc: StrokesFile) -> tuple[list[EventView], list[StretchView]]:
    """Every event with its state, and every not-assessable stretch (time order)."""
    stretches = doc.coverage.not_assessable if doc.coverage else []
    return [event_view(e) for e in doc.events], [stretch_view(s) for s in stretches]
