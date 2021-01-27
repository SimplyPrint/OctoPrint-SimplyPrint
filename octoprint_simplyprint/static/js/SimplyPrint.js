// source: https://simplyprint.dk/
/*
 * JavaScript file for SimplyPrint (c)
 *
 * Author: Albert MN. @ SimplyPrint
 */

$(function () {
    function CopyToClipboard(el) {
        let r = document.createRange();
        r.selectNode(el[0]);
        window.getSelection().removeAllRanges();
        window.getSelection().addRange(r);
        document.execCommand("copy");
        //window.getSelection().removeAllRanges();
    }

    let copiedTimeout = null;

    $("#spbiglcd button").on("click", function () {
        let el = $(this).prev();

        CopyToClipboard(el);
        el.tooltip({
            title: "Copied!",
            placement: "top",
            trigger: "manual",
        }).tooltip("show");

        copiedTimeout = setTimeout(function () {
            el.tooltip("destroy");
            window.getSelection().removeAllRanges();
        }, 800);
    });

    $("#simplyprint_short_setup_code").on("click", function () {
        let theThis = $(this);
        clearTimeout(copiedTimeout);

        CopyToClipboard(theThis);
        theThis.tooltip({
            title: "Copied!",
            placement: "bottom",
            trigger: "manual",
        }).tooltip("show");

        copiedTimeout = setTimeout(function () {
            theThis.tooltip("destroy");
            window.getSelection().removeAllRanges();
        }, 800);
    });

    function SimplyprintViewModel(parameters) {
        let self = this,
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
            self.checkSettings()
            if (!self.settingsViewModel.settings.plugins.SimplyPrint.is_set_up()) {
                $('#SimplyPrintWelcome').modal("show");
            }
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

            $("#wizard_firstrun_start").append(`<hr><p>
                Just interested in a quick setup? Use the SimplyPrint-recommended OctoPrint setup settings;
            </p>
            <button id="setupwizard_sprecommended" class="btn btn-primary" data-toggle="tooltip" title="This is what we do; enable Anonymous Usage Tracking, enable Online Connectivity Check and enable the plugin blacklist. All these settings help you, us and OctoPrint - win win!">
                <img alt="SimplyPrint logo" src="plugin/SimplyPrint/static/img/sp_white_sm.png" style="width:25px;"> Use recommended settings
            </button>`);

            $("#wizard_plugin_corewizard_acl p:first").remove();
            $(`<p style="margin-bottom:20px;">
                Here you must set up a local OctoPrint account. The login information is only stored on the Raspberry Pi,
                and used to log in to OctoPrint. This login is not to be confused with the SimplyPrint account you've made.
            </p>`)
                .insertAfter($("#wizard_plugin_corewizard_acl h3:first").html("Access Control <i>(OctoPrint login)</i>"));

            $("#setupwizard_sprecommended").on("click", function () {
                //On click of "Create account"
                $("#wizard_plugin_corewizard_acl .controls .btn.btn-primary[data-bind]").on("click", function () {
                    $(".button-next").prop("disabled", true);

                    function TrySetAuto() {
                        setTimeout(function () {
                            OctoPrint.postJson("api/settings", {}).fail(function () {
                                TrySetAuto();
                            }).done(function () {
                                //All good!
                                $("#wizard_plugin_tracking .btn.btn-primary[data-bind]").trigger("click");
                                $("#wizard_plugin_corewizard_onlinecheck .btn.btn-primary[data-bind]").trigger("click");
                                $("#wizard_plugin_corewizard_pluginblacklist .btn.btn-primary[data-bind]").trigger("click");
                                $("#wizard_plugin_tracking, #wizard_plugin_corewizard_onlinecheck, #wizard_plugin_corewizard_pluginblacklist").remove();
                                $(".button-next").prop("disabled", false);
                            });
                        }, 150);
                    }

                    TrySetAuto();
                });

                //Remove stuff we set manually
                $("#wizard_plugin_backup_link, #wizard_plugin_backup").remove();

                $("#wizard_plugin_tracking_link").remove();
                $("#wizard_plugin_corewizard_onlinecheck_link").remove();
                $("#wizard_plugin_corewizard_pluginblacklist_link").remove();
                $(".button-next").trigger("click");
            }).tooltip();

            console.log(typeof enableUsage);
            console.log(typeof setup);

            let end = $("#wizard_firstrun_end");

            end.find("p:first").html("OctoPrint is not fully set up, <b>you can now get back to the SimplyPrint setup</b>");
            $("#wizard_dialog .button-finish[name='finish']").on("click", function () {
                $("#simplyprint_dialog").modal("show");
            });
        }

        self.pluginsLoadedCheck = function () {
            let pluginSettings = self.settingsViewModel.settings.plugins.SimplyPrint
            if ($("#settings_plugin_pluginmanager_pluginlist tbody").html().length) {
                //Plugins have been loaded (being called from the API async, not loaded with the page)
                if (typeof pluginSettings.sp_installed_plugins === "undefined" || !Array.isArray(pluginSettings.sp_installed_plugins) || !pluginSettings.sp_installed_plugins.length) {
                    pluginSettings.sp_installed_plugins = ["SimplyPrint"];
                }

                if (typeof pluginSettings.sp_installed_plugins["SimplyPrint"] === "undefined") {
                    pluginSettings.sp_installed_plugins.push("SimplyPrint");
                }

                pluginSettings.sp_installed_plugins.forEach(function (plugin) {
                    if (self.alreadyProcessedPlugins.includes(plugin)) {
                        return;
                    }

                    self.alreadyProcessedPlugins.push(plugin);

                    let thePluginParent = $("#settings_plugin_pluginmanager_pluginlist tr span[data-bind='text: name']:contains('" + plugin + "')").parent().parent().parent();

                    if (thePluginParent.find(".fa.fa-lock").length) {
                        //Not managable by user
                        $(`<img alt="SimplyPrint logo (all rights reserved)" src="plugin/SimplyPrint/static/img/sp_logo.png" title="Plugin installed through SimplyPrint" style="margin-left: 10px;width: 19px;">`).insertAfter(thePluginParent.find(".fa.fa-lock"));
                    }
                });
            } else {
                setTimeout(self.pluginsLoadedCheck, 500);
            }
        }

        self.onEventSettingsUpdated = function () {
            self.checkSettings()
        }

        self.checkSettings = function () {
            let pluginSettings = self.settingsViewModel.settings.plugins.SimplyPrint
            let simplyprint_version = $("#simplyprint_version"),
                simplyprint_version_wrapper = $("#simplyprint_version_wrapper"),
                simplyprint_is_set_up = $("#simplyprint_is_set_up"),
                simplyprint_printer_name = $("#simplyprint_printer_name"),
                simplyprint_not_set_up = $("#simplyprint_not_set_up"),
                simplyprint_short_setup_code = $("#simplyprint_short_setup_code");

            self.pluginsLoadedCheck();

            $("#simplyprint_loading_info").stop().fadeOut("fast", function () {
                if (pluginSettings.sp_local_installed()) {
                    //SimplyPrint is installed!
                    simplyprint_version_wrapper.toggle(pluginSettings.simplyprint_version().length > 0);
                    simplyprint_version.html(pluginSettings.simplyprint_version()).stop().fadeIn();
                    $("#simplyprint_not_installed").hide();

                    if (pluginSettings.is_set_up()) {
                        //Is set up!
                        displaySafeModeWarning = false;
                        if (pluginSettings.printer_name() === "unset" || !pluginSettings.printer_name().length) {
                            simplyprint_printer_name.html("...");
                        } else {
                            simplyprint_printer_name.html(pluginSettings.printer_name());
                        }

                        simplyprint_not_set_up.stop().fadeOut("fast", function () {
                            simplyprint_is_set_up.stop().fadeIn();
                        });
                    } else {
                        //Not set up
                        displaySafeModeWarning = true;
                        if (simplyprint_short_setup_code.text() !== pluginSettings.temp_short_setup_id()) {
                            //When trying to copy, it's quite annoying if the text is changed...
                            simplyprint_short_setup_code.html(pluginSettings.temp_short_setup_id()).removeClass("dot-flashing");
                            $("#spbiglcd").removeClass("dot-flashing").find("span").html(pluginSettings.temp_short_setup_id());
                        }

                        simplyprint_is_set_up.stop().fadeOut("fast", function () {
                            simplyprint_not_set_up.stop().fadeIn();
                        });
                    }
                } else {
                    //SimplyPrint is not installed (locally)
                    $("#simplyprint_not_installed").show();
                    simplyprint_version_wrapper.hide();
                    console.log("Local plugin not installed!");
                }
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

        // Support for installing and uninstalling the SimplyPrintRpiSoftware (CP)
        self.requestInProgress = ko.observable()
        self.doSetup = function () {
            console.log("installing")
            self.requestInProgress(true);
            OctoPrint.simpleApiCommand("SimplyPrint", "setup")
        }

        self.onDataUpdaterPluginMessage = function (plugin, data) {
            if (plugin !== "SimplyPrint") {
                return;
            }
            if (data.success) {
                self.requestInProgress(false);
                new PNotify({
                    "title": "Successfully installed SimplyPrintRPiSoftware",
                    "type": "success",
                    "hide": true,
                })
            } else {
                self.requestInProgress(false);
                if (data.message === "sp-rpi_not_available") {
                    new PNotify({
                        "title": "Error installing SimplyPrintRPiSoftware",
                        "text": "It looks like the dependency has been uninstalled. Please reinstall the plugin",
                        "type": "error",
                        "hide": false,
                    })
                } else if (data.message === "sp-rpi_error") {
                    new PNotify({
                        "title": "Unknown error enabling SimplyPrintRPiSoftware",
                        "text": "Please get in contact so we can resolve this!",
                        "type": "error",
                        "hide": false,
                    })
                }
            }
        }

        self.doUninstall = function () {
            self.requestInProgress(true)
            OctoPrint.simpleApiCommand("SimplyPrint", "uninstall")
                .done(function (response) {
                    self.requestInProgress(false);
                    if (response.success) {
                        new PNotify({
                            "title": "Successfully uninstalled SimplyPrintRPiSoftware",
                            "type": "success",
                            "hide": true,
                        })
                    } else {
                        if (response.message === "sp-rpi_not_available") {
                            new PNotify({
                                "title": "Error uninstalling SimplyPrintRPiSoftware",
                                "text": "It looks like the dependency has already been uninstalled. Failed to uninstall it again",
                                "type": "error",
                                "hide": false,
                            })
                        }
                    }
                })
        }
    }

    OCTOPRINT_VIEWMODELS.push({
        construct: SimplyprintViewModel,
        dependencies: ["settingsViewModel"],
        elements: ["#settings_plugin_SimplyPrint", "#navbar_plugin_SimplyPrint", "#SimplyPrintWelcome"]
    });
});
