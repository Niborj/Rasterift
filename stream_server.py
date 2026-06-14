"""
stream_server.py
================
Streams the core Video-to-ASCII engine to the web via HTTP/WebSocket.
Dependencies: pip install fastapi uvicorn websockets

Priority Order:
  1. --playlist playlist.json  → JSON file (per-video vol, mode, path)
  2. --folder ./videos         → folder scan (filesystem order, not alphabetical)
  3. positional video arg      → single video (legacy behavior)
"""

import asyncio
import subprocess
import json
import re
import uuid
import hashlib
import numpy as np
import cv2
from pathlib import Path
from urllib.parse import quote
from fastapi import Body, FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import os
from websockets.exceptions import ConnectionClosed

# Import the existing engine (ascii_video_player2.py)
from ascii_video_player2 import VideoDecoder, AsciiMapper
from codec import encode_frame

app = FastAPI()


def get_video_dimensions(path: str) -> tuple[int, int]:
    """Quickly probe a video file to get (width, height) without decoding frames."""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise FileNotFoundError(f"Could not open video file: {path!r}")
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return w, h


def calc_auto_rows(cols: int, vid_w: int, vid_h: int, pixel_mode: bool) -> int:
    """
    Calculate rows from video aspect ratio.
    ASCII mode: characters are ~2x taller than wide, so divide by 2.
    Pixel mode: cells are square (CSS stretches), no correction needed.
    """
    ratio = vid_w / max(vid_h, 1)
    if pixel_mode:
        return max(1, round(cols / ratio))
    else:
        return max(1, round(cols / ratio / 2))

# Serve static files (style.css, app.js) from the project directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
SOURCE_CACHE_DIR = os.path.join(BASE_DIR, ".source_cache")
SUPPORTED_UPLOAD_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm"}


class RasteriftStaticFiles(StaticFiles):
    PRIVATE_SOURCE_NAMES = {"rasterift-latex-source.zip"}
    PRIVATE_SOURCE_SUFFIXES = (
        ".tex",
        ".synctex.gz",
        ".fls",
        ".aux",
        ".log",
        ".fdb_latexmk",
        ".out",
    )

    async def get_response(self, path: str, scope):
        name = Path(path).name
        if name in self.PRIVATE_SOURCE_NAMES or name.endswith(self.PRIVATE_SOURCE_SUFFIXES):
            raise HTTPException(status_code=404)
        return await super().get_response(path, scope)


app.mount("/static", RasteriftStaticFiles(directory=BASE_DIR), name="static")


def safe_upload_path(filename: str) -> Path:
    original = Path(filename)
    suffix = original.suffix.lower()
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", original.stem).strip(".-") or "video"
    unique = uuid.uuid4().hex[:8]
    return Path(UPLOAD_DIR) / f"{stem}-{unique}{suffix}"


def current_entry_defaults() -> dict:
    queue = getattr(app.state, "queue", [])
    idx = getattr(app.state, "current_index", 0)
    if queue and idx < len(queue):
        return queue[idx]
    return {}


def probe_video(path: str) -> dict:
    cap = cv2.VideoCapture(path)
    try:
        if not cap.isOpened():
            raise FileNotFoundError(f"Could not open video file: {path!r}")
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    finally:
        cap.release()

    if width <= 0 or height <= 0:
        raise ValueError("The video has no readable video stream.")

    return {
        "width": width,
        "height": height,
        "fps": fps,
        "frames": frame_count,
        "duration": frame_count / fps if fps > 0 and frame_count > 0 else 0,
    }


def library_item(video_id: str, path: str, kind: str, display_name: str | None = None) -> dict:
    stats = os.stat(path)
    meta = probe_video(path)
    return {
        "id": video_id,
        "name": display_name or os.path.basename(path),
        "kind": kind,
        "size": stats.st_size,
        "modified": stats.st_mtime,
        "source_url": f"/source?video_id={quote(video_id, safe='')}",
        **meta,
    }


