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

import logging

import requests
try:
    # Python 3
    from urllib import parse as urlparse
except ImportError:
    import urlparse


class OctoPrintClient:
    """
    OctoPrint API Client wrapping the things SP uses
    Inspired by https://github.com/hroncok/octoclient
    """
    def __init__(self, url, api_key):
        self._logger = logging.getLogger(__name__)

        if not url:
            raise TypeError("Required argument 'url' not found or empty")
        if not api_key:
            raise TypeError("Required argument 'apikey' not found or empty")

        parsed = urlparse.urlparse(url)
        if parsed.scheme not in ["http", "https"]:
            raise TypeError("Provided URL is not http(s)")
        if not parsed.netloc:
            raise TypeError("Provided URL is empty")

        self.url = "{}://{}".format(parsed.scheme, parsed.netloc)
        self.session = requests.Session()
        self.session.headers.update({"X-Api-Key": api_key})

    def _check_response(self, response):
        """
        Make sure response is 20x
        :param response: requests.Response
        :return: response
        """

        if not (200 <= response.status_code < 210):
            error = response.text
            msg = "Response from {} was not OK: {} ({})".format(response.url, error, response.status_code)
            self._logger.error(msg)
            raise OctoPrintApiError(msg)

        return response

    def _get(self, path, params=None):
        """
        Http GET to OctoPrint at the path specified
        """
        url = urlparse.urljoin(self.url, path)
        response = self.session.get(url, params=params, timeout=5)
        self._check_response(response)
        return response.json()

    def _post(self, path, data=None, json=None, ret=True):
        url = urlparse.urljoin(self.url, path)
        response = self.session.post(url, data=data, json=json, timeout=5)
        self._check_response(response)

        if ret:
            return response.json()

    def version(self):
        return self._get("/api/version")

    def server(self):
        return self._get("/api/server")

    def settings(self, settings=None):
        if settings:
            return self._post("/api/settings", json=settings)
        else:
            return self._get("/api/settings")

    def plugin_plugin_manager(self):
        return self._get("/api/plugin/pluginmanager")

    def index(self):
        try:
            self._get("/")
        except ValueError:
            # Expected result, since it is not a JSON endpoint
            # Any other error implies that it is not available
            return True
        except Exception:
            return False


class OctoPrintApiError(Exception):
    """Basic error for a bad response from the API"""
    pass

