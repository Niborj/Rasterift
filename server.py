"""
Production entry point for Render/Docker.

This imports the existing FastAPI app and starts Uvicorn in the foreground.
It intentionally avoids the interactive command loop and any stdin prompts.
"""

import os

import uvicorn

from stream_server import (
    QUALITY_TOLERANCES,
    app,
    build_default_queue,
    initialize_app_state,
)


def env_int(name: str, default: int | None) -> int | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return int(value)


def env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return float(value)


def main() -> None:
    default_video = os.environ.get("RASTERIFT_DEFAULT_VIDEO", "video.mp4")
    queue = build_default_queue(default_video) if default_video else []
    codec_quality = os.environ.get("RASTERIFT_CODEC_QUALITY", "high")
    initialize_app_state(
        queue,
        loop=False,
        tolerance=QUALITY_TOLERANCES.get(codec_quality, QUALITY_TOLERANCES["high"]),
        debug=False,
        cols=None,
        rows=0,
        mode=5,
        vol=1,
        pixel=False,
        ui_render_mode="ascii",
        max_fps=env_float("RASTERIFT_MAX_FPS", 24),
        max_text_cells=env_int("RASTERIFT_MAX_TEXT_CELLS", 12000),
        max_pixel_cells=env_int("RASTERIFT_MAX_PIXEL_CELLS", 60000),
    )

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        ws_ping_interval=None,
        ws_ping_timeout=None,
    )


if __name__ == "__main__":
    main()
