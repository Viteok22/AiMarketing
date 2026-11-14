from __future__ import annotations

import shutil
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAGE_ROOT = Path("C:/Temp/opencode/ai-marketer-build")
STAGE = STAGE_ROOT / "ai-marketer"
ZIP_DIR = Path("C:/Temp/opencode")


def copy_tree(source: Path, target: Path) -> None:
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
    )


def build() -> Path:
    if STAGE_ROOT.exists():
        shutil.rmtree(STAGE_ROOT)
    STAGE.mkdir(parents=True)

    copy_tree(ROOT / "app", STAGE / "app")
    copy_tree(ROOT / "scripts", STAGE / "scripts")
    copy_tree(ROOT / "tests", STAGE / "tests")

    for name in (
        "README.md",
        "DEMO.md",
        "CHECKLIST.md",
        "CONCEPT.md",
        "INSTALL_OTHER_PC.md",
        "requirements.txt",
        ".env.example",
        ".gitignore",
        "run.ps1",
        "restart.ps1",
    ):
        shutil.copy2(ROOT / name, STAGE / name)

    (STAGE / "data").mkdir()
    shutil.copy2(ROOT / "data" / "companies.json", STAGE / "data" / "companies.json")
    (STAGE / "data" / "images").mkdir()

    live_db = ROOT / "data" / "app.db"
    backup_db = STAGE / "data" / "app.db"
    source = sqlite3.connect(live_db)
    target = sqlite3.connect(backup_db)
    source.backup(target)
    copied_images: set[str] = set()
    try:
        for table in ("tasks", "variants"):
            rows = target.execute(
                f"SELECT id, image_path FROM {table} WHERE image_path != ''"
            ).fetchall()
            for row_id, image_path in rows:
                path = Path(image_path)
                if not path.is_absolute():
                    path = ROOT / path
                if not path.exists():
                    print(f"WARN: missing image {image_path}")
                    continue
                target_image = STAGE / "data" / "images" / path.name
                if path.name not in copied_images:
                    shutil.copy2(path, target_image)
                    copied_images.add(path.name)
                target.execute(
                    f"UPDATE {table} SET image_path=? WHERE id=?",
                    (f"data/images/{path.name}", row_id),
                )
        target.commit()
    finally:
        source.close()
        target.close()

    ZIP_DIR.mkdir(parents=True, exist_ok=True)
    zip_base = ZIP_DIR / f"ai-marketer-{datetime.now():%Y%m%d-%H%M}"
    zip_path = Path(shutil.make_archive(str(zip_base), "zip", root_dir=STAGE_ROOT, base_dir="ai-marketer"))
    return zip_path


def inspect(zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
    print("ZIP", zip_path)
    print("SIZE_MB", round(zip_path.stat().st_size / 1024 / 1024, 2))
    print("FILES", len(names))
    suspicious = [
        name
        for name in names
        if any(
            marker in name
            for marker in (".env", ".venv/", "chrome-profile", "data/screens", "app.db-wal", "app.db-shm", "__pycache__")
        )
    ]
    print("SUSPICIOUS", suspicious)
    print("HAS_DB", any(name.endswith("data/app.db") for name in names))
    print("IMAGES", sum(1 for name in names if name.startswith("ai-marketer/data/images/") and not name.endswith("/")))
    print("HAS_ENV", any(name.endswith(".env") for name in names))
    print("INSTALL", any(name.endswith("INSTALL_OTHER_PC.md") for name in names))


if __name__ == "__main__":
    inspect(build())
