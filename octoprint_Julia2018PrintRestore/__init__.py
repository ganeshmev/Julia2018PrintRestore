# coding=utf-8
from __future__ import absolute_import

import octoprint.plugin
from octoprint.events import Events
from flask import jsonify, make_response, request
# from octoprint.settings import settings
# import time
from threading import Timer
import json
import os
import re
import logging
# TODO:
'''
Auto Resurrect
Ask about ressurection when booting on OCtoprnt screen
Autobooting shouldnt clash with touchscreen operation
change code depending on number of toolheads
'''


def isFloat(text):
    try:
        float(text)
        # check for nan/infinity etc.
        if text.isalpha():
            return False
        return True
    except ValueError:
        return False


class RepeatedTimer(object):
    def __init__(self, interval, function, *args, **kwargs):
        self._timer = None
        self.interval = interval
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.is_running = False

    def _run(self):
        self.is_running = False
        self.start()
        self.function(*self.args, **self.kwargs)

    def start(self):
        if not self.is_running:
            self._timer = Timer(self.interval, self._run)
            self._timer.start()
            self.is_running = True

    def stop(self):
        if self.is_running:
            self._timer.cancel()
            self.is_running = False


# def boolConv(input):
#     if input == "true" or input == "True" or input == 1 or input == "1":
#         return True
#     elif input == "false" or input == "False" or input == 0 or input == "0":
#         return False
#     else:
#         return False


