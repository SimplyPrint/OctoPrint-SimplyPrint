# coding=utf-8

########################################################################################################################

# The plugin's identifier, has to be unique
plugin_identifier = "SimplyPrint"

# The plugin's python package, should be "octoprint_<plugin identifier>", has to be unique
plugin_package = "octoprint_simplyprint"

# The plugin's human readable name. Can be overwritten within OctoPrint's internal data via __plugin_name__ in the
# plugin module
plugin_name = "SimplyPrint"

# The plugin's version. Can be overwritten within OctoPrint's internal data via __plugin_version__ in the plugin module
# Remember to bump the version in octoprint_simplyprint/__init__.py as well
plugin_version = "4.2.0"

# The plugin's description. Can be overwritten within OctoPrint's internal data via __plugin_description__ in the plugin
# module
plugin_description = "3D print online anywhere from (probably) the most user friendly all-in-one platform there is, from your computer or phone."

# The plugin's author. Can be overwritten within OctoPrint's internal data via __plugin_author__ in the plugin module
plugin_author = "SimplyPrint"

# The plugin's author's mail address.
plugin_author_email = "albert@simplyprint.io"

# The plugin's homepage URL. Can be overwritten within OctoPrint's internal data via __plugin_url__ in the plugin module
plugin_url = "https://simplyprint.io/"

# The plugin's license. Can be overwritten within OctoPrint's internal data via __plugin_license__ in the plugin module
plugin_license = "AGPLv3"

# Any additional requirements besides OctoPrint should be listed here
plugin_requires = ["requests", "python-crontab", "distro", "sentry-sdk"]

### --------------------------------------------------------------------------------------------------------------------
### More advanced options that you usually shouldn't have to touch follow after this point
### --------------------------------------------------------------------------------------------------------------------

# Additional package data to install for this plugin. The subfolders "templates", "static" and "translations" will
# already be installed automatically if they exist.
plugin_additional_data = []

# Any additional python packages you need to install with your plugin that are not contained in <plugin_package>.*
plugin_additional_packages = []

# Any python packages within <plugin_package>.* you do NOT want to install with your plugin
plugin_ignored_packages = []

# Additional parameters for the call to setuptools.setup. If your plugin wants to register additional entry points,
# define dependency links or other things like that, this is the place to go. Will be merged recursively with the
# default setup parameters as provided by octoprint_setuptools.create_plugin_setup_parameters using
# octoprint.util.dict_merge.
#
# Example:
#     plugin_requires = ["someDependency==dev"]
#     additional_setup_parameters = {"dependency_links": ["https://github.com/someUser/someRepo/archive/master.zip#egg=someDependency-dev"]}
additional_setup_parameters = {}

########################################################################################################################

from setuptools import setup

try:
    import octoprint_setuptools
except:
    print("Could not import OctoPrint's setuptools, are you sure you are running that under "
          "the same python installation that OctoPrint is installed under?")
    import sys

    sys.exit(-1)


###########################################
# SIMPLYPRINT OCTOPRINT VERSION CHECK     #
# SimplyPrint is only working with 1.3.12 #
# Older versions need to update           #
###########################################
try:
    from octoprint.util.version import is_octoprint_compatible
except ImportError:
    print("Could not find OctoPrint, are you sure you are installing under the same python installation"
          "that OctoPrint is installed under?")
    import sys
    sys.exit(-1)

if not is_octoprint_compatible(">=1.3.12"):
    print("\n\n----------------------------------------\n\n")
    print("!! Unsupported OctoPrint version !!\n")
    print("SimplyPrint requires at least OctoPrint 1.3.12 to work properly, please update your OctoPrint install\n")
    print("You can find out more about that here: http://simplyprint.io/redir?r=unsupported-octoprint")
    print("\n")
    import sys
    sys.exit(-1)


setup_parameters = octoprint_setuptools.create_plugin_setup_parameters(
    identifier=plugin_identifier,
    package=plugin_package,
    name=plugin_name,
    version=plugin_version,
    description=plugin_description,
    author=plugin_author,
    mail=plugin_author_email,
    url=plugin_url,
    license=plugin_license,
    requires=plugin_requires,
    additional_packages=plugin_additional_packages,
    ignored_packages=plugin_ignored_packages,
    additional_data=plugin_additional_data
)

if len(additional_setup_parameters):
    from octoprint.util import dict_merge

    setup_parameters = dict_merge(setup_parameters, additional_setup_parameters)

setup(**setup_parameters)
