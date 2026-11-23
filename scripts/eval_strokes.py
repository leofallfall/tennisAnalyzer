"""M2.1 stroke-label dataset tools: templates, checks, input visibility, label comparison.

No stroke detector exists yet. ``compare`` accepts any ``strokes.json``, including one
written by hand. See docs/M2_1_DATASET.md for the formats and the labelling protocol.

Usage:
    uv run python scripts/eval_strokes.py init [DIR]
    uv run python scripts/eval_strokes.py list [STORAGE_DIR]
    uv run python scripts/eval_strokes.py check MANIFEST LABELS [--annotated ANNOTATED.csv]
    uv run python scripts/eval_strokes.py visibility MANIFEST LABELS
        [--window-s 0.3] [--threshold 0.5] [--out REPORT.md] [--json DATA.json]
    uv run python scripts/eval_strokes.py compare MANIFEST LABELS
        [--results-dir DIR] [--tolerance-s 0.25] [--targets forehand,backhand]
        [--out REPORT.md] [--json DATA.json]
    uv run python scripts/eval_strokes.py detect POSE_DIR [--out STROKES.json] [--g-s 0.3]
        [--last-proxy-frame N]
    uv run python scripts/eval_strokes.py detect MANIFEST --results-dir DIR [--g-s 0.3]
        [--last-proxy-frame N]
    uv run python scripts/eval_strokes.py states STROKES [--show-discarded]
    uv run python scripts/eval_strokes.py validate-strokes STROKES [--inputs DIR | --no-inputs]
    uv run python scripts/eval_strokes.py evaluate MANIFEST LABELS --out STROKES_EVAL.json
        [--annotated ANNOTATED.csv] [--results-dir DIR] [--radii 0.1,0.2,0.3,0.5]
        [--targets forehand,backhand] [--outside-search-s 2.0] [--no-input-hashes]
    uv run python scripts/eval_strokes.py report STROKES_EVAL.json MANIFEST --out REPORT.md
        [--results-dir DIR] [--json DATA.json] [--review-csv SHEET.csv] [--review-in FILLED.csv]

``detect`` runs the experimental M2.3 detector (I2, docs/M2_3_EVENTS.md section 9) on
POSE_DIR (pose.json + timemap.json when present) and writes POSE_DIR/strokes.json, or
--out; given a MANIFEST.md it writes DIR/<clip_id>.strokes.json for every clip, the layout
``evaluate --results-dir`` reads. Output is candidate events, not validated strokes.
``--g-s`` overrides the grouping gap G (exploration only; it is recorded in the file).
``--last-proxy-frame`` cuts the inputs at that frame before any computation, as if the
video ended there (Development B stops at 4499: no frame of the Control part of
img5098_full is used); frames, times and hashes stay those of the original files.

``states`` lists the Personal V1 view of a strokes.json: every possible stroke as
*colpo segnalato* / *da verificare* (discarded ones only with --show-discarded) and every
*non valutabile* stretch, each with its reason in words, in time order.

``validate-strokes`` checks a strokes.json 1.x file: version, schema, the eight invariants
and (unless --no-inputs) the sha256 of pose.json / timemap.json in DIR (default: the
folder of STROKES). See docs/M2_3_EVENTS.md.

``evaluate`` associates the events of each clip's strokes file (1.x, or the M2.1 minimal
format) with the labels, one radius at a time, and writes strokes_eval.json: every edge,
connected components, intercepted / missed / ambiguous impacts with miss causes, and every
unassociated event with its position and verdict. See docs/M2_3_EVALUATION.md.

``report`` turns strokes_eval.json into tables (by stroke type, view, label quality,
overlay_wrist, event wrist, clip), per-wrist offset distributions, event statistics and the
false-candidate review sheet; ``--review-in`` merges a filled sheet back. It recomputes
nothing and refuses strokes files changed since the evaluation.

DIR defaults to tests/fixtures/real (git-ignored: real footage is never committed).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from tennis_cv.errors import TennisCVError
from tennis_cv.evaluation.annotated import (
    ANNOTATED_FILE,
    check_against_labels,
    coverage,
    load_annotated,
)
from tennis_cv.evaluation.dataset import (
    LABEL_COLUMNS,
    MANIFEST_COLUMNS,
    POSE_FILE,
    REPORT_FILE,
    TIMEMAP_FILE,
    ClipData,
    Label,
    LabelStrokeType,
    load_clip_data,
    load_labels,
    load_manifest,
)
from tennis_cv.evaluation.eval_artifact import StrokesEvalFile, dump_eval
from tennis_cv.evaluation.eval_report import (
    build_report,
    load_review,
    render_report,
    render_review_csv,
)
from tennis_cv.evaluation.evaluate import (
    DEFAULT_OUTSIDE_SEARCH_S,
    DEFAULT_RADII_S,
    STROKES_FILE,
    LoadedStrokes,
    evaluate_dataset,
    load_events,
    summary_lines,
)
from tennis_cv.evaluation.matching import (
    DEFAULT_TARGETS,
    DEFAULT_TOLERANCE_S,
    ClipMatch,
    load_results,
    match_clip,
)
from tennis_cv.evaluation.report import (
    assess_split,
    clip_groups,
    match_groups,
    render_matches,
    render_visibility,
    visibility_groups,
)
from tennis_cv.evaluation.visibility import (
    DEFAULT_VISIBILITY_THRESHOLD,
    DEFAULT_WINDOW_S,
    analyse_impacts,
)
from tennis_cv.strokes.artifact import check_inputs, dump_strokes, load_strokes
from tennis_cv.strokes.detector import DetectorParams, detect_dir
from tennis_cv.strokes.states import STATE_LABEL, V1State, v1_views

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = ROOT / "tests" / "fixtures" / "real"

_EXAMPLE_ROW = (
    "| clip01 | IMG_0001.MOV | ../../../storage/analyses/AID/videos/VID | behind | right "
    "| two_handed | no | none | dev | example row: replace it |"
)
MANIFEST_TEMPLATE = f"""# Real clips for M2.1 (local only, git-ignored)

