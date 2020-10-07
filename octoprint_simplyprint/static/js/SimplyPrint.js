// source: https://simplyprint.dk/
/*
 * JavaScript file for SimplyPrint (c)
 *
 * Author: Albert MN. @ SimplyPrint
 */
$(function () {
    function SimplyprintViewModel(parameters) {
        var self = this,
            displaySafeModeWarning = false;

        $("body").on("click", "#navbar_systemmenu ul li:nth-child(4)", function () {
            console.log("Clicked the safe mode button!");

            if (displaySafeModeWarning) {
                setTimeout(function () {
                    $(".modal.in .modal-body").append("<hr><h4><strong>SimplyPrint will restart OctoPrint once it sees it's in safe mode, reverting to non-safe mode!</strong></h4><p>" +
                        "This is being done as OctoPrint sometimes start in safe mode for first instances due to setup complications.</p>" +
                        "<p>Once SimplyPrint takes OctoPrint out of safe mode, it won't do so again for the next 10 minutes, so doing it twice in a row will give you 10 minutes in safe mode.</p>" +
                        "<p>To avoid SimplyPrint taking OctoPrint out of setup mode, please set up SimplyPrint or remove the SimplyPrint software from your Raspberry Pi <i>(it's not enough to disable the SimplyPrint OctoPrint plugin)</i>.</p>");
                }, 500);
            }
        });

        self.settingsViewModel = parameters[0];
        self.pluginSettings = null;
        self.alreadyProcessedPlugins = [];

        self.onAfterBinding = function () {
            self.RequestSettings();
        }

        self.OctoSetupChanges = function () {
            $("body").prepend(`<div id="simplyprint_dialog" class="modal hide fade" data-keyboard="true" aria-hidden="true">
                <div class="modal-header">
                    <h3 class="text-center">SimplyPrint ready</h3>
                </div>
                <div class="modal-body text-center">
                    <p>OctoPrint has been fully set up, and you can now return to the SimplyPrint setup!</p>
                    
                    <hr>
                    <h4>FAQ</h4>
                    
                    <p>
                        <b>Why does SimplyPrint need OctoPrint?</b>
                        <br>
                        SimplyPrint uses OctoPrint to communicate with your printer, where SimplyPrint acts as a middle-man so that you can print anywhere in the world
                    </p>
                
                    <p style="margin-top: 20px;">
                        <b>What is the difference between the two?</b>
                        <br>
                        The goal of the SimplyPrint system is to make your printer more accessible - you can print from <i>anywhere</i> in the world with no need to be on the same network as your printer.<br><br>
                            SimplyPrint also brings a much more user friendly and good-looking panel, unique features such as our Filament Management system, Bed Leveling and Filament Change helpers.
                    </p>
                
                    
                    <p style="margin-top: 20px;">
                        <b>Can I use both?</b>
                        <br>
                        One does not eliminate the other! Both OctoPrint and SimplyPrint work best when used together. When you're at home / on the same network as your printer, you can always go to OctoPrint and install plugins and so on - there's even a "Go to OctoPrint" button in the SimplyPrint panel! 
                    </p>
                </div>
                <div class="modal-footer">
                    <div class="pull-left">
                        <button class="btn" data-dismiss="modal" type="button">Close</button>
                    </div>        
                </div>
            </div>`);

            $("#wizard_firstrun_start p:first").html("Let's set up OctoPrint, and get back to the SimplyPrint setup!");
            $("#wizard_plugin_corewizard_printerprofile, #wizard_plugin_corewizard_printerprofile_link").remove();

            let end = $("#wizard_firstrun_end");

            end.find("p:first").html("OctoPrint is not fully set up, <b>you can now get back to the SimplyPrint setup</b>");
            $("#wizard_dialog .button-finish[name='finish']").on("click", function () {
                $("#simplyprint_dialog").modal("show");
            });
        }

        self.pluginsLoadedCheck = function () {
            if ($("#settings_plugin_pluginmanager_pluginlist tbody").html().length) {
                //Plugins have been loaded (being called from the API async, not loaded with the page)
                if (typeof self.pluginSettings.sp_installed_plugins === "undefined" || !Array.isArray(self.pluginSettings.sp_installed_plugins) || !self.pluginSettings.sp_installed_plugins.length) {
                    self.pluginSettings.sp_installed_plugins = ["SimplyPrint"];
                }

                if (typeof self.pluginSettings.sp_installed_plugins["SimplyPrint"] === "undefined") {
                    self.pluginSettings.sp_installed_plugins.push("SimplyPrint");
                }

                self.pluginSettings.sp_installed_plugins.forEach(function (plugin) {
                    if (self.alreadyProcessedPlugins.includes(plugin)) {
                        return;
                    }

                    self.alreadyProcessedPlugins.push(plugin);

                    let thePluginParent = $("#settings_plugin_pluginmanager_pluginlist tr span[data-bind='text: name']:contains('" + plugin + "')").parent().parent().parent();

                    if (thePluginParent.find(".fa.fa-lock").length) {
                        //Not managable by user
                        $(`<img alt="SimplyPrint logo (all rights reserved)" src="plugin/SimplyPrint/static/img/sp_logo.png" title="Plugin installed through, and can only be uninstalled through, SimplyPrint" style="margin-left: 10px;width: 19px;">`).insertAfter(thePluginParent.find(".fa.fa-lock"));
                        thePluginParent.find(".fa.fa-trash-o").addClass("disabled").attr("title", "Can be uninstalled through the SimplyPrint panel");
                    }
                });
            } else {
                setTimeout(self.pluginsLoadedCheck, 500);
            }
        }

        /*self.InstallSimplyPrintRequest = function () {
            $.getJSON(API_BASEURL + "plugin/SimplyPrint", data = {password: $('#SimplyPrintPassword').val()}, function (data) {
                //console.log("Got response");
                //console.log(data);
            });
        }*/

        self.RequestSettings = function () {
            $.getJSON("api/settings", function (data) {
                let simplyprint_version = $("#simplyprint_version"),
                    simplyprint_version_wrapper = $("#simplyprint_version_wrapper"),
                    simplyprint_is_set_up = $("#simplyprint_is_set_up"),
                    simplyprint_printer_name = $("#simplyprint_printer_name"),
                    simplyprint_not_set_up = $("#simplyprint_not_set_up"),
                    simplyprint_short_setup_code = $("#simplyprint_short_setup_code");

                self.pluginSettings = data.plugins.SimplyPrint;

                self.pluginsLoadedCheck();

                $("#simplyprint_loading_info").stop().fadeOut("fast", function () {
                    simplyprint_version.html(self.pluginSettings.simplyprint_version).stop().fadeIn();

                    if (self.pluginSettings.sp_local_installed) {
                        //SimplyPrint is installed!
                        simplyprint_version_wrapper.show();
                        $("#simplyprint_not_installed").hide();

                        if (self.pluginSettings.is_set_up) {
                            //Is set up!
                            displaySafeModeWarning = false;
                            if (self.pluginSettings.printer_name === "unset" || !self.pluginSettings.printer_name.length) {
                                simplyprint_printer_name.html("...");
                                setTimeout(self.RequestSettings, 2500);
                            } else {
                                simplyprint_printer_name.html(self.pluginSettings.printer_name);
                            }

                            simplyprint_not_set_up.stop().fadeOut("fast", function () {
                                simplyprint_is_set_up.stop().fadeIn();
                            });
                        } else {
                            //Not set up
                            displaySafeModeWarning = true;
                            if (simplyprint_short_setup_code.text() !== self.pluginSettings.temp_short_setup_id) {
                                //When trying to copy, it's quite annoying if the text is changed...
                                simplyprint_short_setup_code.html(self.pluginSettings.temp_short_setup_id);
                            }

                            simplyprint_is_set_up.stop().fadeOut("fast", function () {
                                simplyprint_not_set_up.stop().fadeIn();
                            });
                            setTimeout(self.RequestSettings, 3000);
                        }
                    } else {
                        //SimplyPrint is not installed (locally)
                        $("#simplyprint_not_installed").show();
                        simplyprint_version_wrapper.hide();
                        console.log("Local plugin not installed!");

                        setTimeout(self.RequestSettings, 3000);
                    }
                });
            });
        }

        self.ManagedBySimplyPrintAlert = function (extra = "", onlyPartly = false) {
            return `<div class="alert">
                <img alt="SimplyPrint logo (all rights reserved)" src="plugin/SimplyPrint/static/img/sp_logo.png" style="margin-left: 10px;width: 19px;">
                ${onlyPartly ? "Some features here are managed by SimplyPrint" : "This feature is managed by SimplyPrint"}${extra.length ? ". " + extra : ""}
            </div>`;
        }

        self.DisableOverwrittenUI = function () {
            //Printer profiles
            $("#settings_printerProfiles_profiles").css({
                "pointer-events": "none",
                "opacity": "0.4"
            }).parent().prepend(self.ManagedBySimplyPrintAlert("The printer profile is derived directly from your printer settings. If you modify your printer settings in SimplyPrint, they will be synced with OctoPrint"));
            $("#settings_printerProfiles .btn").prop("disabled", true);

            //GCODE scripts
            $("#settings_gcodeScripts .form-horizontal").prepend(self.ManagedBySimplyPrintAlert("The disabled fields can be changed through the SimplyPrint panel <a href='https://simplyprint.dk/panel/gcode_profiles' target='_blank'>from the \"GCODE profiles\" tab</a>. The original GCODE from fields we have replaced is backed up ", true));
            $("#settings_gcodeScripts [data-bind=\"value: scripts_gcode_afterPrintCancelled\"]").prop("disabled", true);
            $("#settings_gcodeScripts [data-bind=\"value: scripts_gcode_afterPrintPaused\"]").prop("disabled", true);
            $("#settings_gcodeScripts [data-bind=\"value: scripts_gcode_beforePrintResumed\"]").prop("disabled", true);
        }

        self.DisableOverwrittenUI();
        self.OctoSetupChanges();
    }

    OCTOPRINT_VIEWMODELS.push({
        construct: SimplyprintViewModel,
        dependencies: ["settingsViewModel"],
        elements: ["#settings_plugin_SimplyPrint", "#navbar_plugin_SimplyPrint"]
    });
});
