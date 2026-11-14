import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.runtime import store, trend_analyzer  # noqa: E402


async def main() -> int:
    try:
        result = await trend_analyzer.refresh()
        output = ROOT / "data" / "trend_analysis.txt"
        output.write_text(result["summary"], encoding="utf-8")
        print("POSTS", len(result["posts"]))
        print("IDEAS", len(result["ideas"]))
        print("SUMMARY_FILE", output)
        if result.get("errors"):
            print("ERRORS", result["errors"])
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
