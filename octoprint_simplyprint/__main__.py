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

import argparse


def run_script(name):
    # Currently there's only one script, but it could change :)
    if name == "run_healthcheck":
        from octoprint_simplyprint.local.background import run_background_check
        run_background_check()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SimplyPrint Local Scripts")
    parser.add_argument("script",
                        default=None,
                        help="The script you want to call, currently only `run_healthcheck` is valid")
    args = parser.parse_args()
    run_script(args.script)
