# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, unicode_literals

#
# SimplyPrint
# Copyright (C) 2020-2021  SimplyPrint ApS
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

"""
Handle all communication to SimplyPrint servers
"""
import json
import logging
import threading
import io
import os
import requests
import time
import sys
import uuid
import tempfile
import datetime

import sarge
import psutil

import octoprint.util
import octoprint.plugin
import octoprint.server
from octoprint.events import Events, eventManager
from octoprint.util.pip import LocalPipCaller
from octoprint.util.commandline import CommandlineCaller, CommandlineError

from .constants import API_VERSION, UPDATE_URL, SIMPLYPRINT_PLUGIN_INSTALL_URL
from .util import is_octoprint_setup, url_quote, has_internet, any_demand
from . import webcam, startup, constants
from .monitor import Monitor

# default close_fds settings (borrowed from OctoPrint core :) )
CLOSE_FDS = True
"""
Default setting for close_fds parameter to Popen/sarge.run.

Set ``close_fds`` on every sub process to this to ensure file handlers will be closed
on child processes on platforms that support this (anything Python 3.7+ or anything
but win32 in earlier Python versions).
"""
if sys.platform == "win32" and sys.version_info < (3, 7):
    # close_fds=True is only supported on win32 with enabled stdout/stderr
    # capturing starting with Python 3.7
    CLOSE_FDS = False


