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
import threading
import time
import uuid

import io
import os
import tempfile
try:
    # Python 3
    from queue import Queue
except ImportError:
    # Python 2
    from Queue import Queue


import requests

# noinspection PyPackageRequirements
from octoprint.settings import settings

from .constants import WEBCAM_SNAPSHOT_URL

log = logging.getLogger("octoprint.plugins.SimplyPrint.comm.webcam")


def post_image(picture_id=None):
    log.debug("Taking picture")

    if picture_id is None:
        picture_id = str(uuid.uuid1())
        upl_url = WEBCAM_SNAPSHOT_URL + "?livestream=" + settings().get(["plugins", "SimplyPrint", "rpi_id"])
    else:
        upl_url = WEBCAM_SNAPSHOT_URL + "?request_id=" + picture_id

    if settings().get(["webcam", "webcamEnabled"]):
        url = settings().get(["webcam", "snapshot"])

        if url is None:
            log.warning("Webcam URL is None")
            return

        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, "simplyprint-webcam-{}.png".format(uuid.uuid1()))

        with io.open(temp_path, "wb") as file:
            log.debug("Downloading image from {}".format(url))
            try:
                download_image(file, url)
            except WebcamError:
                requests.get(upl_url + "&err_msg=Download%20of%20screenshot%20not%20successful", timeout=5)
                return

        with io.open(temp_path, "rb") as file:
            try:
                response = requests.post(upl_url, files={"the_file": file}, timeout=10)
            except requests.exceptions.RequestException as e:
                log.error("Failed to post image to SP")
                log.error(repr(e))
                return

            if int(round(response.status_code / 100)) != 2:
                log.error("Received non-2xx status code from SP for url {}, content: {}".format(upl_url,
                                                                                                response.content))

        try:
            os.remove(temp_path)
        # except FileNotFoundError:
        #    pass
        except Exception:
            log.warning("Could not remove webcam snapshot tempfile")

        return response


def download_image(file_object, url, timeout=3):
    if settings().get(["plugins", "SimplyPrint", "debug_logging"]):
        log.setLevel(logging.DEBUG)
    """
    Download an image from the webcam and write it to the file object
    :param file_object: File-like object, opened in 'wb' mode
    :param url: Fully qualified URL to get the image from
    :param timeout: The timeout for the request
    :return:
    :raises WebcamError:
    """
    try:
        response = requests.get(url, allow_redirects=True, verify=False, timeout=timeout)
    except Exception as e:
        log.error(repr(e))
        raise WebcamError("Failed to request webcam URL {}".format(url))

    # The free space check before dumping to disk was previously commented in SP-RPI-SW, it is not implemented

    if response.status_code == 200:
        try:
            file_object.write(response.content)
        except Exception as e:
            log.error("Failed to save webcam image")
            log.exception(e)
            raise WebcamError("Failed to create file locally")
    else:
        raise WebcamError("Status code from download URL is not 200, was {} instead".format(response.status_code))


def start_livestream(queue, old_thread=None):
    if settings().get(["plugins", "SimplyPrint", "debug_logging"]):
        log.setLevel(logging.DEBUG)

    if old_thread is not None and old_thread.is_alive():
        queue.put("KILL")
        old_thread.join()

    new_queue = Queue()
    t = threading.Thread(target=livestream_loop, name="SimplyPrint webcam stream loop", args=(new_queue,))
    t.daemon = True
    t.start()
    return t


def livestream_loop(queue):
    fails = 0
    every = 1

    log.info("Starting streaming to server")
    log.info("Streaming to; " + WEBCAM_SNAPSHOT_URL)

    while True:
        if fails >= 10 or not queue.empty():
            # Failed to start, or queue message, stop and give up
            break

        start = time.time()
        try:
            request = post_image()
        except WebcamError:
            log.error("Error sending image for livestream")
            fails += 1
            # Sleep to prevent spam/overload
            time.sleep(0.8)
            continue

        if request is None:
            # Error, no response was returned, try again next time
            fails += 1
            time.sleep(0.8)
            continue

        try:
            response = request.json()
        except ValueError:
            log.error("Failed to parse response")
            fails += 1
            # Sleep to prevent spam/overload
            time.sleep(0.8)
            continue

        if "livestream" in response and response["livestream"] is not None:
            if not response["livestream"]["active"]:
                log.debug("Server stopped requesting for livestream")
                break

            every = response["livestream"]["every"]

        request_time = time.time() - start

        log.debug("webcam image took {}".format(request_time))

        if not queue.empty():
            # Message in the queue, need to stop
            break

        # Sleep until it is time to go again
        time.sleep(max((1 / every) - request_time, 0))


class WebcamError(Exception):
    """Standard webcam-related exception"""
    pass
