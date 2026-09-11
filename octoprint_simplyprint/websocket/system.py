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

import ipaddress
import json
import logging
import os
import platform
import re
import socket
import sys
import uuid
from typing import TYPE_CHECKING, Any, Callable

import psutil
import requests
import sarge
from octoprint.events import Events
from octoprint.plugin import PluginSettings
from octoprint.util.commandline import CommandlineCaller
from octoprint.util.pip import LocalPipCaller
from octoprint.util.platform import CLOSE_FDS

from .constants import PLUGIN_INSTALL_URL, TEST_PLUGIN_INSTALL_URL

if TYPE_CHECKING:
    from .simplyprint import SimplyPrintWebsocket

class SystemQuery:
    def __init__(self, settings: PluginSettings) -> None:
        self._settings = settings
        self._logger = logging.getLogger("octoprint.plugins.simplyprint")
        self.command_line = CommandlineCaller()

    def get_system_info(self) -> dict[str, Any]:
        info = {}
        ver, api_ver = self._get_octoprint_version()
        info["ui_version"] = ver
        info["api_version"] = api_ver
        info["python_version"] = self._get_python_version()
        info["machine"] = self._get_cpu_model()
        info["os"] = platform.system()
        info["core_count"] = os.cpu_count()
        info["total_memory"] = psutil.virtual_memory().total
        info.update(self.get_network_info())
        info["mac"] = self.get_mac_address()

        port = self._get_public_port()

        if port:
            info["local_ip"] += ":" + str(port)

        return info

    def _get_python_version(self) -> str:
        return ".".join(str(part) for part in sys.version_info[:3])

    def get_mac_address(self):
        mac_int = uuid.getnode()
        mac_hex = f"{mac_int:012x}"
        mac_address = ":".join(mac_hex[i:i + 2] for i in range(0, 12, 2)).upper()
        return mac_address

    def _get_routed_ip(self) -> str | None:
        try:
            # Connect to an external host (Google DNS server) to determine the local IP
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
        except Exception as e:
            self._logger.warning("Failed to determine the routed IP", exc_info=e)
            local_ip = None

        return local_ip

    def _get_wifi_interface(self) -> tuple[str | None, str | None]:
        os_name = platform.system()
        try:
            if os_name == "Linux":
                cmd = "iwgetid"
                _ret, stdout, _stderr = self.command_line.checked_call(cmd)
                if stdout:
                    parts = stdout[0].strip().split(maxsplit=1)
                    ssid = parts[1].split(":")[-1].strip('"')
                    return parts[0], ssid
            elif os_name == "Windows":
                cmd = ["netsh", "wlan", "show", "interfaces"]
                _ret, stdout, _stderr = self.command_line.checked_call(cmd)
                if stdout:
                    for line in stdout:
                        if " SSID" in line:
                            return None, line.split(":")[1].strip()

        except Exception:
            self._logger.exception("Failed to retrieve wifi interfaces")
        return None, None

    def get_network_info(self) -> dict[str, Any]:
        src_ip = self._get_routed_ip()
        netinfo = {
            "hostname": socket.gethostname(),
            "local_ip": src_ip or "",
            "ssid": "",
            "is_ethernet": True
        }
        wifi_intf, ssid = self._get_wifi_interface()
        os_name = platform.system()
        if os_name == "Linux":
            cmd = "ip -json address"
            try:
                _ret, stdout, _stderr = self.command_line.checked_call(cmd)
                decoded = json.loads("\n".join(stdout))
                for interface in decoded:
                    if (
                        interface['operstate'] != "UP" or
                        interface['link_type'] != "ether" or
                        'address' not in interface
                    ):
                        continue
                    ifname = interface["ifname"]
                    for addr in interface.get('addr_info', []):
                        if "family" not in addr or "local" not in addr:
                            continue
                        if addr["family"] == "inet6" and src_ip is None:
                            ip = ipaddress.ip_address(addr["local"])
                            if ip.is_global:
                                netinfo["local_ip"] = addr["local"]
                                if wifi_intf == ifname:
                                    netinfo["is_ethernet"] = False
                                    netinfo["ssid"] = ssid
                                return netinfo
                        elif src_ip == addr["local"]:
                            netinfo["local_ip"] = addr["local"]
                            if wifi_intf == ifname:
                                netinfo["is_ethernet"] = False
                                netinfo["ssid"] = ssid
                            return netinfo
            except Exception:
                self._logger.exception("Failed to parse network interfaces")
        return netinfo

    def _get_cpu_model(self) -> str:
        os_name = platform.system()
        if os_name == "Linux":
            info_path = "/proc/cpuinfo"
            try:
                with open(info_path, encoding="utf-8") as file:
                    data = file.read()
                cpu_items = [
                    item.strip() for item in data.split("\n\n") if item.strip()
                ]
                # Check for the Raspberry Pi Model
                match = re.search(r"Model\s+:\s+(.+)", cpu_items[-1])
                if match is not None:
                    return match.group(1).strip()
                # Check for the CPU model typically reported by desktop
                # class machines
                for item in cpu_items:
                    match = re.search(r"model name\s+:\s+(.+)", item)
                    if match is not None:
                        return match.group(1).strip()
            except Exception:
                pass
        return "unknown"

    def _get_octoprint_version(self) -> tuple[str, str]:
        """
        Get OctoPrint version and API version
        :return: (tuple) OctoPrint version, API version
        """
        from octoprint.util.version import get_octoprint_version_string
        try:
            from octoprint.server.api import VERSION
        except ImportError:
            # OctoPrint 2.0.0+ removed it
            from octoprint.server.api import API_VERSION_PRE_2_0_0 as VERSION
        return get_octoprint_version_string(), VERSION

    def _get_public_port(self) -> str:
        # noinspection PyProtectedMember
        return self._settings.get(["public_port"])


