from psychopy import plugins, experiment, iohub
import importlib

class TestEyetrackerBackend:
    def setup_class(cls):
        # activate plugins
        plugins.activatePlugins()
    
    def test_eyetrackerBackendFound(self):
        """
        Check that the backend for this eyetracker is found by Builder
        """
        # make an experiment
        exp = experiment.Experiment()
        # get the eyetrackers available from its Settings component
        backends = exp.settings.params['eyetracker'].allowedVals
        # make sure this eyetracker is found
        assert 'EyeLogic' in backends


    def test_eyetrackerBackendImportable(self):
        """
        Check that the path given by ioHub for this EyeTracker is actually importable.
        """
        # get device names (as dict)
        devNames = {key: val for key, val in iohub.util.getDeviceNames()}
        # make sure this device is in there
        assert "EyeLogic" in devNames
        # get path
        path = devNames["EyeLogic"]
        # split into class name and module
        mod = ".".join(path.split(".")[:-1])
        name = path.split(".")[-1]
        # try to import the given path
        pkg = importlib.import_module(f"psychopy.iohub.devices.{mod}")
        cls = getattr(pkg, name)
        # check that it's an EyeTracker
        from psychopy.iohub.devices.eyetracker import EyeTrackerDevice
        assert issubclass(cls, EyeTrackerDevice)
