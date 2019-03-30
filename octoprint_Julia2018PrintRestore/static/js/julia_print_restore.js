$(function() {
    function JuliaPrintRestoreViewModel(parameters) {
        var self = this;

        self.VM_settings = parameters[0];

        self.flashPort = ko.observable(undefined);
        self.hardwareNotReady = ko.observable("");
        self.uploadFilename = ko.observable("");
        self.uploadProgress = ko.observable("");

        self.saveConfig = function() {
            var data = {
                plugins: {
                    Julia2018PrintRestore: {

                    }
                }
            };
            self.VM_settings.saveData(data);
        };

        self.onDataUpdaterPluginMessage = function(plugin, data) {
            if (plugin !== "Julia2018PrintRestore") {
                return;
            }
            console.log(data);
        };

        self.onStartup = function() {

        };

        self.onBeforeBinding = function() {
            console.log('Binding JuliaPrintRestoreViewModel')

            self.Config = self.VM_settings.settings.plugins.Julia2018PrintRestore;

            console.log(self.Config);
        };
    }

    OCTOPRINT_VIEWMODELS.push([
        JuliaPrintRestoreViewModel,
        ["settingsViewModel"],
        ["#settings_julia_print_restore"]
    ]);
});
