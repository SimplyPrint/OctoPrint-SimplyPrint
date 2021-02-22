import logging
import sys

from crontab import CronTab


background_comment = "[SimplyPrint] Background healthcheck (V1)"
background_command = sys.executable + "-m octoprint_simplyrint run_healthcheck"


class CronManager:
    def __init__(self):
        self.cron = CronTab(user=True)
        self._logger = logging.getLogger()

        for job in self.cron:
            comment = job.comment.lower()
            if "simplyprint" in comment and comment is not background_comment.lower():
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

        cron_job = self.cron.new(command=command, user=user)

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
            job_comment = job.comment().lower()
            if "simplyprint" in job_comment and job_comment is comment:
                return True
        return False

    def remove(self, comment):
        for job in self.cron:
            if comment.lower() == job.comment().lower():
                self.cron.remove(job)


def create_cron_jobs():
    cron = CronManager()
    cron.add(True, background_command, background_comment)


def check_cron_jobs():
    cron = CronManager()
    return cron.validate(background_comment)


def remove_cron_jobs():
    cron = CronManager()
    cron.remove(background_comment)