Clips you own or have permission to use. Never commit this folder. One row per clip.
`pose_dir` is the folder holding {POSE_FILE} for the clip (relative to this file or
absolute), e.g. ../../../storage/analyses/<analysis_id>/videos/<video_id>.
Run `uv run python scripts/eval_strokes.py list` to find these folders.

| {" | ".join(MANIFEST_COLUMNS)} |
| {" | ".join("---" for _ in MANIFEST_COLUMNS)} |
{_EXAMPLE_ROW}

Allowed values: view = side_forehand, side_backhand, front, behind, diagonal, unknown;
dominant_hand = right, left, unknown; backhand = one_handed, two_handed, unknown;
other_people = yes, no; occlusion = none, partial; split = dev, test, unassigned.
"""

LABELS_TEMPLATE = (
    "# One row per impact, read on overlay.mp4 (frame number printed top left as #N).\n"
    "# stroke_type: forehand backhand serve volley other none | player: A = analysed player\n"
    "# visibility: full partial none | overlay_wrist: ok off missing other_person, or empty\n"
    + ",".join(LABEL_COLUMNS)
    + "\n"
)


def _err(exc: TennisCVError) -> int:
    print(f"ERROR [{exc.code}] {exc.message}", file=sys.stderr)
    for problem in exc.details.get("problems", []):
        print(f"  - {problem}", file=sys.stderr)
    return 1


def cmd_init(args: argparse.Namespace) -> int:
    d: Path = args.dir
    d.mkdir(parents=True, exist_ok=True)
    for name, text in (("MANIFEST.md", MANIFEST_TEMPLATE), ("labels.csv", LABELS_TEMPLATE)):
        path = d / name
        if path.exists():
            print(f"exists, left unchanged: {path}")
        else:
            path.write_text(text, encoding="utf-8")
            print(f"created: {path}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    base: Path = args.storage / "analyses"
    if not base.is_dir():
        print(f"No analyses folder under {args.storage}", file=sys.stderr)
        return 1
    rows = 0
    for pose in sorted(base.glob(f"*/videos/*/{POSE_FILE}")):
        vdir = pose.parent
        name, fps = "?", "?"
        report = vdir / REPORT_FILE
        if report.is_file():
            data = json.loads(report.read_text(encoding="utf-8"))
            name = str((data.get("source_metadata") or {}).get("path", "?"))
            pm = (data.get("preprocess") or {}).get("metadata") or {}
            fps = str(pm.get("average_fps") or pm.get("nominal_fps") or "?")
        tm = "timemap" if (vdir / TIMEMAP_FILE).is_file() else "no timemap"
        print(f"{vdir.relative_to(args.storage)}  source={name}  proxy_fps={fps}  {tm}")
        rows += 1
    if not rows:
        print("No processed videos found.")
    return 0


def _load(args: argparse.Namespace) -> tuple[list[ClipData], list[Label], list[str]]:
    clips = load_manifest(args.manifest)
    labels = load_labels(args.labels)
    data = [load_clip_data(c) for c in clips]
    warnings: list[str] = []
    known = {c.clip.clip_id: c for c in data}
    problems = [
        f"labels.csv:{lb.line}: unknown clip_id {lb.clip_id!r}"
        for lb in labels
        if lb.clip_id not in known
    ]
    for lb in labels:
        c = known.get(lb.clip_id)
        if c is not None and lb.proxy_frame >= c.frame_count:
            problems.append(
                f"labels.csv:{lb.line}: frame {lb.proxy_frame} is past the end of clip "
                f"{lb.clip_id} ({c.frame_count} frames)"
            )
    if problems:
        raise TennisCVError("Labels do not match the clips", details={"problems": problems})
    for c in data:
        own = [lb for lb in labels if lb.clip_id == c.clip.clip_id]
        if not own:
            warnings.append(f"clip {c.clip.clip_id}: no labels yet")
        if c.timemap is None:
            warnings.append(f"clip {c.clip.clip_id}: no timemap.json (repeated frames unknown)")
        if c.poses.frame_stride > 1:
            warnings.append(
                f"clip {c.clip.clip_id}: pose_stride {c.poses.frame_stride}, some frames "
                "have no pose"
            )
        if c.poses.frames and not any(f.detected for f in c.poses.frames):
            warnings.append(f"clip {c.clip.clip_id}: no pose detected in any frame")
    return data, labels, warnings


def cmd_check(args: argparse.Namespace) -> int:
    data, labels, warnings = _load(args)
    for c in data:
        own = [lb for lb in labels if lb.clip_id == c.clip.clip_id]
        detected = sum(1 for f in c.poses.frames if f.detected)
        print(
            f"{c.clip.clip_id}: {c.frame_count} proxy frames at {c.fps:g} fps, pose in "
            f"{detected}/{len(c.poses.frames)} analysed frames, {len(own)} labels, "
            f"view={c.clip.view.value}, hand={c.clip.dominant_hand.value}"
        )
    annotated: Path | None = args.annotated
    if annotated is None and (args.labels.parent / ANNOTATED_FILE).is_file():
        annotated = args.labels.parent / ANNOTATED_FILE
    n_intervals = 0
    if annotated is None:
        warnings.append(f"no {ANNOTATED_FILE} next to the labels: coverage unknown")
    else:
        intervals = load_annotated(annotated)
        clips = {c.clip.clip_id: c.clip for c in data}
        problems, iv_warnings = check_against_labels(
            intervals,
            labels,
            clips,
            {c.clip.clip_id: c.frame_count for c in data},
            file_name=annotated.name,
            labels_name=args.labels.name,
        )
        if problems:
            raise TennisCVError(
                "Annotated intervals do not match the clips or the labels",
                details={"problems": problems},
            )
        warnings.extend(iv_warnings)
        for iv in intervals:
            cov = coverage(iv)
            missing = [t.value for t in cov.missing_types]
            if cov.missing_players is not None:
                missing.append(f"players={cov.missing_players}")
            state = (
                "exhaustive" if cov.exhaustive else "NOT exhaustive, missing " + ", ".join(missing)
            )
            print(
                f"{annotated.name}:{iv.line}: {iv.clip_id} frames {iv.start_frame}-"
                f"{iv.end_frame}: {state}"
            )
        n_intervals = len(intervals)
    for w in warnings:
        print(f"WARNING {w}")
    print(assess_split(data).statement)
    print(f"OK: {len(data)} clips, {len(labels)} labels, {n_intervals} annotated intervals.")
    return 0


def _write(text: str, payload: object, args: argparse.Namespace) -> None:
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"report written: {args.out}")
    else:
        print(text)
    if args.json:
        args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"data written: {args.json}")


def cmd_visibility(args: argparse.Namespace) -> int:
    data, labels, warnings = _load(args)
    for w in warnings:
        print(f"WARNING {w}", file=sys.stderr)
    items = [
        it
        for c in data
        for it in analyse_impacts(c, labels, window_s=args.window_s, threshold=args.threshold)
    ]
    if not items:
        print("No labelled impacts of player A to analyse.", file=sys.stderr)
        return 1
    groups = visibility_groups(items, {c.clip.clip_id: clip_groups(c) for c in data})
    split = assess_split(data)
    text = render_visibility(groups, split, window_s=args.window_s, threshold=args.threshold)
    payload = {
        "window_s": args.window_s,
        "threshold": args.threshold,
        "split": split.model_dump(mode="json"),
        "groups": [g.model_dump(mode="json") for g in groups],
        "impacts": [it.model_dump(mode="json") for it in items],
    }
    _write(text, payload, args)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    data, labels, warnings = _load(args)
    for w in warnings:
        print(f"WARNING {w}", file=sys.stderr)
    targets = frozenset(LabelStrokeType(t) for t in args.targets.split(",") if t)
    matches: list[ClipMatch] = []
    missing: list[str] = []
    for c in data:
        path = (
            args.results_dir / f"{c.clip.clip_id}.strokes.json"
            if args.results_dir
            else c.clip.pose_dir / "strokes.json"
        )
        if not path.is_file():
            missing.append(c.clip.clip_id)
            continue
        matches.append(
            match_clip(
                c.clip.clip_id,
                labels,
                load_results(path),
                c.fps,
                tolerance_s=args.tolerance_s,
                targets=targets,
            )
        )
    if missing:
        print(f"WARNING no results for clips: {', '.join(missing)} (skipped)", file=sys.stderr)
    if not matches:
        print("No results to compare.", file=sys.stderr)
        return 1
    compared = [c for c in data if c.clip.clip_id not in missing]
    summaries = match_groups(matches, {c.clip.clip_id: clip_groups(c) for c in compared})
    split = assess_split(compared)
    source = (
        f"{args.results_dir}/<clip_id>.strokes.json"
        if args.results_dir
        else "strokes.json in each pose_dir"
    )
    text = render_matches(summaries, split, tolerance_s=args.tolerance_s, source=source)
    payload = {
        "tolerance_s": args.tolerance_s,
        "split": split.model_dump(mode="json"),
        "summaries": [s.model_dump(mode="json") for s in summaries],
        "clips": [m.model_dump(mode="json") for m in matches],
    }
    _write(text, payload, args)
    return 0


def _detect_one(pose_dir: Path, out: Path, params: DetectorParams, last: int | None) -> None:
    doc = detect_dir(pose_dir, params, last_proxy_frame=last)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dump_strokes(doc), encoding="utf-8")
    statuses: dict[str, int] = {}
    for e in doc.events:
        statuses[e.status.value] = statuses.get(e.status.value, 0) + 1
    stretches = doc.coverage.not_assessable if doc.coverage else []
    print(
        f"{out}: {len(doc.wrist_peaks)} wrist peaks, {len(doc.events)} candidate events ("
        + (", ".join(f"{k} {v}" for k, v in sorted(statuses.items())) or "none")
        + f"), {len(stretches)} not-assessable stretches"
    )


def cmd_detect(args: argparse.Namespace) -> int:
    params = DetectorParams() if args.g_s is None else DetectorParams(g_s=args.g_s)
    if args.target.is_dir():
        _detect_one(
            args.target, args.out or args.target / STROKES_FILE, params, args.last_proxy_frame
        )
        return 0
    if args.results_dir is None:
        print("ERROR a MANIFEST needs --results-dir", file=sys.stderr)
        return 1
    if args.out is not None:
        print("ERROR --out is for one POSE_DIR; use --results-dir with a MANIFEST", file=sys.stderr)
        return 1
    for clip in load_manifest(args.target):
        _detect_one(
            clip.pose_dir,
            args.results_dir / f"{clip.clip_id}.strokes.json",
            params,
            args.last_proxy_frame,
        )
    return 0


def _clock(t: float) -> str:
    m, s = divmod(t, 60.0)
    return f"{int(m)}:{s:05.2f}"


def cmd_states(args: argparse.Namespace) -> int:
    events, stretches = v1_views(load_strokes(args.strokes))
    rows: list[tuple[float, str]] = []
    counts = dict.fromkeys(V1State, 0)
    for e in events:
        counts[e.state] += 1
        if e.state is V1State.DISCARDED and not args.show_discarded:
            continue
        t = e.t_s if e.t_s is not None else e.window_t_s[0]
        when = (
            _clock(t)
            if e.t_s is not None
            else f"{_clock(e.window_t_s[0])}-{_clock(e.window_t_s[1])}"
        )
        why = "; ".join(e.reasons) if e.reasons else "-"
        rows.append((t, f"{when:>17}  {STATE_LABEL[e.state]:<16} {e.event_id}  {why}"))
    for s in stretches:
        counts[s.state] += 1
        when = f"{_clock(s.start_t_s)}-{_clock(s.end_t_s)}"
        rows.append((s.start_t_s, f"{when:>17}  {STATE_LABEL[s.state]:<16} {s.reason}"))
    for _, line in sorted(rows, key=lambda r: r[0]):
        print(line)
    print(
        "; ".join(f"{STATE_LABEL[k]}: {v}" for k, v in counts.items())
        + ("" if args.show_discarded else " (scartati nascosti: --show-discarded)")
    )
    return 0


def cmd_validate_strokes(args: argparse.Namespace) -> int:
    doc = load_strokes(args.strokes)
    stale: list[str] = []
    if not args.no_inputs:
        stale = check_inputs(doc, args.inputs or args.strokes.parent)
    statuses: dict[str, int] = {}
    for e in doc.events:
        statuses[e.status.value] = statuses.get(e.status.value, 0) + 1
    grouped = sum(1 for p in doc.wrist_peaks if p.event_id is not None)
    print(
        f"{args.strokes.name}: {doc.schema_version}, producer {doc.producer.algorithm} "
        f"{doc.producer.algorithm_version}, time basis {doc.time_basis.value}"
    )
    print(
        f"  wrist peaks: {len(doc.wrist_peaks)} ({grouped} grouped, "
        f"{len(doc.wrist_peaks) - grouped} discarded); events: {len(doc.events)} "
        + (", ".join(f"{k} {v}" for k, v in sorted(statuses.items())) or "none")
    )
    if args.no_inputs:
        print("  input hashes: not checked (--no-inputs)")
    for problem in stale:
        print(f"WARNING {problem}", file=sys.stderr)
    if stale:
        print("STALE: strokes.json does not match its input files.", file=sys.stderr)
        return 1
    print("OK: schema and the eight invariants hold.")
    return 0


def _radii_arg(text: str) -> list[float]:
    """``--radii``: comma-separated finite numbers > 0; no empty item, no empty list."""
    items = text.split(",")
    if not text.strip():
        raise argparse.ArgumentTypeError("empty: give radii in seconds, e.g. 0.1,0.2,0.3,0.5")
    out: list[float] = []
    for n, item in enumerate(items, start=1):
        if not item.strip():
            raise argparse.ArgumentTypeError(f"{text!r}: item {n} is empty")
        try:
            value = float(item)
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"{text!r}: {item.strip()!r} is not a number"
            ) from None
        if not (math.isfinite(value) and value > 0):
            raise argparse.ArgumentTypeError(
                f"{text!r}: {item.strip()!r} must be a finite number > 0"
            )
        out.append(value)
    return out


def _search_arg(text: str) -> float:
    """``--outside-search-s``: one finite number > 0."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number") from None
    if not (math.isfinite(value) and value > 0):
        raise argparse.ArgumentTypeError(f"{text!r} must be a finite number > 0")
    return value


