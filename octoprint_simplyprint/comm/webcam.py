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
log.setLevel(logging.DEBUG)


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
                requests.get(upl_url + "&err_msg=Download%20of%20screenshot%20not%20successful")
                return

        with io.open(temp_path, "rb") as file:
            try:
                response = requests.post(upl_url, files={"the_file": file})
            except requests.exceptions.RequestException as e:
                log.error("Failed to post image to SP")
                log.exception(e)
                return

            if int(round(response.status_code / 100)) != 2:
                log.error("Received non-2xx status code from SP, content: {}".format(response.content))

        return response


def download_image(file_object, url, timeout=3):
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
        log.exception(e)
        raise WebcamError("Failed to request webcam URL {}".format(url))

    # TODO the free space check before dumping to disk? Was previously commented in SP-RPI-SW

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
