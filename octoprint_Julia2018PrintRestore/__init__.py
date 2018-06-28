# coding=utf-8
from __future__ import absolute_import

import octoprint.plugin
from octoprint.events import eventManager, Events
from flask import jsonify, make_response, request
from octoprint.settings import settings
import time
from threading import Timer
# TODO:
'''
Auto Resurrect
Ask about ressurection when booting on OCtoprnt screen
Autobooting shouldnt clash with touchscreen operation
change code depending on number of toolheads
'''

# def run_async(func):
#     '''
#     Function decorater to make methods run in a thread
#     '''
#     from threading import Thread
#     from functools import wraps
#
#     @wraps(func)
#     def async_func(*args, **kwargs):
#         func_hl = Thread(target=func, args=args, kwargs=kwargs)
#         func_hl.start()
#         return func_hl
#
#     return async_func


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

	def on_after_startup(self):
		'''
        Method to check if resurection file is avialble during server startup
        Also stores other basic settings
        :return: None
        '''
		#check if file is avilable
		self.enabled = bool(self._settings.get(["enabled"]))
		self.autoRestore = bool(self._settings.get(["autoRestore"]))
		#Initialise Repeated Timer Object


	def get_settings_defaults(self):
		'''
        initialises default parameters
        :return:
        '''
		return dict(
			enabled=True,
			autoRestore=False
		)

	'''+++++++++++++++ Octoprint Event Callback ++++++++++++++++++++'''

	def on_event(self, event, payload):
		'''
		Callback when an event is detected. depending on the event, different things are done.
		:param event: event to respond to
		:param payload:
		:return:
		'''

		if event in (Events.CONNECTED):
			if self.enabled and self.autoRestore:
				if self.storageConnected():
					if self.progressFileExists():
						self.restore()

		elif event in (Events.PRINT_STARTED, Events.PRINT_RESUMED):
			if self.enabled:
				if self.storageConnected():
					self.deleteSavedProgress()
					self.startSavingProgrss()

		elif event in Events.PRINT_PAUSED:
			if self.savingProgressFlag:
				self.stopSavingProgress()

		elif event in (Events.PRINT_CANCELLED, Events.PRINT_DONE):
			if self.savingProgressFlag:
				self.stopSavingProgress()
			if self.storageConnected():
				if self.progressFileExists():
					self.deleteSavedProgress()

		elif event in (Events.PRINT_FAILED):
			if self.savingProgressFlag:
				self.stopSavingProgress()


	'''+++++++++++++++ Worker Functions ++++++++++++++++++++'''

	def startSavingProgrss(self):
		'''
		starts the repeated timer that saves the progress
		'''
		self.savingProgressFlag = True
		self.writingToFile = False # flag to check if writing to file is in process, to make sure it multiple callbacks don't access the file


	def stopSavingProgress(self):
		'''
		Stops the repeated timer that saves progress
		:return:
		'''
		self.savingProgressFlag = False



	def saveProgress(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
		'''
		Saves print progress to a file last_position last_temperature printer_profile octoprint.printer.profile
		:return:
		'''

		if self.savingProgressFlag:
			if gcode and gcode == "G1" or gcode == "G0":
				if "E" in cmd:
					if not self.writingToFile:
						try:
							temps =  self._printer.get_current_temperatures()
							file = self._printer.get_current_data()
							# TODO: put conditional for number of extruders
							self.data = {"fileName": file["job"]["file"]["name"], "filePos": file["progress"]["filepos"],
										"path": file["job"]["file"]["path"],
										"tool0Target": temps["tool0"]["target"],
										"bedTarget": temps["bed"]["target"]}


	# is logging was being done, stop it.
	# try:
	# 	temps =  self._printer.get_current_temperatures()
	# 	file = self._printer.get_current_data()
	# 	self.data = {"fileName": file["job"]["file"]["name"], "filePos": file["progress"]["filepos"],
	# 			"path": file["job"]["file"]["path"],
	# 			"tool0Target": temps["tool0"]["target"],
	# 			"tool1Target": temps["tool1"]["target"],
	# 			"bedTarget": temps["bed"]["target"]}
	# 	self.data["e"] = payload["position"]["e"]
	# 	self.data["z"] = payload["position"]["z"]
	# 	self.data["y"] = payload["position"]["y"]
	# 	self.data["x"] = payload["position"]["x"]
	# 	self.data["t"] = payload["position"]["t"]
	# 	self.data["f"] = payload["position"]["f"]
	# 	self._logger.info(self.data)
	# 	self.on_settings_save(self.data)
	# 	self.savingProgress = False
	# 	self._logger.info("Print Resurrection: Print Progress saved")
	# except:
	# 	self.data = {"fileName": "None", "filePos": 0,
	# 				 "path": "None",
	# 				 "tool0Target": 0,
	# 				 "tool1Target": 0,
	# 				 "bedTarget": 0,
	# 				 "x": 0,
	# 				 "y": 0,
	# 				 "z": 0,
	# 				 "e": 0,
	# 				 "t": 0,
	# 				 "f": 0, }
	# 	self.on_settings_save(self.data)
	# 	self._logger.info("Could not save settings, restoring defaults")

	def deleteSavedProgress(self):
		'''
		delets the progress file
		:return:
		'''
		pass
	def openProgressFile(self):
		'''
		Opens a file for writing the print progrss
		:return:
		'''
		pass
	def closeProgressFile(self):
		'''
		closes the print progrss file
		:return:
		'''
		pass

	def storageConnected(self):
		"""
		Checks if USB storage media is present, to which file progress withll be written
		:param self:
		:return: True is media is connected, else False.
		"""
		pass

	def progressFileExists(self):
		'''
		The restore file is present on the USB device and contains sane data
		:return:
		'''
		pass
		if True:
			return True
		else:
			return False

	@octoprint.plugin.BlueprintPlugin.route("/isFailureDetected", methods=["GET"])
	def isFailureDetected(self):
		'''
		API to let client know that storage media has restoration file in it,
		and restore is possible
		'''
		# if self.fileName != "None" and self._printer.is_ready():
		# 	return jsonify(status='available', file = self.fileName)
		# else:
		# 	return jsonify(status='notAvailable')
		if self.progressFileExists():
			return jsonify(status = 'failureDetected')
		else:
			return jsonify(status = 'noFailureDetected')


	@octoprint.plugin.BlueprintPlugin.route("/restore", methods=["GET"])
	def restore(self):
		"""
		Function that restores the print
		"""
		# if self.fileName != "None":
		# 	if self.bedTarget > 0:
		# 		self._printer.set_temperature("bed", self.bedTarget)
		# 	if self.tool0Target > 0:
		# 		self._printer.set_temperature("tool0", self.tool0Target)
		# 	if self.tool1Target > 0:
		# 		self._printer.set_temperature("tool1", self.tool1Target)
		# 	self._printer.home("z")
		# 	self._printer.home(["x", "y"])
		# 	commands = ["M420 S1"
		# 				"G90",
		# 				"T{}".format(self.t),
		# 				"G92 E0",
		# 				"G1 F200 E5",
		# 				"G1 F{}".format(self.f),
		# 				"G92 E{}".format(self.e),
		# 				"G1 X{} Y{}".format(self.x,self.y),
		# 				"G1 Z{}".format(self.z)
		# 				]
		# 	self._printer.commands(commands)
		# 	filenameToSelect = self._file_manager.path_on_disk("local", self.path)
		# 	self._printer.select_file(path=filenameToSelect, sd=False, printAfterSelect=True, pos=self.filePos)
		# 	self._send_status(status_type="PRINT_RESURRECTION_STARTED", status_value=self.fileName,
		# 					  status_description="Print resurrection statred")
		# # return an error or success
		return jsonify(status='restored')





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
		self.enabled = bool(self._settings.get(["enabled"]))
		self.autoRestore = bool(self._settings.get(["autoRestore"]))
		self._settings.save()

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
		"octoprint.comm.protocol.gcode.sent": __plugin_implementation__.saveProgress
	}

