# Biomechanics

## Principles

1. **Measurement ≠ interpretation.** A measurement is a number with a method; an
   interpretation is a conclusion drawn from one or more measurements; a recommendation
   is an action. They are separate types (`Observation`, `Interpretation`,
   `Recommendation` in `tennis_cv.biomechanics.layers`).
2. **Observability first.** Before computing a metric, decide whether it *can* be
   observed in this footage (camera angle, visibility, resolution, frame rate). If not,
   the result is `value = None, observability = not_observable` — enforced by the
   `Measurement` validator.
3. **No invented precision.** Report units and rounding that the method supports.
4. **Confidence is explicit and honest.** Where no calibrated confidence exists, say so
   (today, joint-angle confidence = minimum landmark visibility, labelled as such).

## Measurement record

`value · unit · interval or frame · stroke · body part · confidence · observability ·
method (algorithm/version) · limitations`.

## Initial variables (2D, single camera)

| Metric id | What | Best view | Requires | Status |
| --- | --- | --- | --- | --- |
| `elbow_angle_projected` | Shoulder–elbow–wrist angle in image plane | Side, front | Contact frame | Calculator implemented, not validated |
| `knee_flexion_projected` | 180° − hip–knee–ankle angle | Side | Loading phase | Defined |
| `shoulder_hip_separation_projected` | Shoulder line vs hip line orientation | Behind/front | Backswing | Defined |
| `unit_turn_timing` | Time to completed shoulder turn | Any | Stroke + ball events | Defined |
| `contact_distance_body` | Wrist–hip horizontal distance at contact, in torso lengths | Behind/front | Contact frame | Defined |
| `contact_height` | Wrist height relative to hips at contact | Side, behind | Contact frame | Defined |
| `base_width` | Ankle distance / hip width | Behind/front | Contact | Defined |
| `head_stability` | Nose displacement around contact | Any static camera | Contact | Defined |

Distances are normalised by body segments (torso length, hip width) so that players at
different distances from the camera can be compared; pixel distances are never compared
across videos.

## Required evidence

Every metric value references: the frame index or interval, the pose frames used, the
camera view, and the landmark visibilities. Issues reference the strokes and frames that
support them so the UI can show the evidence.

## Observability conditions

| Condition | Effect |
| --- | --- |
| Joint visibility below threshold (default 0.6) | Metric not observable |
| Camera view not in metric's `suitable_views` | Not observable (or partially, with reason) |
| Short side < 720 px or player height small in frame | Distal joints (wrist) partially observable |
| fps < 50 | Contact timing/phase boundaries coarse; timing metrics flagged |
| Motion blur on arm/racket | Racket/wrist metrics partially observable |
| Moving/panning camera | Displacement metrics not observable until stabilised |
| Occlusion (net, other player, body self-occlusion) | Per-joint not observable |

## Limitations of 2D analysis

- Out-of-plane motion is foreshortened: a 160° elbow can appear as 120° from the front.
- Rotations about the vertical axis (hips, trunk) are only *proxies* in 2D and depend
  strongly on viewpoint.
- MediaPipe `z` is a relative depth estimate, not metric; it is stored but not used for
  measurement.
- Pose models are trained on general activity; extreme tennis poses (full extension,
  racket occluding the arm) reduce accuracy.
- Without camera calibration, absolute distances and speeds are not measurable.

Multi-view and 3D are introduced only when an experiment shows the 2D limits block an
important metric (see ROADMAP M6).

## Facts vs interpretation

| Layer | Example | Allowed source |
| --- | --- | --- |
| Observation | "Elbow angle at the estimated contact frame (#412) is 142° (2D, side view, confidence 0.81)." | Measurements only |
| Interpretation | "The hitting arm is moderately bent at contact; for a semi-western grip this is within normal stylistic range." | Observations + domain knowledge, with confidence and alternatives |
| Recommendation | "Shadow swings emphasising extension through contact; compare against execution #7." | Interpretations |

## Validation before "validated" status

A metric moves from `implemented` to `validated` only after comparison against manual
annotation on a labelled set of real amateur clips (see TESTING_STRATEGY.md), with
reported error and the viewing conditions under which it holds.
