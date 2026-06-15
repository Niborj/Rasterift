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


def main() -> None:
    default_video = os.environ.get("RASTERIFT_DEFAULT_VIDEO", "video.mp4")
    queue = build_default_queue(default_video) if default_video else []
    initialize_app_state(
        queue,
        loop=False,
        tolerance=QUALITY_TOLERANCES["lossless"],
        debug=False,
        cols=None,
        rows=0,
        mode=5,
        vol=1,
        pixel=False,
        ui_render_mode="ascii",
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
