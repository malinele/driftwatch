"""Demo app that reads its configuration from the environment."""

import os

MODE = os.environ["APP_MODE"]
FLAG = os.environ.get("DEMO_FLAG", "")
