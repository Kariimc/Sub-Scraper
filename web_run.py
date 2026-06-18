#!/usr/bin/env python3
"""Launch the Sub-Scraper web interface."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def main() -> None:
    try:
        import uvicorn
    except ImportError:
        print("uvicorn is not installed. Run: pip install -r requirements-web.txt")
        sys.exit(1)

    uvicorn.run(
        "sub_scraper.web.server:app",
        host="0.0.0.0",
        port=8080,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
