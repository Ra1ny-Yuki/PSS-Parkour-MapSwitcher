from mcdreforged.api.types import ServerInterface, PluginServerInterface
from mcdreforged.api.rtext import *
from typing import Union, Optional


DEBUG = True
gl_server: PluginServerInterface = ServerInterface.get_instance().as_plugin_server_interface()
TRANSLATION_KEY_PREFIX = gl_server.get_self_metadata().id


def debug_log(text: Union[RTextBase, str]):
    gl_server.logger.debug(text, no_check=DEBUG)


def tr(translation_key: str, *args, with_prefix=True, **kwargs) -> RTextMCDRTranslation:
    translation_key = translation_key if with_prefix and not translation_key.startswith(TRANSLATION_KEY_PREFIX) else \
        f"{TRANSLATION_KEY_PREFIX}.{translation_key}"
    return gl_server.rtr(translation_key, *args, **kwargs)


def ntr(translation_key: str, *args, with_prefix: bool = True, language: Optional[str] = None, **kwargs) -> str:
    translation_key = translation_key if with_prefix and not translation_key.startswith(TRANSLATION_KEY_PREFIX) else \
        f"{TRANSLATION_KEY_PREFIX}.{translation_key}"
    return gl_server.tr(translation_key, *args, language=language, **kwargs)
