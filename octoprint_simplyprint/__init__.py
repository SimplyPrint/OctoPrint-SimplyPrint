# coding=utf-8
from __future__ import absolute_import

import json
import requests
import threading

# noinspection PyPackageRequirements
import flask

from octoprint.events import Events
import octoprint.plugin
import octoprint.settings

from octoprint_simplyprint.comm import SimplyPrintComm
from octoprint_simplyprint.local import cron


SIMPLYPRINT_EVENTS = [
    Events.CONNECTING,
    Events.CONNECTED,
    Events.DISCONNECTING,
    Events.DISCONNECTED,

    Events.STARTUP,
    Events.SHUTDOWN,

    Events.ERROR,

    Events.FILE_SELECTED,

    Events.PRINT_STARTED,
    Events.PRINT_FAILED,
    Events.PRINT_DONE,
    Events.PRINT_CANCELLING,
    Events.PRINT_CANCELLED,
    Events.PRINT_PAUSED,
    Events.PRINT_RESUMED,

    "plugin_printer_safety_check_warning",
    Events.FIRMWARE_DATA,

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

    Events.METADATA_ANALYSIS_FINISHED,

    "plugin_firmware_check_warning",
    Events.FILE_REMOVED,
]


class SimplyPrint(
    octoprint.plugin.SettingsPlugin,
    octoprint.plugin.StartupPlugin,
    octoprint.plugin.TemplatePlugin,
    octoprint.plugin.SimpleApiPlugin,
    octoprint.plugin.AssetPlugin,
    octoprint.plugin.EventHandlerPlugin,
    octoprint.plugin.ShutdownPlugin,
):

    _files_analyzed = []

    simply_print = None

    host = "127.0.0.1"
    port = "5000"

    def initialize(self):
        # Called once the plugin has been loaded by OctoPrint, all injections complete
        self.simply_print = SimplyPrintComm(self)

    def on_startup(self, host, port):
        # Run startup thread and run the main loop in the background
        self.simply_print.start_startup()
        self.simply_print.start_main_loop()

        self.host = host
        # Remember that this port is internal to OctoPrint, a proxy may exist.
        self.port = port

        ip = host

        if port:
            ip += str(port)

        self._logger.info("Host is; " + str(host) + " and port is; " + str(port))
        self.send_port_ip(None, ip)

    # #~~ StartupPlugin mixin
    def on_after_startup(self):
        self._logger.info("SimplyPrint OctoPrint plugin started")
        # The "Startup" event is never picked up by the plugin, as the plugin is loaded AFTER startup
        self.on_event("Startup", {})

        # If cron jobs don't exist, create them
        if not cron.check_cron_jobs():
            cron.create_cron_jobs()

    def on_shutdown(self):
        if self.simply_print is not None:
            # SimplyPrintComm will stop on next loop
            self.simply_print.run_loop = False

    @staticmethod
    def get_settings_defaults():
        return {
            "request_url": "",
            "rpi_id": "",
            "is_set_up": False,
            "printer_name": "",
            "printer_id": "",
            "temp_short_setup_id": "",
            "from_image": False,
            "sp_installed_plugins": "",
            "display_enabled": True,
            "display_branding": True,
            "display_show_status": True,
            "display_while_printing_type": "0",
            "has_power_controller": False,
            "has_filament_sensor": False,
            "webcam": {
                "flipH": False,
                "flipV": False,
                "rotate90": False
            },
            "info": {
                "last_user_settings_sync": "0000-00-00 00:00:00",
                "gcode_scripts_backed_up": False,
            }
        }

    def get_template_vars(self):
        return {
            "version": self._plugin_version
        }

    @staticmethod
    def get_assets():
        return dict(
            js=["js/SimplyPrint.js"],
            css=["css/SimplyPrint.css"],
            font=["font/lcd.ttf"],
            logo=["img/sp_logo.png"],  # This is not how the webassets work :)
            logo_lg=["img/sp_logo_large.png"],
            logo_white_sm=["img/sp_white_sm.png"]
        )

    def get_api_commands(self):
        return {
            "setup": [],  # Sets up SimplyPrintRPiSoftware
            "uninstall": [],  # Uninstalls SimplyPrintRPiSoftware
        }

    @staticmethod
    def _uninstall_sp():
        # All we need to do here is remove cron jobs, nothing else
        cron.remove_cron_jobs()

    @staticmethod
    def _install_background():
        if not cron.check_cron_jobs():
            cron.create_cron_jobs()

    def on_api_command(self, command, data):
        if command == "setup":
            self._uninstall_sp()
        elif command == "uninstall":
            self._uninstall_sp()

    # Send public port to outside system
    def send_port_ip(self, port=None, ip=None):
        return
        # TODO Send Port to background - should be easyish but I'm not sure how, since it is IPC - might need a file :(
        self._logger.info("Sending port to SimplyPrintLocal")
        try:
            from simplyprint_raspberry import port_ip_comm

        except ImportError:
            self._logger.error("SimplyPrintRPiSoftware not installed - plugin must be reinstalled")
            self._plugin_manager.send_plugin_message("SimplyPrint",
                                                     {"success": False, "message": "sp-rpi_not_available"})
            return
        try:
            if port is not None:
                port_ip_comm.save_port(port)

            if ip is not None:
                port_ip_comm.save_port(ip)

        except Exception as e:
            self._logger.error(repr(e))
            self._logger.error("Failed to setup SimplyPrintRPiSoftware")
            # self._plugin_manager.send_plugin_message("SimplyPrint", {"success": False, "message": "spi-rpi_error"})
            return

    def on_api_get(self, request):
        import flask
        import subprocess
        # self.log(str(request))
        # self.log(str(request.args))

        if request.args is not None:
            if request.args.get("install", default=None, type=None) is not None:
                # Install
                pass
            if request.args.get("send_port", default=None, type=None) is not None:
                # Send port to local scripts
                port = str(request.args.get("send_port", default=None, type=None))
                self.send_port_ip(port)
            if request.args.get("rpi_id", default=None, type=None) is not None:
                # Get RPI id
                pass
            if request.args.get("do_gcode", default=None, type=None) is not None:
                # Execute GCODE
                gcode_todo = str(request.args.get("do_gcode", default=None, type=None))
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
                        self._logger.error("Failed to format request response to JSON; " + str(r.content))
                        return False
                except:
                    pass

    # EventHandler mixin
    def on_event(self, event, payload):
        if event in SIMPLYPRINT_EVENTS:
            self.simply_print.on_event(event, payload)

    # def gcode_sent(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
    #     if gcode and gcode == "M106":
    #         self._logger.info("Just sent M106: {cmd}".format(**locals()))

    # def gcode_received(self, line, *args, **kwargs):
    #     return line

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
__plugin_disabling_discouraged__ = """
Please uninstall SimplyPrint Cloud rather than just disable it, since it sets up some background scripts
that will continue to run if you disable it.
"""


def __plugin_load__():
    global __plugin_implementation__, __plugin_hooks__
    __plugin_implementation__ = SimplyPrint()
    __plugin_hooks__ = {
        "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information,
        # "octoprint.comm.protocol.gcode.received": __plugin_implementation__.gcode_received,
        # "octoprint.comm.protocol.gcode.sent": __plugin_implementation__.gcode_sent,
    }
