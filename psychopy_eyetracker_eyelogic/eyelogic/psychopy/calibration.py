# -----------------------------------------------------------------------
# Copyright (C) 2019-2026, EyeLogic GmbH
#
# Permission is hereby granted, free of charge, to any person or
# organization obtaining a copy of the software and accompanying
# documentation covered by this license (the "Software") to use,
# reproduce, display, distribute, execute, and transmit the Software,
# and to prepare derivative works of the Software, and to permit
# third-parties to whom the Software is furnished to do so.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, TITLE AND
# NON-INFRINGEMENT. IN NO EVENT SHALL THE COPYRIGHT HOLDERS OR ANYONE
# DISTRIBUTING THE SOFTWARE BE LIABLE FOR ANY DAMAGES OR OTHER
# LIABILITY, WHETHER IN CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT
# OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
# -----------------------------------------------------------------------

import logging
import threading

import gevent
from eyelogic.ELApi import *
from psychopy import visual
from psychopy.iohub.devices.eyetracker.calibration import BaseCalibrationProcedure


class EyeLogicCalibrationProcedure(BaseCalibrationProcedure):

    calibrationThread: None | threading.Thread

    def __init__(self, eyetrackerInterface, calibration_args):
        BaseCalibrationProcedure.__init__(self, eyetrackerInterface, calibration_args, allow_escape_in_progress=True)
        self.calibrationCond = threading.Condition()
        self.calibrationThread = None
        self.calibrationResult = None

    def createGraphics(self):
        color_type = self.getCalibSetting('color_type')
        text_color = 'black'
        instuction_text = 'Press SPACE to Start Calibration; ESCAPE to Exit.'
        self.textLineStim = visual.TextStim(self.window, text=instuction_text,
                                            pos=(0, 0), height=36,
                                            color=text_color, colorSpace=color_type,
                                            units='pix', wrapWidth=self.width * 0.9)

    def runCalibration(self):
        """Run calibration sequence"""
        if self.showIntroScreen() is False:
            return False

        self.window.fullscr = False
        self.window.callOnFlip(self.startCalibrationHook)
        self.clearCalibrationWindow()
        self.finishCalibrationHook()

        success = self.calibrationResult is not None and self.calibrationResult == ELApi.ReturnCalibrate.SUCCESS
        if success:
            self.showFinishedScreen()
        else:
            self.showFinishedScreen(text_msg="Calibration Failed! Press 'SPACE' key to continue.")
        self.window.close()

        return success

    def _eyelogicCalibrate(self, mode):
        self.calibrationResult = self._eyetracker._elapi.calibrate(mode)

    def startCalibrationHook(self):
        if self.calibrationThread is not None:
            logging.warning("calibration failed: previous calibration hast not concluded.")
            return
        calib_type = self.getCalibSetting('type')
        calib_points = None
        if 'ONE' in calib_type:
            calib_points = 1
        elif 'TWO' in calib_type:
            calib_points = 2
        elif 'FIVE' in calib_type:
            calib_points = 5
        elif 'NINE' in calib_type:
            calib_points = 9
        mode = None
        if calib_points is not None:
            for i, npts in enumerate(self._eyetracker._elapi.getDeviceConfig().calibrationMethods):
                if calib_points == npts:
                    mode = i
                    break
        if mode is None:
            available_modes = self._eyetracker._elapi.getDeviceConfig().calibrationMethods
            available_modes = sorted(available_modes, key=int)
            available_modes = ', '.join([str(m) for m in available_modes])
            logging.warning(
                "calibration failed: selected calibration type '{}' not supported - available modes: {}".format(type,
                                                                                                                available_modes))
            return
        self.calibrationThread = threading.Thread(target=self._eyelogicCalibrate, args=(mode,))
        self.calibrationThread.start()

    def finishCalibrationHook(self):
        if self.calibrationThread is not None:
            self.calibrationThread.join()
            self.calibrationThread = None
        self.window.fullscr = True
