import functools
import os
import shutil
import time

from mcdreforged.api.types import ServerInterface, PluginServerInterface, CommandSource, PlayerCommandSource
from mcdreforged.api.rtext import *
from typing import Union, Optional, Callable, Any

from .config import config


DEBUG = True
gl_server: PluginServerInterface = ServerInterface.get_instance().as_plugin_server_interface()
meta = gl_server.get_self_metadata()
TRANSLATION_KEY_PREFIX = "mapswitch"


def debug_log(text: Union[RTextBase, str]):
    gl_server.logger.debug(text, no_check=DEBUG)


def cp(this_file: str, target_file: str, allow_not_found=True, override=True):
    if os.path.isfile(target_file):
        debug_log(f'Same name file {target_file} found. Ignored')
        if not override:
            debug_log(f'Ignored')
            return
        else:
            debug_log(f'Overrided')
            rm(target_file)
    if os.path.isfile(this_file):
        if os.path.basename(this_file) not in config.ignored_files:
            shutil.copy(this_file, target_file)
            debug_log(f'Copied file "{this_file}" to "{target_file}"')
        else:
            debug_log(f'Ignored file {this_file}')
    elif os.path.isdir(this_file):
        shutil.copytree(this_file, target_file, ignore=lambda path, files: set(filter(config.is_file_ignored, files)))
        debug_log(f'Copied folder "{this_file}" to "{target_file}"')
    else:
        debug_log(f'File {this_file} not found')
        if not allow_not_found:
            raise FileNotFoundError(f'File not found: {this_file}')


def rm(this_file: str, allow_not_found=True):
    if os.path.isfile(this_file):
        os.remove(this_file)
        debug_log(f'Removed file "{this_file}"')
    elif os.path.isdir(this_file):
        shutil.rmtree(this_file)
        debug_log(f'Removed folder "{this_file}"')
    else:
        debug_log(f'4 File {this_file} not found')
        if not allow_not_found:
            raise FileNotFoundError(f'File not found: {this_file}')


def stop_and_wait(countdown: int = 5, stop_command: str = None):
    if not gl_server.is_on_executor_thread():
        raise RuntimeError('This function can only be called on TaskExecutor thread')
    for num in range(0, countdown):
        gl_server.broadcast(tr('msg.countdown', countdown - num).set_color(RColor.red))
        time.sleep(1)
    if stop_command is None:
        gl_server.stop()
    else:
        gl_server.execute(stop_command)
    gl_server.wait_for_start()


def src_name(source: CommandSource):
    return source.player if isinstance(source, PlayerCommandSource) else source.__class__.__name__


def ign(func: Union[object, Callable], *args, attr: str = None, **kwargs):
    try:
        if attr is not None:
            getattr(func, attr)(*args, **kwargs)
        else:
            func(*args, **kwargs)
    except Exception as exc:
        return exc
    else:
        return True


def tr(translation_key: str, *args, with_prefix=True, **kwargs) -> RTextMCDRTranslation:
    translation_key = f"{TRANSLATION_KEY_PREFIX}.{translation_key}" if with_prefix and not \
        translation_key.startswith(TRANSLATION_KEY_PREFIX) else translation_key
    return gl_server.rtr(translation_key, *args, **kwargs)


def ntr(translation_key: str, *args, with_prefix: bool = True, language: Optional[str] = None, **kwargs) -> str:
    translation_key = translation_key if with_prefix and not translation_key.startswith(TRANSLATION_KEY_PREFIX) else \
        f"{TRANSLATION_KEY_PREFIX}.{translation_key}"
    return gl_server.tr(translation_key, *args, language=language, **kwargs)
