from psychopy.localization import _translate
from psychopy.experiment import Param
from psychopy.experiment.components.settings.eyetracking import EyetrackerBackend


class EyeLogicEyetrackerBackend(EyetrackerBackend):
    """Experiment settings for the EyeLogic eyetrackers.
    """
    label = 'EyeLogic (iohub)'
    key = 'eyetracker.eyelogic.EyeTracker'

    needsFullscreen = False
    needsCalibration = False

    @classmethod
    def getParams(cls):
        # define order
        order = [
            # other settings
            "elgLicenseFile",
            # runtime settings
            "elgSamplingRate",
            "elgTrackerInfront",
            "elgTrackerBelow"
            "elgTrackEyes",
        ]

        # other settings
        params = {}

        # path to the license file
        params['elgLicenseFile'] = Param(
            "",   # default value
            valType='str',
            inputType="file",
            hint=_translate("Path to the license file."),
            label=_translate("License file"),
            categ="Eyetracking"
        )

        # runtime settings
        params['elgSamplingRate'] = Param(
            60,   # default value
            valType='int',
            hint=_translate("The sampling rate of the eyetracker."),
            label=_translate("Sampling rate (Hz)"),
            categ="Eyetracking"
        )
        params['elgTrackerInfront'] = Param(
            0.0,
            valType='float',
            hint=_translate("The distance of the eyetracker from the screen in front of the participant (-300.0 to 300.0 cm)."),
            label=_translate("Distance in front (cm)"),
            categ="Eyetracking"
        )
        params['elgTrackerBelow'] = Param(
            0.0,
            valType='float',
            hint=_translate("The distance of the eyetracker from the screen below the participant (-300.0 to 300.0 cm)."),
            label=_translate("Distance below (cm)"),
            categ="Eyetracking"
        )
        params['elgTrackEyes'] = Param(
            'BINOCULAR',
            valType='str',
            inputType='choice',
            allowedVals=['BINOCULAR',],
            hint=_translate("The eyes to track."),
            label=_translate("Track eyes"),
            categ="Eyetracking"
        )
        return params, order

    @classmethod
    def writeDeviceCode(cls, inits, buff):
        code = (
            "ioConfig[%(eyetracker)s] = {\n"
            "    'name': 'tracker',\n"
            "    'license_file': %(elgLicenseFile)s,\n"
            "    'runtime_settings': {\n"
            "        'sampling_rate': %(elgSamplingRate)s,\n"
            "        'tracker_infront': %(elgTrackerInfront)s,\n"
            "        'tracker_below': %(elgTrackerBelow)s,\n"
            "        'track_eyes': %(elgTrackEyes)s,\n"
            "    },\n"
            "}\n"
        )
        buff.writeIndentedLines(code % inits)
