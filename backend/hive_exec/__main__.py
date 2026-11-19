"""Run the Executive service: python -m hive_exec (from the backend/ directory)."""

import uvicorn

from .api import create_app
from .config import ExecSettings
from .llm.providers import store_path


def main() -> None:
    settings = ExecSettings.from_env()
    print(f"HIVE Executive on http://{settings.host}:{settings.port}/api/exec "
          f"(gateway={settings.gateway}, default planner={settings.default_planner}, "
          f"providers={store_path()})")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
