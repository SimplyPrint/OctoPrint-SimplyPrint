# -*- coding: utf-8 -*-
#
# SimplyPrint
# Copyright (C) 2020-2022  SimplyPrint ApS
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

from __future__ import annotations
import asyncio
import logging
import requests
import base64
from tornado.ioloop import IOLoop

from typing import (
    TYPE_CHECKING,
    Callable,
    Optional,
)

if TYPE_CHECKING:
    from octoprint.plugin import PluginSettings

class WebcamStream:
    def __init__(
        self, settings: PluginSettings, image_callback: Callable[[str], None]
    ) -> None:
        self._settings = settings
        self._logger = logging.getLogger("octoprint.plugins.simplyprint")
        self.url: str = settings.global_get(["webcam", "snapshot"])
        self.running = False
        self.interval: float = 1.
        self.on_image_received = image_callback
        self.stream_task: Optional[asyncio.Task] = None
        self._connection_test_passed = False

    @property
    def webcam_connected(self):
        return self._connection_test_passed

    async def test_connection(self) -> None:
        if not hasattr(self, "_loop"):
            self._loop = IOLoop.current()
        img = await self._loop.run_in_executor(None, self.extract_image)
        self._connection_test_passed = img is not None

    def extract_image(self) -> Optional[str]:
        headers = {"Accept": "image/jpeg"}
        try:
            resp = requests.get(
                self.url, headers=headers, verify=False, timeout=2
            )
            resp.raise_for_status()
        except Exception:
            return None
        return self._encode_image(resp.content)

    def _encode_image(self, image: bytes) -> str:
        return base64.b64encode(image).decode()

    async def _stream(self) -> None:
        while self.running:
            try:
                ret = await self._loop.run_in_executor(
                    None, self.extract_image
                )
                if ret is not None:
                    self.on_image_received(ret)
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                break

    def start(self, interval: float) -> None:
        if not self.url.startswith("http"):
            self._logger.info(
                f"Invalid webcam url, aborting stream: {self.url}"
            )
            return
        if self.running:
            return
        if not hasattr(self, "_loop"):
            self._loop = IOLoop.current()
        self.interval = interval
        self.running = True
        self.stream_task = self._loop.add_callback(self._stream)

    def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        if self.stream_task is not None:
            self.stream_task.cancel()
            self.stream_task = None