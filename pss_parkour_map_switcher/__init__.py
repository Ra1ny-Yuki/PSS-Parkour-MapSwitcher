from mcdreforged.api.all import *

from .utils import tr
from .config import config
from .storage import storage
from .sessions import AbstractSession, AutoMapRollingSession, LoadSlotSession
from .core import register_command, roller


def on_unload(*args, **kwargs):
    AbstractSession.on_unload()


def on_load(server: PluginServerInterface, prev_module):
    server.register_help_message(config.primary_prefix, tr('help.mcdr'))
    register_command()
    if prev_module is not None:
        LoadSlotSession.current_slot = prev_module.LoadSlotSession.current_slot
    if len(storage.get_slots_info()) <= 1:
        server.logger.warning("Auto rolling didn't start because not adequate map to switch")
        server.logger.warning("Reload this plugin after loaded 2 or more maps")
    else:
        AutoMapRollingSession(roller).set_session()
