# coding=utf-8
from __future__ import absolute_import

import octoprint.plugin
from octoprint.events import Events
from flask import jsonify, make_response, request
from octoprint.util.comm import parse_firmware_line
# from octoprint.settings import settings
# import time
from threading import Timer
import json
import os
import re
import logging

from ._version import get_versions
__version__ = get_versions()['version']
del get_versions


class RepeatedTimer(object):
	"""Wrapper for a Timer object that repeatatively calls a function after an interval.

	Args:
		interval (int): Delay interval in seconds
		function (object): The "function" to repeat
		*args: Variable arguments for the "function"
		**kwargs: Keyword arguments for the "function"
	"""

	def __init__(self, interval, function, *args, **kwargs):
		self._timer = None
		self.interval = interval
		self.function = function
		self.args = args
		self.kwargs = kwargs
		self.is_running = False

	def _run(self):
		"""Helper to call the "function" and set the timer again"""
		self.is_running = False
		self.start()
		self.function(*self.args, **self.kwargs)

	def start(self):
		"""Sets the Timer to call the helper"""
		if not self.is_running:
			self._timer = Timer(self.interval, self._run)
			self._timer.start()
			self.is_running = True

	def stop(self):
		"""Stop the timer"""
		if self.is_running:
			self._timer.cancel()
			self.is_running = False


