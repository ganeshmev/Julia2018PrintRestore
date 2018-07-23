# coding=utf-8
from __future__ import absolute_import

import octoprint.plugin
from octoprint.events import eventManager, Events
from flask import jsonify, make_response, request
from octoprint.settings import settings
import time
from threading import Timer
import json
import os
# TODO:
'''
Auto Resurrect
Ask about ressurection when booting on OCtoprnt screen
Autobooting shouldnt clash with touchscreen operation
change code depending on number of toolheads
'''
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


class Julia2018PrintRestore(octoprint.plugin.StartupPlugin,
							   octoprint.plugin.EventHandlerPlugin,
							   octoprint.plugin.SettingsPlugin,
							   octoprint.plugin.TemplatePlugin,
							   octoprint.plugin.BlueprintPlugin):
	'''+++++++++++++++ Octoprint Startup Functions ++++++++++++++++++++'''
	def initialize(self):
		'''
		Initialises board
		:return: None
		'''
		self._logger.info("Print Restore Plugin initialised ")
		self.savingProgressFlag = False

	def on_after_startup(self):
		'''
        Method to check if resurection file is avialble during server startup
        Also stores other basic settings
        :return: None
        '''
		#check if file is avilable
		self.enabled = bool(self._settings.get(["enabled"]))
		self.autoRestore = bool(self._settings.get(["autoRestore"]))
		self.interval = float(self._settings.get(["interval"]))
		#Initialise Repeated Timer Object
		self.saveProgressRepeatedTimer = RepeatedTimer(self.interval, self.saveProgress)
		#get printer settings
		if self._printer.profile["extruder"]["count"]>1:
			self.isDual = True
			self._logger.info("Print Restore: Dual Extruder Config")
		else:
			self.isDual = False
			self._logger.info("Print Restore: Single Extruder Config")


	def get_settings_defaults(self):
		'''
        initialises default parameters
        :return:
        '''
		return dict(
			enabled=True,
			autoRestore=False,
			interval=1
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
				if self.progressFileExists():
					if self.autoRestore:
						self.restore()
				else:
					self.loadRestoreFile()

			elif event in (Events.PRINT_STARTED, Events.PRINT_RESUMED):
				self.deleteSavedProgress()
				self.startSavingProgrss()

			elif event in Events.PRINT_PAUSED:
				self.stopSavingProgress()

			elif event in Events.PRINT_DONE:
				self.stopSavingProgress()
				self.deleteSavedProgress()

			elif event in (Events.PRINT_FAILED, Events.PRINT_CANCELLED):
				self.stopSavingProgress()


	'''+++++++++++++++ Worker Functions ++++++++++++++++++++'''

	def startSavingProgrss(self):
		'''
		starts the repeated timer that saves the progress
		'''
		self._logger.info("Save progress started, by setting Flag")
		self.savingProgressFlag = True
		self.writingToFile = False # flag to check if writing to file is in process, to make sure it multiple callbacks don't access the file
		self.storeData = {}
		self.position = {}
		self.saveProgressRepeatedTimer.start()

	def stopSavingProgress(self):
		'''
		Stops the repeated timer that saves progress
		:return:
		'''
		self.savingProgressFlag = False
		self._logger.info("Save progress stopped, by resetting Flag")
		self.saveProgressRepeatedTimer.stop()

	def deleteSavedProgress(self):
		'''
		delets the progress file
		:return:
		'''
		if self.progressFileExists():
			try:
				os.remove('/home/pi/restore.json')
				self._logger.info("Restore progress file was deleted")
			except:
				self._logger.info("Error deleting restore file")

	def loadRestoreFile(self):
		if self.progressFileExists():
			try:
				with open("/home/pi/restore.json") as restoreFile:
					self.loadedData = json.load(restoreFile)
					self._logger.info("Restore file opened")
					return True
			except:
				self._logger.info("Error: could not open restore file")
				return False

	def progressFileExists(self):
		'''
		The restore file is present on the USB device and contains sane data
		:return:
		'''
		if os.path.isfile('/home/pi/restore.json'):
			return True
		else:
			return False

	def saveProgress(self):
		'''
		worker function that does the actual saving to file
		:return:
		'''
		if not self.writingToFile:
			temps = self._printer.get_current_temperatures()
			file = self._printer.get_current_data()
			if self.isDual:
				self.storeData = {"fileName": file["job"]["file"]["name"],
								  "filePos": file["progress"]["filepos"],
								  "path": file["job"]["file"]["path"],
								  "tool0Target": temps["tool0"]["target"],
								  "tool1Target": temps["tool1"]["target"],
								  "bedTarget": temps["bed"]["target"],
								  "position": self.position}
			else:
				self.storeData = {"fileName": file["job"]["file"]["name"], "filePos": file["progress"]["filepos"],
								  "path": file["job"]["file"]["path"],
								  "tool0Target": temps["tool0"]["target"],
								  "bedTarget": temps["bed"]["target"],
								  "position": self.position}
			self.writingToFile = True
			with open('/home/pi/restore.json.tmp', 'w') as restoreFile:
				json.dump(self.storeData, restoreFile)
				os.fsync(restoreFile)
			os.rename('/home/pi/restore.json.tmp','/home/pi/restore.json')
			self.writingToFile = False

	def latestCommandSent(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
		'''
		notes the print information on the last sent command to the printer
		:return:
		self._currentTool
		'''
		if self.savingProgressFlag:
			try:
				if gcode and gcode == "G1" or gcode == "G0":
					if "X" in cmd:
						self.position["X"] = cmd[cmd.index('X') + 1:].split(' ', 1)[0]
					if "Y" in cmd:
						self.position["Y"] = cmd[cmd.index('Y') + 1:].split(' ', 1)[0]
					if "Z" in cmd:
						self.position["Z"] = cmd[cmd.index('Z') + 1:].split(' ', 1)[0]
					if "E" in cmd:
						self.position["E"] = cmd[cmd.index('E') + 1:].split(' ', 1)[0]
					if "F" in cmd:
						self.position["F"] = cmd[cmd.index('F') + 1:].split(' ', 1)[0]
				elif gcode and gcode =="M106":
					if "S" in cmd:
						self.position["FAN"] = cmd[cmd.index('S') + 1:].split(' ', 1)[0]
				elif gcode and gcode =="M107":
					if "S" in cmd:
						self.position["FAN"] = 0
				elif gcode and gcode =="T0":
					self.position["T"] = 0
				elif gcode and gcode =="T1":
					self.position["T"] = 1
			except:
				self._logger.info("Error getting latest command sent to printer")


	def restore(self):
		'''
		restores the print progress from saved file
		:return:
		'''
		try:
			with open("/home/pi/restore.json") as restoreFile:
				self.loadedData = json.load(restoreFile)
			if self.loadedData["fileName"] != "None": #file name is not none
				if self.loadedData["bedTarget"] > 0:
					self._printer.commands("M190 S{}".format(self.loadedData["bedTarget"]))
				if self.isDual:
					if self.loadedData["tool0Target"] > 0:
						self._printer.commands("M104 T0 S{}".format(self.loadedData["tool0Target"]))
					if self.loadedData["tool1Target"] > 0:
						self._printer.commands("M109 T1 S{}".format(self.loadedData["tool1Target"]))
					if self.loadedData["tool0Target"] > 0:
						self._printer.commands("M109 T0 S{}".format(self.loadedData["tool0Target"]))
					if "T" in self.loadedData["position"].keys():
						self._printer.commands("T{}".format(self.loadedData["position"]["T"]))
				else:
					if self.loadedData["tool0Target"] > 0:
						self._printer.commands("M109 S{}".format(self.loadedData["tool0Target"]))
				if self.loadedData["position"]["FAN"] > 0:
					self._printer.commands("M106 S{}".format(self.loadedData["position"]["FAN"]))
				commands = ["M420 S1"
							"G90",
							"G92 E0",
							"G1 F200 E5",
							"G1 F{}".format(self.loadedData["position"]["F"]),
							"G92 E{}".format(self.loadedData["position"]["E"]),
							"G1 X{} Y{}".format(self.loadedData["position"]["X"], self.loadedData["position"]["Y"]),
							"G1 Z{}".format(self.loadedData["position"]["Z"])
							]
				self._printer.commands(commands)
				self._printer.select_file(path=self._file_manager.path_on_disk("local", self.loadedData["fileName"]),
										  sd=False, printAfterSelect=True, pos=self.loadedData["filePos"])
				self._send_status(status_type="PRINT_RESURRECTION_STARTED", status_value=self.loadedData["fileName"],
								  status_description="Print resurrection statred")
				return True
			else: # file name is None
				return False
		except:
			return False


	'''+++++++++++++++ API Functions ++++++++++++++++++++'''

	@octoprint.plugin.BlueprintPlugin.route("/isFailureDetected", methods=["GET"])
	def isFailureDetected(self):
		'''
		API to let client know that storage media has restoration file in it,
		and restore is possible
		'''
		if self._printer.is_printing() or self._printer.is_paused():
			return jsonify(status="Printer is already printing", canRestore=False)
		else:
			if self.progressFileExists():
				try:
					with open("/home/pi/restore.json") as restoreFile:
						self.loadedData = json.load(restoreFile)
						return jsonify(status="failureDetected", canRestore=True, file=self.loadedData["fileName"])
				except:
					return jsonify(status="failureDetected", canRestore=False)
			else:
				return jsonify(status="noFailureDetected", canRestore=False)

	@octoprint.plugin.BlueprintPlugin.route("/restore", methods=["POST"])
	def restoreAPI(self):
		"""
		Function that restores the print
		"""
		if not "application/json" in request.headers["Content-Type"]:
			return make_response("Expected content type JSON", 400)

		try:
			data = request.json
		except :
			return make_response("Malformed JSON body in request", 400)

		if self._printer.is_printing() or self._printer.is_paused():
			return jsonify(status="Printer is already printing", canRestore=False)
		else:
			if data["restore"] == True:
				if self.progressFileExists():
					result = self.restore()
					if result == True:
						return jsonify(status="Successfully Restored")
					else:
						return jsonify(status="Error: Could not restore")
				else:
					return jsonify(status="Error: Could not restore, no progress file exists")
			elif data["restore"] == False:
				self.deleteSavedProgress()
				return jsonify(status="Progress file discarded")

	@octoprint.plugin.BlueprintPlugin.route("/getSettings", methods=["GET"])
	def getSettigns(self):
		return jsonify(interval = self.interval, autoRestore = self.autoRestore, enabled = self.enabled)

	@octoprint.plugin.BlueprintPlugin.route("/saveSettings", methods=["POST"])
	def saveSettings(self):

		if not "application/json" in request.headers["Content-Type"]:
			return make_response("Expected content type JSON", 400)

		try:
			data = request.json
		except :
			return make_response("Malformed JSON body in request", 400)
		if all (item in data.keys() for item in ("autoRestore", "enabled", "interval")):
			self.on_settings_save(data)
			return make_response("Settings Saved",200)




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
		self.enabled = bool(self._settings.get(["enabled"]))
		self.autoRestore = bool(self._settings.get(["autoRestore"]))
		self.interval = float(self._settings.get(["interval"]))
		if not self.enabled:
			if self._printer.is_printing() or self._printer.is_paused():
				self.stopSavingProgress()
				self.deleteSavedProgress()

	def get_update_information(self):
		"""
		Function for OTA update thrpugh the software update plugin
		:return:
		"""
		return dict(
			Julia2018PrintRestore=dict(
				displayName="Julia2018PrintRestore",
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

__plugin_name__ = "Julia2018PrintRestore"
__plugin_version__ = "0.0.1"


def __plugin_load__():
	global __plugin_implementation__
	__plugin_implementation__ = Julia2018PrintRestore()

	global __plugin_hooks__
	__plugin_hooks__ = {
		"octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information,
		"octoprint.comm.protocol.gcode.sent": __plugin_implementation__.latestCommandSent
	}

