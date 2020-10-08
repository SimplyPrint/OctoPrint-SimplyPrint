# coding=utf-8
from __future__ import absolute_import

import os
import sys

import octoprint
import octoprint.settings
import json
import requests


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

            "plugin_simplypowercontroller_power_on",
            "plugin_simplypowercontroller_power_off",

            "MetadataAnalysisFinished",
        ]

    # #~~ StartupPlugin mixin
    def on_after_startup(self):
        self.log("OctoPrint plugin started")
        # The "Startup" event is never picked up by the plugin, as the plugin is loaded AFTER startup
        self.on_event("Startup", "")

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
            sp_installed_plugins=""
        )

    @staticmethod
    def get_assets():
        return dict(
            js=["js/SimplyPrint.js"],
            logo=["img/sp_logo.png"],
            logo_lg=["img/sp_logo_large.png"]
        )

    def get_api_commands(self):
        return dict(
            command1=[],
            command2=["some_parameter"]
        )

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
            '''
            if request.args.get("install_system", default=None, type=None) is not None:
                # Install SimplyPrint system
                url = "https://simplyprint.dk/software/install_system.sh"
                filename = self.get_plugin_data_folder() + "/sp_install_system.sh"
                r = requests.get(url, allow_redirects=True, verify=False)
                open(filename, 'wb').write(r.content)
                try:
                    st = os.stat(filename)
                    os.chmod(filename, st.st_mode | stat.S_IEXEC)
                    pwd = request.args.get("password", default=None, type=None) + "\n"
                    self.log(pwd)
                    if pwd is not None:
                        cmd_run_shell = "sudo -S {} &".format(filename)
                        self.log(cmd_run_shell)
                        # os.popen("sudo -S %s"%(command), 'w').write(pwd)
                        cmd_run_shell_results = subprocess.Popen(cmd_run_shell.split(), stdin=subprocess.PIPE,
                                                                 universal_newlines=True)
                        sudo_prompt = cmd_run_shell_results.communicate(pwd)[1]
                        self.log(sudo_prompt)
                        return flask.jsonify(success="everything worked")
                    else:
                        return flask.jsonify(error="no password given")
                except subprocess.CalledProcessError as e:
                    self.log(e)
                    return flask.jsonify(error="something went wrong".format(e))
                return flask.jsonify(error="something went wrong")
            '''

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

            # Not "ELif" as we also want to check for "Startup" once again
            if event_name in ["plugin_pluginmanager_install_plugin",
                              "plugin_pluginmanager_uninstall_plugin",
                              "plugin_pluginmanager_enable_plugin",
                              "plugin_pluginmanager_disabled_plugin"] or event_name == "Startup":
                # A plugin has been installed/removed/enabled/disabled - update SP
                default_plugins = [
                    "SimplyPrint",
                ]

                # Get plugins that have been installed through SP
                sp_plugins = []
                if os.path.isfile("/home/pi/SimplyPrint/sp_installed_plugins.txt"):
                    with open("/home/pi/SimplyPrint/sp_installed_plugins.txt") as f:
                        for line in f:
                            sp_plugins.append(line.replace("\n", ""))

                # Get all installed plugins
                installed_plugins = []
                plugins = self._plugin_manager.plugins
                for key, plugin in plugins.items():
                    if not plugin.bundled and not plugin.hidden and plugin.key not in default_plugins and plugin.enabled:
                        installed_plugins.append({
                            "key": plugin.key,
                            "name": plugin.name,
                            "author": plugin.author,
                            "version": plugin.version,
                            "sp_installed": plugin.name in sp_plugins,
                            "pip_name": self._plugin_manager.get_plugin_info(plugin.key).origin.package_name
                        })

                url_parameters += "&octoprint_plugins" + requests.utils.quote(json.dumps(installed_plugins))

            if url_parameters is not "":
                # Send web request to update server (if needed)
                printer_state = str(self._printer.get_current_data()["state"]["text"])

                base_url = str(self._settings.get(["request_url"]))
                if not self._settings.get(["is_set_up"]):
                    base_url += "&new=true"

                base_url += url_parameters + "&pstatus=" + printer_state
                url = base_url.replace(" ", "%20")
                self.log("Doing web request from event. Url is;\n" + str(url) + "\n")

                # "requests" solution
                r = None
                the_json = None

                try:
                    r = requests.get(url, allow_redirects=True)
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

    def get_update_information(self):
        return dict(
            simplyprint=dict(
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


__plugin_pythoncompat__ = ">=2.7,<4"


def __plugin_load__():
    global __plugin_implementation__, __plugin_hooks__
    __plugin_implementation__ = SimplyPrint()
    __plugin_hooks__ = {
        "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information
    }
