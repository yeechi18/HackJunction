"""A cross-platform API entry point using Psycopg's supported event loop."""

import argparse
import asyncio

import uvicorn

from company.runtime import configure_asyncio


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the company API; no reload subprocess.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    arguments = parser.parse_args()
    configure_asyncio()
    server = uvicorn.Server(
        uvicorn.Config("company.main:app", host=arguments.host, port=arguments.port)
    )
    asyncio.run(server.serve())


if __name__ == "__main__":
    main()

