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
This script should be run in the background on the Raspberry Pi, to check that OctoPrint is alive and if it is not alive
then it will let SP know that it has died, and await instruction on how to proceed.
"""
import logging
import threading
import time

import requests

from octoprint.settings import settings
from octoprint.util import ResettableTimer
from octoprint.util.commandline import CommandlineCaller

from octoprint_simplyprint.comm.constants import UPDATE_URL, API_VERSION
from octoprint_simplyprint.local.util import OctoPrintClient, OctoPrintApiError


def run_background_check():
    simply_background = SimplyPrintBackground()
    simply_background.mainloop()


class SimplyPrintBackground:
    def __init__(self):
        self._logger = logging.getLogger("octoprint.plugins.SimplyPrint.background")
        self._logger.setLevel(logging.DEBUG)

        try:
            self._octoprint_settings = settings(init=True)
            # We need init as this runs in a separate process
            # NOTE: This should not be used to write to the settings file, since it could cause a conflict
            # with OctoPrint's settings, and lose some settings there.
        except ValueError:
            self._logger.error("This script shouldn't be run in the same process as OctoPrint")
            self._logger.error("So don't do that :) ")
            return

        self.octoprint = None

        self.was_octoprint_up = False
        self.failed_checks = 0

        self.main_thread = None
        self.run = True

        self.safe_mode_checks = 0

    def mainloop(self):
        port = self._octoprint_settings.get(["public_port"])
        if port is not None and port is not 80:
            ip = "http://127.0.0.1:{}".format(port)
        else:
            ip = "http://127.0.0.1"

        self.octoprint = OctoPrintClient(ip, self._octoprint_settings.get(["api", "key"]))

        while self.run:
            try:
                start = time.time()

                check_result = self.check_octoprint()
                if not check_result and self.was_octoprint_up:  # Only restart if OctoPrint was previously up
                    self._logger.warning("OctoPrint is not OK...")
                    self.ping_simplyprint("&octoprint_status=Shutdown")
                    self.failed_checks += 1
                    if self.failed_checks >= 2:
                        # Only restart after 2 consecutive failed checks
                        self._logger.warning("Trying to restart it now")
                        self.restart_octoprint()

                elif not check_result and not self.was_octoprint_up:
                    self._logger.warning("OctoPrint hasn't been seen yet, skipping")

                else:
                    self._logger.debug("OctoPrint seems OK")
                    self.was_octoprint_up = True
                    self.failed_checks = 0

                    safe_mode = self.check_safemode()
                    if not safe_mode:
                        self._logger.warning("OctoPrint is in safe mode")
                        if self.safe_mode_checks == 0 or self.safe_mode_checks > 10:
                            # Restart immediately, or after more than 10 mins
                            self.restart_octoprint()
                            self.safe_mode_checks = 0

                    else:
                        self._logger.debug("OctoPrint is not in safe mode")

                    self.safe_mode_checks += 1

                total_time = time.time() - start
                self._logger.debug("OctoPrint health check took {}".format(total_time))
                if self.run:
                    time.sleep(60 - total_time)
            except Exception as e:
                self._logger.exception(e)
                time.sleep(60)

    def check_octoprint(self):
        """
        Checks OctoPrint is alive
        """
        try:
            version = self.octoprint.version()
        except OctoPrintApiError:
            return False
        except Exception:
            # Some other random error, possibly connection refused if proxy dies
            return False

        if "octoprint" in version["text"].lower():
            return True
        else:
            return False

    def check_safemode(self):
        def check_server():
            try:
                server = self.octoprint.server()
            except OctoPrintApiError:
                # OctoPrint < 1.5.0, no /api/server
                return False

            return server["safemode"] is not None

        def check_pgmr():
            try:
                pgmr = self.octoprint.plugin_plugin_manager()
            except OctoPrintApiError:
                # Now it's possible its dead, more likely that user disabled plugin manager :/
                # Return True since its not definitive
                return True

            for plugin in pgmr["plugins"]:
                if plugin["safe_mode_victim"]:
                    return False

            return True

        if not check_server():
            return check_pgmr()
        else:
            return True

    def restart_octoprint(self):
        command = self._octoprint_settings.get(["server", "commands", "serverRestartCommand"])
        if not command:
            self._logger.warning("No command configured, can't restart")
            return

        caller = CommandlineCaller()
        try:
            code, stdout, stderr = caller.call(command, **{"shell": True}) # Use shell=True, as we have to trust user input
        except Exception as e:
            self._logger.error("Error calling command to restart server {}".format(command))
            self._logger.exception(e)
            return

        if code != 0:
            self._logger.error("Non zero return code running '{}' to restart server: {}".format(command, code))
            self._logger.exception("STDOUT: {}".format(stdout))
            self._logger.exception("STDERR: {}".format(stderr))

    def ping_simplyprint(self, parameters):
        rpi_id = self._octoprint_settings.get(["plugins", "SimplyPrint", "rpi_id"])
        if not rpi_id:
            # Not set up properly, nothing we can do - let the plugin handle getting a new ID
            return

        url = UPDATE_URL + "?id=" + rpi_id + "&api_version=" + API_VERSION
        url = url.replace(" ", "%20")

        try:
            response = requests.get(url, timeout=5)
        except requests.exceptions.RequestException as e:
            self._logger.error("Error sending get request to SimplyPrint")
            self._logger.exception(e)
            raise

        return response


if __name__ == '__main__':
    run_background_check()