class SystemManager:
    def __init__(self, simplyprint: SimplyPrintWebsocket) -> None:
        self.simplyprint = simplyprint
        self.logger = self.simplyprint._logger
        self.settings = self.simplyprint.settings
        self.installed_plugins: list[str] = self.settings.get(["sp_installed_plugins"])
        if not isinstance(self.installed_plugins, list):
            self.installed_plugins = []
            self.settings.set(["sp_installed_plugins"], [])
            self.settings.save()
        self._pip_caller = LocalPipCaller(
            # Use --user on commands if user has configured it
            force_user=self.settings.global_get_boolean(
                ["plugins", "pluginmanager", "pip_force-user"]
            )
        )

    def update_simplyprint(self) -> None:
        test = self.simplyprint.test
        url = TEST_PLUGIN_INSTALL_URL if test else PLUGIN_INSTALL_URL
        data = {
            "name": "SimplyPrint",
            "pip_name": "SimplyPrint",
            "key": "SimplyPrint",
            "install_url": url
        }
        if self.install_plugin(data):
            self.restart_octoprint()

    def install_plugin(self, plugins: list[dict[str, str]]) -> bool:
        install_error = False
        for plugin_data in plugins:
            name = plugin_data['name']
            url = plugin_data["install_url"]
            self.logger.info(
                "Installing OctoPrint Plugin '%s' at "
                "request from SimplyPrint from %s",
                name, url
            )
            key = plugin_data.get("key")
            if key is not None and key not in self.installed_plugins:
                self.installed_plugins.append(key)
                self.settings.set(["sp_installed_plugins"], self.installed_plugins)
                self.settings.save()
            args: list[str] = ["install", url, "--no-cache-dir"]
            try:
                code, stdout, stderr = self._call_pip(*args)
            except Exception:
                self.logger.exception("Failed to install plugin: %s", name)
                install_error = True
            if code != 0:
                self.logger.error(
                    "Failed to install plugin %s, returned with %s\n"
                    "%s\n%s",
                    name, code, stdout, stderr
                )
                install_error = True
        return not install_error

    def uninstall_plugin(self, plugin_data: dict[str, str]) -> bool:
        name = plugin_data['name']
        self.logger.info(
            "Uninstalling OctoPrint Plugin '%s' at "
            "request from SimplyPrint",
            name
        )
        key = plugin_data.get("key")
        if key is not None and key in self.installed_plugins:
            self.installed_plugins.remove(key)
            self.settings.set(["sp_installed_plugins"], self.installed_plugins)
            self.settings.save()
        pip_name = plugin_data["pip_name"].replace(" ", "-")
        args: list[str] = ["uninstall", "--yes", pip_name]
        try:
            code, stdout, stderr = self._call_pip(*args)
        except Exception:
            self.logger.exception("Failed to uninstall plugin %s", name)
            return False
        if code != 0:
            self.logger.error(
                "Failed to uninstall plugin %s, returned with %s\n"
                "%s\n%s",
                name, code, stdout, stderr
            )
            return False
        return True

    def restart_octoprint(self) -> None:
        self.logger.info("Restarting OctoPrint at request from SimplyPrint")
        command = self.settings.global_get(
            ["server", "commands", "serverRestartCommand"]
        )
        if command:
            self.simplyprint.close()
            self.simplyprint.event_bus.fire(Events.SHUTDOWN)
            self._run_system_command(command, do_async=True)

    def stop_octoprint(self) -> None:
        self.logger.info("Stopping OctoPrint at request from SimplyPrint")
        restart_cmd = self.settings.global_get(
            ["server", "commands", "serverRestartCommand"]
        )
        command = ""
        if isinstance(restart_cmd, str):
            restart_cmd = restart_cmd.strip()
            if restart_cmd.startswith("sudo service "):
                svc = restart_cmd.split()[2]
                command = f"sudo service {svc} stop"
            elif command.startswith("sudo systemctl"):
                svc = restart_cmd.split()[-1]
                command = f"sudo systemctl stop {svc}"
        if command:
            self.simplyprint.close()
            self.simplyprint.event_bus.fire(Events.SHUTDOWN)
            self._run_system_command(command, do_async=True)

    def shutdown_machine(self) -> None:
        self.logger.info("Machine shutdown at request from SimplyPrint")
        command = self.settings.global_get(
            ["server", "commands", "systemShudownCommand"]
        )
        if command:
            self.simplyprint.close()
            self.simplyprint.event_bus.fire(Events.SHUTDOWN)
            self._run_system_command(command, do_async=True)

    def reboot_machine(self) -> None:
        self.logger.info("Machine reboot at request from SimplyPrint")
        command = self.settings.global_get(
            ["server", "commands", "systemRestartCommand"]
        )
        if command:
            self.simplyprint.close()
            self.simplyprint.event_bus.fire(Events.SHUTDOWN)
            self._run_system_command(command, do_async=True)

    def _call_pip(self, *args) -> Any:
        if self._pip_caller is None or not self._pip_caller.available:
            self.logger.error("No pip available, can't install plugin")
            raise Exception("No pip available, can't install")
        return self._pip_caller.execute(*args)

    def power_on_printer(self) -> None:
        self._do_power_action("on")

    def power_off_printer(self) -> None:
        self._do_power_action("off")

    def get_power_state(self) -> bool | None:
        if not self.settings.get_boolean(["has_power_controller"]):
            return None
        helpers = self.simplyprint.get_helpers("psucontrol")
        if helpers is None:
            helpers = self.simplyprint.get_helpers("simplypowercontroller")
            if helpers is not None and "get_status" in helpers:
                status = helpers["get_status"]()
                return status["isPSUOn"]

        elif "get_psu_state" in helpers:
            return helpers["get_psu_state"]()
        return None

    def _do_power_action(self, action: str) -> None:
        helpers = self.simplyprint.get_helpers("psucontrol")
        func: Callable | None = None
        if helpers is None:
            helpers = self.simplyprint.get_helpers("simplypowercontroller")
            if helpers is not None:
                func = helpers.get(f"psu_{action}")
        else:
            # psu control is available
            func = helpers.get(f"turn_psu_{action}")
        if func is not None:
            func()

    def get_filament_sensor_state(self) -> str | None:
        if not self.settings.get_boolean(["has_filament_sensor"]):
            return None
        helpers = self.simplyprint.get_helpers("simplyfilamentsensor")
        if helpers is not None and "get_status" in helpers:
            status = helpers["get_status"]()
            return "loaded" if status["has_filament"] else "runout"
        return None

    def _run_system_command(
        self, command: str, do_async: bool = False
    ) -> None:
        try:
            ret = sarge.run(
                command, close_fds=CLOSE_FDS, stdout=sarge.Capture(),
                stderr=sarge.Capture(), async_=do_async
            )
        except Exception:
            self.logger.exception("Error running command: %s", command)
            return
        if not do_async and ret.returncode != 0:
            stdout = ret.stdout.text  # type: ignore
            stderr = ret.stderr.text  # type: ignore
            self.logger.error(
                "Failed to run command '%s', returned with "
                "%s\n%s\n%s",
                command, ret.returncode, stdout, stderr
            )

    def check_software_update(self) -> list[dict[str, Any]]:
        port = self.simplyprint.plugin.port
        api_key = self.settings.global_get(["api", "key"])
        url = f"http://127.0.0.1:{port}/plugin/softwareupdate/check"
        try:
            resp = requests.get(
                url, headers={"X-Api-Key": api_key}, timeout=5
            )
            if not 200 <= resp.status_code <= 210:
                # Response code no good
                self.logger.warning("Couldn't check for an OctoPrint update, API returned invalid response")
                return []
            ret: dict[str, Any] = resp.json()
        except Exception:
            self.logger.warning("Error fetching OctoPrint Updates", exc_info=True)
            return []
        updates: list[dict[str, Any]] = []
        uinfo: dict[str, Any]
        for name, uinfo in ret.get("information", {}).items():
            if uinfo.get("updateAvailable", False):
                local_ver = uinfo["information"]["local"]["value"]
                remote_ver = uinfo["information"]["remote"]["value"]
                updates.append({
                    "plugin": name,
                    "local_version": local_ver,
                    "available_version": remote_ver,
                    "release_notes": uinfo["releaseNotes"]
                })
        return updates
