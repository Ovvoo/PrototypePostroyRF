import json
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import bot, dp
from aiogram.types import Update
from http.server import BaseHTTPRequestHandler


async def process_update(update_data: dict):
    update = Update.model_validate(update_data)
    await dp.feed_update(bot=bot, update=update)
    await bot.session.close()


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            update_data = json.loads(body)
            asyncio.run(process_update(update_data))
            self.send_response(200)
        except Exception as e:
            print(f"Webhook error: {e}")
            self.send_response(500)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot webhook is active")
