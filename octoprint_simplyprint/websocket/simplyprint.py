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
import json
import pathlib
import logging
import functools
import threading
import re
import tornado.websocket
from octoprint.util import server_reachable
from tornado.ioloop import IOLoop

from .constants import WS_TEST_ENDPOINT, WS_PROD_ENDPOINT
from .system import SystemQuery, SystemManager
from .webcam import WebcamStream
from .file_handler import SimplyPrintFileHandler
from ..comm.monitor import Monitor
import octoprint.server
import octoprint.util
from octoprint.plugin import PluginSettings
from octoprint.printer import PrinterInterface
from octoprint.filemanager import FileManager
from octoprint.events import Events, EventManager
import requests
import datetime

# XXX: The below imports are for inital dev and
# debugging.  They are used to create a logger for
# messages sent to and received from the simplyprint
# backend
import logging.handlers
from queue import SimpleQueue

from typing import (
    TYPE_CHECKING,
    Callable,
    Optional,
    Awaitable,
    Dict,
    List,
    Tuple,
    Union,
    Any,
    cast,
)
if TYPE_CHECKING:
    from tornado.websocket import WebSocketClientConnection
    from ..import SimplyPrint
    TimerCallback = Callable[[float], Union[float, Awaitable[float]]]


UPDATE_CHECK_TIME = 24. * 60. * 60
KEEPALIVE_TIME = 96.0
# TODO: Increase this time to something greater, perhaps 30 minutes
CONNECTION_ERROR_LOG_TIME = 60.
VALID_STATES= [
    "offline",  "operational", "printing", "cancelling",
    "pausing", "paused", "resuming", "error"
]
PRE_SETUP_EVENTS = [
    "connection", "state_change", "shutdown", "machine_data", "keepalive",
    "firmware"
]

