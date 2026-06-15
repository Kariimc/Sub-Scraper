#!/usr/bin/env python3
import faulthandler

from sub_scraper.gui.app import App


def main() -> None:
    # Print a Python traceback if the interpreter hard-crashes (segfault),
    # which a normal try/except cannot catch.
    faulthandler.enable()
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
