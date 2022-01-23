from mcdreforged.api.all import *

from .utils import tr
from .config import config
from .core import register_command


def on_load(server: PluginServerInterface, prev_module):
    server.register_help_message(config.primary_prefix, tr('help.mcdr'))
    register_command()
