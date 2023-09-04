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

SP_BACKEND_VERSION = "0.1"
WS_TEST_ENDPOINT = "wss://testws2.simplyprint.io/%s/p" % (SP_BACKEND_VERSION, )
WS_PROD_ENDPOINT = "wss://ws.simplyprint.io/%s/p" % (SP_BACKEND_VERSION, )

PLUGIN_INSTALL_URL = "https://github.com/SimplyPrint/OctoPrint-SimplyPrint/archive/master.zip"
TEST_PLUGIN_INSTALL_URL = "https://github.com/Arksine/OctoPrint-SimplyPrint/dev-sp-websocket-20220414/master.zip"

LOGS_TEST_UPLOAD_URL = "https://apirewrite.simplyprint.io/printers/ReceiveLogs?pid="
LOGS_PROD_UPLOAD_URL = "https://api.simplyprint.io/printers/ReceiveLogs?pid="