def cmd_evaluate(args: argparse.Namespace) -> int:
    annotated = args.annotated
    if annotated is None and (args.labels.parent / ANNOTATED_FILE).is_file():
        annotated = args.labels.parent / ANNOTATED_FILE
    doc = evaluate_dataset(
        args.manifest,
        args.labels,
        annotated,
        results_dir=args.results_dir,
        radii=args.radii,
        targets=frozenset(LabelStrokeType(t) for t in args.targets.split(",") if t),
        outside_search_s=args.outside_search_s,
        check_input_hashes=not args.no_input_hashes,
    )
    for w in doc.warnings:
        print(f"WARNING {w}", file=sys.stderr)
    for c in doc.clips:
        for w in c.warnings:
            print(f"WARNING {w}", file=sys.stderr)
    for line in summary_lines(doc):
        print(line)
    args.out.write_text(dump_eval(doc), encoding="utf-8")
    print(f"evaluation written: {args.out}")
    if not doc.clips:
        print("No clip had a strokes file: nothing evaluated.", file=sys.stderr)
        return 1
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    doc = StrokesEvalFile.model_validate_json(args.strokes_eval.read_bytes())
    clips = {c.clip_id: c for c in load_manifest(args.manifest)}
    strokes: dict[str, LoadedStrokes] = {}
    for ce in doc.clips:
        clip = clips.get(ce.clip_id)
        if clip is None:
            print(f"WARNING clip {ce.clip_id} not in the manifest", file=sys.stderr)
            continue
        path = (
            args.results_dir / f"{ce.clip_id}.strokes.json"
            if args.results_dir
            else clip.pose_dir / STROKES_FILE
        )
        if path.is_file():
            strokes[ce.clip_id] = load_events(path)
        else:
            print(
                f"WARNING clip {ce.clip_id}: {path.name} not found, event details unknown",
                file=sys.stderr,
            )
    review = load_review(args.review_in) if args.review_in else None
    rep = build_report(doc, clips, strokes, review)
    for s in rep.stale_review_rows:
        print(
            f"WARNING review line {s.line} ({s.clip_id} {s.event_id}): {s.reason}", file=sys.stderr
        )
    args.out.write_text(render_report(rep, source=args.strokes_eval.name), encoding="utf-8")
    print(f"report written: {args.out}")
    if args.json:
        args.json.write_text(rep.model_dump_json(indent=2), encoding="utf-8")
        print(f"data written: {args.json}")
    if args.review_csv:
        args.review_csv.write_text(render_review_csv(rep.false_candidates), encoding="utf-8")
        rows = sum(fc.in_review_sheet for fc in rep.false_candidates)
        print(f"review sheet written: {args.review_csv} ({rows} rows)")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="create MANIFEST.md and labels.csv templates")
    s.add_argument("dir", type=Path, nargs="?", default=DEFAULT_DIR)
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("list", help="list processed videos and their pose folders")
    s.add_argument("storage", type=Path, nargs="?", default=ROOT / "storage")
    s.set_defaults(func=cmd_list)

    for name, func, help_ in (
        ("check", cmd_check, "validate manifest, labels and pose files"),
        ("visibility", cmd_visibility, "input visibility around labelled impacts"),
        ("compare", cmd_compare, "compare labels with strokes.json events"),
    ):
        s = sub.add_parser(name, help=help_)
        s.add_argument("manifest", type=Path)
        s.add_argument("labels", type=Path)
        s.set_defaults(func=func)
        if name == "check":
            s.add_argument(
                "--annotated",
                type=Path,
                default=None,
                help=f"annotated intervals (default: {ANNOTATED_FILE} next to LABELS, if any)",
            )
        if name in ("visibility", "compare"):
            s.add_argument("--out", type=Path, default=None, help="Markdown report file")
            s.add_argument("--json", type=Path, default=None, help="Raw data as JSON")
        if name == "visibility":
            s.add_argument("--window-s", type=float, default=DEFAULT_WINDOW_S)
            s.add_argument("--threshold", type=float, default=DEFAULT_VISIBILITY_THRESHOLD)
        if name == "compare":
            s.add_argument("--results-dir", type=Path, default=None)
            s.add_argument("--tolerance-s", type=float, default=DEFAULT_TOLERANCE_S)
            s.add_argument("--targets", default=",".join(sorted(t.value for t in DEFAULT_TARGETS)))

    s = sub.add_parser("detect", help="experimental M2.3 detector: write strokes.json")
    s.add_argument("target", type=Path, help="a POSE_DIR, or a MANIFEST.md with --results-dir")
    s.add_argument("--out", type=Path, default=None, help="strokes.json to write (POSE_DIR)")
    s.add_argument(
        "--results-dir", type=Path, default=None, help="folder for <clip_id>.strokes.json"
    )
    s.add_argument("--g-s", type=_search_arg, default=None, help="grouping gap G (exploration)")
    s.add_argument(
        "--last-proxy-frame",
        type=int,
        default=None,
        help="use no frame after this one (data splits, e.g. Development B: 4499)",
    )
    s.set_defaults(func=cmd_detect)

    s = sub.add_parser("states", help="V1 view: states and reasons in words")
    s.add_argument("strokes", type=Path)
    s.add_argument("--show-discarded", action="store_true", help="also list discarded events")
    s.set_defaults(func=cmd_states)

    s = sub.add_parser("validate-strokes", help="check a strokes.json 1.x file")
    s.add_argument("strokes", type=Path)
    g = s.add_mutually_exclusive_group()
    g.add_argument("--inputs", type=Path, default=None, help="folder with pose.json/timemap.json")
    g.add_argument("--no-inputs", action="store_true", help="skip the input hash check")
    s.set_defaults(func=cmd_validate_strokes)

    s = sub.add_parser("evaluate", help="associate strokes.json events with labels")
    s.add_argument("manifest", type=Path)
    s.add_argument("labels", type=Path)
    s.add_argument("--out", type=Path, required=True, help="strokes_eval.json to write")
    s.add_argument(
        "--annotated",
        type=Path,
        default=None,
        help=f"annotated intervals (default: {ANNOTATED_FILE} next to LABELS, if any)",
    )
    s.add_argument("--results-dir", type=Path, default=None)
    s.add_argument(
        "--radii",
        type=_radii_arg,
        default=list(DEFAULT_RADII_S),
        help="comma-separated radii in seconds, each finite and > 0",
    )
    s.add_argument("--targets", default=",".join(sorted(t.value for t in DEFAULT_TARGETS)))
    s.add_argument(
        "--outside-search-s",
        type=_search_arg,
        default=DEFAULT_OUTSIDE_SEARCH_S,
        help="search limit of event_outside_radius in seconds, finite and > 0",
    )
    s.add_argument(
        "--no-input-hashes", action="store_true", help="skip the pose/timemap sha256 check"
    )
    s.set_defaults(func=cmd_evaluate)

    s = sub.add_parser("report", help="tables and review sheet from strokes_eval.json")
    s.add_argument("strokes_eval", type=Path)
    s.add_argument("manifest", type=Path)
    s.add_argument("--out", type=Path, required=True, help="Markdown report to write")
    s.add_argument("--results-dir", type=Path, default=None)
    s.add_argument("--json", type=Path, default=None, help="report data as JSON")
    s.add_argument("--review-csv", type=Path, default=None, help="false-candidate sheet")
    s.add_argument("--review-in", type=Path, default=None, help="filled sheet to merge back")
    s.set_defaults(func=cmd_report)

    args = p.parse_args(argv)
    try:
        return int(args.func(args))
    except TennisCVError as exc:
        return _err(exc)
    except ValueError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