def list_video_library() -> list[dict]:
    items = []
    startup_queue = getattr(app.state, "startup_queue", [])
    for idx, entry in enumerate(startup_queue):
        path = entry.get("video")
        if path and os.path.exists(path):
            try:
                items.append(library_item(f"startup:{idx}", path, "source", os.path.basename(path)))
            except (FileNotFoundError, ValueError):
                continue

    upload_path = Path(UPLOAD_DIR)
    if upload_path.exists():
        for path in sorted(upload_path.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if path.is_file() and path.suffix.lower() in SUPPORTED_UPLOAD_EXTENSIONS:
                try:
                    items.append(library_item(f"upload:{path.name}", str(path), "upload"))
                except (FileNotFoundError, ValueError):
                    continue

    return items


def probe_media_streams(path: str) -> dict:
    """Probe container/codecs with ffprobe for browser-source decisions."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_streams",
                "-show_format",
                "-of", "json",
                path,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, json.JSONDecodeError):
        return {}

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


def can_serve_directly_to_browser(path: str, media: dict | None = None) -> bool:
    """Return true when the selected file is already a conservative HTML video source."""
    media = media or probe_media_streams(path)
    streams = media.get("streams", [])
    fmt = media.get("format", {})
    format_names = set(str(fmt.get("format_name", "")).split(","))
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]

    if not video:
        return False

    video_ok = video.get("codec_name") == "h264"
    audio_ok = all(stream.get("codec_name") in {"aac", "mp3"} for stream in audio_streams)
    container_ok = bool(format_names.intersection({"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}))
    return video_ok and audio_ok and container_ok


def browser_source_cache_path(path: str) -> Path:
    source = Path(path).resolve()
    stats = source.stat()
    digest = hashlib.sha256(
        f"{source}:{stats.st_size}:{stats.st_mtime_ns}".encode("utf-8")
    ).hexdigest()[:20]
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", source.stem).strip(".-") or "video"
    return Path(SOURCE_CACHE_DIR) / f"{safe_stem}-{digest}.browser.mp4"


def browser_compatible_source(path: str) -> tuple[str, bool]:
    """Return a path that browser <video> can decode; transcode once when needed."""
    media = probe_media_streams(path)
    if can_serve_directly_to_browser(path, media):
        return path, False

    os.makedirs(SOURCE_CACHE_DIR, exist_ok=True)
    output = browser_source_cache_path(path)
    if output.exists() and output.stat().st_size > 0:
        return str(output), True

    temp_output = output.with_suffix(".tmp.mp4")
    temp_output.unlink(missing_ok=True)
    print(f"[SOURCE] Transcoding browser-safe copy: {path} → {output}")

    command = [
        "ffmpeg",
        "-y",
        "-i", path,
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        "-loglevel", "error",
        str(temp_output),
    ]
    subprocess.run(command, check=True)
    temp_output.replace(output)
    return str(output), True


def resolve_library_video(video_id: str) -> str:
    if video_id.startswith("startup:"):
        try:
            idx = int(video_id.split(":", 1)[1])
        except ValueError:
            raise HTTPException(status_code=404, detail="Video not found.")

        startup_queue = getattr(app.state, "startup_queue", [])
        if idx < 0 or idx >= len(startup_queue):
            raise HTTPException(status_code=404, detail="Video not found.")
        path = startup_queue[idx].get("video")
        if path and os.path.exists(path):
            return path

    if video_id.startswith("upload:"):
        filename = Path(video_id.split(":", 1)[1]).name
        path = (Path(UPLOAD_DIR).resolve() / filename).resolve()
        upload_root = Path(UPLOAD_DIR).resolve()
        if upload_root in path.parents and path.exists() and path.suffix.lower() in SUPPORTED_UPLOAD_EXTENSIONS:
            return str(path)

    raise HTTPException(status_code=404, detail="Video not found.")


def entry_for_video(path: str, mode: str) -> dict:
    text_cols = getattr(app.state, "default_text_cols", 200)
    pixel_cols = getattr(app.state, "default_pixel_cols", 450)
    vol = getattr(app.state, "default_vol", 1)

    if mode == "pixel":
        return {
            "video": path,
            "mode": 5,
            "vol": vol,
            "pixel": True,
            "cols": pixel_cols,
            "rows": getattr(app.state, "rows", 0),
        }

    return {
        "video": path,
        # UI Glyph mode uses the source-sampled color path: [glyph, R, G, B]
        # per cell. The paper's benchmarked Glyph result was monochrome, so
        # live Glyph payload rates can be higher than the manuscript figure.
        "mode": 5,
        "vol": vol,
        "pixel": False,
        "cols": text_cols,
        "rows": getattr(app.state, "rows", 0),
    }


def select_library_video(video_id: str, mode: str) -> dict:
    if mode not in {"original", "ascii", "pixel"}:
        raise HTTPException(status_code=400, detail="Choose original, ascii, or pixel mode.")

    path = resolve_library_video(video_id)
    item = library_item(video_id, path, "upload" if video_id.startswith("upload:") else "source")

    app.state.active_video_id = video_id
    app.state.ui_render_mode = mode
    app.state.current_index = 0

    if mode in {"ascii", "pixel"}:
        app.state.queue = [entry_for_video(path, mode)]

    return {
        "mode": mode,
        "video": item,
        "source_url": item["source_url"],
    }

def get_html_content():
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read()

def resolve_video_path(video: str) -> str:
    """
    Resolves a video path by checking multiple locations in order:
      1. As-is (absolute or relative to CWD)
      2. Inside the project root (BASE_DIR)
      3. Inside BASE_DIR/videos/ subfolder
    Returns the first path that exists, or the original string if none found.
    """
    candidates = [
        video,
        os.path.join(BASE_DIR, video),
        os.path.join(BASE_DIR, "videos", os.path.basename(video)),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return video  # Return original; error will be caught during playback

def load_playlist(playlist_path: str) -> list[dict]:
    """Loads playlist from a JSON file and resolves all video paths."""
    with open(playlist_path, "r", encoding="utf-8") as f:
        items = json.load(f)
    for item in items:
        item["video"] = resolve_video_path(item["video"])
    return items

def load_folder(folder_path: str, default_mode: int, default_vol: int) -> list[dict]:
    """
    Scans a folder for video files in filesystem order (top to bottom,
    as they appear in the directory — not alphabetically sorted).
    """
    supported = (".mp4", ".mkv", ".avi", ".mov", ".webm")
    entries = []
    with os.scandir(folder_path) as it:
        for entry in it:
            if entry.is_file() and entry.name.lower().endswith(supported):
                entries.append({
                    "video": entry.path,
                    "mode":  default_mode,
                    "vol":   default_vol
                })
    # Filesystem order (no sort applied)
    return entries

def build_queue(args) -> list[dict]:
    """
    Builds the video queue based on argument priority:
      1. --playlist JSON file
      2. --folder directory
      3. Single positional video argument
    """
    if args.playlist:
        print(f"[PLAYLIST] Loading: {args.playlist}")
        items = load_playlist(args.playlist)
        # Fill missing fields with global defaults
        for item in items:
            item.setdefault("mode", args.mode)
            item.setdefault("vol",  args.vol)
            item.setdefault("pixel", args.pixel)
            
            is_pixel = item.get("pixel", False)
            default_cols = args.cols if args.cols is not None else (450 if is_pixel else 200)
            item.setdefault("cols", default_cols)
            item.setdefault("rows", args.rows)
        return items

    if args.folder:
        print(f"[FOLDER] Scanning: {args.folder}")
        items = load_folder(args.folder, args.mode, args.vol)
        default_cols = args.cols if args.cols is not None else (450 if args.pixel else 200)
        for item in items:
            item["pixel"] = args.pixel
            item["cols"] = default_cols
            item["rows"] = args.rows
        return items

    # Legacy: single video argument
    default_cols = args.cols if args.cols is not None else (450 if args.pixel else 200)
    return [{"video": resolve_video_path(args.video), "mode": args.mode, "vol": args.vol, "pixel": args.pixel, "cols": default_cols, "rows": args.rows}]


# ── APP STATE ──────────────────────────────────────────────
# Queue is stored in app.state so the WebSocket endpoint can read it.
# current_index tracks which video is playing.
# loop flag controls infinite playback.
# ──────────────────────────────────────────────────────────

@app.get("/")
async def root():
    """Serves the Frontend (HTML/JS/CSS) file to the client."""
    return HTMLResponse(get_html_content())


@app.get("/videos")
async def videos():
    """Return the startup source plus uploaded videos available in the UI."""
    return {
        "videos": list_video_library(),
        "active_id": getattr(app.state, "active_video_id", "startup:0"),
        "mode": getattr(app.state, "ui_render_mode", "ascii"),
    }


@app.delete("/videos")
async def delete_video(payload: dict = Body(...)):
    """Delete an uploaded video and fall back to the original source if needed."""
    video_id = payload.get("video_id")
    if not isinstance(video_id, str) or not video_id:
        raise HTTPException(status_code=400, detail="Choose an uploaded video to delete.")
    if not video_id.startswith("upload:"):
        raise HTTPException(status_code=400, detail="Only uploaded videos can be deleted.")

    path = Path(resolve_library_video(video_id))
    path.unlink(missing_ok=True)

    mode = getattr(app.state, "ui_render_mode", "ascii")
    if getattr(app.state, "active_video_id", "startup:0") == video_id:
        fallback = "startup:0"
        try:
            select_library_video(fallback, mode)
        except HTTPException:
            app.state.queue = []
            app.state.active_video_id = None
            app.state.current_index = 0

    print(f"[DELETE] {path}")

    return {
        "deleted_id": video_id,
        "videos": list_video_library(),
        "active_id": getattr(app.state, "active_video_id", "startup:0"),
        "mode": getattr(app.state, "ui_render_mode", "ascii"),
    }


@app.post("/select")
async def select_video(payload: dict = Body(...)):
    """Select a library video and playback mode for the next stream."""
    video_id = payload.get("video_id")
    mode = payload.get("mode", "ascii")
    if not isinstance(video_id, str) or not video_id:
        raise HTTPException(status_code=400, detail="Choose a video.")
    if not isinstance(mode, str):
        raise HTTPException(status_code=400, detail="Choose original, ascii, or pixel mode.")
    return select_library_video(video_id, mode)


@app.get("/source")
async def source_video(video_id: str):
    """Serve a selected video file for original-source playback."""
    path = resolve_library_video(video_id)
    try:
        browser_path, transcoded = browser_compatible_source(path)
    except (FileNotFoundError, subprocess.CalledProcessError):
        raise HTTPException(status_code=500, detail="Could not prepare browser-compatible source video.")

    media_type = "video/mp4" if transcoded or Path(browser_path).suffix.lower() == ".mp4" else None
    return FileResponse(browser_path, media_type=media_type)


@app.post("/upload")
async def upload_video(file: UploadFile = File(...)):
    """Accept a local video upload, add it to the library, and select it."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Choose a video file to upload.")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in SUPPORTED_UPLOAD_EXTENSIONS:
        allowed = ", ".join(sorted(SUPPORTED_UPLOAD_EXTENSIONS))
        raise HTTPException(status_code=400, detail=f"Unsupported video type. Use: {allowed}.")

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    target = safe_upload_path(file.filename)

    size = 0
    try:
        with open(target, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                out.write(chunk)
    finally:
        await file.close()

    try:
        meta = probe_video(str(target))
    except (FileNotFoundError, ValueError):
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Rasterift could not read that video file.")

    video_id = f"upload:{target.name}"
    selected = select_library_video(video_id, getattr(app.state, "ui_render_mode", "ascii"))

    print(f"[UPLOAD] {file.filename} → {target} ({meta['width']}x{meta['height']}, {meta['fps']:.2f} FPS)")

    return {
        "filename": file.filename,
        "stored_as": target.name,
        "size": size,
        "mode": selected["mode"],
        "video": selected["video"],
        **meta,
    }


@app.get("/audio")
async def audio_stream():
    """
    Extracts and streams audio from the currently active video entry.
    Server-side volume control via the entry's 'vol' field (0-5 scale).
      0 = Muted (FFmpeg never runs)
      1 = Normal (1.0x)
      5 = Double  (2.0x)
    """
    queue = getattr(app.state, "queue", [])
    idx   = getattr(app.state, "current_index", 0)
    entry = queue[idx] if queue else {}

    vol_level  = entry.get("vol", 1)
    video_path = entry.get("video", "video.mp4")

    # vol 0 → skip audio entirely, no FFmpeg process
    if vol_level <= 0:
        from fastapi import Response
        return Response(status_code=204)

    if not os.path.exists(video_path):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Video file not found")

    # Map 1-5 → 1.0x-2.0x FFmpeg volume
    ffmpeg_vol = 1.0 + (vol_level - 1) * 0.25

    def audio_generator():
        process = subprocess.Popen(
            [
                "ffmpeg",
                "-i", video_path,
                "-vn",
                "-filter:a", f"volume={ffmpeg_vol}",
                "-acodec", "libmp3lame",
                "-ab", "128k",
                "-ar", "44100",
                "-f", "mp3",
                "-loglevel", "quiet",
                "pipe:1"
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )
        try:
            while True:
                chunk = process.stdout.read(4096)
                if not chunk:
                    break
                yield chunk
        finally:
            process.stdout.close()
            process.wait()

    return StreamingResponse(
        audio_generator(),
        media_type="audio/mpeg",
        headers={"Accept-Ranges": "bytes"}
    )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Streams ASCII frames for every video in the queue.
    Advances to the next entry automatically when a video ends.
    Loops back to the start if --loop is set.
    """
    await websocket.accept()

    # Opt-in adaptive codec (raw/zlib/delta). Legacy clients omit it and get
    # the original uncompressed binary protocol, byte-for-byte unchanged.
    adaptive = websocket.query_params.get("codec") == "adaptive"
    tolerance = getattr(app.state, "tolerance", 0)  # lossy colour drift budget

    queue = getattr(app.state, "queue", [])
    loop  = getattr(app.state, "loop", False)

    if not queue:
        await websocket.send_text("Error: No video in queue!")
        await websocket.close()
        return

    queue_index = 0  # local index; advances through the queue

    try:
        while True:
            entry      = queue[queue_index]
            video_path = entry["video"]
            render_mode= entry["mode"]
            pixel_mode = entry.get("pixel", False)
            cols       = entry.get("cols", 200)
            rows_cfg   = entry.get("rows", 0)

            # IMPORTANT: Update current_index BEFORE sending INIT so that
            # when the client reloads /audio in response to INIT, the endpoint
            # already serves the correct video's audio.
            app.state.current_index = queue_index

            print(f"[PLAYING] ({queue_index + 1}/{len(queue)}) {video_path}  "
                  f"mode={render_mode}  pixel={pixel_mode}  vol={entry['vol']}")

            # ── Auto-calculate rows if not specified ──
            try:
                vid_w, vid_h = get_video_dimensions(video_path)
            except FileNotFoundError:
                await websocket.send_text(f"Error: '{video_path}' not found!")
                queue_index += 1
                if queue_index >= len(queue):
                    if loop:
                        queue_index = 0
                    else:
                        break
                continue

            if rows_cfg == 0:
                rows = calc_auto_rows(cols, vid_w, vid_h, pixel_mode)
                print(f"[AUTO] {vid_w}x{vid_h} → grid {cols}x{rows}")
            else:
                rows = rows_cfg

            try:
                decoder = VideoDecoder(video_path, cols, rows, skip_gray=pixel_mode)
            except FileNotFoundError:
                await websocket.send_text(f"Error: '{video_path}' not found!")
                queue_index += 1
                if queue_index >= len(queue):
                    if loop:
                        queue_index = 0
                    else:
                        break
                continue

            mapper       = AsciiMapper()
            source_fps   = decoder.fps
            MAX_FPS      = 30
            char_byte_lut= np.array([ord(c) for c in mapper._lut], dtype=np.uint8)
            qb           = {5: 0, 4: 2, 3: 3, 2: 5}.get(render_mode, 0)

            # ── FPS DECIMATION ──
            # If source > 30 FPS, skip every Nth frame using grab() (no decode).
            # This halves CPU load for 60 FPS sources.
            if source_fps > MAX_FPS:
                skip_n = round(source_fps / MAX_FPS)  # e.g. 60/30 = 2
                effective_fps = source_fps / skip_n
            else:
                skip_n = 1
                effective_fps = source_fps
            frame_t = 1.0 / effective_fps

            await websocket.send_text(f"INIT:{effective_fps}:{render_mode}:{cols}:{rows}:{int(pixel_mode)}")
            if skip_n > 1:
                print(f"[FPS CAP] {source_fps} FPS → {effective_fps} FPS (skip every {skip_n} frames)")

            frame_buf = np.empty((rows, cols, 4), dtype=np.uint8) if render_mode > 1 else None

            import struct
            import time
            start_time = asyncio.get_event_loop().time()
            bw_start_time = time.time()
            bw_bytes_sent = 0
            bw_raw_bytes = 0
            debug_mode = getattr(app.state, "debug", False)
            frame_index = 0
            prev_frame = None  # previous framebuffer snapshot for delta coding

            # Pre-allocate send buffer WITH header space to avoid per-frame concat
            if pixel_mode:
                # Zero-Copy Pixel: 4-byte header + raw BGR (3 bytes per pixel)
                pixel_send_buf = bytearray(4 + rows * cols * 3)
            elif render_mode > 1:
                # ASCII Color: 4-byte header + [char,R,G,B] per pixel
                ascii_send_buf = bytearray(4 + rows * cols * 4)

            raw_frame_num = 0
            try:
                while True:
                    # ── FPS DECIMATION via grab() ──
                    # For 60→30 fps: grab (skip) 1 frame, then decode 1 frame.
                    # grab() is ~10x faster than read() because it skips decoding.
                    for _ in range(skip_n - 1):
                        if not decoder.grab():
                            break  # EOF reached during skip

                    try:
                        gray_frame, bgr_frame = next(decoder)
                    except StopIteration:
                        break

                    if pixel_mode:
                        # ── PIXEL MODE: raw BGR (3 bytes/cell) ──
                        raw_size = 4 + rows * cols * 3
                        if adaptive:
                            msg, prev_frame = encode_frame(
                                np.ascontiguousarray(bgr_frame),
                                prev_frame, frame_index, tolerance=tolerance)
                            await websocket.send_bytes(msg)
                            bw_bytes_sent += len(msg)
                            bw_raw_bytes += raw_size
                        else:
                            # ── ZERO-COPY PIXEL MODE (legacy) ──
                            struct.pack_into(">I", pixel_send_buf, 0, frame_index)
                            pixel_send_buf[4:] = bgr_frame.tobytes()
                            await websocket.send_bytes(bytes(pixel_send_buf))
                            bw_bytes_sent += len(pixel_send_buf)
                            bw_raw_bytes += len(pixel_send_buf)
                    else:
                        indices = np.floor_divide(gray_frame, max(1, 256 // mapper._n))
                        np.clip(indices, 0, mapper._n - 1, out=indices)

                        if render_mode == 1:
                            char_matrix = mapper._lut[indices]
                            lines = [''.join(row) for row in char_matrix]
                            payload = f"{frame_index}\n" + '\n'.join(lines)
                            await websocket.send_text(payload)
                            payload_size = len(payload.encode('utf-8'))
                            bw_bytes_sent += payload_size
                            bw_raw_bytes += payload_size
                        else:
                            char_codes = char_byte_lut[indices]
                            rgb = bgr_frame[:, :, ::-1]
                            if qb > 0:
                                rgb = (rgb >> qb) << qb
                            frame_buf[:, :, 0] = char_codes
                            frame_buf[:, :, 1:] = rgb
                            raw_size = 4 + rows * cols * 4
                            if adaptive:
                                msg, prev_frame = encode_frame(
                                    frame_buf, prev_frame, frame_index,
                                    tolerance=tolerance)
                                await websocket.send_bytes(msg)
                                bw_bytes_sent += len(msg)
                                bw_raw_bytes += raw_size
                            else:
                                struct.pack_into(">I", ascii_send_buf, 0, frame_index)
                                ascii_send_buf[4:] = frame_buf.tobytes()
                                await websocket.send_bytes(bytes(ascii_send_buf))
                                bw_bytes_sent += len(ascii_send_buf)
                                bw_raw_bytes += len(ascii_send_buf)

                    current_time = time.time()
                    if debug_mode and current_time - bw_start_time >= 1.0:
                        raw_kbps = bw_raw_bytes / 1024
                        wire_kbps = bw_bytes_sent / 1024
                        ratio = raw_kbps / wire_kbps if wire_kbps > 0 else 0
                        print(f"[BW] RAW: {raw_kbps:.1f} KB/s | WIRE: {wire_kbps:.1f} KB/s | {ratio:.1f}x compression")
                        bw_start_time = current_time
                        bw_bytes_sent = 0
                        bw_raw_bytes = 0

                    elapsed = asyncio.get_event_loop().time() - start_time
                    wait = (frame_index * frame_t) - elapsed
                    if wait > 0:
                        await asyncio.sleep(wait)
                    
                    frame_index += 1

            finally:
                decoder.release()

            # Video finished → advance queue
            queue_index += 1
            if queue_index >= len(queue):
                if loop:
                    print("[LOOP] Restarting queue from the beginning.")
                    queue_index = 0
                else:
                    print("[DONE] All videos finished.")
                    break

    except (WebSocketDisconnect, ConnectionClosed):
        print("Client disconnected from the stream.")


ASCII_LOGO = "\033[36m" + r"""
 _____         _   _     _           
|_   _|____  _| |_(_) __| | ___  ___ 
  | |/ _ \ \/ / __| |/ _` |/ _ \/ _ \
  | |  __/>  <| |_| | (_| |  __/ (_) |
  |_|\___/_/\_\\__|_|\__,_|\___|\___/ 
""" + "\033[0m"

HELP_TEXT = "\033[1;37m" + """
╔═══════════════════════════════════════════════════╗
║               Rasterift —  COMMANDS               ║
╠═══════════════════════════════════════════════════╣
║                                                   ║
║  \033[36m/help\033[1;37m      Show this help message               ║
║  \033[36m/status\033[1;37m    Show current server & playback info  ║
║  \033[36m/quit\033[1;37m      Stop the server and exit             ║
║                                                   ║
╠═══════════════════════════════════════════════════╣
║             CLI LAUNCH OPTIONS                    ║
╠═══════════════════════════════════════════════════╣
║                                                   ║
║  \033[33m─── Source ───\033[1;37m                                  ║
║  \033[32mvideo\033[1;37m          Path to a single video file      ║
║  \033[32m--playlist\033[1;37m     JSON playlist file               ║
║  \033[32m--folder\033[1;37m       Play all videos in a folder      ║
║                                                   ║
║  \033[33m─── Render ───\033[1;37m                                  ║
║  \033[32m--mode\033[1;37m  \033[35m1-5\033[1;37m    Color quality                    ║
║     1=B&W  2=512c  3=32Kc  4=262Kc  5=16M        ║
║  \033[32m--pixel\033[1;37m        Pixel block mode (with mode 2-5) ║
║  \033[32m--cols\033[1;37m  \033[35mN\033[1;37m      Grid columns  (default: 200)     ║
║  \033[32m--rows\033[1;37m  \033[35mN\033[1;37m      Grid rows     (default: auto)    ║
║                                                   ║
║  \033[33m─── Playback ───\033[1;37m                                ║
║  \033[32m--vol\033[1;37m   \033[35m0-5\033[1;37m    Volume (0=mute, 1=normal, 5=2x)  ║
║  \033[32m--loop\033[1;37m         Loop the playlist infinitely     ║
║  \033[32m--quality\033[1;37m \033[35mlvl\033[1;37m  Codec quality (lossless,low,etc) ║
║                                                   ║
║  \033[33m─── Server ───\033[1;37m                                  ║
║  \033[32m--port\033[1;37m  \033[35mN\033[1;37m      Server port    (default: 8000)    ║
║  \033[32m--debug\033[1;37m        Show bandwidth stats (RAW/WIRE)  ║
║                                                   ║
╚═══════════════════════════════════════════════════╝
""" + "\033[0m"


def print_status():
    """Prints current server status."""
    queue = getattr(app.state, "queue", [])
    idx   = getattr(app.state, "current_index", 0)
    loop  = getattr(app.state, "loop", False)
    cols  = getattr(app.state, "cols", 0)
    rows  = getattr(app.state, "rows", 0)

    print(f"\n\033[1;37m{'═'*55}\033[0m")
    print(f" \033[32m▶\033[0m \033[1mQueue\033[0m      : {len(queue)} video(s)")
    print(f" \033[32m▶\033[0m \033[1mNow Playing\033[0m: {idx + 1}/{len(queue)}")
    if queue and idx < len(queue):
        entry = queue[idx]
        px = ' \033[35m[PIXEL]\033[0m' if entry.get('pixel') else ''
        cols = entry.get('cols', cols)
        rows = entry.get('rows', rows)
        print(f" \033[32m▶\033[0m \033[1mVideo\033[0m      : \033[36m{entry['video']}\033[0m")
        print(f" \033[32m▶\033[0m \033[1mSettings\033[0m   : mode={entry['mode']}{px} vol={entry['vol']}")
    res_str = f"{cols}x{rows}" if rows > 0 else f"{cols}x(auto)"
    print(f" \033[32m▶\033[0m \033[1mResolution\033[0m : {res_str}")
    print(f" \033[32m▶\033[0m \033[1mLoop\033[0m       : {'ON' if loop else 'OFF'}")
    print(f"\033[1;37m{'═'*55}\033[0m\n")


def command_loop():
    """Interactive command listener — runs in main thread alongside uvicorn."""
    print(f" \033[90mType \033[36m/help\033[90m for available commands.\033[0m\n")
    while True:
        try:
            cmd = input().strip().lower()
            if cmd in ('/help', 'help'):
                print(HELP_TEXT)
            elif cmd in ('/status', 'status'):
                print_status()
            elif cmd in ('/quit', 'quit', 'exit'):
                print("\n \033[33m⏹  Shutting down Rasterift...\033[0m\n")
                os._exit(0)
            elif cmd:
                print(f" \033[90mUnknown command: '{cmd}'. Type \033[36m/help\033[90m for options.\033[0m")
        except (EOFError, KeyboardInterrupt):
            print("\n \033[33m⏹  Shutting down Rasterift...\033[0m\n")
            os._exit(0)


if __name__ == "__main__":
    import argparse
    import os
    import threading
    
    # Enable ANSI escape sequences on Windows
    os.system("")

    parser = argparse.ArgumentParser(
        description=f"{ASCII_LOGO}\nReal-Time ASCII Web Server\n"
                    "Stream local videos to your browser with high performance ASCII and Pixel rendering.",
        formatter_class=argparse.RawTextHelpFormatter
    )

    # ── Source ──
    src = parser.add_argument_group('\033[33mSource\033[0m')
    src.add_argument(
        "video",
        nargs="?",
        default="video.mp4",
        help="Single video file to stream"
    )
    src.add_argument(
        "--playlist",
        metavar="FILE",
        default=None,
        help="Path to a playlist JSON file\n"
             "  Format: [{\"video\": \"a.mp4\", \"mode\": 5, \"vol\": 3}, ...]"
    )
    src.add_argument(
        "--folder",
        metavar="DIR",
        default=None,
        help="Path to a folder; plays all videos in filesystem order"
    )

    # ── Render ──
    render = parser.add_argument_group('\033[33mRender\033[0m')
    render.add_argument(
        "--mode",
        type=int, choices=[1, 2, 3, 4, 5], default=5,
        help="Color quality: 1=B&W  2=512c  3=32Kc  4=262Kc  5=16M Ultra"
    )
    render.add_argument(
        "--pixel",
        action="store_true", default=False,
        help="Pixel mode: replaces ASCII characters with colored blocks (combine with --mode 2-5)"
    )
    render.add_argument("--cols", type=int, default=None, help="Grid columns (default: 200 for text, 450 for pixel)")
    render.add_argument("--rows", type=int, default=0,   help="Grid rows    (default: auto from video aspect ratio)")

    # ── Playback ──
    playback = parser.add_argument_group('\033[33mPlayback\033[0m')
    playback.add_argument(
        "--vol",
        type=int, default=1,
        help="Volume 0-5  (0=muted, 1=normal, 5=double)"
    )
    playback.add_argument("--loop", action="store_true", default=False, help="Loop the queue infinitely")
    playback.add_argument(
        "--quality",
        choices=["lossless", "high", "balanced", "low"], default="lossless",
        help="Adaptive-codec colour fidelity (lossless = bit-exact; lower = "
             "smaller stream via lossy temporal delta). Chars always exact."
    )

    # ── Server ──
    srv = parser.add_argument_group('\033[33mServer\033[0m')
    srv.add_argument("--port", type=int, default=8000, help="Server port (default: 8000)")
    srv.add_argument("--debug", action="store_true", default=False, help="Enable bandwidth debug logging (RAW vs WIRE)")

    args = parser.parse_args()

    # Validate: --pixel requires color mode (2-5)
    if args.pixel and args.mode == 1:
        print("[ERROR] --pixel requires a color mode (--mode 2-5). B&W mode is text-only.")
        exit(1)

    # Build the queue
    queue = build_queue(args)

    if not queue:
        print("[ERROR] No videos found. Check your --playlist / --folder / video argument.")
        exit(1)

    # Save state
    app.state.queue         = queue
    app.state.startup_queue = [entry.copy() for entry in queue]
    app.state.current_index = 0
    app.state.loop          = args.loop
    app.state.tolerance     = {"lossless": 0, "high": 4, "balanced": 8, "low": 16}[args.quality]
    app.state.debug         = args.debug
    global_default_cols     = args.cols if args.cols is not None else (450 if args.pixel else 200)
    app.state.cols          = global_default_cols
    app.state.rows          = args.rows
    app.state.default_mode  = args.mode
    app.state.default_vol   = args.vol
    app.state.default_pixel = args.pixel
    app.state.default_text_cols = args.cols if args.cols is not None else 200
    app.state.default_pixel_cols = args.cols if args.cols is not None else 450
    app.state.active_video_id = "startup:0"
    app.state.ui_render_mode = "pixel" if args.pixel else "ascii"

    # ── High FPS Warning ──
    high_fps_videos = []
    for entry in queue:
        cap = cv2.VideoCapture(entry['video'])
        if cap.isOpened():
            fps = cap.get(cv2.CAP_PROP_FPS)
            if fps > 35:  # Consider > 35 as high FPS
                high_fps_videos.append((entry['video'], fps))
        cap.release()

    if high_fps_videos:
        print("\n\033[1;33m[WARNING] High FPS Source(s) Detected:\033[0m")
        for vid, fps in high_fps_videos:
            print(f"  - \033[36m{vid}\033[0m is \033[1;31m{fps:.1f} FPS\033[0m")
        print("\033[33mRasterift is optimized for 24-30 FPS cinematic playback.")
        print("High FPS videos will automatically be decimated to ~30 FPS,")
        print("but performance may still drop depending on the system's CPU.")
        print("For optimal performance, we recommend using 30 FPS source videos.\033[0m\n")
        
        while True:
            choice = input("\033[1mDo you want to continue anyway? (y/n): \033[0m").strip().lower()
            if choice == 'y':
                break
            elif choice == 'n':
                print("Exiting...")
                exit(0)

    # ── Startup Banner ──
    print(ASCII_LOGO)
    print(f"\033[1;37m{'═'*55}\033[0m")
    print(f" \033[32m▶\033[0m \033[1mQueue\033[0m     : {len(queue)} video(s)")
    print(f" \033[32m▶\033[0m \033[1mLoop\033[0m      : {'ON' if args.loop else 'OFF'}")
    res_str = f"{global_default_cols}x{args.rows}" if args.rows > 0 else f"{global_default_cols}x(auto)"
    print(f" \033[32m▶\033[0m \033[1mResolution\033[0m: {res_str}")
    print(f" \033[32m▶\033[0m \033[1mDefault\033[0m   : mode={args.mode} | pixel={'ON' if args.pixel else 'OFF'} | vol={args.vol}")
    print(f"\033[1;37m{'─'*55}\033[0m")
    for i, entry in enumerate(queue, 1):
        px = ' \033[35m[PIXEL]\033[0m' if entry.get('pixel') else ''
        print(f"  {i:2}. \033[36m{entry['video']}\033[0m  (mode={entry['mode']}{px} vol={entry['vol']})")
    print(f"\033[1;37m{'═'*55}\033[0m\n")
    print(f" \033[1;32m🚀 Server live →\033[0m \033[4;36mhttp://localhost:{args.port}\033[0m\n")

    # ── Run server in background thread, command loop in main thread ──
    server_thread = threading.Thread(
        target=uvicorn.run,
        args=(app,),
        kwargs={
            "host": "0.0.0.0",
            "port": args.port,
            "ws_ping_interval": None,
            "ws_ping_timeout": None,
            "log_level": "warning",
        },
        daemon=True
    )
    server_thread.start()
    command_loop()
