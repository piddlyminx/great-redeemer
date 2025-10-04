from __future__ import annotations

import time

from .tasks import start_background_threads


def main() -> None:
    start_background_threads()
    # Block forever while threads run
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