class Julia2018PrintRestore(octoprint.plugin.StartupPlugin,
							octoprint.plugin.EventHandlerPlugin,
							octoprint.plugin.SettingsPlugin,
							octoprint.plugin.AssetPlugin,
							octoprint.plugin.TemplatePlugin,
							octoprint.plugin.BlueprintPlugin):
	"""OctoPrint print restore plugin for Fracktal Works 3D printers."""

	# region "Plugin settings"
	@property
	def enabled(self):
		"""(bool) Get print restore enabled state plugin setting."""
		return self._settings.get_boolean(["enabled"])

	@property
	def autoRestore(self):
		"""(bool) Get auto print restore enabled state plugin setting."""
		return self._settings.get_boolean(["autoRestore"])

	@property
	def interval(self):
		"""(int) Get printer state monitor interval plugin setting."""
		return self._settings.get_int(["interval"])

	@property
	def enableBabystep(self):
		"""(bool) Get babystep monitor enabled state plugin setting."""
		return self._settings.get_boolean(["enableBabystep"])
	# endregion

	# region "IPC"
	def _send_status(self, status_type, status_value, status_description=""):
		"""Send data to all registered mesage reveivers

		Args:
			status_type (str): Type of status message.
			status_value (any): Actual message.
			status_description (str, optional): Defaults to "". Human readable message description.
		"""
		self._plugin_manager.send_plugin_message(self._identifier,
												 dict(type="status", status_type=status_type, status_value=status_value,
													  status_description=status_description))
	# endregion

	# region "Printer state monitor"
	def init_printer_state_monitor(self):
		"""Initialize printer state monitor."""
		if self._timer_printer_state_monitor is None:
			self._timer_printer_state_monitor = RepeatedTimer(self.interval, self.write_restore_file)

	def start_printer_state_monitor(self):
		"""Start monitoring and saving printer state."""
		self._logger.info("Printer state monitor started")
		self.flag_is_saving_state = True
		self.flag_restore_file_write_in_progress = False
		self.state_position = {}
		self._timer_printer_state_monitor.start()

	def stop_printer_state_monitor(self):
		"""Stop monitoring and saving printer state."""
		self.flag_is_saving_state = False
		self._logger.info("Printer state monitor stopped")
		self._timer_printer_state_monitor.stop()
		# self._timer_printer_state_monitor = None

	def check_restore_file_exists(self):
		"""Check if restore file exists

		Returns:
			bool: True if restore file exists
		"""
		if os.path.isfile(self.__RESTORE_FILE):
			return True
		else:
			return False

	def write_restore_file(self):
		"""Write and commit restore file to disk"""
		if self.flag_restore_in_progress or self.flag_restore_file_write_in_progress:
			return

		temps = self._printer.get_current_temperatures()
		file = self._printer.get_current_data()
		data = {"fileName": file["job"]["file"]["name"],
				"filePos": file["progress"]["filepos"],
				"path": file["job"]["file"]["path"],
				"tool0Target": temps["tool0"]["target"],
				"bedTarget": temps["bed"]["target"],
				"position": self.state_position,
				"babystep": self.state_babystep if self.enableBabystep else 0
				}
		if "tool1" in temps.keys():
			if temps["tool1"]["target"] is not None:
				data["tool1Target"] = temps["tool1"]["target"]

		if data["filePos"] is None or "Z" not in data["position"].keys():  # prevents saving when file is garbage
			return

		self.flag_restore_file_write_in_progress = True
		with open(self.__TEMP_RESTORE_FILE, 'w') as restoreFile:
			json.dump(data, restoreFile)
			os.fsync(restoreFile)
		os.rename(self.__TEMP_RESTORE_FILE, self.__RESTORE_FILE)
		self.flag_restore_file_write_in_progress = False

	def parse_restore_file(self, log=False):
		"""Read and parse restore file data
			log (bool, optional): Defaults to False. Log parsed data

		Returns:
			tuple: (status, data) status is True if parsing was successful, False with data set to None otherwise.
		"""
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
		"""Delete the print restore file from disk"""
		if self.check_restore_file_exists():
			try:
				os.remove(self.__RESTORE_FILE)
				self._logger.info("Restore progress file was deleted")
			except:
				self._logger.info("Error deleting restore file")

	def detect_babystep_support(self, line):
		"""Check if firmware has support for babystep. Use to check if babystep needs to be saved.

		Args:
			line (str): The line received from the printer.

		Returns:
			str: Modified or untouched line
		"""
		if "FIRMWARE_NAME" in line:
			# self._logger.info("FIRMWARE_NAME line: {}".format(line))
			# Create a dict with all the keys/values returned by the M115 request
			data = parse_firmware_line(line)

			regex = r"Marlin J18([A-Z]{2})_([0-9]{6}_[0-9]{4})_HA"
			matches = re.search(regex, data['FIRMWARE_NAME'])

			enable_babystep = matches and len(matches.groups()) == 2 and matches.group(1) in ["PT", "PE"]
			if self.enableBabystep != enable_babystep:
				self._settings.set_boolean(["enableBabystep"], enable_babystep)
				self._settings.save()
		return line

	def record_current_state(self, gcode, cmd):
		"""Log current position and temperatures of the printer for saving to restore file.

		Args:
			gcode (str): Parsed GCODE command. None if no known command could be parsed.
			cmd (str): Command to be sent to the printer.
		"""
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
						try:
							self.state_babystep = self.state_babystep + float(val)
						except Exception as e:
							self._logger.error("Could not parse babystep: " + e.message)
			except:
				self._logger.info("Error getting latest command sent to printer")
	# endregion

	# region "Print Restore"
	def start_restore(self):
		"""Try to restore the failed print.
		Initialize printer temperatures and position to last known state.

		Returns:
			tuple: (status, error) status is True if printer was initialized to last known state, False and error is not None otherwise.
		"""
		try:
			restore_state = self.parse_restore_file()
			if not restore_state[0]:
				raise Exception("Did not load data")

			data = restore_state[1]

			if data["fileName"] != "None":   # file name is not none
				self._printer.commands("M117 RESTORE_STARTED")

				#start heating to prepare for initial move

				if data["bedTarget"] > 0:
					self._printer.commands("M140 S{}".format(data["bedTarget"]))
				if "tool0Target" in data.keys():
					if data["tool0Target"] > 0:
						self._printer.commands("M104 T0 S140".format(data["tool0Target"]))
				if "tool1Target" in data.keys():
					if data["tool1Target"] > 0:
						self._printer.commands("M104 T1 S140".format(data["tool1Target"]))
				if "tool0Target" in data.keys():
					if data["tool0Target"] > 0:
						self._printer.commands("M109 T0 S140") #just enough heat to remove nozzle without disloging print
				if "tool1Target" in data.keys():
					if data["tool1Target"] > 0:
						self._printer.commands("M109 T1 S140")  #just enough heat to remove nozzle without disloging print
				# Move the print head
				self._printer.commands("T0")
				self._printer.home("z")
				self._printer.home(["x", "y"])

				#Set to actual heating temperatures

				if "tool0Target" in data.keys():
					if data["tool0Target"] > 0:
						self._printer.commands("M104 T0 S{}".format(data["tool0Target"]))
				if "tool1Target" in data.keys():
					if data["tool1Target"] > 0:
						self._printer.commands("M104 T1 S{}".format(data["tool1Target"]))
				if data["bedTarget"] > 0:
					self._printer.commands("M190 S{}".format(data["bedTarget"]))
				if "tool0Target" in data.keys():
					if data["tool0Target"] > 0:
						self._printer.commands("M109 T0 S{}".format(data["tool0Target"]))
				if "tool1Target" in data.keys():
					if data["tool1Target"] > 0:
						self._printer.commands("M109 T1 S{}".format(data["tool1Target"]))

				self._printer.commands("G1 X10 Y10 F2000")
				# self._printer.commands("G1 X0 Y0 Z10 F9000")
				if "T" not in data["position"].keys():
					data["position"]["T"] = 0

				if "FAN" in data["position"].keys():
					if data["position"]["FAN"] > 0:
						self._printer.commands("M106 S{}".format(data["position"]["FAN"]))

				commands = ["M420 S1",
							"G90",
							"G1 Z{} F4000".format(data["position"]["Z"]),
							"T{}".format(data["position"]["T"]),
							"G92 E0",
							"G1 F200 E3",
							"G92 E{}".format(data["position"]["E"]),
							"G1 X{} Y{} F3000".format(data["position"]["X"], data["position"]["Y"]),
							"G1 F{}".format(data["position"]["F"])
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

	def detect_restore_phase(self, gcode, cmd):
		"""Detect start of restore and the point when job file is resumed.

		Need this to not save printer state during restore attempt. Done by sending M117 with constants.

		Args:
			gcode (str): Parsed GCODE command. None if no known command could be parsed.
			cmd (str): Command to be sent to the printer.
		"""
		if gcode and gcode == "M117":
			if "RESTORE_STARTED" in cmd:
				self._logger.info("RESTORE_STARTED")
				self.flag_restore_in_progress = True
			elif "RESTORE_COMPLETE" in cmd:
				self._logger.info("RESTORE_COMPLETE")
				self.flag_restore_in_progress = False
	# endregion

	# region "Flask blueprint routes"
	@octoprint.plugin.BlueprintPlugin.route("/isFailureDetected", methods=["GET"])
	def route_check_restore_file(self):
		"""REST endpoint that checks for a failed print and if restore is possible"""
		if self._printer.is_printing() or self._printer.is_paused():
			return jsonify(status="Printer is already printing", canRestore=False)
		else:
			if self.check_restore_file_exists():
				restore_state = self.parse_restore_file(log=True)
				if restore_state[0] and "fileName" in restore_state[1].keys():
					return jsonify(status="failureDetected", canRestore=True, file=restore_state[1]["fileName"])
				else:
					return jsonify(status="failureDetected", canRestore=False)
			else:
				return jsonify(status="noFailureDetected", canRestore=False)

	@octoprint.plugin.BlueprintPlugin.route("/restore", methods=["POST"])
	def route_restore(self):
		"""REST endpoint to start print restore"""
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
		"""REST endpoint to get plugin settings"""
		return jsonify(interval=self.interval, autoRestore=self.autoRestore, enabled=self.enabled, version=__version__)

	@octoprint.plugin.BlueprintPlugin.route("/saveSettings", methods=["POST"])
	def route_save_settings(self):
		"""REST endpoint to change plugin settings"""
		if "application/json" not in request.headers["Content-Type"]:
			return make_response("Expected content type JSON", 400)

		try:
			data = request.json
		except:
			return make_response("Malformed JSON body in request", 400)
		if all(item in data.keys() for item in ("autoRestore", "enabled", "interval")):
			self.on_settings_save(data)
			return make_response("Settings Saved", 200)
	# endregion

	# region "Plugin management"
	def initialize(self):
		"""Initialize plugin: loggings, restore file path, state, flags"""
		self._logger.info("Print Restore plugin initialised")

		debug_file = os.path.join(self._settings.getBaseFolder("logs"), "print_restore.log")
		file_handler = logging.handlers.RotatingFileHandler(debug_file, maxBytes=(2 * 1024 * 1024))
		file_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
		# file_handler.setLevel(logging.DEBUG)
		self._logger.addHandler(file_handler)

		basedir = self._settings.getBaseFolder("base")
		if basedir is not None and os.path.exists(basedir):
			self.__RESTORE_FILE = os.path.join(basedir, "print_restore.json")
		else:
			self.__RESTORE_FILE = "/home/pi/print_restore.json"
		self.__TEMP_RESTORE_FILE = self.__RESTORE_FILE + ".tmp"

		# self.enabled = bool(boolConv(self._settings.get(["enabled"])))
		# self.autoRestore = bool(boolConv(self._settings.get(["autoRestore"])))
		# self.interval = float(self._settings.get(["interval"]))
		self._timer_printer_state_monitor = None
		self.state_position = {}
		self.state_babystep = 0
		self.flag_is_saving_state = False
		self.flag_restore_in_progress = False

	def on_after_startup(self):
		"""Called just after launch of the server.

		Initialize printer state monitor
		"""
		self.init_printer_state_monitor()

	def on_event(self, event, payload):
		"""Called by OctoPrint upon processing of a fired event.

		* Start/stop print state monitor
		* Handle auto restore.
		* Handle tool change

		Args:
			event (str): The type of event that got fired
			payload (dict): The payload as provided with the event
		"""
		if self.enabled:
			if event in (Events.CONNECTED):
				if self.check_restore_file_exists():
					if self.autoRestore:
						self.start_restore()
				# else:
				#     self.parse_restore_file()

			elif event in (Events.PRINT_STARTED, Events.PRINT_RESUMED):
				#self.delete_restore_file()
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

	def get_assets(self):
		"""Define the static assets the plugin offers."""
		return dict(
			js=["js/julia_print_restore.js"],
		)

	def get_template_configs(self):
		"""Allow configuration of injected OctoPrint UI template"""
		return [dict(type="settings", custom_bindings=True)]

	def get_settings_version(self):
		return 2

	def get_settings_defaults(self):
		"""Define plugin settngs and their default values"""
		return dict(
			enabled=True,
			autoRestore=False,
			interval=1,
			enableBabystep=None
		)

	def on_settings_migrate(self, target, current):
		if target == 2:
			self._settings.set_boolean(["enabled"], self._settings.get_boolean(["enabled"]))
			self._settings.set_boolean(["autoRestore"], self._settings.get_boolean(["autoRestore"]))
			self._settings.set_int(["interval"], self._settings.get_int(["interval"]))
			self._settings.set_boolean(["enableBabystep"], self._settings.get_boolean(["enableBabystep"]))
			self._settings.save()

	def on_settings_save(self, data):
		"""React to changes in plugin settings"""
		interval = self.interval
		octoprint.plugin.SettingsPlugin.on_settings_save(self, data)
		self._settings.save()
		# self.enabled = bool(boolConv(self._settings.get(["enabled"])))
		# self.autoRestore = bool(boolConv(self._settings.get(["autoRestore"])))
		# self.interval = float(self._settings.get(["interval"]))
		self._logger.info("Print Restore settings saved")
		if self._timer_printer_state_monitor.interval != interval:
			if self.flag_is_saving_state:
				self.stop_printer_state_monitor()
				self.init_printer_state_monitor()
				self.start_printer_state_monitor()
			else:
				self.init_printer_state_monitor()
		if not self.enabled:
			if self._printer.is_printing() or self._printer.is_paused():
				self.stop_printer_state_monitor()
				self.delete_restore_file()
		else:
			if self._printer.is_printing() or self._printer.is_paused():
				self.start_printer_state_monitor()
	# endregion

	# region "OctoPrint hooks"
	def gcode_sent_hook(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
		"""This phase is triggered just after the command was handed over to the serial connection to the printer.

		Args:
			comm_instance  (object): The MachineCom instance which triggered the hook.
			phase (str): The current phase in the command progression, either queuing, queued, sending or sent. Will always match the <phase> of the hook.
			cmd (str): Command to be sent to the printer.
			cmd_type (str): Type of command, e.g. temperature_poll for temperature polling or sd_status_poll for SD printing status polling.
			gcode (str): Parsed GCODE command. None if no known command could be parsed.
		"""
		self.record_current_state(gcode, cmd)

	def gcode_received_hook(self, comm, line, *args, **kwargs):
		"""Get the returned lines sent by the printer.

		Args:
			comm_instance  (object): The MachineCom instance which triggered the hook.
			line (str): The line received from the printer.

		Returns:
			str: Modified or untouched line
		"""
		return self.detect_babystep_support(line)

	def gcode_queuing_hook(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
		"""Get the returned lines sent by the printer.

		Args:
			comm_instance  (object): The MachineCom instance which triggered the hook.
			phase (str): The current phase in the command progression, either queuing, queued, sending or sent. Will always match the <phase> of the hook.
			cmd (str): Command to be sent to the printer.
			cmd_type (str): Type of command, e.g. temperature_poll for temperature polling or sd_status_poll for SD printing status polling.
			gcode (str): Parsed GCODE command. None if no known command could be parsed.
		"""
		self.detect_restore_phase(gcode, cmd)
	# endregion

	#region Update Info
	def get_update_information(self):
		"""Plugin configuration for software update."""
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
	# endregion



__plugin_name__ = "Julia Print Restore"
__plugin_version__ = __version__
__plugin_pythoncompat__ = ">=2.7,<4"


def __plugin_load__():
	global __plugin_implementation__
	__plugin_implementation__ = Julia2018PrintRestore()

	global __plugin_hooks__
	__plugin_hooks__ = {
		"octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information,
		"octoprint.comm.protocol.gcode.sent": __plugin_implementation__.gcode_sent_hook,
		"octoprint.comm.protocol.gcode.received": __plugin_implementation__.gcode_received_hook,
		"octoprint.comm.protocol.gcode.queuing": __plugin_implementation__.gcode_queuing_hook
	}
