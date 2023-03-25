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

import socket

from octoprint.settings import settings
import octoprint.server
from urllib.parse import quote


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