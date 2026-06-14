#!/usr/bin/env bash
# Generate the synthetic test clips the test suite uses (ffmpeg lavfi sources).
# Deterministic and dependency-free so CI and local runs match.
set -eu
cd "$(dirname "$0")/.."
mkdir -p videos
ff(){ ffmpeg -y -loglevel error "$@"; }

ff -f lavfi -i "testsrc2=size=640x360:rate=30" -f lavfi -i "sine=frequency=440:duration=6" \
   -t 6 -pix_fmt yuv420p videos/test.mp4
ff -f lavfi -i "mandelbrot=size=640x480:rate=24:end_scale=0.3" -t 5 -pix_fmt yuv420p videos/mandel.mp4
ff -f lavfi -i "life=size=320x240:rate=24:mold=10:ratio=0.1:death_color=#101030:life_color=#30ff80" \
   -t 5 -pix_fmt yuv420p videos/life.mp4
ff -f lavfi -i "smptebars=size=640x360:rate=24" \
   -t 4 -pix_fmt yuv420p videos/bars.mp4

echo "generated: $(ls videos/*.mp4 | tr '\n' ' ')"
