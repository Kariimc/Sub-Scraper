#!/usr/bin/env python3
"""Launch the Sub-Scraper web interface."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def main() -> None:
    try:
        import uvicorn
    except ImportError:
        print("uvicorn is not installed. Run: pip install -r requirements-web.txt")
        sys.exit(1)

    # Hosting platforms (Railway, Render, Fly, Heroku, …) inject the port to bind
    # to via $PORT — honour it so the platform's health check + routing work.
    # Falls back to 8080 for local runs.
    port = int(os.environ.get("PORT", "8080"))
    host = os.environ.get("HOST", "0.0.0.0")

    print(f"Starting Sub-Scraper web UI on http://{host}:{port}")
    uvicorn.run(
        "sub_scraper.web.server:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
