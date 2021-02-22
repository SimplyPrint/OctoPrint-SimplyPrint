import logging
import socket
import sys  # Used to get python version, ignore linter warnings
import threading
import io
import os

from octoprint.util.commandline import CommandlineCaller, CommandlineError

from .util import url_quote

GET_IP_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)), "get_ip.sh")


class SimplyPrintStartup:
    def __init__(self, simply_print):
        self.simply_print = simply_print
        self._logger = logging.getLogger("octoprint.plugins.SimplyPrint.comm.startup")
        self.startup_thread = None
        self.command_line = CommandlineCaller()

    def run_startup(self):
        if self.startup_thread is not None and self.startup_thread.is_alive():
            # Startup is in progress, lets leave it this way
            return

        thread = threading.Thread(target=self.startup)
        thread.daemon = True
        thread.start()
        self.startup_thread = thread

    def startup(self):
        ip = self.get_ip()
        pi_model = self.get_pi_model()
        ssid = self.get_wifi()
        hostname = self.get_hostname()
        has_camera = "0"
        octoprint_version, octoprint_api_version = self.get_octoprint_version()
        python_version = self.get_python_version_str()

        url = "&startup=true" \
              "&device_ip={ip}" \
              "&pi_model={pi_model}" \
              "&wifi_ssid={ssid}" \
              "&hostname={hostname}" \
              "&has_camera={has_camera}" \
              "&octoprint_version={octoprint_version}" \
              "&octoprint_api_version={octoprint_api_version}" \
              "&python_version={python_version}".format(**locals())

        request = self.simply_print.ping(url)

    @staticmethod
    def get_hostname():
        return socket.gethostname()

    @staticmethod
    def get_python_version_str():
        version_info = sys.version_info
        return "{version_info[0]}.{version_info[1]}.{version_info[2]}".format(**locals())

    @staticmethod
    def get_pi_model():
        try:
            with io.open("/proc/device-tree/model", "rt", encoding="utf-8") as file:
                return file.readline().strip(" \t\r\n\0")
        except:
            return

    def get_wifi(self):
        def iwgetid():
            try:
                returncode, stdout, stderr = self.command_line.checked_call(["/usr/sbin/iwgetid", "-r"])
            except CommandlineError:
                raise

            return stdout[0].strip("\r\n")

        def iwlist():
            try:
                returncode, stdout, stderr = self.command_line.checked_call(["/usr/sbin/iwlist", "wlan0", "scan"])
            except CommandlineError:
                raise

            for line in stdout:
                line = line.lstrip()
                if line.startswith("ESSID"):
                    return line.split('"')[1]

        try:
            ssid = iwgetid()
        except CommandlineError:
            self._logger.warning("iwgetid failed")
            ssid = None

        if not ssid:
            try:
                ssid = iwlist()
            except CommandlineError:
                self._logger.warning("iwlist failed, can't get SSID")
                return None

        return ssid

    def get_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        except Exception:
            ip = ""

        if not ip or ip is None or ip == "127.0.1.1":
            try:
                returncode, stdout, stderr = self.command_line.checked_call(["bash", GET_IP_PATH])
            except CommandlineError:
                return None
            ip = stdout.strip("\r\n ").replace("\n", "")

        return ip

    @staticmethod
    def get_octoprint_version():
        """
        Get OctoPrint version and API version
        :return: (tuple) OctoPrint version, API version
        """
        from octoprint.server.api import VERSION
        from octoprint import __version__
        return __version__, VERSION