class SimplyPrintWebsocket:
    def __init__(self, plugin: SimplyPrint) -> None:
        self.plugin = plugin
        self._logger = logging.getLogger("octoprint.plugins.simplyprint")
        self.settings = cast(PluginSettings, plugin._settings)
        self.printer = cast(PrinterInterface, plugin._printer)
        self.file_manager = cast(FileManager, plugin._file_manager)
        self.sys_manager = SystemManager(self)
        self._event_bus = cast(EventManager, plugin._event_bus)
        if self.settings.get_boolean(["debug_logging"]):
            self._logger.setLevel(logging.DEBUG)
        self.test = False
        if self.settings.get(["endpoint"]) == "test":
            self.test = True
        self.connected = False
        self.is_set_up = self.settings.get(["is_set_up"])
        self._set_ws_url()

        self.simplyprint_thread = threading.Thread(
            target=self._run_simplyprint_thread
        )
        self.simplyprint_thread.daemon = True
        self.is_closing = False
        self.is_connected = False
        self._user_input_req = False
        self.ws: Optional[WebSocketClientConnection] = None
        self.cache = ReportCache()
        self.current_layer: int = -1
        self.last_received_temps: Dict[str, float] = {}
        self.last_err_log_time: float = 0.
        self.download_progress: int = -1
        self.intervals: Dict[str, float] = {
            "job": 5.,
            "temps": 5.,
            "temps_target": 2.5,
            "cpu": 30.,
            "reconnect": 0,
            "ai": 60.,
            "ready_message": 60.
        }
        self.temp_timer = FlexTimer(self._handle_temperature_update)
        self.job_info_timer = FlexTimer(self._handle_job_info_update)
        self.cpu_timer = FlexTimer(self._handle_cpu_update)
        self.printer_reconnect_timer = FlexTimer(self._handle_printer_reconnect)
        self.update_timer = FlexTimer(self._handle_update_check)
        self.monitor = Monitor(
            logging.getLogger("octoprint.plugins.simplyprint.monitor")
        )
        self.ai_timer = FlexTimer(self._handle_ai_snapshot)
        self.failed_ai_attempts = 0
        self.scores = []
        self.reset_printer_display_timer = FlexTimer(self._reset_printer_display)
        self.webcam_stream = WebcamStream(self.settings, self._send_image)
        self.amb_detect = AmbientDetect(
            self.cache, self._on_ambient_changed,
            self.settings.get_int(["ambient_temp"])
        )
        self.file_handler = SimplyPrintFileHandler(self)
        self.heaters: Dict[str, str] = {}
        self.missed_job_events: List[Dict[str, Any]] = []
        self.keepalive_hdl: Optional[asyncio.TimerHandle] = None
        self.connection_task: Optional[asyncio.Task] = None
        self.reconnect_delay: float = 1.
        self.reconnect_token: Optional[str] = None
        self._last_ping_received: float = 0.
        self.gcode_terminal_enabled: bool = False
        self.cached_events: List[Tuple[Callable, Tuple[Any, ...]]] = []

        # XXX: The call below is for dev, remove before release
        self._setup_simplyprint_logging()


    ######################################################################
    #
    # Plugin Methods / Properties
    #
    # These methods are defined in the original "SimplyPrintComm" class
    # and called by the SimplyPrint plugin.  When it is no longer required
    # to support both they can be refactored.
    #
    ######################################################################

    def get_helpers(self, plugin_name: str) -> Optional[Dict[str, Callable]]:
        pm = self.plugin._plugin_manager
        return pm.get_helpers(plugin_name)  # type: ignore

    def on_startup(self) -> None:
        if self.simplyprint_thread.is_alive():
            return
        self.simplyprint_thread.start()

    def _run_simplyprint_thread(self) -> None:
        self._aioloop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._aioloop)
        self._aioloop.run_until_complete(self._initialize())

    def on_layer_change(self, layer: int) -> None:
        self.current_layer = layer

    def on_pause_at_command(self, message: str) -> None:
        # TODO: Not sure what to submit here
        pass

    def on_gcode_received(self, line: str) -> None:
        if not hasattr(self, "_loop"):
            return
        if self.gcode_terminal_enabled:
            self._loop.add_callback(
                self.send_sp, "term_update", {"response": [line]}
            )

    def on_gcode_sent(self, cmd: str) -> None:
        if not hasattr(self, "_loop"):
            return
        if self.gcode_terminal_enabled:
            self._loop.add_callback(
                self.send_sp, "term_update", {"command": [cmd]}
            )

    def _queue_event(self, func: Callable, *args) -> None:
        self.cached_events.append((func, args))

    def on_event(self, event: str, payload: Dict[str, Any]) -> None:
        if not hasattr(self, "_loop"):
            add_callback = self._queue_event
        else:
            add_callback = self._loop.add_callback

        if event == Events.PRINTER_STATE_CHANGED:
            state = payload["state_id"].lower()
            if state in ["operational", "printing", "pausing", "resuming"]:
                add_callback(self._on_state_event, state)
        elif event in [Events.CONNECTING, Events.DISCONNECTING]:
            add_callback(self._send_connection_state, event.lower())
        elif event == Events.CONNECTED:
            add_callback(self._on_printer_connected)
        elif event == Events.DISCONNECTED:
            add_callback(self._on_printer_disconnected)
        elif event == Events.ERROR:
            msg = payload.get("error", "")
            add_callback(self._on_printer_error, msg)
        elif event == Events.PRINT_STARTED:
            add_callback(self._on_print_start, payload)
        elif event == Events.PRINT_FAILED:
            if payload.get("reason", "") == "error":
                add_callback(self._on_print_done, "failed")
        elif event == Events.PRINT_DONE:
            add_callback(self._on_print_done, "finished")
        elif event == Events.PRINT_CANCELLING:
            msg = payload.get("firmwareError", "")
            add_callback(self._on_cancelling_event, msg)
        elif event == Events.PRINT_PAUSED:
            add_callback(self._on_print_paused)
        elif event == Events.PRINT_RESUMED:
            add_callback(self._on_print_resumed)
        elif event == Events.TOOL_CHANGE:
            if "new" not in payload:
                return
            add_callback(self._send_active_extruder, payload["new"])
        elif event == Events.FIRMWARE_DATA:
            add_callback(self._send_firmware_data, payload)
        elif event == Events.METADATA_ANALYSIS_FINISHED:
            add_callback(self._on_metadata_update, payload)
        elif event == "plugin_firmware_check_warning":
            add_callback(self._on_firmware_warning)
        elif event == "plugin_printer_safety_check_warning":
            add_callback(self._on_firmware_warning)
        elif event == "plugin_pi_support_throttle_state":
            add_callback(self._on_cpu_throttled, payload)
        elif event == "plugin_bedlevelvisualizer_mesh_data_collected":
            add_callback(self._send_mesh_data, payload["mesh"])
        elif event == Events.FILE_SELECTED:
            # TODO: not sure we need this one
            pass
        elif event == Events.FILE_REMOVED:
            # TODO: not sure we need this one
            pass
        elif event == "plugin_simplyfilamentsensor_filament_loaded":
            add_callback(self.send_sp, "filament_sensor", {"state": "loaded"})
        elif event == "plugin_simplyfilamentsensor_filament_runout":
            add_callback(self.send_sp, "filament_sensor", {"state": "runout"})
        elif event == "plugin_simplyfilamentsensor_filament_no_filament_print_on_print_start":
            add_callback(self.send_sp, "filament_sensor", {"state": "print_stopped"})
        elif event == "plugin_psucontrol_psu_state_changed":
            is_on = payload["isPSUOn"]
            add_callback(self.send_sp, "power_controller", {"on": is_on})
        elif event == "plugin_simplypowercontroller_power_on":
            add_callback(self.send_sp, "power_controller", {"on": True})
        elif event == "plugin_simplypowercontroller_power_off":
            add_callback(self.send_sp, "power_controller", {"on": False})
        if event in [
            "plugin_pluginmanager_install_plugin",
            "plugin_pluginmanager_uninstall_plugin",
            "plugin_pluginmanager_enable_plugin",
            "plugin_pluginmanager_disabled_plugin",
        ]:
            if event == "plugin_pluginmanager_uninstall_plugin":
                if payload.get("id", "") == "SimplyPrint":
                    self._logger.info("The SimplyPrint plugin was uninstalled")
            add_callback(self._send_installed_plugins)

    @property
    def user_input_required(self) -> bool:
        return self._user_input_req

    @user_input_required.setter
    def user_input_required(self, state: bool) -> None:
        if self._user_input_req != state:
            self._user_input_req = state
            if not self.printer.is_printing():
                return
            new_state = "paused" if state else "printing"
            self._loop.add_callback(self._update_state, new_state)

    def _monotonic(self) -> float:
        return self._aioloop.time()

    async def _initialize(self) -> None:
        self._loop = IOLoop.current()
        for (func, args) in self.cached_events:
            func(*args)
        self.cached_events = []
        self.update_timer.start(delay=60.)
        self.cpu_timer.start()
        if self.printer.is_operational():
            self._on_printer_connected()
        else:
            self._update_state_from_octo()
        self.cache.machine_data = await self._get_machine_data()
        await self.webcam_stream.test_connection()
        await self._connect()

    async def _get_machine_data(self) -> Dict[str, Any]:
        sys_query = SystemQuery(self.settings)
        data = await self._loop.run_in_executor(None, sys_query.get_system_info)
        data["ui"] = "OctoPrint"
        data["api"] = "OctoPrint"
        data["sp_version"] = self.plugin._plugin_version
        return data

    async def _connect(self) -> None:
        log_connect = True
        failed_attempts = 0
        while not self.is_closing:
            url = self.connect_url
            if self.reconnect_token is not None:
                url = f"{self.connect_url}/{self.reconnect_token}"
            if log_connect:
                self._logger.info(f"Connecting To SimplyPrint: {url}")
                log_connect = False
            url_start = self.connect_url[6:]
            url_start_match = re.match(r"wss://([^/]+)", self.connect_url)
            if url_start_match is not None:
                url_start = url_start_match.group(1)
            try:
                reachable = await self._loop.run_in_executor(
                    None, server_reachable, url_start, 80
                )
                if not reachable:
                    raise Exception("SimplyPrint not Reachable")
                self.ws = await tornado.websocket.websocket_connect(
                    url, connect_timeout=5.
                )
                setattr(self.ws, "on_ping", self._on_ws_ping)
                cur_time = self._monotonic()
                self._last_ping_received = cur_time
            except asyncio.CancelledError:
                raise
            except Exception:
                curtime = self._monotonic()
                timediff = curtime - self.last_err_log_time
                if timediff > CONNECTION_ERROR_LOG_TIME:
                    self.last_err_log_time = curtime
                    self._logger.exception(
                        f"Failed to connect to SimplyPrint")
                failed_attempts += 1
                if not failed_attempts % 10:
                    self.set_display_message("Can't reach SP", True)
                    self.reset_printer_display_timer.start(delay=120)
            else:
                if failed_attempts:
                    self.set_display_message("Back online!", True)
                failed_attempts = 0
                self._logger.info("Connected to SimplyPrint Cloud")
                self._start_printer_reconnect()
                await self._read_messages()
                log_connect = True
            if not self.is_closing:
                await asyncio.sleep(self.reconnect_delay)

    async def _read_messages(self) -> None:
        message: Union[str, bytes, None]
        while self.ws is not None:
            message = await self.ws.read_message()
            if isinstance(message, str):
                self._process_message(message)
            elif message is None:
                self.webcam_stream.stop()
                cur_time = self._monotonic()
                ping_time: float = cur_time - self._last_ping_received
                reason = code = None
                if self.ws is not None:
                    reason = self.ws.close_reason
                    code = self.ws.close_code
                msg = (
                    f"SimplyPrint Disconnected - Code: {code}, Reason: {reason}, "
                    f"Server Ping Time Elapsed: {ping_time}"
                )
                self._logger.info(msg)
                self.connected = False
                self.ws = None
                if self.keepalive_hdl is not None:
                    self.keepalive_hdl.cancel()
                    self.keepalive_hdl = None
                break

    def _on_ws_ping(self, data: bytes = b"") -> None:
        self._last_ping_received = self._monotonic()

    def _process_message(self, msg: str) -> None:
        self._sock_logger.info(f"received: {msg}")
        self._reset_keepalive()
        try:
            packet: Dict[str, Any] = json.loads(msg)
        except json.JSONDecodeError:
            self._logger.debug(f"Invalid message, not JSON: {msg}")
            return
        event: str = packet.get("type", "")
        data: Optional[Dict[str, Any]] = packet.get("data")
        if event == "connected":
            self._logger.info("SimplyPrint Reports Connection Success")
            self.connected = True
            self.reconnect_token = None
            if data is not None:
                if data.get("in_setup", 0) == 1:
                    self.is_set_up = False
                    self.settings.set_boolean(["is_set_up"], False)
                    self.settings.set(["printer_id"], "")
                    self.settings.set(["printer_name"], "")
                    if "short_id" in data:
                        self.settings.set(["temp_short_setup_id"], data["short_id"])
                    self.settings.save(trigger_event=True)
                interval = data.get("interval")
                self._set_intervals(interval)
                self.reconnect_token = data.get("reconnect_token")
                name = data.get("name")
                if name is not None:
                    self._save_item("printer_name", name)
            self.reconnect_delay = 1.
            self._push_initial_state()
        elif event == "error":
            self._logger.info(f"SimplyPrint Connection Error: {data}")
            self.reconnect_delay = 30.
            self.reconnect_token = None
        elif event == "new_token":
            if data is None:
                self._logger.debug("Invalid message, no data")
                return
            if data.get("no_exist", False) is True and self.is_set_up:
                self.is_set_up = False
                self.settings.set_boolean(["is_set_up"], False)
                self.settings.save()
            token = data.get("token")
            if not isinstance(token, str):
                self._logger.info(f"Invalid token received: {token}")
                return
            self._logger.info(f"SimplyPrint Token Received")
            self._save_item("printer_token", token)
            short_id = data.get("short_id")
            if not isinstance(short_id, str):
                self._logger.debug(f"Invalid short_id received: {short_id}")
            else:
                self.settings.set(["temp_short_setup_id"], data["short_id"])
                self.settings.save(trigger_event=True)
            self._set_ws_url()
        elif event == "complete_setup":
            if data is None:
                self._logger.debug("Invalid message, no data")
                return
            printer_id = data.get("printer_id")
            self.settings.set(["printer_id"], str(printer_id))
            self.is_set_up = True
            self.settings.set_boolean(["is_set_up"], True)
            self.settings.set(["temp_short_setup_id"], "")
            self.settings.save(trigger_event=True)
            self.set_display_message("Set up!", True)
            self._set_ws_url()
        elif event == "demand":
            if data is None:
                self._logger.debug(f"Invalid message, no data")
                return
            demand = data.pop("demand", "unknown")
            self._process_demand(demand, data)
        elif event == "interval_change":
            self._set_intervals(data)
        else:
            # TODO: It would be good for the backend to send an
            # event indicating that it is ready to recieve printer
            # status.
            self._logger.debug(f"Unknown event: {msg}")

    def _set_intervals(self, data):
        if isinstance(data, dict):
            ai_timer_retart = False
            for key, val in data.items():
                if key == "ai" and val / 1000. < self.intervals.get("ai"):
                    ai_timer_retart = True
                self.intervals[key] = val / 1000.
            self._logger.debug(f"Intervals Updated: {self.intervals}")
            if ai_timer_retart and self.printer.is_printing():
                td = 0 if datetime.datetime.now() > self.ai_timer_not_before else 120. - (self.ai_timer_not_before - datetime.datetime.now()).total_seconds()
                self.ai_timer.stop()
                self.ai_timer.start(delay=td)
            self._start_printer_reconnect()

    def _process_demand(self, demand: str, args: Dict[str, Any]) -> None:
        if demand == "pause":
            self.set_display_message("Pausing...", True)
            self._update_state("pausing")
            self._loop.run_in_executor(None, self.printer.pause_print)
        elif demand == "resume":
            self._update_state("resuming")
            self.set_display_message("Resuming...", True)
            self._loop.run_in_executor(None, self.printer.resume_print)
        elif demand == "cancel":
            self._update_state("cancelling")
            self.set_display_message("Cancelling...", True)
            self._loop.run_in_executor(None, self.printer.cancel_print)
        elif demand == "terminal":
            if "enabled" in args:
                self.gcode_terminal_enabled = args["enabled"]
        elif demand == "gcode":
            script_list = args.get("list", [])
            if script_list:
                self._loop.run_in_executor(
                    None, self.printer.commands, script_list
                )
        elif demand == "test_webcam":
            self._loop.add_callback(self._test_webcam)
        elif demand == "stream_on":
            interval: float = args.get("interval", 1000) / 1000
            self.webcam_stream.start(interval)
        elif demand == "stream_off":
            self.webcam_stream.stop()
        elif demand == "file":
            self.set_display_message("Preparing...", True)
            url: Optional[str] = args.get("url")
            if not isinstance(url, str):
                self._logger.debug(f"Invalid url in message")
                return
            start = bool(args.get("auto_start", 0))
            self.file_handler.download_file(url, start)
        elif demand == "start_print":
            def _on_start_finished(fut: asyncio.Future):
                if not fut.result():
                    self._logger.debug("Failed to start print")
            fut: asyncio.Future = self._loop.run_in_executor(  # type: ignore
                None, self.file_handler.start_print
            )
            fut.add_done_callback(_on_start_finished)
        elif demand == "connect_printer":
            if self.printer.is_closed_or_error():
                self._logger.info(
                    "Connecting to printer at request from SimplyPrint"
                )
                self._loop.run_in_executor(None, self.printer.connect)
        elif demand == "disconnect_printer":
            self._logger.info(
                "Disconnecting printer at request from SimplyPrint"
            )
            self._loop.run_in_executor(None, self.printer.disconnect)
        elif demand == "system_restart":
            self.set_display_message("Rebooting...", True)
            self._loop.run_in_executor(
                None, self.sys_manager.reboot_machine
            )
        elif demand == "system_shutdown":
            self.set_display_message("Shutting Down...", True)
            self._loop.run_in_executor(
                None, self.sys_manager.shutdown_machine
            )
        elif demand == "api_restart":
            self.set_display_message("Restarting OctoPrint", True)
            self._loop.run_in_executor(
                None, self.sys_manager.restart_octoprint
            )
        elif demand == "api_shutdown":
            self.set_display_message("Stopping OctoPrint...", True)
            self._loop.run_in_executor(
                None, self.sys_manager.stop_octoprint
            )
        elif demand == "update":
            self.set_display_message("Updating...", True)
            self._loop.run_in_executor(
                None, self.sys_manager.update_simplyprint
            )
        elif demand == "plugin_install":
            def _on_install_finished(install_success: asyncio.Future):
                if install_success.result():
                    self._logger.info("Restarting OctoPrint after plugin install.")
                    self._loop.run_in_executor(
                        None, self.sys_manager.restart_octoprint
                    )
                else:
                    self._logger.debug("Failed to install plugin")
            install_success: asyncio.Future = self._loop.run_in_executor(  # type: ignore
                None, self.sys_manager.install_plugin, args
            )
            install_success.add_done_callback(_on_install_finished)
        elif demand == "plugin_uninstall":
            def _on_uninstall_finished(uninstall_success: asyncio.Future):
                if uninstall_success.result():
                    self._logger.info("Restarting OctoPrint after plugin uninstall.")
                    self._loop.run_in_executor(
                        None, self.sys_manager.restart_octoprint
                    )
                else:
                    self._logger.debug("Failed to uninstall plugin")
            uninstall_success: asyncio.Future = self._loop.run_in_executor(  # type: ignore
                None, self.sys_manager.uninstall_plugin, args
            )
            uninstall_success.add_done_callback(_on_uninstall_finished)
        elif demand == "printer_settings":
            sp_settings = args.get("printer_settings")
            if sp_settings is not None:
                self._sync_settings_from_simplyprint(sp_settings)
        elif demand == "webcam_settings_updated":
            cam_settings = args.get("webcam_settings")
            if cam_settings is not None:
                self._sync_webcam_settings(cam_settings)
        elif demand == "set_printer_profile":
            profile = args.get("printer_profile")
            if profile is not None:
                self._loop.run_in_executor(
                    None, self._save_printer_profile, profile
                )
        elif demand == "get_gcode_script_backups":
            force = args.get("force", False)
            self._send_gcode_scripts(force)
        elif demand == "has_gcode_changes":
            scripts = args.get("scripts")
            if scripts is not None:
                self._save_gcode_scripts(scripts)
        elif demand in ["psu_on", "psu_keepalive"]:
            self._loop.run_in_executor(
                None, self.sys_manager.power_on_printer
            )
        elif demand == "psu_off":
            self._loop.run_in_executor(
                None, self.sys_manager.power_off_printer
            )
        elif demand == "disable_websocket":
            self._save_item("websocket_ready", False)
            self._loop.run_in_executor(
                None, self.sys_manager.restart_octoprint
            )
        else:
            self._logger.debug(f"Unknown demand: {demand}")

    def _sync_settings_from_simplyprint(
        self, sp_settings: Dict[str, Any]
    ) -> None:
        self._logger.info(
            "Syncing Settings at request from SimplyPrint"
        )
        if "display" in sp_settings:
            display = sp_settings["display"]
            if "enabled" in sp_settings["display"]:
                self.settings.set_boolean(
                    ["display_enabled"], display["enabled"]
                )
            if "branding" in display:
                self.settings.set_boolean(
                    ["display_branding"], display["branding"]
                )
            if "while_printing_type" in display:
                wpt = str(display["while_printing_type"])
                self.settings.set(["display_while_printing_type"], wpt)
            if "show_status" in display:
                self.settings.set_boolean(
                    ["display_show_status"], display["show_status"]
                )
        if "has_power_controller" in sp_settings:
            has_pwr = sp_settings["has_power_controller"]
            self.settings.set_boolean(["has_power_controller"], has_pwr)
            if has_pwr:
                self._send_power_state()
        if "has_filament_sensor" in sp_settings:
            has_fs = sp_settings["has_filament_sensor"]
            self.settings.set_boolean(["has_filament_sensor"], has_fs)
            if has_fs:
                self._send_filament_sensor_state()
        self.settings.set(
            ["info", "last_user_settings_sync"],
            sp_settings["updated_datetime"]
        )
        self.settings.save(trigger_event=True)

    def _sync_webcam_settings(self, cam_settings: Dict[str, Any]) -> None:
        self._logger.info(
            "Syncing Webcam Settings at request from SimplyPrint"
        )
        flip_h = cam_settings.get("flipH", False)
        flip_v = cam_settings.get("flipV", False)
        rotate_90 = cam_settings.get("rotate90", False)
        self.settings.global_set(["webcam", "flipH"], flip_h)
        self.settings.global_set(["webcam", "flipV"], flip_v)
        self.settings.global_set(["webcam", "rotate90"], rotate_90)
        data = {"flipH": flip_h, "flipV": flip_v, "rotate90": rotate_90}
        self.settings.set(["webcam"], data)
        self.settings.save()

    def _save_printer_profile(self, sp_profile: Dict[str, Any]) -> None:
        profile_mgr = octoprint.server.printerProfileManager
        current_prof = profile_mgr.get("sp_printer")
        if current_prof is None:
            current_prof = profile_mgr.get_default()
        merged = octoprint.util.dict_merge(current_prof, sp_profile)
        make_default = False
        if "default" in merged:
            make_default = True
            sp_profile.pop("default", None)
            merged["id"] = "sp_printer"
        success = True
        try:
            profile_mgr.save(
                merged, allow_overwrite=True, make_default=make_default,
                trigger_event=True
            )
        except Exception:
            self._logger.exception("Failed to save SimplyPrint Profile")
            success = False
        self._loop.add_callback(
            self.send_sp, "profile_saved", {"success": success}
        )

    def _send_gcode_scripts(self, force: bool = False):
        backed_up = self.settings.get_boolean(
            ["info", "gcode_scripts_backed_up"]
        )
        data: Optional[Dict[str, str]] = None
        if not backed_up or force:
            default_cancel_gc = (
                ";disablemotorsM84;disableallheaters"
                "{%snippet'disable_hotends'%}{%snippet'disable_bed'%};"
                "disablefanM106S0"
            )
            cur_cancel_gc = self.settings.settings.loadScript(
                "gcode", "afterPrintCancelled", source=True
            )

            def remove_whitespace(data: str) -> str:
                return data.strip().replace(" ", "").replace("\n", "")

            if remove_whitespace(cur_cancel_gc) == default_cancel_gc:
                cur_cancel_gc = ""
            cur_resume_gc = self.settings.settings.loadScript(
                "gcode", "beforePrintResumed", source=True
            )
            cur_pause_gc = self.settings.settings.loadScript(
                "gcode", "afterPrintPaused", source=True
            )
            if cur_cancel_gc or cur_pause_gc or cur_resume_gc:
                self._logger.info(
                    "Sending G-Code scripts at request of SimplyPrint"
                )
                data = {
                    "cancel_gcode": cur_cancel_gc.splitlines() if cur_cancel_gc else [],
                    "pause_gcode": cur_pause_gc.splitlines() if cur_pause_gc else [],
                    "resume_gcode": cur_resume_gc.splitlines() if cur_resume_gc else []
                }
            self.settings.set_boolean(["info", "gcode_scripts_backed_up"], True)
            if not cur_cancel_gc.startswith("; synced from SimplyPrint GCODE Macros") or \
                    not cur_resume_gc.startswith("; synced from SimplyPrint GCODE Macros") or \
                    not cur_pause_gc.startswith("; synced from SimplyPrint GCODE Macros"):
                self.send_sp("gcode_scripts", {"scripts": data})

    def _save_gcode_scripts(self, scripts: Dict[str, str]) -> None:
        def fix_script(data: str) -> str:
            data = data.replace("\r\n", "\n").replace("\r", "\n")
            return octoprint.util.to_unicode(data)

        if (
            "cancel" in scripts and
            "pause" in scripts and
            "resume" in scripts
        ):
            self._logger.info("Saving G-Code scripts at request of SimplyPrint")
            self.settings.settings.saveScript(
                "gcode", "afterPrintCancelled", fix_script(scripts["cancel"])
            )
            self.settings.settings.saveScript(
                "gcode", "afterPrintPaused", fix_script(scripts["pause"])
            )
            self.settings.settings.saveScript(
                "gcode", "beforePrintResumed", fix_script(scripts["resume"])
            )
            self.settings.save(force=True, trigger_event=True)
            self.send_sp("gcode_scripts", {"saved": True})

    async def _test_webcam(self) -> None:
        await self.webcam_stream.test_connection()
        self.send_sp(
            "webcam_status", {"connected": self.webcam_stream.webcam_connected}
        )

    def _save_item(self, name: str, data: Any):
        self.settings.set([name], data)
        self.settings.save()

    def _set_ws_url(self):
        token: str = self.settings.get(["printer_token"])
        if not token:
            # attempt to fall back to pi id of token is not available
            token = self.settings.get(["rpi_id"])
            if token:
                self._save_item("printer_token", token)
        printer_id: str = self.settings.get(["printer_id"])
        ep = WS_TEST_ENDPOINT if self.test else WS_PROD_ENDPOINT
        self.connect_url = f"{ep}/0/0"
        if token:
            if not printer_id:
                self.connect_url = f"{ep}/0/{token}"
            else:
                self.connect_url = f"{ep}/{printer_id}/{token}"

    def _on_state_event(self, new_state: str) -> None:
        if not self.is_connected:
            return
        self._update_state(new_state)

    def _on_printer_connected(self):
        self.is_connected = True
        self._send_connection_state("connected")
        if self.printer.is_printing():
            self.job_info_timer.start()
            self._update_state("printing")
        elif self.printer.is_operational():
            self._update_state("operational")
            if self.settings.get_boolean(["display_show_status"]):
                self.reset_printer_display_timer.start()
        self.temp_timer.start()
        self.amb_detect.start()
        self.printer_reconnect_timer.stop()
        self._send_active_extruder(0)

    def _on_printer_disconnected(self):
        self.is_connected = False
        self._update_state("offline")
        self._send_connection_state("disconnected")
        self.temp_timer.stop()
        self.job_info_timer.stop()
        self.webcam_stream.stop()
        self.ai_timer.stop()
        self.reset_printer_display_timer.stop()
        self.cache.reset_print_state()
        self._start_printer_reconnect()

    def _on_printer_error(self, msg: str) -> None:
        self._update_state("error")
        self.send_sp("printer_error", {"error": msg})

    def _on_print_start(
        self, print_data: Dict[str, Any], need_start_event: bool = True
    ) -> None:
        # inlcludes started and resumed events
        if self.file_handler.start_pending():
            self.send_sp("file_progress", {"state": "started"})
            self._loop.run_in_executor(None, self.file_handler.notify_started)
        self._update_state("printing")
        filename = print_data["name"]
        dest = print_data["origin"]
        path = print_data["path"]
        metadata: Dict[str, Any] = {}
        if self.file_manager.has_analysis(dest, path):
            metadata = self.file_manager.get_metadata(dest, path)["analysis"]
        job_info: Dict[str, Any] = {"filename": filename}
        filament: float = 0.
        for tool, data in metadata.get("filament", {}).items():
            if tool.startswith("tool") and "length" in data:
                filament += data["length"]
        if filament:
            job_info["filament"] = round(filament)
        est_time = metadata.get("estimatedPrintTime")
        if est_time is not None:
            job_info["time"] = int(est_time + .5)
        self.cache.job_info.update(job_info)
        if need_start_event:
            job_info["started"] = True
        self.job_info_timer.start()
        self._send_job_event(job_info)
        self.set_display_message("Printing...", True)
        self.ai_timer_not_before = datetime.datetime.now() + datetime.timedelta(seconds=120)
        self.ai_timer.start(delay=120.)
        self.scores = []
        # self.reset_printer_display_timer.stop()

    def _on_print_paused(self) -> None:
        self.job_info_timer.stop()
        self.ai_timer.stop()
        self.send_sp("job_info", {"paused": True})
        self._update_state("paused")
        if self.settings.get_boolean(["display_show_status"]):
            self.set_display_message("Paused", True)

    def _on_print_resumed(self) -> None:
        self.job_info_timer.start()
        self.ai_timer.start(delay=self.intervals.get("ai"))
        self._update_state("printing")
        self.scores = []

    def _on_print_done(
        self, job_state: str, payload: Optional[Dict[str, Any]] = None
    ) -> None:
        self.job_info_timer.stop()
        self.ai_timer.stop()
        # self.reset_printer_display_timer.start()
        event_payload: Dict[str, Any] = {job_state: True}
        if payload is not None:
            event_payload.update(payload)
        self._send_job_event(event_payload)
        self.cache.job_info = {}
        self.current_layer = -1
        self.set_display_message("Print Complete", True)

    def _on_metadata_update(self, payload: Dict[str, Any]) -> None:
        if (
            "result" not in payload or
            payload["result"].get("analysisPending", False)
        ):
            return
        self.file_handler.check_analysis(payload)
        fname = payload.get("name", None)
        fpath = payload.get("path", "")
        if (
            self.printer.is_printing() and
            self.printer.is_current_file(fpath, False) and
            fname == self.cache.job_info.get("filename", "")
        ):
            metadata: Dict[str, Any] = payload["result"]
            job_info: Dict[str, Any] = {}
            filament: float = 0.
            for tool, data in metadata.get("filament", {}).items():
                if tool.startswith("tool") and "length" in data:
                    filament += data["length"]
            if filament:
                job_info["filament"] = round(filament)
            est_time = metadata.get("estimatedPrintTime")
            if est_time is not None:
                job_info["time"] = int(est_time + .5)
            diff = self._get_object_diff(job_info, self.cache.job_info)
            if diff:
                self.cache.job_info.update(diff)
                self.send_sp("job_info", diff)

    def _on_cancelling_event(self, msg: str):
        self.cache.firmware_error = msg
        self._update_state("cancelling")

    async def _handle_cpu_update(self, eventtime: float) -> float:
        sys_stats: Dict[str, Any] = await self._loop.run_in_executor(
            None, self.monitor.get_all_resources
        )
        cpu: float = sys_stats["cpu"]["average"]
        mem_pct: float = sys_stats["memory"].get("percent", 0)
        temp_data: Optional[Dict[str, Any]] = sys_stats["temp"]
        temp: float = 0.
        if isinstance(temp_data, dict):
            temp = temp_data.get("current", 0)
        cpu_data = {
            "usage": int(cpu + .5),
            "temp": int(temp + .5),
            "memory": int(mem_pct + .5),
            "flags": self.cache.throttled_state.get("raw_value", 0)
        }
        diff = self._get_object_diff(cpu_data, self.cache.cpu_info)
        if diff:
            self.cache.cpu_info.update(cpu_data)
            self.send_sp("cpu", diff)
        return eventtime + self.intervals["cpu"]

    def _on_cpu_throttled(self, payload: Dict[str, Any]):
        self.cache.throttled_state = payload

    def _on_ambient_changed(self, new_ambient: int) -> None:
        self._save_item("ambient_temp", new_ambient)
        self.send_sp("ambient", {"new": new_ambient})

    async def _handle_job_info_update(self, eventtime: float) -> float:
        if self.cache.state != "printing":
            return eventtime + self.intervals["job"]
        job_info: Dict[str, Any] = {}
        cur_data = await self._loop.run_in_executor(None, self.printer.get_current_data)
        progress: Dict[str, Any] = cur_data["progress"]
        time_left: Optional[float] = progress.get("printTimeLeft")
        pct_done: Optional[int] = None
        if time_left is not None:
            last_time_left = self.cache.job_info.get("time", time_left + 60.)
            time_diff = last_time_left - time_left
            if (
                (time_left < 60 or time_diff >= 30) and
                time_left != last_time_left
            ):
                job_info["time"] = time_left
                if self.settings.get_int(["display_while_printing_type"]) == 1:
                    remaining_time = str(datetime.timedelta(seconds=time_left))
                    self.set_display_message(f"Time Remaining: {remaining_time}", True)
            if progress.get("PrintTimeLeftOrigin", "") == "genius":
                ptime = progress.get("printTime", 0)
                total = ptime + time_left
                pct_done = int(ptime / total * 100 + .5)
        if pct_done is None and "completion" in progress:
            pct_done = int(progress["completion"] + .5)
        if (
            pct_done is not None and
            pct_done != self.cache.job_info.get("progress", 0)
        ):
            job_info["progress"] = pct_done
            if self.settings.get_int(["display_while_printing_type"]) == 0:
                self.set_display_message(f"Printing {pct_done}%", True)
        layer = self.current_layer
        if layer != self.cache.job_info.get("layer", -1):
            job_info["layer"] = layer
        if job_info:
            self.cache.job_info.update(job_info)
            self.send_sp("job_info", job_info)
        return eventtime + self.intervals["job"]

    def _handle_temperature_update(self, eventtime: float) -> float:
        if not self.printer.is_operational():
            return eventtime + self.intervals["temps"]
        current_temps: Dict[str, Any] = self.printer.get_current_temperatures()
        need_rapid_update: bool = False
        temp_data: Dict[str, List[int]] = {}
        for heater, temps in current_temps.items():
            if heater == "chamber":
                continue
            try:
                reported_temp = temps["actual"]
                ret = [
                    int(reported_temp + .5),
                    int(temps["target"] + .5)
                ]
            except Exception:
                continue
            last_temps = self.cache.temps.get(heater, [-100., -100.])
            if ret[1] == last_temps[1]:
                if ret[1]:
                    seeking_target = abs(ret[1] - ret[0]) > 5
                else:
                    seeking_target = ret[0] >= self.amb_detect.ambient + 25
                need_rapid_update |= seeking_target
                # The target hasn't changed and not heating, debounce temp
                if heater in self.last_received_temps and not seeking_target:
                    last_reported = self.last_received_temps[heater]
                    if abs(reported_temp - last_reported) < .75:
                        self.last_received_temps.pop(heater)
                        continue
                if ret[0] == last_temps[0]:
                    self.last_received_temps[heater] = reported_temp
                    continue
                temp_data[heater] = ret[:1]
            else:
                # target has changed, send full data
                temp_data[heater] = ret
            self.last_received_temps[heater] = reported_temp
            self.cache.temps[heater] = ret
        if temp_data:
            self.send_sp("temps", temp_data)
        interval = self.intervals["temps"]
        if need_rapid_update:
            interval = self.intervals["temps_target"]
        return eventtime + interval

    def _start_printer_reconnect(self) -> None:
        reconnect_time = self.intervals.get("reconnect", 0)
        if not reconnect_time or self.printer.is_operational():
            return
        self.printer_reconnect_timer.start(delay=2.)

    async def _handle_printer_reconnect(self, eventtime: float) -> float:
        rt = self.intervals.get("reconnect", 2.)
        if not rt or self.printer.is_operational():
            self.printer_reconnect_timer.stop()
        elif self.printer.get_state_id() != "CONNECTING":
            await self._loop.run_in_executor(None, self.printer.connect)
        return eventtime + rt

    async def _handle_update_check(self, eventtime: float) -> float:
        updates = await self._loop.run_in_executor(
            None, self.sys_manager.check_software_update
        )
        if updates != self.cache.updates:
            self.cache.updates = updates
            self.send_sp("software_updates", {"available": updates})
        return eventtime + UPDATE_CHECK_TIME

    async def _make_ai_request(self, endpoint, data, headers, timeout=10):
        return await self._loop.run_in_executor(
            None,
            functools.partial(
                requests.get, endpoint, data=data, headers=headers,
                timeout=timeout
            )
        )

    async def _handle_ai_snapshot(self, eventtime: float) -> float:
        ai_interval = self.intervals.get("ai", 0)
        if ai_interval > 0 and self.webcam_stream.webcam_connected:
            img_data = await self._loop.run_in_executor(None, self.webcam_stream.extract_image)
            headers = {"User-Agent": "Mozilla/5.0"}
            data = json.dumps(
                {
                    "api_key": self.settings.get(["printer_token"]),
                    "image_array": img_data,
                    "interval": ai_interval,
                    "printer_id" : self.settings.get(["printer_id"]),
                    "settings" : {
                        "buffer_percent" : 80,
                        "confidence" : 60,
                        "buffer_length" : 16
                    },
                    "scores" : self.scores
                }
            ).encode('utf8')
            try:
                response = await self._make_ai_request("https://ai.simplyprint.io/api/v2/infer", data=data, headers=headers, timeout=10)
                self.failed_ai_attempt = 0
                response_json = response.json()
                self.scores = response_json.get("scores", self.scores)
                self.send_sp("ai_resp", {"ai" : response_json.get("s1", [0, 0, 0])})
            except:
                self.failed_ai_attempts += 1
                td = ai_interval + (self.failed_ai_attempts * 5.0) if self.failed_ai_attempts <= 10 else 120.
                self.ai_timer.start(delay=td)
                
            self.ai_timer.start(delay=ai_interval)
        elif ai_interval == 0:
            self.ai_timer.stop()
        return eventtime + ai_interval

    def _update_state_from_octo(self) -> None:
        state: str = self.printer.get_state_id()  # type: ignore
        if state in VALID_STATES:
            self._update_state(state.lower())

    def _update_state(self, new_state: str) -> None:
        if self.cache.state == new_state:
            return
        if self.cache.state == "cancelling":
            payload = {"error": self.cache.firmware_error}
            self.cache.firmware_error = ""
            self._on_print_done("cancelled", payload)
        self.cache.state = new_state
        self.send_sp("state_change", {"new": new_state})

    def _send_connection_state(self, conn_state: str) -> None:
        self.send_sp("connection", {"new": conn_state})

    def _send_mesh_data(self, mesh: Dict[str, Any]) -> None:
        self.cache.mesh = mesh
        self.send_sp("mesh_data", mesh)

    def _send_job_event(self, job_info: Dict[str, Any]) -> None:
        if self.connected:
            self.send_sp("job_info", job_info)
        else:
            job_info.update(self.cache.job_info)
            job_info["delay"] = self._monotonic()
            self.missed_job_events.append(job_info)
            if len(self.missed_job_events) > 10:
                self.missed_job_events.pop(0)

    def _on_firmware_warning(self, payload: Dict[str, Any]) -> None:
        self.cache.firmware_warning = payload
        fw_info = self.cache.firmware_info
        fw_info["unsafe"] = True
        self.send_sp("firmware", fw_info)

    def _send_firmware_data(self, payload: Dict[str, Any]) -> None:
        fw_info = {"fw": payload, "raw": True, "unsafe": False}
        self.cache.firmware_info = fw_info
        self.send_sp("firmware", fw_info)

    def _send_active_extruder(self, new_index: int):
        tool = f"T{new_index}"
        if tool == self.cache.active_extruder:
            return
        self.cache.active_extruder = tool
        self.send_sp("tool", {"new": tool})

    def _send_webcam_config(self) -> None:
        wc_data = {
            "flipH": self.settings.global_get(["webcam", "flipH"]),
            "flipV": self.settings.global_get(["webcam", "flipV"]),
            "rotate90": self.settings.global_get(["webcam", "rotate90"]),
        }
        self.send_sp("webcam", wc_data)

    def _send_image(self, base_image: str) -> None:
        self.send_sp("stream", {"base": base_image})

    def _send_installed_plugins(self) -> None:
        sp_plugins = self.settings.get(["sp_installed_plugins"])
        if not isinstance(sp_plugins, list):
            sp_plugins = []
        pm = self.plugin._plugin_manager
        installed_plugins: List[Dict[str, Any]] = []
        plugins: Dict[str, Any] = pm.plugins  # type: ignore
        for plugin in plugins.values():
            if not plugin.bundled and plugin.enabled:
                is_sp = plugin.key.lower() == "simplyprint"
                pinfo = pm.get_plugin_info(plugin.key)  # type: ignore
                if not hasattr(pinfo.origin, "package_name"):
                    # Only report "EntryPointOrigin" packages
                    continue
                installed_plugins.append({
                    "key": plugin.key,
                    "name": plugin.name,
                    "author": plugin.author,
                    "version": plugin.version,
                    "sp_installed": plugin.name in sp_plugins or is_sp,
                    "pip_name": pinfo.origin.package_name
                })
        self.send_sp("installed_plugins", {"plugins": installed_plugins})

    def _send_power_state(self) -> None:
        psu_state = self.sys_manager.get_power_state()
        if psu_state is not None:
            self.send_sp("power_controller", {"on": psu_state})

    def _send_filament_sensor_state(self) -> None:
        fs_state = self.sys_manager.get_filament_sensor_state()
        if fs_state is not None:
            self.send_sp("filament_sensor", {"state": fs_state})

    def _push_initial_state(self):
        # TODO: This method is called after SP is connected
        # and ready to receive state.  We need a list of items
        # we can safely send if the printer is not setup (ie: has no
        # printer ID)
        #
        # The firmware data and machine data is likely saved by
        # simplyprint.  It might be better for SP to request it
        # rather than for the client to send it on every connection.
        self.send_sp("state_change", {"new": self.cache.state})
        if self.cache.temps:
            self.send_sp("temps", self.cache.temps)
        if self.cache.firmware_info:
            self.send_sp("firmware", self.cache.firmware_info)
        if self.cache.machine_data:
            self.send_sp("machine_data", self.cache.machine_data)
        self._send_webcam_config()
        curtime = self._monotonic()
        for evt in self.missed_job_events:
            evt["delay"] = int((curtime - evt["delay"]) + .5)
            self.send_sp("job_info", evt)
        self.missed_job_events = []
        if self.cache.active_extruder:
            self.send_sp("tool", {"new": self.cache.active_extruder})
        if self.cache.cpu_info:
            self.send_sp("cpu_info", self.cache.cpu_info)
        self.send_sp("ambient", {"new": self.amb_detect.ambient})
        self._send_power_state()
        self._send_filament_sensor_state()
        self._send_installed_plugins()
        if self.cache.updates:
            self.send_sp("software_updates", {"available": self.cache.updates})
        self.send_sp(
            "webcam_status", {"connected": self.webcam_stream.webcam_connected}
        )

    def _check_setup_event(self, evt_name: str) -> bool:
        return self.is_set_up or evt_name in PRE_SETUP_EVENTS

    def send_sp(
        self, evt_name: str, data: Any
    ) -> Union[asyncio.Future, asyncio.Task]:
        if (
            not self.connected or
            self.ws is None or
            self.ws.protocol is None or
            not self._check_setup_event(evt_name)
        ):
            fut = self._aioloop.create_future()
            fut.set_result(False)
            return fut
        packet = {"type": evt_name, "data": data}
        if evt_name != "stream":
            self._sock_logger.info(f"sent: {packet}")
        else:
            self._sock_logger.info("sent: webcam stream")
        try:
            fut = self.ws.write_message(json.dumps(packet))
        except tornado.websocket.WebSocketClosedError:
            fut = self._aioloop.create_future()
            fut.set_result(False)
        else:
            async def fut_wrapper():
                try:
                    await fut
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
            task = self._aioloop.create_task(fut_wrapper())
            self._reset_keepalive()
            return task
        return fut

    def set_display_message(self, message: str, short_branding=False) -> None:
        enabled = self.settings.get_boolean(["display_enabled"])
        if not self.is_set_up or not enabled:
            return
        if not isinstance(message, str):
            try:
                message = str(message)
            except Exception:
                return
        if message == self.cache.message:
            return
        self.cache.message = message
        if self.settings.get_boolean(["display_branding"]):
            prefix = "[SP] " if short_branding else "[SimplyPrint] "
            message = prefix + message
        self._loop.run_in_executor(None, self.printer.commands, f"M117 {message}")

    async def _reset_printer_display(self, eventtime: float) -> float:
        self._logger.debug(f"resetting display at {eventtime}")
        if self.settings.get_boolean(["display_show_status"]) is False:
            self.reset_printer_display_timer.stop()
        is_online = await self._loop.run_in_executor(
            None,
            functools.partial(
                server_reachable, "www.google.com", 80
            ))
        if not is_online:
            self.set_display_message(f"No Internet", True)
        elif self.is_connected and not self.printer.is_printing():
            self.set_display_message(f"Ready", True)
        return eventtime + self.intervals["ready_message"]

    def _reset_keepalive(self):
        if self.keepalive_hdl is not None:
            self.keepalive_hdl.cancel()
        self.keepalive_hdl = self._aioloop.call_later(
            KEEPALIVE_TIME, self._do_keepalive)

    def _do_keepalive(self):
        self.keepalive_hdl = None
        self.send_sp("keepalive", None)

    def _setup_simplyprint_logging(self):
        fpath = self.settings.get_plugin_logfile_path()
        log_path = pathlib.Path(fpath)
        queue: SimpleQueue = SimpleQueue()
        queue_handler = logging.handlers.QueueHandler(queue)
        self._sock_logger = logging.getLogger(
            "octoprint.plugins.simplyprint.test"
        )
        self._sock_logger.addHandler(queue_handler)
        self._sock_logger.propagate = False
        file_hdlr = logging.handlers.TimedRotatingFileHandler(
            log_path, when='midnight', backupCount=2)
        formatter = logging.Formatter(
            '%(asctime)s [%(funcName)s()] - %(message)s')
        file_hdlr.setFormatter(formatter)
        self.qlistner = logging.handlers.QueueListener(queue, file_hdlr)
        self.qlistner.start()

    def _get_object_diff(
        self, new_obj: Dict[str, Any], cached_obj: Dict[str, Any]
    ) -> Dict[str, Any]:
        if not cached_obj:
            return new_obj
        diff: Dict[str, Any] = {}
        for key, val in new_obj.items():
            if key in cached_obj and val == cached_obj[key]:
                continue
            diff[key] = val
        return diff

    async def _do_close(self):
        self._logger.info("Closing SimplyPrint Websocket...")
        self.amb_detect.stop()
        self.temp_timer.stop()
        self.job_info_timer.stop()
        self.cpu_timer.stop()
        self.ai_timer.stop()
        self.printer_reconnect_timer.stop()
        self.update_timer.stop()
        try:
            await self.send_sp("shutdown", None)
        except tornado.websocket.WebSocketClosedError:
            pass
        self.qlistner.stop()
        self.is_closing = True
        if self.ws is not None:
            self.ws.close(1001, "Client Shutdown")
        if self.keepalive_hdl is not None:
            self.keepalive_hdl.cancel()
            self.keepalive_hdl = None
        if (
            self.connection_task is not None and
            not self.connection_task.done()
        ):
            try:
                await asyncio.wait_for(self.connection_task, 2.)
            except asyncio.TimeoutError:
                pass

    def close(self):
        if self._aioloop.is_running():
            self._loop.add_callback(self._do_close)
            self.simplyprint_thread.join()

    @property
    def event_bus(self):
        return self._event_bus


