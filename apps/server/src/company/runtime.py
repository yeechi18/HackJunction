"""Event loop compatibility for the native Windows/macOS CLI entry points."""

import asyncio
import sys


def configure_asyncio() -> None:
    # Psycopg async connections do not support Windows' default Proactor loop.
    # The project pins Python <3.13, where this policy is available.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

