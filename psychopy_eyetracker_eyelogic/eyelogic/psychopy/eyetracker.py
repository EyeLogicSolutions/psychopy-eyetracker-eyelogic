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

import copy
import time
import typing as T
from pathlib import Path

import numpy as np
from psychopy import logging
from psychopy.iohub.constants import EventConstants, DeviceConstants, EyeTrackerConstants
from psychopy.iohub.devices import Computer, Device, ioDeviceError
from psychopy.iohub.devices.eyetracker import EyeTrackerDevice
from psychopy.iohub.devices.eyetracker.eye_events import *
from psychopy.iohub.errors import print2err, printExceptionDetailsToStdErr

from .calibration import EyeLogicCalibrationProcedure

try:
    from eyelogic.ELApi import *
except (ModuleNotFoundError, ImportError, NameError):
    logging.error("Error importing EyeLogic api.")
try:
    import win32api
    import win32con
    import ctypes
    from ctypes import wintypes
except (ModuleNotFoundError, ImportError, NameError):
    logging.error(
        "Error importing windows api modules. EyeLogic devices can currently only be used on Windows machines.")

class EyeTracker(EyeTrackerDevice):
    plugin = "psychopy-eyetracker-eyelogic"
    # specify what libraries it has code for - PsychoPy and/or PsychoJS
    targets = ["PsychoPy"]
    """
    To run an experiment with an EyeLogic eyetracker within psychopy:
     - install the psychopy-eyetracker-eyelogic plugin via the builders plugin manager
     - start the EyeLogic server
     - connect your EyeLogic devide to the experiment PC
     - within builder -> experiment settings -> Eyetracking: select EyeLogic
     
    .. note::
       It is strongly recommended to calibrate EyeLogic devices before recording data, 
       as well as to recalibrate for each new test subject.
        
    .. note::
        Only **one** instance of EyeTracker can be created within an experiment.
        Attempting to create > 1 instance will raise an exception.

    """

    configFile = Path(__file__).parent / "default_eyetracker.yaml"

    # Used to hold the EyeTracker subclass instance to ensure only one instance of
    # a given eye tracker type is created. This is a current ioHub limitation, not the limitation of
    # all eye tracking hardware.
    _INSTANCE = None

    #: The multiplier needed to convert a device's native time base to sec.msec-usec times.
    DEVICE_TIMEBASE_TO_SEC = 1

    # Used by pyEyeTrackerDevice implementations to store relationships between an eye
    # trackers command names supported for EyeTrackerDevice sendCommand method and
    # a private python function to call for that command. This allows an implementation
    # of the interface to expose functions that are not in the core EyeTrackerDevice spec
    # without have to use the EXT extension class.
    _COMMAND_TO_FUNCTION = {}

    DEVICE_TYPE_ID = DeviceConstants.EYETRACKER
    DEVICE_TYPE_STRING = 'EYETRACKER'

    EVENT_CLASS_NAMES = [
        'MonocularEyeSampleEvent',
        'BinocularEyeSampleEvent',
        'FixationStartEvent',
        'FixationEndEvent',
        'SaccadeStartEvent',
        'SaccadeEndEvent',
        'BlinkStartEvent',
        'BlinkEndEvent'
    ]

    __slots__ = [
        '_elapi',
        '_event_callback',
        '_sample_callback',
        '_is_recording',
        '_tracker_below',
        '_timestamp_anchor',
        '_tracker_infront',
        '_latest_sample',
        '_latest_gaze_position',
        '_runtime_settings',
        '_INVALID_VALUE'
    ]

    _event_callback: DeviceEventCallback | None
    _sample_callback: GazeSampleCallback | None
    _is_recording: bool
    _tracker_below: float | None
    _tracker_infront: float | None
    _timestamp_anchor: T.Tuple[float, float] | None

    def __init__(self, *args, **kwargs):
        EyeTrackerDevice.__init__(self, *args, **kwargs)
        self._event_callback = None
        self._sample_callback = None
        self._timestamp_anchor = None

        self._elapi = ELApi('psychopy client')
        self._elapi.registerDeviceEventCallback(self._getDeviceEventCallback())
        self._elapi.registerGazeSampleCallback(self._getSampleCallback())
        self._elapi.connect()
        self._is_recording = False
        self._tracker_below = None
        self._tracker_infront = None
        self._INVALID_VALUE = np.nan

    def _getSampleCallback(self):
        @GazeSampleCallback
        def sampleCallback(sample: POINTER(ELGazeSample)):
            self._handleNativeEvent(sample)

        if self._sample_callback is None:
            self._sample_callback = GazeSampleCallback(sampleCallback)
        return self._sample_callback

    def _getDeviceEventCallback(self):
        @DeviceEventCallback
        def deviceEventCallback(event: ELDeviceEvent):
            if ELDeviceEvent(event) == ELDeviceEvent.DEVICE_CONNECTED:
                logging.info("EyeLogic device connected.")
            elif ELDeviceEvent(event) == ELDeviceEvent.CONNECTION_CLOSED:
                logging.warning("Connection to EyeLogic device closed remotely.")
                self.setRecordingState(False)
                self.setConnectionState(False)
            elif ELDeviceEvent(event) == ELDeviceEvent.SCREEN_CHANGED:
                logging.warning("EyeLogic device setup changed remotely.")
                self.setRecordingState(False)
            elif ELDeviceEvent(event) == ELDeviceEvent.DEVICE_DISCONNECTED:
                logging.warning("EyeLogic device disconnected.")
                self.setRecordingState(False)
            elif ELDeviceEvent(event) == ELDeviceEvent.TRACKING_STOPPED:
                logging.warning("EyeLogic device stopped remotely.")
                self.setRecordingState(False)

        if self._event_callback is None:
            self._event_callback = DeviceEventCallback(deviceEventCallback)

        return self._event_callback

    def enableEventReporting(self, enabled=True):
        """enableEventReporting is functionally identical to the eye tracker
        device specific enableEventReporting method."""
        try:
            success = EyeTrackerDevice.enableEventReporting(self, enabled)
            self.setRecordingState(enabled and success)
            return enabled == self._is_recording

        except Exception:
            printExceptionDetailsToStdErr()
        return EyeTrackerConstants.EYETRACKER_ERROR

    def _handleNativeEvent(self, *args, **kwargs):
        """This function is called with every new gaze sample delivered by the EyeLogic device.
                This is roughly equivalent to the selected sampling rate. It is important to keep
                this function as simple as possible as long executions times of this function could
                lead to sample drops.
                Args:
                    args(tuple): tuple of non keyword arguments passed to the callback.

                Kwargs:
                    kwargs(dict): dict of keyword arguments passed to the callback.

                Returns:
                    None
                """

        sample = copy.deepcopy(args[0].contents)
        local_time = Computer.getTime()
        sample_time = self._sampleTimeToDeviceTime(sample.timestampMicroSec)
        delay = local_time - sample_time
        event_time = sample_time
        self._addNativeEventToBuffer((local_time, sample_time, event_time, delay, sample))

    def _getIOHubEventObject(self, native_event_data):
        """The _getIOHubEventObject method is called by the ioHub Process to
        convert new native device event objects that have been received to the
        appropriate ioHub Event type representation.

        This function converts the EyeLogic gaze sample into a BinocularEyeSampleEvent.

        Args:
            native_event_data: object or tuple of (callback_time, native_event_object)

        Returns:
            tuple: The appropriate ioHub Event type in list form.

        """
        logged_time, sample_time, event_time, delay, gaze_sample = native_event_data

        status = 0
        if gaze_sample.porLeftX == ELInvalidValue:
            status += 20
        if gaze_sample.porRightX == ELInvalidValue:
            status += 2

        def replaceInvalid(value):
            return self._INVALID_VALUE if value == ELInvalidValue else value

        sample_eye_left_X = replaceInvalid(gaze_sample.eyePositionLeftX)
        sample_eye_left_Y = replaceInvalid(gaze_sample.eyePositionLeftY)
        sample_eye_left_Z = replaceInvalid(gaze_sample.eyePositionLeftZ)
        sample_radius_left = replaceInvalid(gaze_sample.pupilRadiusLeft)
        sample_eye_right_X = replaceInvalid(gaze_sample.eyePositionRightX)
        sample_eye_right_Y = replaceInvalid(gaze_sample.eyePositionRightY)
        sample_eye_right_Z = replaceInvalid(gaze_sample.eyePositionRightZ)
        sample_radius_right = replaceInvalid(gaze_sample.pupilRadiusRight)
        # por filtered is unused on purpose
        sample_por_raw_X_display, sample_por_raw_Y_display = self._eyeTrackerToDisplayCoords(
            [gaze_sample.porRawX, gaze_sample.porRawY])
        sample_por_left_X_display, sample_por_left_Y_display = self._eyeTrackerToDisplayCoords(
            [gaze_sample.porLeftX, gaze_sample.porLeftY])
        sample_por_right_X_display, sample_por_right_Y_display = self._eyeTrackerToDisplayCoords(
            [gaze_sample.porRightX, gaze_sample.porRightY])

        confidence_interval = -1.

        eventTypeBinoc = EventConstants.BINOCULAR_EYE_SAMPLE
        self._latest_sample = [
            # DeviceEvent
            0,  # experiment_id
            0,  # session_id
            0,  # device_id
            Device._getNextEventID(),  # event_id
            eventTypeBinoc,  # type
            sample_time,  # device_time
            logged_time,  # logged_time
            event_time,  # event time
            confidence_interval,  # confidence_interval
            delay,  # delay
            0,  # filter_id
            # BinocularEyeSampleEvent
            sample_por_left_X_display,  # left_gaze_x, 'f4'
            sample_por_left_Y_display,  # left_gaze_y, 'f4'
            EyeTrackerConstants.UNDEFINED,  # left_gaze_z, 'f4'
            sample_eye_left_X,  # left_eye_cam_x, 'f4'
            sample_eye_left_Y,  # left_eye_cam_y, 'f4'
            sample_eye_left_Z,  # left_eye_cam_z, 'f4'
            EyeTrackerConstants.UNDEFINED,  # left_angle_x, 'f4'
            EyeTrackerConstants.UNDEFINED,  # left_angle_y, 'f4'
            EyeTrackerConstants.UNDEFINED,  # left_raw_x, 'f4'
            EyeTrackerConstants.UNDEFINED,  # left_raw_y, 'f4'
            2 * sample_radius_left,  # left_pupil_measure1, 'f4'
            EyeTrackerConstants.PUPIL_DIAMETER_MM,  # left_pupil_measure1_type, 'u1'
            EyeTrackerConstants.UNDEFINED,  # left_pupil_measure2, 'f4'
            EyeTrackerConstants.UNDEFINED,  # left_pupil_measure2_type, 'u1'
            EyeTrackerConstants.UNDEFINED,  # left_ppd_x, 'f4'
            EyeTrackerConstants.UNDEFINED,  # left_ppd_y, 'f4'
            EyeTrackerConstants.UNDEFINED,  # left_velocity_x, 'f4'
            EyeTrackerConstants.UNDEFINED,  # left_velocity_y, 'f4'
            EyeTrackerConstants.UNDEFINED,  # left_velocity_xy, 'f4'
            sample_por_right_X_display,  # right_gaze_x, 'f4'
            sample_por_right_Y_display,  # right_gaze_y, 'f4'
            EyeTrackerConstants.UNDEFINED,  # right_gaze_z, 'f4'
            sample_eye_right_X,  # right_eye_cam_x, 'f4'
            sample_eye_right_Y,  # right_eye_cam_y, 'f4'
            sample_eye_right_Z,  # right_eye_cam_z, 'f4'
            EyeTrackerConstants.UNDEFINED,  # right_angle_x, 'f4'
            EyeTrackerConstants.UNDEFINED,  # right_angle_y, 'f4'
            EyeTrackerConstants.UNDEFINED,  # right_raw_x, 'f4'
            EyeTrackerConstants.UNDEFINED,  # right_raw_y, 'f4'
            2 * sample_radius_right,  # right_pupil_measure1, 'f4'
            EyeTrackerConstants.PUPIL_DIAMETER_MM,  # right_pupil_measure1_type, 'u1'
            EyeTrackerConstants.UNDEFINED,  # right_pupil_measure2, 'f4'
            EyeTrackerConstants.UNDEFINED,  # right_pupil_measure2_type, 'u1'
            EyeTrackerConstants.UNDEFINED,  # right_ppd_x, 'f4'
            EyeTrackerConstants.UNDEFINED,  # right_ppd_y, 'f4'
            EyeTrackerConstants.UNDEFINED,  # right_velocity_x, 'f4'
            EyeTrackerConstants.UNDEFINED,  # right_velocity_y, 'f4'
            EyeTrackerConstants.UNDEFINED,  # right_velocity_xy, 'f4'
            status  # status, 'u1
        ]

        if status != 22:
            self._latest_gaze_position = [sample_por_raw_X_display, sample_por_raw_Y_display]

        return self._latest_sample

    def _sampleTimeToDeviceTime(self, sample_time):
        """ converts sample timestamps [us] to [s] in device time equivalent time base

        :param sample_time: sample timestamp in microseconds since epoch
        :return: time in seconds in Computer.getTime's time base
        """
        if self._timestamp_anchor is None:
            self._timestamp_anchor = (Computer.getTime(), int(time.time_ns() / 1000))

        elapsed_time_us = sample_time - self._timestamp_anchor[1]
        return self._timestamp_anchor[0] + elapsed_time_us * 1E-6

    def trackerTime(self):
        """
        trackerTime The EyeLogic gaze sample timestamps are based on the system
        clock and converted to Computer.getTime's time base upon conversion to
        BinocularEyeSampleEvents.

        Args:
            None

        Return:
            float: The time as reported by Computer.getTime

        """
        return Computer.getTime()

    def trackerSec(self):
        """
        See trackerTime.

        Args:
            None

        Return:
            float: The time as reported by Computer.getTime. This is already formatted as required.
        """
        return self.trackerTime()

    def setConnectionState(self, enable):
        """setConnectionState either connects ( setConnectionState(True) ) or
        disables ( setConnectionState(False) ) active communication between the
        ioHub and the Eye Tracker.

        .. note::
            A connection to the Eye Tracker is automatically established
            when the ioHub Process is initialized (based on the device settings
            in the iohub_config.yaml), so there is no need to
            explicitly call this method in the experiment script.

        .. note::
            Connecting an EyeLogic eyetracker to the ioHub does **not** collect and send
            eye sample data to the ioHub Process. To start actual data collection,
            use the Eye Tracker method setRecordingState(bool) or the ioHub Device method (device type
            independent) enableEventRecording(bool).

        .. note::
            In order to establish a connection to your EyeLogic device the device must be connected to
            the experiment PC and the EyeLogic server software must be running.

        Args:
            enable (bool): True = enable the connection, False = disable the connection.

        Return:
            bool: indicates the current connection state to the eye tracking hardware.

        """
        try:
            if enable:
                self._elapi.connect()
            else:
                self.setRecordingState(False)
                self._elapi.disconnect()
            return self._elapi.isConnected()
        except Exception:
            if self._elapi:
                self._elapi.disconnect()
            printExceptionDetailsToStdErr()
            return False

    def isConnected(self):
        """isConnected returns whether the ioHub EyeTracker Device is connected
        to the eye tracker hardware or not. An eye tracker must be connected to
        the ioHub for any of the Common Eye Tracker Interface functionality to
        work.

        Args:
            None

        Return:
            bool:  True = the eye tracking hardware is connected. False otherwise.

        """
        try:
            return self._elapi.isConnected()
        except Exception:
            if self._elapi is not None:
                self._elapi.disconnect()
            printExceptionDetailsToStdErr()
            return False

    def sendCommand(self, key, value=None):
        """
        The sendCommand method is not supported by the EyeLogic implementation
        of the EyeTrackerDevice interface.
        """
        return EyeTrackerConstants.INTERFACE_METHOD_NOT_SUPPORTED

    def sendMessage(self, message_contents, time_offset=None):
        """
        The sendMessage method is not supported by the EyeLogic implementation
        of the EyeTrackerDevice interface. EyeLogic devices do not internally
        offer the option of saving the sample stream to disk.
        """
        return EyeTrackerConstants.INTERFACE_METHOD_NOT_SUPPORTED

    def runSetupProcedure(self, calibration_args=None):
        """
        The runSetupProcedure method starts the eye tracker calibration
        routine.
        The calibration of the EyeLogic device is fully handled by the
        EyeLogic server software. This function triggers this process with
        the selected calibration mode and waits for the process to conclude.

        .. note::
            This is a blocking call for the PsychoPy Process
            and will not return to the experiment script until the necessary steps
            have been completed so that the eye tracker is ready to start collecting
            eye sample data when the method returns.

        Args:
            None
        Return:
            bool: True upon successful calibration, False otherwise
        """
        try:
            turn_off_after = False
            if not self._is_recording:
                turn_off_after = True
                if not self.setRecordingState(recording=True):
                    return False
            else:
                # the expectation is that runtime settings cannot change during an experiment
                assert self._tracker_infront == self._runtime_settings['tracker_infront']
                assert self._tracker_below == self._runtime_settings['tracker_below']
            if not self._is_recording:
                return False

            calibration_args = self._normaliseCalibrationArgs(calibration_args)
            genv = EyeLogicCalibrationProcedure(self, calibration_args)
            genv.runCalibration()
            if turn_off_after:
                self.setRecordingState(False)

            result = genv.calibrationResult
            success = result is not None and result == ELApi.ReturnCalibrate.SUCCESS
            if not success:
                logging.critical("EyeLogic calibration failed.")
            return success
        except Exception:
            printExceptionDetailsToStdErr()
            return False

    def setRecordingState(self, recording) -> bool:
        """The setRecordingState method is used to start or stop the recording
        and transmission of eye data from the eye tracking device to the ioHub
        Process.

        Args:
            recording (bool): if True, the eye tracker will start recordng data.; false = stop recording data.

        Return:
            bool: the current recording state of the eye tracking device

        """
        if not self.isConnected() and not self.setConnectionState(True):
            logging.critical("Failed to start eyetracker recording: Not connected to an EyeLogic device.")
            return False
        if recording == self.isRecordingEnabled():
            logging.info("EyeLogic device tracking already: {}".format('started' if self._is_recording else 'stopped'))
            return True

        if recording:
            display_name = self._mapIndex2WindowsName(self._display_device.getIndex())
            if display_name is None:
                logging.critical(
                    "Failed to start eyetracker recording: Could not map PsychoPy monitor index to Windows display id.")
                return False
            infront = self._runtime_settings['tracker_infront']
            below = self._runtime_settings['tracker_below']
            returnSetScreen = self._elapi.setActiveScreen(display_name, ELApi.DeviceGeometry(below, infront))
            if returnSetScreen != ELApi.ReturnSetActiveScreen.SUCCESS:
                logging.critical(
                    "Failed to start eyetracker recording: ELApi.setActiveScreen returns with error code {}.".format(
                        returnSetScreen))
                return False

            requested_rate = self._runtime_settings['sampling_rate']
            sampling_mode = None
            if requested_rate == 'default':
                sampling_mode = 0
            else:
                requested_rate = int(requested_rate)
                for i, available_rate in enumerate(self._elapi.getDeviceConfig().frameRates):
                    if requested_rate == available_rate:
                        sampling_mode = i
                        break
            if sampling_mode is None:
                available_rates = self._elapi.getDeviceConfig().frameRates
                available_rates = sorted(available_rates, key=int)
                available_rates = ' Hz, '.join(str(r) for r in available_rates) + ' Hz'
                logging.critical(
                    "Failed to start eyetracker recording: Requested sampling rate ({} Hz) is not available. Available rates: {}".format(
                        requested_rate, available_rates))
                return False
            returnStart = self._elapi.requestTracking(sampling_mode)
            if returnStart != ELApi.ReturnStart.SUCCESS:
                logging.critical(
                    "Failed to start eyetracker recording: ELApi.requestTracking returns with error code {}".format(
                        returnStart))
                return False
            if EyeTrackerDevice.enableEventReporting(self, True):
                self._is_recording = True
                self._tracker_below = below
                self._tracker_infront = infront
            else:
                return False
        else:
            EyeTrackerDevice.enableEventReporting(self, False)
            self._elapi.unrequestTracking()
            self._is_recording = False
            self._tracker_below = None
            self._tracker_infront = None

        self._latest_sample = None
        self._latest_gaze_position = None
        logging.info("EyeLogic device tracking {}".format('started' if self._is_recording else 'stopped'))
        return self._is_recording

    def isRecordingEnabled(self) -> bool:
        """The isRecordingEnabled method indicates if the eye tracker device is
        currently recording data.

        Args:
           None

        Return:
            bool: True == the device is recording data; False == Recording is not occurring

        """
        return self._is_recording

    def getLastSample(self):
        """The getLastSample method returns the most recent eye sample received
        from the Eye Tracker. The Eye Tracker must be in a recording state for
        a sample event to be returned, otherwise None is returned.

        Args:
            None

        Returns:
            None: If the eye tracker is not currently recording data.

            BinocularEyeSample:  If the eye tracker is recording in a binocular tracking
            mode, the latest sample event of this event type is returned.

        """
        return self._latest_sample

    def getLastGazePosition(self):
        """The getLastGazePosition method returns the most recent eye gaze
        position received from the Eye Tracker. This is the position on the
        calibrated 2D surface that the eye tracker is reporting as the current
        eye position. The units are in the units in use by the ioHub Display
        device.

        If no samples have been received from the eye tracker, or the
        eye tracker is not currently recording data, None is returned.

        .. note:: The position reported here is a "smart average" of the individual
           eye gaze positions.

        Args:
            None

        Returns:
            None: If the eye tracker is not currently recording data or no eye samples have been received.

            tuple: Latest (gaze_x,gaze_y) position of the eye(s)
        """
        return self._latest_gaze_position

    def getPosition(self):
        """
        See getLastGazePosition().
        """
        return self.getLastGazePosition()

    def getPos(self):
        """
        See getLastGazePosition().
        """
        return self.getLastGazePosition()

    def _eyeTrackerToDisplayCoords(self, eyetracker_point):
        """
        Converts the EyeLogic gaze positions into the psychopy display_device
        coodinate system.

        Args:
            eyetracker_point (object): 2-D floating point coordinates in the top-left
            originating, native resolution pixel-space of the display.

        Returns:
            (x,y): The x,y eye position on the calibrated surface in the current
            ioHub.devices.Display coordinate type and space.

        """
        screen_index = self._mapWindowsName2Index(self._elapi.getActiveScreen().id)
        if screen_index is None:
            return EyeTrackerConstants.EYETRACKER_ERROR
        gaze_x = eyetracker_point[0]
        gaze_y = eyetracker_point[1]
        if gaze_x == ELInvalidValue:
            return self._INVALID_VALUE, self._INVALID_VALUE
        return self._display_device._pixel2DisplayCoord(gaze_x, gaze_y, screen_index)

    def _displayToEyeTrackerCoords(self, display_x, display_y):
        """
        Converts points from the psychopy display_device coordinate space into the
        EyeLogic top-left originating pixel-space.

        Args:
            display_x (float): The horizontal eye position on the calibrated 2D surface
            in ioHub.devices.Display coordinate space.
            display_y (float): The vertical eye position on the calibrated 2D surface in
            ioHub.devices.Display coordinate space.

        Returns:
            (object): 2-D floating point coordinates in the top-left originating, native
            resolution pixel-space of the display.

        """
        screen_index = self._mapWindowsName2Index(self._elapi.getActiveScreen().id)
        if screen_index is None:
            return EyeTrackerConstants.EYETRACKER_ERROR
        pixel_x, pixel_y = self._display_device.display2PIxelCoord(display_x, display_y, screen_index)
        return pixel_x, pixel_y

    def _getIndex2WindowsNameMap(self):
        """
        Generates a mapping of the psychopy display indices to the windows dislpay names as
        required by ELApi.

        Returns:
            (list): with tuple elements (bool, int, struct) were the
            boolean: indicates whether the windows api designates this display as primary
            int:     is equivalent to the psychopy display index
            struct:  contains various info on the display as returned by the windows api
        """

        class DisplayDeviceW(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('DeviceName', wintypes.WCHAR * 32),
                ('DeviceString', wintypes.WCHAR * 128), ('StateFlags', wintypes.INT),
                ('DeviceID', wintypes.WCHAR * 128), ('DeviceKey', wintypes.WCHAR * 128)]

        EnumDisplayDevices = ctypes.windll.user32.EnumDisplayDevicesW
        EnumDisplayDevices.restype = ctypes.c_bool

        displays = []
        i = 0
        while True:
            INFO = DisplayDeviceW()
            INFO.cb = ctypes.sizeof(INFO)
            Monitor_INFO = DisplayDeviceW()
            Monitor_INFO.cb = ctypes.sizeof(Monitor_INFO)
            if not EnumDisplayDevices(None, i, ctypes.byref(INFO), 0):
                break
            state_flags = INFO.StateFlags
            if state_flags & win32con.DISPLAY_DEVICE_ATTACHED_TO_DESKTOP:
                displays.append((state_flags & win32con.DISPLAY_DEVICE_PRIMARY_DEVICE > 0, i, INFO))
            i += 1
        return displays

    def _mapIndex2WindowsName(self, index):
        mapping = self._getIndex2WindowsNameMap()
        try:
            return mapping[index][2].DeviceName
        except IndexError:
            return None

    def _mapWindowsName2Index(self, name):
        mapping = self._getIndex2WindowsNameMap()
        for m in mapping:
            if name == m[2].DeviceName:
                return m[1]
        return None

    @staticmethod
    def _normaliseCalibrationArgs(calibration_args):
        """Return calibration args accepted by PsychoPy's BaseCalibrationProcedure."""
        if calibration_args is None:
            calibration_args = {}
        else:
            calibration_args = dict(calibration_args)
        calibration_args.setdefault('target_attributes', {})
        return calibration_args

    @staticmethod
    def getCalibrationDict(calib):
        return EyeTracker._normaliseCalibrationArgs({'type': calib.targetLayout})

    def __del__(self):
        """Do any final cleanup of the eye tracker before the object is
        destroyed."""
        self._elapi.disconnect()
        self.__class__._INSTANCE = None
