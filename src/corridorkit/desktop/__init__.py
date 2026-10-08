"""Optional Qt desktop application.

The module does not import Qt at package import time so the geometry engine
remains usable in headless environments.
"""


def main() -> int:
    from corridorkit.desktop.app import main as run

    return run()