class SimplyPrintComm:
    def __init__(self, plugin):
        self.plugin = plugin
        self._logger = logging.getLogger("octoprint.plugins.SimplyPrint.comm")

        self._settings = plugin._settings
        self.printer = plugin._printer

        if self._settings.get(["debug_logging"]):
            self._logger.setLevel(logging.DEBUG)

        # Submodules - these depend on SimplyPrint sometimes, eg. self.ping()
        self.startup = startup.SimplyPrintStartup(self)

        # Various state-things
        self.request_settings_next_time = False
        self.has_checked_webcam_options = False
        self.has_checked_firmware_info = False
        self.has_checked_power_controller = False
        self.has_checked_filament_sensor = False
        self.previous_printer_text = ""
        self.state_timer = None
        self.user_input_required = False
        self.downloading = False
        self.download_status = None

        self.last_connection_attempt = time.time()
        self.first = True
        self.requests_failed = 0
        self.last_json_err = None
        self._files_analyzed = []

        self.times_per_minute = 45
        self.main_loop_thread = None
        self.livestream_thread = None
        self.next_check_update = datetime.date.today().day + 1

        # This uses OctoPrint's built in pip caller, exactly the same as PGMR/SWU use it
        self._pip_caller = LocalPipCaller(
            # Use --user on commands if user has configured it
            force_user=self._settings.global_get_boolean(["plugins", "pluginmanager", "pip_force-user"])
        )
        self.command_line = CommandlineCaller()

        # This variable will stop the loop when set to False, used on shutdown
        self.run_loop = True

    def main_loop(self):
        # Start a repeated timer to reset the state every minute
        if not self.state_timer or (self.state_timer and not self.state_timer.is_alive()):
            self.state_timer = octoprint.util.RepeatedTimer(60, self.reset_minute_checks)
            self.state_timer.start()

        self._logger.info("Starting webrequest loop")
        while self.run_loop:
            start_time = time.time()
            self._logger.debug("Request... {} times per minute".format(self.times_per_minute))

            # Check for update will only run each day
            self.update_check()

            # SimplyPrint ping & do commands
            try:
                request = self.request()

                if request:
                    # Request is good!
                    if self.requests_failed > 0:
                        self._set_display("Back online!", True)
                        self.requests_failed = 0
                else:
                    tenth = self.requests_failed % 10 == 0

                    if request is False:
                        # Failed to request - probably no internet
                        if has_internet():
                            # SimplyPrint is down / can't be reached - has connection to the internet
                            if tenth:
                                self._logger.info("Can't reach SimplyPrint ({})".format(str(self.requests_failed + 1)))
                                self._set_display("Can't reach SP", True)
                        else:
                            # Internet down
                            if tenth:
                                self._logger.info("Has no internet ({})".format(str(self.requests_failed + 1)))
                                self._set_display("No internet", True)
                    elif request is None:
                        # Failed to decode JSON
                        if tenth:
                            opt_json = ""

                            self._set_display("Decode failed", True)

                            if self.last_json_err is not None:
                                opt_json = "; " + str(self.last_json_err)

                            self._logger.info("Failed to decode JSON from SimplyPrint request ({})".format(
                                str(self.requests_failed + 1)) + opt_json)

                    self.requests_failed += 1

                    if self.requests_failed >= 3:
                        # Wait some time before trying again
                        if self.requests_failed >= 500:
                            sleeptime = 120
                        elif self.requests_failed >= 200:
                            sleeptime = 30
                        elif self.requests_failed >= 100:
                            sleeptime = 20
                        elif self.requests_failed > 50:
                            sleeptime = 10
                        else:
                            sleeptime = 5

                        if 60 / sleeptime <= self.times_per_minute:
                            time.sleep(sleeptime)
                            # Do next request right away
                            continue

            except Exception as e:
                self._logger.exception(e)

            # Performance monitoring
            request_time = time.time() - start_time
            self._logger.debug("request took {}".format(request_time))

            if request_time >= 60 / self.times_per_minute:
                # We took longer than we should have, proceed straight away to next req
                continue
            else:
                # Sleep for the difference and then go again :)
                time.sleep((60 / self.times_per_minute) - request_time)

    def start_main_loop(self):
        if self.main_loop_thread is None:
            self.main_loop_thread = threading.Thread(target=self.main_loop)
            self.main_loop_thread.daemon = True
            self.main_loop_thread.start()

    def start_startup(self):
        self.startup.run_startup()

    def reset_minute_checks(self):
        """
        This emulates previous behaviour where the script was run once per minute
        """
        self.has_checked_webcam_options = False
        self.has_checked_firmware_info = False
        self.has_checked_power_controller = False
        self.has_checked_filament_sensor = False
        self.first = True
        self.previous_printer_text = ""

    def update_check(self):
        # Only check for updates once per day
        if datetime.date.today() == self.next_check_update:
            self.check_for_updates()
            self.next_check_update = datetime.date.today().day + 1

    def _simply_get(self, url):
        url = url.replace(" ", "%20")

        headers = {
            "User-Agent": "OctoPrint-SimplyPrint/{}".format(self.plugin._plugin_version),
            "Connection": "close"
        }

        self._logger.debug("Sending GET to {}".format(url))

        try:
            response = requests.get(url, headers=headers, timeout=5)
        except requests.exceptions.RequestException as e:
            if self.requests_failed % 10 == 0:
                self._logger.error("Error sending get request to SimplyPrint")
                self._logger.error(repr(e))
            return False

        return response

    def ping(self, parameters=None):
        url = UPDATE_URL + "?id=" + self._settings.get(["rpi_id"]) + \
              "&api_version=" + API_VERSION

        if parameters is not None:
            url += parameters

        if not is_octoprint_setup():
            self._logger.info("OctoPrint is not set up yet")
            url += "&octoprint_not_set_up"

        printer_info = self.get_printer_info()
        try:
            printer_state = printer_info["state"]["text"]
        except KeyError:
            printer_state = printer_info["state"]

        if not self._settings.get_boolean(["is_set_up"]):
            # Make sure the printer is connected when it's expecting setup
            if time.time() > self.last_connection_attempt + 60:
                if self.printer.is_closed_or_error():
                    # Only re-connect if we aren't already connected
                    self._logger.info("Connecting printer")
                    self.printer.connect()
                    self.last_connection_attempt = time.time()

            url += "&new=true&printer_tmp_state=" + printer_state + \
                   "&custom_sys_version=" + str(self.plugin._plugin_version)

        else:
            if "offline" in printer_state.lower():
                printer_state = "Offline"

            to_set = {}
            if self.printer.is_operational():
                if "temperature" in printer_info and "bed" in printer_info["temperature"]:
                    bed_temp = printer_info["temperature"]["bed"]["actual"]
                    if bed_temp is not None:
                        to_set["bed_temp"] = round(bed_temp)
                    else:
                        to_set["bed_temp"] = 0

                    bed_target = printer_info["temperature"]["bed"]["target"]
                    if bed_target is not None:
                        to_set["target_bed_temp"] = round(bed_target)
                    else:
                        to_set["target_bed_temp"] = 0

                if "temperature" in printer_info and "tool0" in printer_info["temperature"]:
                    tool_temp = printer_info["temperature"]["tool0"]["actual"]
                    if tool_temp is not None:
                        to_set["tool_temp"] = round(tool_temp)
                    else:
                        to_set["tool_temp"] = 0

                    tool_target = printer_info["temperature"]["tool0"]["target"]
                    if tool_target is not None:
                        to_set["target_tool_temp"] = round(tool_target)
                    else:
                        to_set["target_tool_temp"] = 0

            if (
                    self.printer.is_printing()
                    or self.printer.is_cancelling()
                    or self.printer.is_pausing()
                    or self.printer.is_paused()
            ):
                print_job = self.get_print_job()
                if "completion" in print_job["progress"] and print_job["progress"]["completion"] is not None:
                    to_set["completion"] = round(float(print_job["progress"]["completion"]))
                if "printTimeLeftOrigin" in print_job["progress"] and print_job["progress"][
                    "printTimeLeftOrigin"] == "genius":
                    to_set["completion"] = round(float(print_job["progress"]["printTime"] or 0) / (
                                float(print_job["progress"]["printTime"] or 0) + float(
                            print_job["progress"]["printTimeLeft"])) * 100)
                to_set["estimated_finish"] = print_job["progress"]["printTimeLeft"]

                try:
                    if "filament" in print_job["job"] and print_job["job"]["filament"] is not None and "tool0" in \
                            print_job["job"]["filament"] and "volume" in print_job["job"]["filament"]["tool0"]:
                        to_set["filament_usage"] = print_job["job"]["filament"]["tool0"]["volume"]
                except:
                    pass

                to_set["initial_estimate"] = print_job["job"]["estimatedPrintTime"]

            url += "&custom_sys_version=" + str(self.plugin._plugin_version)

            url += "&pstatus=" + printer_state
            if self.user_input_required:
                url += "&userinputrequired"
            url += "&extra=" + url_quote(json.dumps(to_set))
            resources = Monitor(self._logger)
            url += "&health=" + url_quote(json.dumps(resources.get_all_resources()))

        return self._simply_get(url)

    def get_printer_info(self):
        """
        Similar to the OctoPrint API, but with a lot stripped out
        since SimplyPrint doesn't need it
        :return result: (dict) The current printer state
        """
        result = {}
        result.update({"temperature": self.printer.get_current_temperatures()})
        if self._settings.global_get_boolean(["feature", "sdSupport"]):
            result.update({"sd": {"ready": self.printer.is_sd_ready()}})

        result.update({"state": self.printer.get_current_data()["state"]})
        if self.user_input_required and self.printer.is_printing():
            result.update({"state": {"text": "Paused", "flags": {"paused": True}}})

        return result

    def get_print_job(self):
        """
        Similar to the OctoPrint API, returning the necessary data
        for SimplyPrint to know about the job
        :return result: (dict) the current print job data
        """
        current_data = self.printer.get_current_data()
        result = {
            "job": current_data["job"],
            "progress": current_data["progress"],
            "state": current_data["state"]["text"],
        }

        return result

    def request(self):
        extra = ""
        rpi_id = self._settings.get(["rpi_id"])
        if not rpi_id:
            try:
                response = self.ping("&request_rpid")
                if response is False:
                    return False
            except Exception as e:
                self._logger.error("Exception pinging simplyprint for rpid")
                self._logger.error(repr(e))
                return False
        else:
            if self.request_settings_next_time:
                extra = "&request_settings"
                self.request_settings_next_time = False

            if self._settings.get_boolean(["is_set_up"]):
                if self.first:
                    extra += "&first"
                    self.first = False

                if self._settings.get_boolean(["has_power_controller"]) and not self.has_checked_power_controller:
                    helpers = self.plugin._plugin_manager.get_helpers(
                        "psucontrol") or self.plugin._plugin_manager.get_helpers("simplypowercontroller")
                    if helpers:
                        if "get_psu_state" in helpers:
                            status = helpers["get_psu_state"]()
                            if status is True:
                                extra += "&power_controller=on"
                            else:
                                extra += "&power_controller=off"
                        if "get_status" in helpers:
                            status = helpers["get_status"]()
                            if "isPSUOn" in status and status["isPSUOn"]:
                                extra += "&power_controller=on"
                            else:
                                extra += "&power_controller=off"

                    self.has_checked_power_controller = True

                if self._settings.get_boolean(["has_filament_sensor"]) and not self.has_checked_filament_sensor:
                    helpers = self.plugin._plugin_manager.get_helpers("simplyfilamentsensor", "get_status")
                    try:
                        if helpers and helpers["get_status"]:
                            status = helpers["get_status"]()
                            if status["has_filament"]:
                                state = "loaded"
                            else:
                                state = "runout"
                            extra += "&filament_sensor=" + state
                    except Exception:
                        self._logger.warning("Couldn't get filament sensor status")
                    else:
                        self.has_checked_filament_sensor = True

                if not self.has_checked_webcam_options:
                    octoprint_webcam = {
                        "flipH": self._settings.global_get(["webcam", "flipH"]),
                        "flipV": self._settings.global_get(["webcam", "flipV"]),
                        "rotate90": self._settings.global_get(["webcam", "rotate90"]),
                    }
                    simplyprint_webcam = self._settings.get(["webcam"], merged=True)
                    diff = octoprint.util.dict_minimal_mergediff(octoprint_webcam, simplyprint_webcam)
                    if diff:
                        # Webcam settings in OctoPrint are different to SimplyPrint
                        extra += "&webcam_options=" + url_quote(json.dumps(octoprint_webcam))

                    self.has_checked_webcam_options = True

            try:
                response = self.ping("&recv_commands" + extra)
                if response is False:
                    return False
            except Exception as e:
                self._logger.error("Exception pinging simplyprint for extra {}".format(extra))
                self._logger.error(repr(e))
                return False

        try:
            response_json = response.json()
            self.last_json_err = None
        except ValueError as e:
            # Response was not valid json
            self.last_json_err = response.content
            if self.requests_failed % 10 == 0:
                self._logger.error("Failed to parse json, response from; " + response.text)
                self._logger.exception(e)
            return None

        if not rpi_id:
            # had empty RPI id - save new from server

            if "generated_rpi_id" in response_json and response_json["generated_rpi_id"]:
                self._logger.info("Tried to get RPI id from server")
                self._settings.set(["rpi_id"], response_json["generated_rpi_id"])
                self._settings.save()
            else:
                self._logger.info("Had empty RPI ID, still empty :/")

        if not self._settings.get_boolean(["is_set_up"]) and "printer_set_up" in response_json and response_json[
            "printer_set_up"]:
            # RPI thinks its not set up, but server does
            self._logger.debug("Setting is_set_up")
            self._settings.set_boolean(["is_set_up"], True)
            self._settings.set(["temp_short_setup_id"], "")
            self._settings.save(trigger_event=True)

            self.start_startup()

        elif not response_json["printer_set_up"] and self._settings.get_boolean(["is_set_up"]):
            # RPI thinks it is set up, but server disagrees
            self._logger.info("Printer is not set up anymore")
            self._settings.set_boolean(["is_set_up"], False)
            self._settings.save(trigger_event=True)
            return

        if "status" in response_json and response_json["status"]:
            demand_list = response_json["printer_demands"]
            response_settings = response_json["settings"]

            if int(response_settings["times_per_minute"]) != self.times_per_minute:
                self.times_per_minute = int(response_settings["times_per_minute"])

            self.process_demands(demand_list, response_json)

        return True

    def process_demands(self, demand_list, response_json):
        if any_demand(demand_list, ["identify_printer", "do_gcode", "gcode_code"]):
            self.demand_gcode(demand_list)

        if "send_octoprint_apikey" in demand_list:
            self.demand_octoprint_apikey()

        if "missing_info" in demand_list:
            # Server is missing info from startup
            self.start_startup()

        # Check setup state
        if not self._settings.get_boolean(["is_set_up"]):
            self.demand_not_set_up(demand_list, response_json)
            # If not setup, then don't bother with the rest
            return

        # Is set up, start processing demands
        try:
            self.demand_set_printer_name(response_json)
        except Exception as e:
            self._logger.error("Failed to update printer name and id")
            self._logger.error(repr(e))
            self._logger.error(repr(e))

        if "printer_settings" in demand_list:
            self.demand_sync_printer_settings(demand_list)

        if response_json["settings_updated"] > self._settings.get(["info", "last_user_settings_sync"]):
            self.request_settings_next_time = True

        if self.printer.is_printing():
            if self._settings.get_int(["display_while_printing_type"]) != 2:
                print_job = self.get_print_job()
                if "printTimeLeftOrigin" in print_job["progress"] and print_job["progress"][
                    "printTimeLeftOrigin"] == "genius":
                    progress = float(print_job["progress"]["printTime"] or 0) / (
                                float(print_job["progress"]["printTime"] or 0) + float(
                            print_job["progress"]["printTimeLeft"])) * 100
                else:
                    progress = print_job["progress"]["completion"]
                if progress is not None:
                    self._set_display("Printing {}%".format(int(round(progress))), True)
            else:
                self._set_display("Printing...", True)

        elif self.printer.is_operational() and self._settings.get_boolean(["display_show_status"]):
            self._set_display("Ready")

        elif self.printer.is_paused and self._settings.get_boolean(["display_show_status"]):
            self._set_display("Paused", True)

        if "system_reboot" in demand_list:
            self._set_display("Rebooting...", True)
            command = self._settings.global_get(["server", "commands", "systemRestartCommand"])
            if command:
                self._run_system_command("reboot", command)

        if "system_shutdown" in demand_list:
            self._set_display("Shutting down", True)
            command = self._settings.global_get(["server", "commands", "systemShudownCommand"])
            if command:
                self._run_system_command("shutdown", command)

        if "start_octoprint" in demand_list:
            # Useless, as we are already in OctoPrint
            pass

        if "shutdown_octoprint" in demand_list:
            self._set_display("Shutting down OctoPrint", True)
            command = self._settings.global_get(["server", "commands", "serverRestartCommand"])
            # OctoPrint does not have fields for server shutdown, if the restart command is there adapt that
            if command == "sudo service octoprint restart":
                self._run_system_command("server shutdown", "sudo service octoprint stop")

        if "restart_octoprint" in demand_list:
            self._set_display("Restarting OctoPrint", True)
            command = self._settings.global_get(["server", "commands", "serverRestartCommand"])
            if command:
                self._run_system_command("server restart", command)

        if "update_octoprint" in demand_list:
            # If you want to update OctoPrint, do it like this :)
            # Probably best as a separate function
            # self._set_display("Updating OctoPrint", True)
            # self._logger.info("Updating OctoPrint to the latest version")
            # pip_args = [
            #     "install",
            #     "https://get.octoprint.org/latest",
            #     "--no-cache-dir"
            # ]
            # try:
            #     returncode, stdout, stderr = self._call_pip(pip_args)
            # except Exception as e:
            #     self._logger.error("Could not update OctoPrint")
            #     return
            #
            # if returncode != 0:
            #     self._logger.error("Failed to update OctoPrint, failed with code {}".format(returncode))
            #     self._logger.error("!!STDOUT {}".format(stdout))
            #     self._logger.error("!!STDERR {}".format(stderr))
            # else:
            #     self._logger.info("Installation successful - server must now be restarted")
            pass

        if any_demand(demand_list, ["psu_on", "psu_keepalive"]):
            helpers = self.plugin._plugin_manager.get_helpers("psucontrol") or self.plugin._plugin_manager.get_helpers(
                "simplypowercontroller")
            # psucontrol plugin
            if "turn_psu_on" in helpers:
                helpers["turn_psu_on"]()
            # simplypowercontroller plugin
            if "psu_on" in helpers:
                helpers["psu_on"]()

        if "psu_off" in demand_list:
            helpers = self.plugin._plugin_manager.get_helpers("psucontrol") or self.plugin._plugin_manager.get_helpers(
                "simplypowercontroller")
            # psucontrol plugin
            if "turn_psu_off" in helpers:
                helpers["turn_psu_off"]()
            # simplypowercontroller plugin
            if "psu_off" in helpers:
                helpers["psu_off"]()
            pass

        if "webcam_settings_updated" in demand_list:
            self.demand_sync_webcam_settings(demand_list)

        if "octoprint_plugin_action" in demand_list:
            self.demand_plugin_action(demand_list)

        if "set_printer_profile" in demand_list:
            self.save_profile(demand_list)

        if "get_gcode_script_backups" in demand_list:
            self.demand_backup_gcode_scripts()

        if "has_gcode_changes" in demand_list:
            self.demand_pull_gcode_scripts(demand_list)

        if "take_picture" in demand_list:
            try:
                webcam.post_image(demand_list["picture_job_id"])
            except webcam.WebcamError:
                self._logger.error("Error taking picture, skipping")

        if "livestream" in demand_list:
            self.livestream_thread = webcam.start_livestream(self.livestream_thread)

        if "update_system" in demand_list:
            self.demand_update_system()

        if "connect_printer" in demand_list:
            if time.time() > self.last_connection_attempt + 60:
                if self.printer.is_closed_or_error():
                    # Only re-connect if we aren't already connected
                    self._logger.info("Connecting printer")
                    self.printer.connect()
                    self.last_connection_attempt = time.time()

        if "disconnect_printer" in demand_list:
            self._logger.info("Disconnecting printer")
            self.printer.disconnect()

        # ~ Logs
        # if has_demand("send_custom_log"):
        #     pass

        # if has_demand("send_octoprint_log"):
        #     pass

        # if has_demand("send_octoprint_serial_log"):
        #     pass

        if "stop_print" in demand_list:
            self._set_display("Cancelling...", True)
            self._logger.debug("Cancelling print")
            self.printer.cancel_print()

        if "do_pause" in demand_list:
            if not self.printer.is_paused():
                self._set_display("Pausing...", True)
                self._logger.debug("Pausing print")
                self.printer.pause_print()

        if "do_resume" in demand_list:
            if self.printer.is_paused():
                self._set_display("Resuming...", True)
                self._logger.debug("Resuming print")
                self.printer.resume_print()

        if "process_file" in demand_list:
            if self.printer.is_operational() and not self.downloading:
                self._set_display("Preparing...", True)

                download_url = demand_list["print_file"]
                if "file_name" in demand_list:
                    name = demand_list["file_name"]
                else:
                    name = str(uuid.uuid1())

                name = "{}.gcode".format(name)

                self.downloading = True
                download_thread = threading.Thread(target=self._process_file_request, args=(download_url, name),
                                                   daemon=True)
                download_thread.start()
                # create a fake thread loop, which can be broken out of
                internal_timer = time.time()
                while self.downloading:
                    if time.time() > (internal_timer + 5):
                        self._logger.debug("still downloading {}".format(name))
                        self.ping("&file_downloading=true&filename={}".format(name))
                        internal_timer = time.time()
                if not self.download_status:
                    if not self.printer.is_operational():
                        message = "Printer is not ready to print (state is not operational)"
                    else:
                        message = "Failed to get file, check logs for details"
                    self.ping("&file_downloaded=false&not_ready={}".format(message))
                else:
                    self.ping("&file_downloaded=true&filename={}".format(name))

        if "start_print" in demand_list:
            self._logger.info("Starting print")
            self.printer.start_print()

        if "test_endpoint" in demand_list:
            constants.UPDATE_URL = "https://testrequest.simplyprint.io/"
        else:
            constants.UPDATE_URL = "https://request.simplyprint.io/"

        if "test_livestream" in demand_list:
            constants.WEBCAM_SNAPSHOT_URL = "https://testlivestream.simplyprint.io/"
        else:
            constants.WEBCAM_SNAPSHOT_URL = "https://livestream.simplyprint.io/"

    def demand_update_system(self):
        self._logger.info("Updating system...")
        self._set_display("Updating...", True)
        data = {
            "name": "SimplyPrint",
            "pip_name": "SimplyPrint",
            "key": "SimplyPrint",
            "install_url": SIMPLYPRINT_PLUGIN_INSTALL_URL
        }
        self.install_plugin(data)
        self.ping("&system_update_started=true")

        command = self._settings.global_get(["server", "commands", "serverRestartCommand"])
        if command:
            self._run_system_command("restart", command)

    def demand_pull_gcode_scripts(self, demand_list):
        script_list = demand_list["has_gcode_changes"]
        if "cancel" in script_list and "pause" in script_list and "resume" in script_list:
            self._settings.settings.saveScript(
                "gcode", "afterPrintCancelled",
                octoprint.util.to_unicode("\n".join(script_list["cancel"]).replace("\r\n", "\n").replace("\r", "\n"))
            )
            self._settings.settings.saveScript(
                "gcode", "afterPrintPaused",
                octoprint.util.to_unicode("\n".join(script_list["pause"]).replace("\r\n", "\n").replace("\r", "\n"))
            )
            self._settings.settings.saveScript(
                "gcode", "beforePrintResumed",
                octoprint.util.to_unicode("\n".join(script_list["resume"]).replace("\r\n", "\n").replace("\r", "\n"))
            )
            self._logger.info("Pulled GCODE scripts from SP server")
            # Scripts don't go to config.yaml, so it does not show as modified - force it
            self._settings.save(force=True, trigger_event=True)

            self.ping("&gcode_scripts_fetched")

    def _run_system_command(self, name, command):
        self._logger.info("Performing command for {}: {}".format(name, command))

        def execute():
            # we run this with shell=True since we have to trust whatever
            # our admin configured as command and since we want to allow
            # shell-alike handling here...
            p = sarge.run(
                command,
                close_fds=CLOSE_FDS,
                stdout=sarge.Capture(),
                stderr=sarge.Capture(),
                shell=True,
            )

            if p.returncode != 0:
                stdout_text = p.stdout.text
                stderr_text = p.stderr.text
                self._logger.error("Command for {}:{} failed with return code {}:"
                                   "\nSTDOUT: {}\nSTDERR: {}".format(name, command, p.returncode, stdout_text,
                                                                     stderr_text))
            else:
                self._logger.debug("Command successful :)")

        try:
            execute()
        except Exception as e:
            self._logger.error("Error running command {}".format(command))
            self._logger.error(repr(e))

    def demand_backup_gcode_scripts(self):
        if not self._settings.get_boolean(["info", "gcode_scripts_backed_up"]):
            # Check if user has GCODE scripts in OctoPrint that should be backed up
            default_cancel_gcode = ";disablemotorsM84;disableallheaters{%snippet'disable_hotends'%}{%snippet'disable_bed'%};disablefanM106S0"

            current_cancel_gcode = self._settings.settings.loadScript("gcode", "afterPrintCancelled", source=True)
            if current_cancel_gcode.replace(" ", "").replace("\n", "") == default_cancel_gcode:
                current_cancel_gcode = ""

            current_resume_gcode = self._settings.settings.loadScript("gcode", "beforePrintResumed", source=True)
            current_pause_gcode = self._settings.settings.loadScript("gcode", "afterPrintPaused", source=True)

            if current_cancel_gcode or current_resume_gcode or current_pause_gcode:
                self._logger.info("Syncing local GCODE scripts to SP")
                self.ping("&gcode_scripts_backed_up=" + url_quote(json.dumps({
                    "cancel_gcode": current_cancel_gcode,
                    "pause_gcode": current_pause_gcode,
                    "resume_gcode": current_resume_gcode,
                })))
            else:
                self._logger.info("No backups needed, no user modified GCODE scripts")
                self.ping("&no_gcode_script_backup_needed")

            self._settings.set_boolean(["info", "gcode_scripts_backed_up"], True)
        else:
            self._logger.info("SP asked for GCODE backups, but already done that for this instance")
            self.ping("&no_gcode_script_backup_needed&alreadydone")

    # Demand handling ====================================

    def demand_gcode(self, demand_list):
        if isinstance(demand_list["gcode_code"], list):
            self.printer.commands(demand_list["gcode_code"])
        else:
            self._logger.error("Server said do gcode, but didn't give us a list of commands")

    def demand_octoprint_apikey(self):
        self.ping("&octoprint_api_key=" + self._settings.global_get(["api", "key"]))

    def demand_not_set_up(self, demand_list, response_json):
        # Not set up...
        if response_json["locked"]:
            self._set_display("Locked")
        elif "printer_set_up" in demand_list:
            self._logger.info("Printer is not set up, but server says yes.. setting up")
            self._settings.set_boolean(["is_set_up"], True)
            self._settings.set(["temp_short_setup_id"], "")
            self._settings.save(trigger_event=True)
            self._set_display("Set up!", True)
        else:
            if self._settings.get(["temp_short_setup_id"]) != response_json["printer_set_up_short_id"]:
                self._settings.set(["temp_short_setup_id"], response_json["printer_set_up_short_id"])
                self._settings.save(trigger_event=True)

        if "update_system" in demand_list:
            self.demand_update_system()

        if "missing_firmware_info" in demand_list:
            if not self.has_checked_firmware_info:
                self.printer.disconnect()
                self.has_checked_firmware_info = True
            else:
                self.printer.connect()
        self._set_display(response_json["printer_set_up_short_id"])

    def demand_set_printer_name(self, response_json):
        if self._settings.get(["printer_id"]) != str(response_json["printer_id"]):
            self._settings.set(["printer_id"], str(response_json["printer_id"]))
        if sys.version_info.major == 3:
            printer_name = str(response_json["printer_name"]).strip()
        else:
            printer_name = response_json["printer_name"].encode("utf-8").strip()
        if self._settings.get(["printer_name"]) != printer_name:
            self._logger.info("Updating printer name to {}".format(printer_name))
            self._settings.set(["printer_name"], printer_name)

        self._settings.save(trigger_event=True)

    def demand_sync_printer_settings(self, demand_list):
        printer_settings = demand_list["printer_settings"]
        if "display" in printer_settings:
            display_settings = printer_settings["display"]
            if "enabled" in display_settings:
                if display_settings["enabled"]:
                    self._settings.set(["display_enabled"], True)
                else:
                    self._settings.set(["display_enabled"], False)

            if "branding" in display_settings:
                if display_settings["branding"]:
                    self._settings.set(["display_branding"], True)
                else:
                    self._settings.set(["display_branding"], False)

            if "while_printing_type" in display_settings:
                self._settings.set(["display_while_printing_type"], str(display_settings["while_printing_type"]))

            if "show_status" in display_settings:
                if display_settings["show_status"]:
                    self._settings.set_boolean(["display_show_status"], True)
                else:
                    self._settings.set_boolean(["display_show_status"], False)
        if "has_power_controller" in printer_settings:
            if printer_settings["has_power_controller"]:
                self._settings.set_boolean(["has_power_controller"], True)
            else:
                self._settings.set_boolean(["has_power_controller"], False)
        if "has_filament_sensor" in printer_settings:
            if printer_settings["has_filament_sensor"]:
                self._settings.set_boolean(["has_filament_sensor"], True)
            else:
                self._settings.set_boolean(["has_filament_sensor"], False)
        self._settings.set(["info", "last_user_settings_sync"], printer_settings["updated_datetime"])
        self._settings.save(trigger_event=True)

    def demand_sync_webcam_settings(self, demand_list):
        self._logger.info("Webcam settings update")
        cam_settings = json.loads(demand_list["webcam_settings_updated"])
        new = {
            "flipH": False,
            "flipV": False,
            "rotate90": False,
        }
        if "flipH" in cam_settings and cam_settings["flipH"]:
            new["flipH"] = True
            self._settings.global_set(["webcam", "flipH"], True)
        else:
            self._settings.global_set(["webcam", "flipH"], False)

        if "flipV" in cam_settings and cam_settings["flipV"]:
            new["flipV"] = True
            self._settings.global_set(["webcam", "flipV"], True)
        else:
            self._settings.global_set(["webcam", "flipV"], False)

        if "rotate90" in cam_settings and cam_settings["rotate90"]:
            new["rotate90"] = True
            self._settings.global_set(["webcam", "rotate90"], True)
        else:
            self._settings.global_set(["webcam", "rotate90"], False)

        self._settings.set(["webcam"], new)  # Set in SP
        self._settings.save()

    def demand_plugin_action(self, demand_list):
        restart_octoprint = False
        if "is_plugin_update" in demand_list:
            self.ping("&system_update_started")

        for action in demand_list["octoprint_plugin_action"]:
            if action["type"] == "install":
                restart_octoprint = self.install_plugin(action)

            elif action["type"] == "uninstall":
                restart_octoprint = self.uninstall_plugin(action)

            elif action["type"] == "set_settings":
                self.set_plugin_settings(action)

            if "restart" in action and action["restart"]:
                restart_octoprint = True

        self.ping("&plugin_actions_executed")

        # End of plugin action loop
        if restart_octoprint:
            self._logger.info("Restarting OctoPrint")
            command = self._settings.global_get(["server", "commands", "serverRestartCommand"])
            if command:
                self._run_system_command("server restart", command)

    def install_plugin(self, action):
        self._logger.info("Installing OctoPrint plugin {}".format(action["name"]))

        sp_installed_plugins = self._settings.get(["sp_installed_plugins"])
        if action["key"] not in sp_installed_plugins:
            sp_installed_plugins.append(action["key"])
        if isinstance(sp_installed_plugins, list):
            self._settings.set(
                ["sp_installed_plugins"],
                sp_installed_plugins
            )
        else:
            if action["key"] is not None:
                self._settings.set(["sp_installed_plugins"], [action["key"]])

        self._settings.save()

        pip_name = action["pip_name"]
        install_url = action["install_url"]
        self._logger.info("Installing plugin {} from {}".format(pip_name, install_url))
        pip_args = [
            "install",
            install_url,
            "--no-cache-dir",
        ]
        try:
            returncode, stdout, stderr = self._call_pip(pip_args)
        except Exception as e:
            self._logger.exception("Could not install plugin from {}".format(install_url))
            self._logger.exception(e)
            return

        if returncode != 0:
            self._logger.error("Plugin install failed with return code {}".format(returncode))
            self._logger.error("!!STDOUT: {}".format(stdout))
            self._logger.error("!!STDERR: {}".format(stderr))
        else:
            self._logger.info("Installation successful")
            self._settings.save()

        restart_octoprint = True
        return restart_octoprint

    def uninstall_plugin(self, action):
        self._logger.info("Uninstalling plugin {}".format(action["name"]))
        simplyprint_plugins = self._settings.get(["sp_installed_plugins"])
        if isinstance(simplyprint_plugins, list) and action["name"] in simplyprint_plugins:
            simplyprint_plugins.remove(action["name"])
            self._settings.set(["sp_installed_plugins"], simplyprint_plugins)
            self._settings.save()

        pip_args = [
            "uninstall",
            "--yes",
            action["pip_name"].replace(" ", "-")
        ]
        try:
            returncode, stdout, sdterr = self._call_pip(pip_args)
        except Exception as e:
            self._logger.error("Could not uninstall plugin")
            self._logger.exception(e)
            # return
        restart_octoprint = True
        return restart_octoprint

    def set_plugin_settings(self, action):
        plugins_to_set = list(action["settings"]["plugins"].keys())

        if "plugins" in action["settings"]:
            # Find the plugin implementations and get them to save the data
            for plugin in self.plugin._plugin_manager.get_implementations(
                    octoprint.plugin.SettingsPlugin
            ):
                plugin_id = plugin._identifier
                if plugin_id in action["settings"]["plugins"]:
                    # Set the settings for this plugin, which we have data for
                    try:
                        plugin.on_settings_save(action["settings"]["plugins"][plugin_id])
                        # Remove from pending list
                        plugins_to_set.remove(plugin_id)
                    except Exception as e:
                        self._logger.exception("Could not save settings for plugin {}".format(plugin._plugin_name))
                        self._logger.exception(e)

            if plugins_to_set:
                # There are some that we can't call, probably just installed, not loaded
                # Merge with what's there, if any, then force write it to config.yaml
                for plugin_id in plugins_to_set:
                    current = self._settings.global_get(["plugins", plugin_id])
                    if current is None:
                        current = {}

                    # Merge new settings on top of any current
                    new_current = octoprint.util.dict_merge(current, action["settings"]["plugins"][plugin_id])

                    # Force write to config.yaml
                    self._settings.global_set(["plugins", plugin_id], new_current, force=True)

            self._settings.save(trigger_event=True)

    def save_profile(self, demand_list):
        profile_manager = octoprint.server.printerProfileManager
        simplyprint_profile = demand_list["set_printer_profile"]

        # Slightly borrowed from OctoPrint's API
        profile = profile_manager.get("sp_printer")
        if profile is None:
            profile = profile_manager.get_default()
        new_profile = simplyprint_profile
        merged_profile = octoprint.util.dict_merge(profile, new_profile)
        make_default = False
        if "default" in merged_profile:
            make_default = True
            del new_profile["default"]

            merged_profile["id"] = "sp_printer"
        try:
            _saved_profile = profile_manager.save(
                merged_profile,
                allow_overwrite=True,
                make_default=make_default,
                trigger_event=True,
            )
        except Exception as e:
            self._logger.exception("Failed to save printer profile")
            self._logger.error(repr(e))
            return

        self.ping("&type_settings_fetched")

    # Event handling ================================================
    def on_event(self, event, payload):
        if payload != "" and payload is not None:
            try:
                event_details = json.dumps(payload)
            except ValueError:
                event_details = payload
        else:
            event_details = ""

        url_parameters = ""

        if event in [Events.CONNECTING, Events.CONNECTED, Events.DISCONNECTING, Events.DISCONNECTED]:
            url_parameters += "&connection_status=" + event

        elif event[0:5] == "Print" or event == Events.FILE_SELECTED:
            if event == Events.PRINT_FAILED:
                url_parameters += "&failed_reason=" + payload["reason"]
            url_parameters += "&print_status=" + event

        elif event in [Events.SHUTDOWN, Events.STARTUP]:
            url_parameters += "&octoprint_status=" + event

        elif event == Events.FIRMWARE_DATA:
            url_parameters += "&firmware_data=" + url_quote(event_details)

        elif event == "plugin_firmware_check_warning":
            url_parameters += "&firmware_warning=" + str(event)

        elif event == "plugin_bedlevelvisualizer_mesh_data_collected":
            mesh_data = url_quote(json.dumps(payload["mesh"]))
            url_parameters += "&mesh_data=" + mesh_data

        elif event == "plugin_simplyfilamentsensor_filament_loaded":
            url_parameters += "&filament_sensor=loaded"

        elif event == "plugin_simplyfilamentsensor_filament_runout":
            url_parameters += "&filament_sensor=runout"

        elif event == "plugin_simplyfilamentsensor_filament_no_filament_print_on_print_start":
            url_parameters += "&filament_sensor=print_stopped"

        elif event == "plugin_psucontrol_psu_state_changed":
            if payload["isPSUOn"] is True:
                url_parameters += "&power_controller=on"
            else:
                url_parameters += "&power_controller=off"

        elif event == "plugin_simplypowercontroller_power_on":
            url_parameters += "&power_controller=on"

        elif event == "plugin_simplypowercontroller_power_off":
            url_parameters += "&power_controller=off"

        # OctoPrint metadata analysis
        elif event == Events.METADATA_ANALYSIS_FINISHED:
            if (
                    "result" in payload
                    and "analysisPending" in payload["result"]
                    and not payload["result"]["analysisPending"]
                    and payload["name"][0:3] == "sp_"
                    and "filament" in payload["result"]
                    and payload["path"] not in self._files_analyzed
                    and payload["origin"] == "local"
                    and self.printer.is_current_file(payload["path"], False)
            ):
                self._logger.info("Got analysis from a SP uploaded file")

                length = 0

                for tool in payload["result"]["filament"]:
                    if "length" in payload["result"]["filament"][tool]:
                        length += payload["result"]["filament"][tool]["length"]

                length = round(length)
                if length > 0:
                    self._files_analyzed.append(payload["path"])
                    self._logger.debug("Using {}mm of filament".format(length))
                    url_parameters += "&filament_analysis=" + str(length)
                else:
                    self._logger.warning("Filament usage is reportedly 0mm, not worth reporting")

        elif (
                event == Events.FILE_REMOVED
                and isinstance(payload, dict)
                and "name" in payload
                and payload["name"][:3] == "sp_"
        ):
            url_parameters += "&file_removed=" + url_quote(payload["name"])

        # Not elif as we also want to check for startup again
        if event in [
            "plugin_pluginmanager_install_plugin",
            "plugin_pluginmanager_uninstall_plugin",
            "plugin_pluginmanager_enable_plugin",
            "plugin_pluginmanager_disabled_plugin",
            Events.STARTUP,
        ]:
            if event == "plugin_pluginmanager_uninstall_plugin":
                if "id" in payload and payload["id"] == "SimplyPrint":
                    self._logger.info("The SimplyPrint plugin was uninstalled")
                    self.plugin._uninstall_sp()

            sp_plugins = self._settings.get(["sp_installed_plugins"])
            if not isinstance(sp_plugins, list):
                sp_plugins = []
            installed_plugins = []

            plugins = self.plugin._plugin_manager.plugins
            for key, plugin in plugins.items():
                if not plugin.bundled and plugin.enabled:  # plugin.hidden removed for 1.3.12 compat
                    installed_plugins.append({
                        "key": plugin.key,
                        "name": plugin.name,
                        "author": plugin.author,
                        "version": plugin.version,
                        "sp_installed": plugin.name in sp_plugins or plugin.key == "simplyprint",
                        "pip_name": self.plugin._plugin_manager.get_plugin_info(plugin.key).origin.package_name
                    })
            url_parameters += "&octoprint_plugins" + url_quote(json.dumps(installed_plugins))

        if url_parameters != "":
            self.ping(url_parameters)

    def check_for_updates(self):
        port = self.plugin.port
        api_key = self._settings.global_get(["api", "key"])
        try:
            response = requests.get("http://127.0.0.1:{}/plugin/softwareupdate/check".format(port),
                                    headers={"X-Api-Key": api_key}, timeout=5)
            if not 200 <= response.status_code <= 210:
                # Response code no good
                self._logger.warning("Couldn't check for an OctoPrint update, API returned invalid response")
                return
            response_json = response.json()
        except requests.exceptions.RequestException:
            self._logger.error("Problem requesting URL, maybe OctoPrint is offline...?")
            return
        except ValueError:
            self._logger.error("Problem converting response to JSON, can't check for updates")
            return
        except Exception as e:
            self._logger.error("Unexpected error checking for updates")
            self._logger.exception(e)
            return

        available_updates = []
        if "information" in response_json:
            for plugin in response_json["information"]:
                if "updateAvailable" in response_json["information"][plugin] and response_json["information"][plugin][
                    "updateAvailable"]:
                    release_notes = response_json["information"][plugin]["releaseNotes"]
                    new_version = response_json["information"][plugin]["information"]["remote"]["name"]

                    available_updates.append({
                        "plugin": plugin,
                        "version": new_version,
                        "release_notes": release_notes
                    })

        if available_updates:
            self._logger.info("Updates available")
            self.ping("&updates_available=" + url_quote(json.dumps(available_updates)))

    # Helpers =======================================================

    def _call_pip(self, args):
        if self._pip_caller is None or not self._pip_caller.available:
            self._logger.error("No pip available, can't install plugin")
            raise RuntimeError("No pip available, can't operate")

        return self._pip_caller.execute(*args)

    def _set_display(self, string, short_branding=False):
        if self._settings.get_boolean(["is_set_up"]) and not self._settings.get_boolean(["display_enabled"]):
            # Don't set display if set up and not enabled, bail instead
            # Text should always display if printer is in setup mode
            return

        if not isinstance(string, str):
            # Attempt to stringify what we have
            try:
                string = str(string)
            except Exception as e:
                self._logger.error("Could not stringify {} to set printer's display".format(string))
                return

        if string == self.previous_printer_text:
            return

        self.previous_printer_text = string

        prefix = ""
        if self._settings.get_boolean(["display_branding"]):
            if short_branding:
                prefix = "[SP] "
            else:
                prefix = "[SimplyPrint] "

        self.printer.commands("M117 {}{}".format(prefix, string))

    def _process_file_request(self, download_url, new_filename):
        from octoprint.filemanager.util import DiskFileWrapper
        from octoprint.filemanager.destinations import FileDestinations
        local = FileDestinations.LOCAL

        # Delete any old files in SimplyPrint folder
        files = self.plugin._file_manager.list_files(local, "SimplyPrint")
        for file, data in files["local"].items():
            # assume we only upload to this folder and delete everything...
            self.plugin._file_manager.remove_file(local, data["path"])

        if new_filename is None:
            new_filename = str(uuid.uuid1())

        # Free space usage
        free = psutil.disk_usage(
            self._settings.global_get_basefolder("uploads", check_writable=False)
        ).free

        self._logger.info("Downloading new file, name: {}, free space: {}".format(new_filename, free))

        try:
            # Use a long timeout - the file might be large and slow
            response = requests.get(download_url, allow_redirects=True, timeout=30)
        except Exception as e:
            self._logger.error("Unable to download file from {}".format(download_url))
            self._logger.error(repr(e))
            self.download_status = False
            self.downloading = False
            return False

        if not response.status_code == 200:
            self._logger.error("Reponse from download URL {} was {}".format(download_url, response.status_code))
            self.download_status = False
            self.downloading = False
            return False

        # response.content currently contains the file's content in memory, now write it to a temporary file
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, "simplyprint-file-upload-{}".format(new_filename))

        self._logger.debug("Saving temporary file to {}".format(temp_path))
        with io.open(temp_path, "wb") as temp_file:
            temp_file.write(response.content)

        self._logger.debug("Copying file to filemanager")
        upload = DiskFileWrapper(new_filename, temp_path)

        try:
            canon_path, canon_filename = self.plugin._file_manager.canonicalize(
                FileDestinations.LOCAL, "SimplyPrint/{}".format(upload.filename)
            )
            future_path = self.plugin._file_manager.sanitize_path(FileDestinations.LOCAL, canon_path)
            future_filename = self.plugin._file_manager.sanitize_name(FileDestinations.LOCAL, canon_filename)
        except Exception as e:
            # Most likely the file path is not valid for some reason
            self._logger.exception(e)
            self.download_status = False
            self.downloading = False
            return False

        future_full_path = self.plugin._file_manager.join_path(
            FileDestinations.LOCAL, future_path, future_filename
        )
        future_full_path_in_storage = self.plugin._file_manager.path_in_storage(
            FileDestinations.LOCAL, future_full_path
        )

        # Check the file is not in use by the printer (ie. currently printing)
        if not self.printer.can_modify_file(future_full_path_in_storage, False):  # args: path, is sd?
            self._logger.error("Tried to overwrite file in use")
            self.download_status = False
            self.downloading = False
            return False

        try:
            added_file = self.plugin._file_manager.add_file(
                FileDestinations.LOCAL,
                future_full_path_in_storage,
                upload,
                allow_overwrite=True,
                display=canon_filename
            )
        except octoprint.filemanager.storage.StorageError as e:
            self._logger.error("Could not upload the file {}".format(new_filename))
            self._logger.exception(e)
            self.download_status = False
            self.downloading = False
            return False

        # Select the file for printing
        self.printer.select_file(
            future_full_path_in_storage,
            False,  # SD?
            False,  # Print after select?
        )

        # Fire file uploaded event
        payload = {
            "name": future_filename,
            "path": added_file,
            "target": FileDestinations.LOCAL,
            "select": True,
            "print": False,
        }
        eventManager().fire(Events.UPLOAD, payload)
        self._logger.debug("Finished uploading the file")

        # Remove temporary file (we didn't forget about you!)
        try:
            os.remove(temp_path)
        # except FileNotFoundError:
        #    pass
        except Exception:
            self._logger.warning("Failed to remove file at {}".format(temp_path))

        # We got to the end \o/
        # Likely means everything went OK
        self.download_status = False
        self.downloading = False
        return True
