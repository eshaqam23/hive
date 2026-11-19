"""Run the Executive service: python -m hive_exec (from the backend/ directory)."""

import uvicorn

from .api import create_app
from .config import ExecSettings


def main() -> None:
    settings = ExecSettings.from_env()
    print(f"HIVE Executive on http://{settings.host}:{settings.port}/api/exec "
          f"(gateway={settings.gateway}, qwen={'configured' if settings.qwen.base_url else 'NOT configured'})")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
