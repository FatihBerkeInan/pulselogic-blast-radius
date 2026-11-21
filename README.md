# PulseLogic: Blast Radius

Original test-impact selector for the Builders Buildathon RWX track.

Build: `docker build --platform linux/amd64 -t pulselogic .`

The container reads `/data/tests.json` and `/data/commits/*.patch`, then prints one JSON line per commit. It uses baseline coverage and source dependencies from the pinned SQLGlot commit. Runtime verification uses fresh patched overlays with bounded CPU and wall time. No patch-answer lookup tables are included.

The repository contains only the runtime entry, its baseline dependency map, and required license/build files. Development reports, generated mutation outcomes, sample answers, credentials, and browser data are excluded.