class Julia2018PrintRestore(octoprint.plugin.StartupPlugin,
                            octoprint.plugin.EventHandlerPlugin,
                            octoprint.plugin.SettingsPlugin,
                            octoprint.plugin.TemplatePlugin,
                            octoprint.plugin.BlueprintPlugin):

    # __RESTORE_FILE = "/home/pi/print_restore.json"
    # __TEMP_RESTORE_FILE = __RESTORE_FILE + ".tmp"
    # __LOG_FILE = "/home/pi/.octoprint/logs/print_restore.log"

    @property
    def enabled(self):
        return self._settings.get_boolean(["enabled"])

    @property
    def autoRestore(self):
        return self._settings.get_boolean(["autoRestore"])

    @property
    def interval(self):
        return self._settings.get_int(["interval"])

    @property
    def enableBabystep(self):
        return self._settings.get_boolean(["enableBabystep"])

    '''+++++++++++++++ Octoprint Startup Functions ++++++++++++++++++++'''
    def initialize(self):
        '''
        Initialises board
        :return: None
        '''
        self._logger.info("Print Restore plugin initialised")

        fh = logging.handlers.RotatingFileHandler(self._settings.get_plugin_logfile_path(postfix="debug"), maxBytes=(2 * 1024 * 1024))
        fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        # fh.setLevel(logging.DEBUG)
        self._logger.addHandler(fh)

        basedir = self._settings.getBaseFolder("base")
        if basedir is not None and os.path.exists(basedir):
            self.__RESTORE_FILE = os.path.join(basedir, "print_restore.json")
        else:
            self.__RESTORE_FILE = "/home/pi/print_restore.json"
        self.__TEMP_RESTORE_FILE = self.__RESTORE_FILE + ".tmp"

        # self.enabled = bool(boolConv(self._settings.get(["enabled"])))
        # self.autoRestore = bool(boolConv(self._settings.get(["autoRestore"])))
        # self.interval = float(self._settings.get(["interval"]))
        self.state_position = {}
        self.state_babystep = 0
        self.flag_is_saving_state = False
        self.flag_restore_in_progress = False

    def on_after_startup(self):
        '''
        Method to check if resurection file is avialble during server startup
        Also stores other basic settings
        :return: None
        '''
        # Initialise Repeated Timer Object
        self.init_print_state_monitor()
        # get printer settings

    def get_settings_defaults(self):
        '''
        initialises default parameters
        :return:
        '''
        return dict(
            enabled=True,
            autoRestore=False,
            interval=1,
            enableBabystep=None
        )

    '''+++++++++++++++ Octoprint Event Callback ++++++++++++++++++++'''

    def on_event(self, event, payload):
        '''
        Callback when an event is detected. depending on the event, different things are done.
        :param event: event to respond to
        :param payload:
        :return:
        '''
        if self.enabled:
            if event in (Events.CONNECTED):
                if self.check_restore_file_exists():
                    if self.autoRestore:
                        self.start_restore()
                # else:
                #     self.parse_restore_file()

            elif event in (Events.PRINT_STARTED, Events.PRINT_RESUMED):
                self.delete_restore_file()
                self.start_printer_state_monitor()

            elif event in Events.PRINT_PAUSED:
                self.stop_printer_state_monitor()

            elif event in Events.PRINT_DONE:
                self.stop_printer_state_monitor()
                self.delete_restore_file()

            elif event in (Events.PRINT_FAILED, Events.PRINT_CANCELLED, Events.DISCONNECTED):
                self.stop_printer_state_monitor()

            elif event is Events.TOOL_CHANGE:
                if self.flag_is_saving_state:
                    self.state_position["T"] = payload["new"]

    '''+++++++++++++++ Worker Functions ++++++++++++++++++++'''
    def init_print_state_monitor(self):
        if self._timer_printer_state_monitor is None:
            self._timer_printer_state_monitor = RepeatedTimer(self.interval, self.write_restore_file)

    def start_printer_state_monitor(self):
        """Start monitoring and saving printer state
        """
        self._logger.info("Printer state monitor started")
        self.flag_is_saving_state = True
        self.flag_restore_file_write_in_progress = False
        self.state_position = {}
        self._timer_printer_state_monitor.start()

    def stop_printer_state_monitor(self):
        '''
        Stops the repeated timer that saves progress
        :return:
        '''
        self.flag_is_saving_state = False
        self._logger.info("Printer state monitor stopped")
        self._timer_printer_state_monitor.stop()
        self._timer_printer_state_monitor = None

    def check_restore_file_exists(self):
        '''
        The restore file is present on the USB device and contains sane data
        :return:
        '''
        if os.path.isfile(self.__RESTORE_FILE):
            return True
        else:
            return False

    def write_restore_file(self):
        '''
        worker function that does the actual saving to file
        :return:
        '''
        if self.flag_restore_in_progress or self.flag_restore_file_write_in_progress:
            return

        temps = self._printer.get_current_temperatures()
        file = self._printer.get_current_data()
        data = {"fileName": file["job"]["file"]["name"], "filePos": file["progress"]["filepos"],
                "path": file["job"]["file"]["path"],
                "tool0Target": temps["tool0"]["target"],
                "bedTarget": temps["bed"]["target"],
                "position": self.state_position,
                "babystep": self.state_babystep if self.enableBabystep else 0
                }
        if "tool1" in temps.keys():
            if temps["tool1"]["target"] is not None:
                data["tool1Target"] = temps["tool1"]["target"]
        self.flag_restore_file_write_in_progress = True
        with open(self.__TEMP_RESTORE_FILE, 'w') as restoreFile:
            json.dump(data, restoreFile)
            os.fsync(restoreFile)
        os.rename(self.__TEMP_RESTORE_FILE, self.__RESTORE_FILE)
        self.flag_restore_file_write_in_progress = False

    def parse_restore_file(self, log=False):
        if self.check_restore_file_exists():
            try:
                with open(self.__RESTORE_FILE) as f:
                    txt = f.read()
                    txt = txt.encode('ascii', 'ignore')
                    txt = "".join(c for c in txt if 31 < ord(c) < 127)
                    try:
                        data = json.loads(txt)
                        if log:
                            self._logger.info("Print restore data:\n" + json.dumps(data))
                        return (True, data)
                    except Exception as e:
                        self._logger.error("Invalid JSON data in restore file: {}\n{}".format(txt, e.message))
            except Exception as e:
                self._logger.error("Could not open restore file\n" + e.message)
        return (False, None)

    def delete_restore_file(self):
        '''
        delets the progress file
        :return:
        '''
        if self.check_restore_file_exists():
            try:
                os.remove(self.__RESTORE_FILE)
                self._logger.info("Restore progress file was deleted")
            except:
                self._logger.info("Error deleting restore file")

    def gcode_sent_hook(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
        '''
        notes the print information on the last sent command to the printer
        :return:
        self._currentTool
        '''
        if not gcode:
            return

        if self.flag_is_saving_state:
            try:
                if gcode == "G1" or gcode == "G0":
                    if "X" in cmd:
                        self.state_position["X"] = cmd[cmd.index('X') + 1:].split(' ', 1)[0]
                    if "Y" in cmd:
                        self.state_position["Y"] = cmd[cmd.index('Y') + 1:].split(' ', 1)[0]
                    if "Z" in cmd:
                        self.state_position["Z"] = cmd[cmd.index('Z') + 1:].split(' ', 1)[0]
                    if "E" in cmd:
                        self.state_position["E"] = cmd[cmd.index('E') + 1:].split(' ', 1)[0]
                    if "F" in cmd:
                        self.state_position["F"] = cmd[cmd.index('F') + 1:].split(' ', 1)[0]
                elif gcode == "M106":
                    if "S" in cmd:
                        self.state_position["FAN"] = cmd[cmd.index('S') + 1:].split(' ', 1)[0]
                elif gcode == "M107":
                    if "S" in cmd:
                        self.state_position["FAN"] = 0
                elif gcode == "M290":
                    if "Z" in cmd:
                        val = cmd[cmd.index('Z') + 1:].split(' ', 1)[0]
                        if isFloat(val):
                            self.state_babystep = self.state_babystep + float(val)
            except:
                self._logger.info("Error getting latest command sent to printer")

    def start_restore(self):
        '''
        restores the print progress from saved file
        :return:
        '''
        try:
            restore_state = self.parse_restore_file()
            if not restore_state[0]:
                raise Exception("Did not load data")

            data = restore_state[1]

            if data["fileName"] != "None":   # file name is not none
                self._printer.commands("M117 RESTORE_STARTED")

                if data["bedTarget"] > 0:
                    self._printer.commands("M190 S{}".format(data["bedTarget"]))
                if "tool0Target" in data.keys():
                    if data["tool0Target"] > 0:
                        self._printer.commands("M104 T0 S{}".format(data["tool0Target"]))
                if "tool1Target" in data.keys():
                    if data["tool1Target"] > 0:
                        self._printer.commands("M104 T1 S{}".format(data["tool1Target"]))
                if "tool0Target" in data.keys():
                    if data["tool0Target"] > 0:
                        self._printer.commands("M109 T0 S{}".format(data["tool0Target"]))
                if "tool1Target" in data.keys():
                    if data["tool1Target"] > 0:
                        self._printer.commands("M109 T1 S{}".format(data["tool1Target"]))
                self._printer.commands("T0")
                self._printer.home("z")
                self._printer.home(["x", "y"])
                # self._printer.commands("G1 X0 Y0 Z10 F9000")
                if "T" in data["position"].keys():
                    self._printer.commands("T{}".format(data["position"]["T"]))
                if "FAN" in data["position"].keys():
                    if data["position"]["FAN"] > 0:
                        self._printer.commands("M106 S{}".format(data["position"]["FAN"]))

                commands = ["M420 S1"
                            "G90",
                            "G92 E0",
                            "G1 F200 E5",
                            "G1 F{}".format(data["position"]["F"]),
                            "G92 E{}".format(data["position"]["E"]),
                            "G1 X{} Y{}".format(data["position"]["X"], data["position"]["Y"]),
                            "G1 Z{}".format(data["position"]["Z"]),
                            ]
                self._printer.commands(commands)

                if "babystep" in data.keys():
                    if data["babystep"] != 0:
                        self._printer.commands("M290 Z{}".format(data["babystep"]))

                self._printer.select_file(path=self._file_manager.path_on_disk("local", data["fileName"]),
                                          sd=False, printAfterSelect=True, pos=data["filePos"])

                self._printer.commands("M117 RESTORE_COMPLETE")

                self._send_status(status_type="PRINT_RESURRECTION_STARTED", status_value=data["fileName"],
                                  status_description="Print resurrection started")
                return (True, None)
            else:    # file name is None
                self._logger.error("Did not find print job filename in restore file\n" + json.dumps(data))
                return (False, "Gcode file name is none")
        except Exception as e:
            self._logger.error("Restore error\n" + e.message)
            return (False, e.message)

    '''+++++++++++++++ API Functions ++++++++++++++++++++'''

    @octoprint.plugin.BlueprintPlugin.route("/isFailureDetected", methods=["GET"])
    def route_check_restore_file(self):
        '''
        API to let client know that storage media has restoration file in it,
        and restore is possible
        '''
        if self._printer.is_printing() or self._printer.is_paused():
            return jsonify(status="Printer is already printing", canRestore=False)
        else:
            if self.check_restore_file_exists():
                restore_state = self.parse_restore_file()
                if restore_state[0] and "fileName" in restore_state[1].keys():
                    return jsonify(status="failureDetected", canRestore=True, file=restore_state[1]["fileName"])
                else:
                    return jsonify(status="failureDetected", canRestore=False)
            else:
                return jsonify(status="noFailureDetected", canRestore=False)

    @octoprint.plugin.BlueprintPlugin.route("/restore", methods=["POST"])
    def route_restore(self):
        """
        Function that restores the print
        """
        if "application/json" not in request.headers["Content-Type"]:
            return make_response("Expected content type JSON", 400)

        try:
            data = request.json
        except:
            return make_response("Malformed JSON body in request", 400)

        if self._printer.is_printing() or self._printer.is_paused():
            return jsonify(status="Printer is already printing", canRestore=False)
        else:
            if data["restore"] is True:
                if self.check_restore_file_exists():
                    result = self.start_restore()
                    if result[0] is True:
                        return jsonify(status="Successfully Restored")
                    else:
                        return jsonify(status="Error: Could not restore", error=result[1])
                else:
                    return jsonify(status="Error: Could not restore, no progress file exists")
            else:
                self.delete_restore_file()
                return jsonify(status="Progress file discarded")

    @octoprint.plugin.BlueprintPlugin.route("/getSettings", methods=["GET"])
    def route_get_settings(self):
        return jsonify(interval=self.interval, autoRestore=self.autoRestore, enabled=self.enabled)

    @octoprint.plugin.BlueprintPlugin.route("/saveSettings", methods=["POST"])
    def route_save_settings(self):
        if "application/json" not in request.headers["Content-Type"]:
            return make_response("Expected content type JSON", 400)

        try:
            data = request.json
        except:
            return make_response("Malformed JSON body in request", 400)
        if all(item in data.keys() for item in ("autoRestore", "enabled", "interval")):
            self.on_settings_save(data)
            return make_response("Settings Saved", 200)

    '''+++++++++++++++ Octoprint Helper Functions ++++++++++++++++++++'''

    def get_template_configs(self):
        '''
        Bindings for the jinja files
        :return:
        '''
        return [dict(type="settings", custom_bindings=False)]

    def _send_status(self, status_type, status_value, status_description=""):
        """
        sends a plugin message, from the SockJS server
        :param status_type:
        :param status_value:
        :param status_description:
        :return:
        """
        self._plugin_manager.send_plugin_message(self._identifier,
                                                 dict(type="status", status_type=status_type, status_value=status_value,
                                                      status_description=status_description))

    def on_settings_save(self, data):
        """
        Saves and updates the file settings of resurection
        :param data:
        :return:
        """
        octoprint.plugin.SettingsPlugin.on_settings_save(self, data)
        self._settings.save()
        # self.enabled = bool(boolConv(self._settings.get(["enabled"])))
        # self.autoRestore = bool(boolConv(self._settings.get(["autoRestore"])))
        # self.interval = float(self._settings.get(["interval"]))
        self._logger.info("Print Restore settings saved")
        if self._timer_printer_state_monitor.interval != self.interval:
            if self.flag_is_saving_state:
                self.stop_printer_state_monitor()
                self.init_print_state_monitor()
                self.start_printer_state_monitor()
            else:
                self.init_print_state_monitor()
        if not self.enabled:
            if self._printer.is_printing() or self._printer.is_paused():
                self.stop_printer_state_monitor()
                self.delete_restore_file()
        else:
            if self._printer.is_printing() or self._printer.is_paused():
                self.start_printer_state_monitor()

    def gcode_received_hook(self, comm, line, *args, **kwargs):
        if "FIRMWARE_NAME" in line:
            # self._logger.info("FIRMWARE_NAME line: {}".format(line))
            from octoprint.util.comm import parse_firmware_line
            # Create a dict with all the keys/values returned by the M115 request
            data = parse_firmware_line(line)

            regex = r"Marlin J18([A-Z]{2})_([0-9]{6}_[0-9]{4})_HA"
            matches = re.search(regex, data['FIRMWARE_NAME'])

            enable_babystep = matches and len(matches.groups()) == 2 and matches.group(1) in ["PT", "PE"]
            if self.enableBabystep != enable_babystep:
                self._settings.set_boolean(["enableBabystep"], enable_babystep)
                self._settings.save()

        return line

    def print_restore_progress_hook(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
        if gcode and gcode == "M117":
            if "RESTORE_STARTED" in cmd:
                self._logger.info("RESTORE_STARTED")
                self.flag_restore_in_progress = True
            elif "RESTORE_COMPLETE" in cmd:
                self._logger.info("RESTORE_COMPLETE")
                self.flag_restore_in_progress = False

    def get_update_information(self):
        """
        Function for OTA update thrpugh the software update plugin
        :return:
        """
        return dict(
            Julia2018PrintRestore=dict(
                displayName="Julia Print Restore",
                displayVersion=self._plugin_version,
                # version check: github repository
                type="github_release",
                user="FracktalWorks",
                repo="Julia2018PrintRestore",
                current=self._plugin_version,
                # update method: pip
                pip="https://github.com/FracktalWorks/Julia2018PrintRestore/archive/{target_version}.zip"
            )
        )


__plugin_name__ = "Julia Print Restore"
__plugin_version__ = "1.2.2"


def __plugin_load__():
    global __plugin_implementation__
    __plugin_implementation__ = Julia2018PrintRestore()

    global __plugin_hooks__
    __plugin_hooks__ = {
        "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information,
        "octoprint.comm.protocol.gcode.sent": __plugin_implementation__.gcode_sent_hook,
        "octoprint.comm.protocol.gcode.received": __plugin_implementation__.gcode_received_hook,
        "octoprint.comm.protocol.gcode.queuing": __plugin_implementation__.print_restore_progress_hook
    }
