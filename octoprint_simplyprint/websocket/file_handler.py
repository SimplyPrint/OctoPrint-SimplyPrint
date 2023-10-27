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
import requests
import pathlib
import logging
import tempfile
import time
import re
from threading import Event as ThreadEvent
from tornado.ioloop import IOLoop
from tornado.escape import url_escape, url_unescape

from octoprint.filemanager.util import DiskFileWrapper
from octoprint.filemanager.destinations import FileDestinations
from octoprint.events import eventManager, Events


from typing import (
    TYPE_CHECKING,
    Tuple,
    Awaitable,
    Dict,
    List,
    Any,
)
if TYPE_CHECKING:
    from .simplyprint import SimplyPrintWebsocket


def escape_query_string(qs: str) -> str:
    parts = qs.split("&")
    escaped: List[str] = []
    for p in parts:
        item = p.split("=", 1)
        key = url_escape(item[0])
        if len(item) == 2:
            escaped.append(f"{key}={url_escape(item[1])}")
        else:
            escaped.append(key)
    return "&".join(escaped)


class SimplyPrintFileHandler:
    def __init__(self, socket: SimplyPrintWebsocket) -> None:
        self.socket = socket
        self.file_manager = socket.file_manager
        self.printer = socket.printer
        self._logger = logging.getLogger("octoprint.plugins.simplyprint")
        self.pending_file: str = ""
        self.download_progress = -1
        self.start_event = ThreadEvent()
        self.analysis_event = ThreadEvent()
        self.start_event.set()
        self.analysis_event.set()

    def download_file(self, url: str, start: bool) -> Awaitable:
        if not hasattr(self, "_loop"):
            self._loop = IOLoop.current()
        return self._loop.run_in_executor(
            None, self._download_sp_file, url, start
        )

    def _escape_url(self, url: str) -> str:
        # escape the url
        match = re.match(r"(https?://[^/?#]+)([^?#]+)?(\?[^#]+)?(#.+)?", url)
        if match is not None:
            uri, path, qs, fragment = match.groups()
            if path is not None:
                uri += "/".join([url_escape(p, plus=False)
                                 for p in path.split("/")])
            if qs is not None:
                uri += "?" + escape_query_string(qs[1:])
            if fragment is not None:
                uri += "#" + url_escape(fragment[1:], plus=False)
            url = uri
        return url

    def _parse_content_disposition(self, data: str) -> str:
        fnr = r"filename[^;\n=]*=(['\"])?(utf-8\'\')?([^\n;]*)(?(1)\1|)"
        matches: List[Tuple[str, str, str]] = re.findall(fnr, data)
        is_utf8 = False
        filename: str = ""
        for (_, encoding, fname) in matches:
            if encoding.startswith("utf-8"):
                # Prefer the utf8 filename if included
                filename = url_unescape(
                    fname, encoding="utf-8", plus=False)
                is_utf8 = True
                break
            filename = fname
        self._logger.debug(
            "Content-Disposition header received: filename = "
            f"{filename}, utf8: {is_utf8}"
        )
        return filename

    def _download_sp_file(self, url: str, start: bool):
        tmp_fname = f"sp-{time.monotonic_ns()}.gcode"
        tmp_path = pathlib.Path(tempfile.gettempdir()).joinpath(tmp_fname)
        size: int = 0
        downloaded: int = 0
        last_pct: int = 0
        url_parts = url.rsplit("/", 1)
        filename: str = tmp_fname
        if len(url_parts) == 2 and "." in url_parts[-1]:
            # The last fragment of the path looks like a filename,
            # initialize to that.
            filename = url_parts[-1]
        url = self._escape_url(url)
        self.download_progress = -1
        try:
            kwargs = dict(allow_redirects=True, stream=True, timeout=3600.)
            with requests.get(url, **kwargs) as resp:
                resp.raise_for_status()
                self._update_progress(0)
                size = int(resp.headers.get("content-length", 0))
                if "content-disposition" in resp.headers:
                    cd = resp.headers["content-disposition"]
                    fname = self._parse_content_disposition(cd)
                    if fname:
                        filename = fname
                with tmp_path.open("wb") as f:
                    for chunk in resp.iter_content(8192):
                        downloaded += len(chunk)
                        f.write(chunk)
                        if size:
                            pct = int(downloaded / size * 100 + .4)
                            if pct != last_pct:
                                last_pct = pct
                                self._update_progress(pct)
        except Exception as e:
            self._logger.exception("Error downloading print")
            self._loop.add_callback(
                self.socket.send_sp, "file_progress",
                {"state": "error", "message": "Network Error", "exception": str(e)}
            )
            return
        local = FileDestinations.LOCAL
        if not self.file_manager.folder_exists(local, "SimplyPrint"):
            self.file_manager.add_folder(local, "SimplyPrint")
        count: int = 0
        while count < 100:
            try:
                cpath, cname = self.file_manager.canonicalize(
                    local, f"SimplyPrint/{filename}"
                )
                folder = self.file_manager.sanitize_path(local, cpath)
                name = self.file_manager.sanitize_name(local, cname)
                dest_path = self.file_manager.join_path(local, folder, name)
                storage_path = self.file_manager.path_in_storage(local, dest_path)
                if self.printer.can_modify_file(storage_path, False):
                    fobj = DiskFileWrapper(tmp_fname, str(tmp_path))
                    added_file = self.file_manager.add_file(
                        local, storage_path, fobj, allow_overwrite=True,
                        display=cname
                    )
                    break
                count += 1
                # This file name won't work, try another one
                fparts = filename.split(".", 1)
                ext = "gcode" if len(fparts) < 2 else fparts[-1]
                filename = f"{fparts[0]}_copy{count}.{ext}"
            except Exception as e:
                self._logger.exception("Error locating file destination")
                self._loop.add_callback(
                    self.socket.send_sp, "file_progress",
                    {"state": "error", "message": "Error processing download: " + str(e)}
                )
                return
        else:
            self._loop.add_callback(
                self.socket.send_sp, "file_progress",
                {"state": "error", "message": "Error processing download"}
            )
            return
        self.analysis_event.clear()
        try:
            self.printer.select_file(storage_path, False, False)
        except Exception:
            pass
        selected = self.printer.is_current_file(storage_path, False)
        # Fire the upload event
        eventManager().fire(
            Events.UPLOAD,
            {
                "name": name,
                "path": added_file,
                "target": local,
                "select": selected,
                "print": False
            }
        )
        if not selected:
            self._loop.add_callback(
                self.socket.send_sp, "file_progress",
                {"state": "error", "message": "Error selecting file"}
            )
            self.analysis_event.set()
            return

        self.analysis_event.wait(5.)
        self.analysis_event.set()
        self.pending_file = storage_path
        if start:
            self.start_print()
        else:
            self._loop.add_callback(
                self.socket.send_sp, "file_progress",
                {"state": "ready"}
            )

    def start_print(self) -> bool:
        if not hasattr(self, "_loop"):
            # File has never been uploaded or selected
            return False
        if not self.pending_file:
            return False
        pending = self.pending_file
        self.pending_file = ""
        if not self.printer.is_current_file(pending, False):
            self._loop.add_callback(
                self.socket.send_sp, "file_progress",
                {"state": "error", "message": "File not selected"}
            )
            return False
        self.start_event.clear()
        self.printer.start_print()
        printing = self.start_event.wait(10.)
        self.start_event.set()
        if not printing:
            self._loop.add_callback(
                self.socket.send_sp, "file_progress",
                {"state": "error", "message": "Error starting print"}
            )
            return False
        return True

    def start_pending(self) -> bool:
        return not self.start_event.is_set()

    def notify_started(self) -> None:
        self.start_event.set()

    def _update_progress(self, percent: int) -> None:
        if percent == self.download_progress:
            return
        self.download_progress = percent
        self._loop.add_callback(
            self.socket.send_sp, "file_progress",
            {"state": "downloading", "percent": percent}
        )

    def check_analysis(self, payload: Dict[str, Any]) -> None:
        if self.analysis_event.is_set():
            return
        if self.printer.is_current_file(payload["path"], False):
            self.analysis_event.set()