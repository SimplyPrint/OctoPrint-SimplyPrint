import socket

from octoprint.settings import settings
import octoprint.server

try:
    # Python 3
    from urllib.parse import quote
except ImportError:
    # Python 2
    from urllib import quote


def url_quote(string):
    return quote(string)


def has_internet():
    try:
        socket.create_connection(("www.google.com", 80))
        return True
    except OSError:
        return False


def is_octoprint_setup():
    return not (
        settings().getBoolean(["server", "firstRun"]) and (
            octoprint.server.userManager is None
            or not octoprint.server.userManager.has_been_customized()
        )
    )


def any_demand(demand_list, demands):
    for demand in demands:
        if demand in demand_list:
            return True

    return False