# coding=utf-8
from __future__ import absolute_import

import os
import sys
import json
import requests
import threading

import flask

import octoprint
import octoprint.settings


class SimplyPrint(octoprint.plugin.SettingsPlugin,
                  octoprint.plugin.StartupPlugin,
                  octoprint.plugin.TemplatePlugin,
                  octoprint.plugin.SimpleApiPlugin,
                  octoprint.plugin.AssetPlugin,
                  octoprint.plugin.EventHandlerPlugin):

    def __init__(self):
        self._files_analyzed = []
        self.events = [
            "Connecting",
            "Connected",
            "Disconnecting",
            "Disconnected",

            "Shutdown",
            "Startup",

            "Error",

            "FileSelected",

            "PrintStarted",
            "PrintFailed",
            "PrintDone",
            "PrintCancelling",
            "PrintCancelled",
            "PrintPaused",
            "PrintResumed",

            "plugin_printer_safety_check_warning",
            "FirmwareData",

            "plugin_bedlevelvisualizer_mesh_data_collected",

            "plugin_pluginmanager_install_plugin",
            "plugin_pluginmanager_uninstall_plugin",
            "plugin_pluginmanager_enable_plugin",
            "plugin_pluginmanager_disabled_plugin",

            "plugin_simplyfilamentsensor_filament_loaded",
            "plugin_simplyfilamentsensor_filament_runout",
            "plugin_simplyfilamentsensor_filament_no_filament_print_on_print_start",
            "plugin_simplyfilamentsensor_no_filament_on_print_start_paused",
            "plugin_simplyfilamentsensor_no_filament_on_print_start_cancelled",

            "plugin_simplypowercontroller_power_on",
            "plugin_simplypowercontroller_power_off",

            "MetadataAnalysisFinished",

            "plugin_firmware_check_warning",
            "FileRemoved"
        ]

    # #~~ StartupPlugin mixin
    def on_after_startup(self):
        self.log("OctoPrint plugin started")
        # The "Startup" event is never picked up by the plugin, as the plugin is loaded AFTER startup
        self.on_event("Startup", "")
        if not self._settings.get(["sp_local_installed"]) or int(
                self._settings.get(["simplyprint_version"]).replace(".", "")) < 234:
            self._logger.info("SimplyPrintLocal not setup, will do so now.")
            thread = threading.Thread(target=self.setup_local)
            thread.start()

    @staticmethod
    def get_settings_defaults():
        return dict(
            request_url="",
            rpi_id="",
            is_set_up=False,
            sp_local_installed=False,
            printer_name="",
            printer_id="",
            simplyprint_version="",
            temp_short_setup_id="",
            sp_installed_plugins="",
        )

    @staticmethod
    def get_assets():
        return dict(
            js=["js/SimplyPrint.js"],
            css=["css/SimplyPrint.css"],
            font=["font/lcd.ttf"],
            logo=["img/sp_logo.png"],
            logo_lg=["img/sp_logo_large.png"],
            logo_white_sm=["img/sp_white_sm.png"]
        )

    def get_api_commands(self):
        return {
            "setup": [],  # Sets up SimplyPrintRPiSoftware
            "uninstall": [],  # Uninstalls SimplyPrintRPiSoftware
        }

    def _uninstall_sp(self):
        try:
            from simplyprint_raspberry import uninstall
        except ImportError:
            self._logger.error("SimplyPrintRPiSoftware not installed, couldn't setup.")
            return flask.jsonify({"success": False, "message": "sp-rpi_not_available"})

        # self._settings.set(["sp_local_installed"], False, trigger_event=True)  # Fire event
        # self._settings.clean_all_data()
        self._settings.global_remove(["plugins", "SimplyPrint"])
        octoprint.settings.settings().save()

        uninstall.run_uninstall()
        self._logger.info("Uninstalled SimplyPrintRPiSoftware")

    def on_api_command(self, command, data):
        if command == "setup":
            thread = threading.Thread(target=self.setup_local)
            thread.start()
        elif command == "uninstall":
            self._uninstall_sp()
            # At this point SimplyPrintRPiSoftware is gone... :(
            return flask.jsonify({"success": True, "message": "sp-rpi_installed"})

    def setup_local(self):
        self._logger.info("Starting setup of SimplyPrintLocal")
        try:
            from simplyprint_raspberry import crontab_manager, startup
            from simplyprint_raspberry.__main__ import run_initial_webrequest
        except ImportError:
            self._logger.error("SimplyPrintRPiSoftware not installed - plugin must be reinstalled")
            self._plugin_manager.send_plugin_message("SimplyPrint",
                                                     {"success": False, "message": "sp-rpi_not_available"})
            return
        try:
            crontab_manager.create_cron_jobs()
            # Startup can hang for a bit, so run as a thread
            startup_thread = threading.Thread(target=startup.run_startup)
            startup_thread.start()
            run_initial_webrequest()
        except Exception as e:
            self._logger.error(repr(e))
            self._logger.error("Failed to setup SimplyPrintRPiSoftware")
            self._plugin_manager.send_plugin_message("SimplyPrint", {"success": False, "message": "spi-rpi_error"})
            return

        self._logger.info("Successfully setup SimplyPrintRPiSoftware")
        self._settings.set(["sp_local_installed"], True)
        octoprint.settings.settings().save(trigger_event=True)
        self._plugin_manager.send_plugin_message("SimplyPrint", {"success": True, "message": "sp-rpi_installed"})

    def on_api_get(self, request):
        import flask
        import subprocess
        # self.log(str(request))
        # self.log(str(request.args))

        if request.args is not None:
            if request.args.get("install", default=None, type=None) is not None:
                # Install
                pass
            if request.args.get("rpi_id", default=None, type=None) is not None:
                # Get RPI id
                pass
            if request.args.get("do_gcode", default=None, type=None) is not None:
                # Execute GCODE
                gcode_todo = str(request.args.get("do_gcode", default=None, type=None))
                self.log("Should do GCODE; " + gcode_todo)
                self._printer.commands(gcode_todo.split(","))
                pass
            if request.args.get("power_controller", default=None, type=None) is not None:
                # Power Controller
                new_state = str(request.args.get("power_controller", default=None, type=None))
                psu_state = ""
                if new_state == "1":
                    # Turn Power Controller on
                    psu_state = "turnPSUOn"
                elif new_state == "0":
                    # Turn Power Controller off
                    psu_state = "turnPSUOff"
                elif new_state == "get":
                    # Turn Power Controller off
                    psu_state = "getPSUState"

                try:
                    r = requests.post("http://localhost/api/plugin/psucontrol", data={"command": psu_state},
                                      allow_redirects=True, verify=False)
                    r.raise_for_status()

                    # Parse
                    try:
                        the_json = json.loads(r.content)
                    except:
                        self.log("Failed to format request response to JSON; " + str(r.content))
                        return False
                except:
                    pass

    def log(self, msg):
        self._logger.info("[SimplyPrint] " + msg)

    # #-- EventHandler mixin
    def on_event(self, event, payload):
        event_name = event
        event_details = ""

        # if event_name not in self.events:
        #    self.log("------ GOT EVENT NOT IN LIST; " + event_name)

        if event_name in self.events:
            if payload != "" and payload is not None:
                try:
                    event_details = json.dumps(payload)
                except:
                    event_details = payload

            # self.log("got even from list!; " + str(event_name) + ", payload; " + str(event_details))
            self.log("got even from list!; " + str(event_name))
            url_parameters = ""

            if event_name == self.events[0] or event_name == self.events[1] or event_name == self.events[
                2] or event_name == \
                    self.events[3]:
                # Connecting, Connected, Disconnecting, Disconnected
                url_parameters += "&connection_status=" + event_name

            elif event_name[0:5] == "Print" or event_name == "FileSelected":
                if event_name == "PrintFailed":
                    url_parameters += "&failed_reason=" + payload["reason"]

                url_parameters += "&print_status=" + event_name

            elif event_name == self.events[4] or event_name == self.events[5]:
                # Shutdown or Startup
                url_parameters += "&octoprint_status=" + event_name

            elif event_name == "FirmwareData":
                url_parameters += "&firmware_data=" + str(event_details)

            elif event_name == "plugin_printer_safety_check_warning":
                # Unsafe firmware - suggest update
                url_parameters += "&unsafe_firmware=" + str(event_details)
            elif event_name == "plugin_firmware_check_warning":
                # Printer firmware warning - not the same as unsafe firmware
                url_parameters += "&firmware_warning=" + str(event_details)

            elif event_name == "Error":
                url_parameters += "&printer_error=" + str(event_details)

            # Bed Leveling mesh received!
            elif event_name == "plugin_bedlevelvisualizer_mesh_data_collected":
                # Get mesh bed level data from external plugin
                mesh_data = requests.utils.quote(json.dumps(payload["mesh"]))
                url_parameters += "&mesh_data=" + mesh_data

            # Filament sensor stuff
            elif event_name == "plugin_simplyfilamentsensor_filament_loaded":
                # Filament is loaded
                url_parameters += "&filament_sensor=loaded"
            elif event_name == "plugin_simplyfilamentsensor_filament_runout":
                # Filament has run out!
                url_parameters += "&filament_sensor=runout"
            elif event_name == "plugin_simplyfilamentsensor_filament_no_filament_print_on_print_start":
                # The filament sensor plugin stopped the print from starting due to no filament at start
                url_parameters += "&filament_sensor=print_stopped"

            # Power controller
            elif event_name == "plugin_simplypowercontroller_power_on":
                url_parameters += "&power_controller=on"
            elif event_name == "plugin_simplypowercontroller_power_off":
                url_parameters += "&power_controller=off"

            # Metadata analysis
            elif event_name == "MetadataAnalysisFinished":
                if "result" in payload and "analysisPending" in payload["result"]:
                    if not payload["result"]["analysisPending"]:
                        if payload["name"][0:3] == "sp_" and "filament" in payload["result"]:
                            # Result is OK - not pending
                            if payload["path"] not in self._files_analyzed and payload["origin"] == "local":
                                if self._printer.is_current_file(payload["path"], False):
                                    self.log("Got analysis data from a SP-uploaded file; " + str(payload["path"]))

                                    total_length = 0

                                    for x in payload["result"]["filament"]:
                                        if "length" in payload["result"]["filament"][x]:
                                            total_length += payload["result"]["filament"][x]["length"]

                                    total_length = round(total_length)
                                    if total_length > 0:
                                        self._files_analyzed.append(payload["path"])
                                        self.log("Using filament; " + str(total_length) + "mm")
                                        url_parameters += "&filament_analysis=" + str(total_length)
                                    else:
                                        self.log(
                                            "Filament usage is reportedly 0mm... Not worth reporting (and might not be true)")

            elif event_name == "FileRemoved":
                # "Re-print"
                if isinstance(payload, dict) and "name" in payload and payload["name"][:3] == "sp_":
                    # SimplyPrint file removed
                    url_parameters += "&file_removed=" + requests.utils.quote(payload["name"])

            # Not "ELif" as we also want to check for "Startup" once again
            if event_name in ["plugin_pluginmanager_install_plugin",
                              "plugin_pluginmanager_uninstall_plugin",
                              "plugin_pluginmanager_enable_plugin",
                              "plugin_pluginmanager_disabled_plugin"] or event_name == "Startup":

                if event_name == "plugin_pluginmanager_uninstall_plugin":
                    if "id" in payload and payload["id"] == "SimplyPrint":
                        self.log("The SimplyPrint software was uninstalled (all of it)")
                        self._uninstall_sp()

                # Get plugins that have been installed through SP
                sp_plugins = []
                sp_plugins_path = "/home/pi/SimplyPrint/sp_installed_plugins.txt"
                if os.path.isfile(sp_plugins_path):
                    with open("/home/pi/SimplyPrint/sp_installed_plugins.txt") as f:
                        for line in f:
                            sp_plugins.append(line.replace("\n", ""))

                # Get all installed plugins
                installed_plugins = []
                plugins = self._plugin_manager.plugins
                for key, plugin in plugins.items():
                    if not plugin.bundled and not plugin.hidden and plugin.enabled:
                        installed_plugins.append({
                            "key": plugin.key,
                            "name": plugin.name,
                            "author": plugin.author,
                            "version": plugin.version,
                            "sp_installed": plugin.name in sp_plugins or plugin.key == "simplyprint",
                            "pip_name": self._plugin_manager.get_plugin_info(plugin.key).origin.package_name
                        })

                url_parameters += "&octoprint_plugins" + requests.utils.quote(json.dumps(installed_plugins))

            if url_parameters is not "":
                # Send web request to update server (if needed)
                printer_state = str(self._printer.get_current_data()["state"]["text"])

                base_url = str(self._settings.get(["request_url"]))
                if not self._settings.get(["is_set_up"]):
                    base_url += "&new=true"

                base_url += url_parameters + "&event&pstatus=" + printer_state
                url = base_url.replace(" ", "%20")
                self.log("Doing web request from event. Url is;\n" + str(url) + "\n")

                # "requests" solution
                r = None
                the_json = None

                try:
                    r = requests.get(url, allow_redirects=True, verify=False)
                    r.raise_for_status()

                    # Parse
                    try:
                        the_json = json.loads(r.content)
                    except:
                        self.log("Failed to format request response to JSON; " + str(r.content))
                        return False

                except requests.exceptions.HTTPError as errh:
                    self.log("Web request HTTP error; " + str(errh))
                except requests.exceptions.ConnectionError as errc:
                    self.log("Web request connection error; " + str(errc))
                except requests.exceptions.Timeout as errt:
                    self.log("Web request timeout; " + str(errt))
                except requests.exceptions.RequestException as err:
                    self.log("Web request FAILED; " + str(err))
                except Exception:
                    # In case it returns something else completely
                    import traceback
                    self.log("Web request FAILED; " + str(traceback.format_exc()))

                # Check URL status
                if r is not None and the_json is not None:
                    if the_json["status"]:
                        self.log("Web request success! Got message; " + str(the_json["message"]))
                    else:
                        self.log("Web request returned false; " + str(the_json["message"]))
            else:
                self.log("url parameters is empty - not requesting")

    '''def gcode_sent(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
        if gcode and gcode == "M106":
            self._logger.info("Just sent M106: {cmd}".format(**locals()))

    def gcode_received(self, line, *args, **kwargs):
        return line'''

    def get_update_information(self):
        return dict(
            SimplyPrint=dict(
                displayName="SimplyPrint",
                displayVersion=self._plugin_version,

                # version check: github repository
                type="github_release",
                user="SimplyPrint",
                repo="OctoPrint-SimplyPrint",
                current=self._plugin_version,

                # update method: pip
                pip="https://github.com/SimplyPrint/OctoPrint-SimplyPrint/archive/{target_version}.zip"
            )
        )


__plugin_name__ = "SimplyPrint Cloud"
__plugin_pythoncompat__ = ">=2.7,<4"


def __plugin_load__():
    global __plugin_implementation__, __plugin_hooks__
    __plugin_implementation__ = SimplyPrint()
    __plugin_hooks__ = {
        "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information,
        # "octoprint.comm.protocol.gcode.received": __plugin_implementation__.gcode_received,
        # "octoprint.comm.protocol.gcode.sent": __plugin_implementation__.gcode_sent,
    }
