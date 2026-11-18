# Test fixtures

## Synthetic videos (generated, not committed)

`tests/conftest.py` generates every video the automated tests need with FFmpeg's
`testsrc2` source at test time, so the repository stays small and licence-clean:

| Fixture         | What it exercises                                              |
| --------------- | -------------------------------------------------------------- |
| `basic_video`   | 640x360, 30 fps, 2 s H.264 - metadata, frames, API lifecycle   |
| `rotated_video` | Same pixels with a 90° display matrix (portrait phone capture) |
| `vfr_video`     | Variable frame rate (10 fps then 30 fps)                       |
| `not_a_video`   | Text file with a video extension                               |
| `make_video()`  | Factory for custom size / fps / duration (e.g. too-small)      |

Test-pattern videos contain **no person**, so pose tests on them assert the honest
outcome: *not observable*. That is a deliberate check that the system does not
invent poses.

## Real tennis footage (not committed)

Real amateur smartphone footage is required to validate Milestone 1 (see
`docs/TESTING_STRATEGY.md`). Place clips you own or have permission to use in
`tests/fixtures/real/` (git-ignored) and describe each one in
`tests/fixtures/real/MANIFEST.md` (camera position, fps, resolution, lighting,
stroke types, handedness). Do not commit footage of other people or
copyrighted broadcast footage.
