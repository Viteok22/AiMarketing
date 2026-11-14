import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.runtime import store, trend_analyzer  # noqa: E402
from app.store import DEFAULT_TREND_SOURCES  # noqa: E402


async def main() -> int:
    for row in store.list_trend_sources(enabled_only=False):
        store.delete_trend_source(row["channel"])
    for channel in DEFAULT_TREND_SOURCES:
        store.add_trend_source(channel)
    result = await trend_analyzer.refresh()
    output = ROOT / "data" / "trend_analysis.txt"
    output.write_text(result["summary"], encoding="utf-8")
    print("SOURCES", [row["channel"] for row in store.list_trend_sources()])
    print("POSTS", len(result["posts"]), "IDEAS", len(result["ideas"]))
    print("SUMMARY_FILE", output)
    if result.get("errors"):
        print("ERRORS", result["errors"])
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
