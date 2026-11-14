from __future__ import annotations

import asyncio
import base64
import io
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.generator import DeepSeekGenerator  # noqa: E402
from app.models import Company  # noqa: E402
from app.prompts import parse_post_response  # noqa: E402


def _png_b64() -> str:
    image = Image.new("RGB", (128, 128), (30, 90, 140))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)

        if self.path.endswith("/images/generations"):
            body = json.dumps({"data": [{"b64_json": _png_b64()}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        content = (
            "ЗАГОЛОВОК: Стекло, которое работает на проект\n"
            "ТЕКСТ:\n"
            "Архитектурное стекло задаёт характер здания и помогает использовать естественный свет.\n"
            "Для проектировщиков важны стабильность качества, крупные форматы и техническая экспертиза.\n"
            "ХЕШТЕГИ: #стекло #архитектура #фасады\n"
            "ИЗОБРАЖЕНИЕ: photorealistic modern glass facade, daylight, no text, no logos"
        )
        body = json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: ANN002
        return


async def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 8877), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    company = Company(
        id="c-test",
        name="АО «Салаватстекло»",
        description="Производитель листового и архитектурного стекла",
        products="Архитектурное стекло, стеклопакеты",
        audience="Архитекторы и проектировщики",
        advantages="Полный цикл, лаборатория, 60 лет опыта",
    )
    generator = DeepSeekGenerator(
        base_url="http://127.0.0.1:8877/v1",
        api_key="test-key",
        model="test-model",
        images_dir=ROOT / "data" / "images",
        image_base_url="http://127.0.0.1:8877/v1",
        image_api_key="test-image-key",
        image_model="gpt-image-2.5",
    )
    target = ROOT / "data" / "images" / "mock-post.png"
    raw, image = await generator.generate_post(
        company=company,
        request="Подготовь пост про архитектурное стекло",
        history="История пуста.",
        image_path=target,
    )
    draft = parse_post_response(raw)
    print("RAW_OK", bool(raw))
    print("HEADLINE", draft.headline)
    print("IMAGE", image, Path(image).stat().st_size)
    server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
