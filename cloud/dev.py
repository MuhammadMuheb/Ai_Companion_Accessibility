"""Run the Vercel site locally: the page and the API on one port, like Vercel serves them.

    python cloud/dev.py            real chat (needs ANTHROPIC_API_KEY and NOVA_ACCESS_CODE or NOVA_PUBLIC=1)
    python cloud/dev.py --demo     no key needed: replies are a canned, streamed demo text
"""

from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "api"))

import index  # noqa: E402  (cloud/api/index.py)
from fastapi.staticfiles import StaticFiles  # noqa: E402


class _DemoStream:
    stop_reason = "end_turn"

    def __init__(self, params):
        last = params["messages"][-1]["content"]
        self.text_stream = self._words(f"Demo mode: you said “{last[:120]}”. On Vercel this reply comes from "
                                       f"{params['model']}, streamed word by word. Set ANTHROPIC_API_KEY to try it.")

    @staticmethod
    def _words(text):
        for word in text.split(" "):
            time.sleep(0.03)
            yield word + " "

    def get_final_message(self):
        return self


class _DemoClient:
    class beta:
        class messages:
            @staticmethod
            @contextmanager
            def stream(**params):
                yield _DemoStream(params)


def main() -> None:
    import uvicorn

    if "--demo" in sys.argv:
        index.get_client = lambda: _DemoClient()
        os.environ.setdefault("ANTHROPIC_API_KEY", "demo")
        os.environ.setdefault("NOVA_PUBLIC", "1")
    index.app.mount("/", StaticFiles(directory=HERE / "public", html=True), name="public")
    port = int(os.environ.get("PORT", "3000"))
    uvicorn.run(index.app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