class ReportCache:
    def __init__(self) -> None:
        self.state = "offline"
        self.temps: Dict[str, Any] = {}
        self.mesh: Dict[str, Any] = {}
        self.job_info: Dict[str, Any] = {}
        self.active_extruder: str = ""
        # Persistent state across connections
        self.firmware_info: Dict[str, Any] = {}
        self.firmware_warning: Dict[str, Any] ={}
        self.machine_data: Dict[str, Any] = {}
        self.cpu_info: Dict[str, Any] = {}
        self.throttled_state: Dict[str, Any] = {}
        self.download_progress: int = -1
        self.message: str = ""
        self.updates: List[Dict[str, Any]] = []
        self.firmware_error: str = ""

    def reset_print_state(self) -> None:
        self.temps = {}
        self.mesh = {}
        self.job_info = {}

AMBIENT_CHECK_TIME = 5. * 60.
TARGET_CHECK_TIME = 60. * 60.
SAMPLE_CHECK_TIME = 20.

class AmbientDetect:
    CHECK_INTERVAL = 5
    def __init__(
        self,
        cache: ReportCache,
        changed_cb: Callable[[int], None],
        initial_ambient: int
    ) -> None:
        self._logger = logging.getLogger(
            "octoprint.plugins.simplyprint")
        self.cache = cache
        self._initial_sample: int = -1000
        self._ambient = initial_ambient
        self._on_ambient_changed = changed_cb
        self._last_sample_time: float = 0.
        self._update_interval = AMBIENT_CHECK_TIME
        self._detect_timer = FlexTimer(self._handle_detect_timer)

    @property
    def ambient(self) -> int:
        return self._ambient

    def _handle_detect_timer(self, eventtime: float) -> float:
        if "tool0" not in self.cache.temps:
            self._initial_sample = -1000
            return eventtime + self.CHECK_INTERVAL
        temp, target = self.cache.temps["tool0"]
        if target:
            self._initial_sample = -1000
            self._last_sample_time = eventtime
            self._update_interval = TARGET_CHECK_TIME
            return eventtime + self.CHECK_INTERVAL
        if eventtime - self._last_sample_time < self._update_interval:
            return eventtime + self.CHECK_INTERVAL
        if self._initial_sample == -1000:
            self._initial_sample = temp
            self._update_interval = SAMPLE_CHECK_TIME
        else:
            diff = abs(temp - self._initial_sample)
            if diff <= 2:
                last_ambient = self._ambient
                self._ambient = int((temp + self._initial_sample) / 2 + .5)
                self._initial_sample = -1000
                self._last_sample_time = eventtime
                self._update_interval = AMBIENT_CHECK_TIME
                if last_ambient != self._ambient:
                    self._logger.debug(f"SimplyPrint: New Ambient: {self._ambient}")
                    self._on_ambient_changed(self._ambient)
            else:
                self._initial_sample = temp
                self._update_interval = SAMPLE_CHECK_TIME
        return eventtime + self.CHECK_INTERVAL

    def start(self) -> None:
        if self._detect_timer.is_running():
            return
        if "tool0" in self.cache.temps:
            cur_temp = self.cache.temps["tool0"][0]
            if cur_temp < self._ambient:
                self._ambient = cur_temp
                self._on_ambient_changed(self._ambient)
        self._detect_timer.start()

    def stop(self) -> None:
        self._detect_timer.stop()

class FlexTimer:
    def __init__(self, callback: TimerCallback) -> None:
        self.callback = callback
        self.timer_handle: Optional[asyncio.Handle] = None
        self.running: bool = False

    def start(self, delay: float = 0.):
        if self.running:
            return
        if not hasattr(self, "_aioloop"):
            self._aioloop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
        self.running = True
        call_time = self._aioloop.time() + delay
        self.timer_handle = self._aioloop.call_at(
            call_time, self._schedule_task
        )

    def stop(self):
        if not self.running:
            return
        self.running = False
        if self.timer_handle is not None:
            self.timer_handle.cancel()
            self.timer_handle = None

    def _schedule_task(self):
        self.timer_handle = None
        self._aioloop.create_task(self._call_wrapper())

    def is_running(self) -> bool:
        return self.running

    async def _call_wrapper(self):
        if not self.running:
            return
        ret = self.callback(self._aioloop.time())
        if isinstance(ret, Awaitable):
            ret = await ret
        if self.running:
            self.timer_handle = self._aioloop.call_at(ret, self._schedule_task)
