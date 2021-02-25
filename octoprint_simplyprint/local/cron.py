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
import sys
import subprocess

from crontab import CronTab

background_comment = "[SimplyPrint] Background healthcheck (V1.1)"
background_command = sys.executable + " -m octoprint_simplyprint run_healthcheck"


class CronManager:
    def __init__(self):
        self.cron = CronTab(user=True)
        self._logger = logging.getLogger()

        for job in self.cron:
            comment = job.comment.lower()
            if (
                ("simplyprint" in comment and comment is not background_comment.lower())
                # Remove commands with the simplyrint in them, this was a typo in previous versions
                or ("simplyrint" in job.command and comment is not background_comment.lower())
            ):
                self._logger.info("Removing job, {}".format(comment))
                self.cron.remove(job)

    def add(self, user, command, comment, on_reboot=False, daily=False):
        exist_check = self.cron.find_comment(comment)
        try:
            if sys.version_info.major == 3:
                job = next(exist_check)
            else:
                job = exist_check.next()

            if len(job) > 0:
                self._logger.info("Cronjob for SimplyPrint check already exists - not creating")
                return True

        except StopIteration:
            pass

        self._logger.info("Creating cronjob...")

        cron_job = self.cron.new(command=command, comment=comment, user=user)

        if on_reboot:
            cron_job.every_reboot()
        else:
            if not daily:
                cron_job.minute.every(1)
            else:
                cron_job.hour.on(0)
                cron_job.minute.on(0)

        cron_job.enable()
        self.cron.write()
        return True

    def validate(self, comment):
        """
        Validates that an entry with the comment exists in the crontab
        """
        for job in self.cron:
            job_comment = job.comment.lower()
            if "simplyprint" in job_comment and job_comment is comment:
                return True
        return False

    def remove(self, comment):
        for job in self.cron:
            if comment.lower() == job.comment().lower():
                self.cron.remove(job)


def create_cron_jobs():
    cron = CronManager()
    cron.add(True, background_command, background_comment, True)

    # Start the background process *now*, but don't wait for it (it will run forever...)
    subprocess.Popen([sys.executable, "-m", "octoprint_simplyprint", "run_healthcheck"])


def check_cron_jobs():
    cron = CronManager()
    return cron.validate(background_comment)


def remove_cron_jobs():
    cron = CronManager()
    cron.remove(background_comment)
