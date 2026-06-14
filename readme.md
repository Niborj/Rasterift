# Rasterift

Rasterift is a local web demo and research prototype for comparing how the
same video behaves when it is carried as ordinary media, coloured glyphs, and a
reduced pixel stream.

The first goal is simple: upload a video, press play, switch modes, and see the
same content change form in real time. The research layer then explains what
changes in data rate, fidelity, browser rendering, and security observation
points as the representation changes.

## Why It Exists

Modern video codecs are extremely efficient, so Rasterift is not trying to
beat MP4, H.264, H.265, or AV1. Instead, it makes representation boundaries
visible.

A normal video file can be decoded into frames, transformed into glyph cells,
packed into reduced pixel fields, compressed into WebSocket payloads, and
reconstructed on a browser canvas. Rasterift lets you compare those paths side
by side and ask:

- What does the viewer still recognize?
- How much data does each representation carry?
- Which parts of the system can still inspect the content?
- Does a security or policy decision made on one representation still apply to
  another?

## Modes

| Mode | What happens | Why compare it |
| --- | --- | --- |
| Original | The selected source is played through a normal browser video element. | Baseline for ordinary playback and compressed source-file rate. |
| Glyph | Frames are sampled into coloured character cells and drawn on canvas. | Shows what survives when moving image data becomes text-shaped visual data. |
| Pixel | Frames are sampled into a reduced colour grid and drawn as image data. | Keeps more visual detail than Glyph while remaining smaller than raw full-resolution RGB. |

## Features

- Browser-based demo for Original, Glyph, and Pixel playback.
- Upload, select, and delete local videos from the UI.
- Data-throughput cards for Original, Glyph, Pixel, and raw RGB context.
- Colour Glyph rendering with sampled source colours applied to each glyph.
- Pixel rendering through reduced BGR cell fields and browser canvas
  reconstruction.
- Adaptive WebSocket frame codec using raw, zlib full-frame, and zlib delta
  payloads.
- Audio extraction and sync for transformed playback.
- Technical Pipeline and Policy Risk explainers.
- Public research paper as HTML and PDF.

## Requirements

- Python 3.11 or newer
- FFmpeg available on your `PATH`
- A modern browser

Install dependencies:

```bash
pip install -r requirements.txt
```

If you are not using `requirements.txt`, install the core packages directly:

```bash
pip install fastapi uvicorn opencv-python numpy websockets python-multipart
```

Install FFmpeg:

```bash
# macOS
brew install ffmpeg

# Ubuntu or Debian
sudo apt install ffmpeg

# Windows
winget install ffmpeg
```

## Run Locally

Place a demo video at `video.mp4`, or pass a video path explicitly:

```bash
python stream_server.py video.mp4 --port 8000
```

Open the app:

```text
http://localhost:8000/
```

Run from a folder of videos:

```bash
python stream_server.py --folder videos --port 8000
```

Run from a playlist:

```bash
python stream_server.py --playlist playlist.json --port 8000
```

Common options:

```bash
python stream_server.py video.mp4 --cols 200
python stream_server.py video.mp4 --pixel --cols 450
python stream_server.py video.mp4 --quality balanced
python stream_server.py video.mp4 --debug
```

`--cols` controls the transformed grid width. Rows are derived from the source
aspect ratio unless `--rows` is provided. Glyph defaults to 200 columns. Pixel
defaults to 450 columns.

## Throughput Model

Rasterift separates several accounting boundaries:

- Source container rate: selected file size divided by duration.
- Raw RGB rate: width times height times three colour bytes times FPS.
- Glyph payload rate: observed transformed glyph payload bytes per frame times
  source FPS.
- Pixel payload rate: observed transformed pixel payload bytes per frame times
  source FPS.

The research paper does not claim total network traffic or codec superiority.
WebSocket framing, HTTP headers, browser caching, TCP/IP overhead, TLS,
retransmission, initialization messages, and transformed audio are separate
layers.

## Tests

Generate codec fixtures and verify JavaScript decoding against Python output:

```bash
bash experiments/make_test_clips.sh
python experiments/gen_vectors.py
node experiments/check_vectors.js
```

Run the end-to-end WebSocket comparison against a live Rasterift server:

```bash
python stream_server.py videos/test.mp4 --port 8011
node experiments/test_e2e.js 8011 60
```

The end-to-end test compares legacy full-frame WebSocket output with adaptive
codec output decoded by the shipped browser decoder.

## Repository Notes

The public repository is intended to contain source code, UI assets, research
figures, the public HTML/PDF paper, compact measurement data, and tests.

The following are intentionally local-only:

- uploaded videos
- `.source_cache/` browser-compatible source copies
- large video fixtures and measurement clips
- virtual environments
- Python caches
- manuscript TeX source
- LaTeX build outputs

## License

Add or review the repository license before publishing or redistributing this
project. If this repository includes a `LICENSE` file, its copyright notices
and restrictions should be retained as required by that license.
