import re

from typing import Union, Iterable, List
from mcdreforged.api.types import CommandSource
from mcdreforged.api.rtext import *
from mcdreforged.api.command import *

from .utils import gl_server, tr, DEBUG
from .config import config


def show_help(source: CommandSource):
    meta = gl_server.get_self_metadata()
    source.reply(tr('help.detailed'), prefix=config.primary_prefix, name=meta.name, ver=str(meta.version))


def htr(key: str, *args, **kwargs) -> Union[str, RTextBase]:
    help_message, help_msg_rtext = gl_server.tr(key, *args, **kwargs), RTextList()
    if not isinstance(help_message, str):
        gl_server.logger.error('Error translate text "{}"'.format(key))
        return key
    for line in help_message.splitlines():
        result = re.search(r'(?<=§7){}[\S ]*?(?=§)'.format(config.prefix[0]), line)
        if result is not None:
            cmd = result.group() + ' '
            help_msg_rtext.append(RText(line).c(RAction.suggest_command, cmd).h(tr('hover.suggest', cmd)))
        else:
            help_msg_rtext.append(line)
        if line != help_message.splitlines()[-1]:
            help_msg_rtext.append('\n')
    return help_msg_rtext


def reload_self(source: CommandSource):
    gl_server.reload_plugin(gl_server.get_self_metadata().id)
    source.reply(tr('msg.reloaded'))


def register_command():
    def permed_literal(literals: Union[str, Iterable[str]]) -> Literal:
        literals = {literals} if isinstance(literals, str) else set(literals)
        perm = 1
        for item in literals:
            target_perm = config.get_prem(item)
            if target_perm > perm:
                perm = target_perm
        return Literal(literals).requires(lambda src: src.has_permission(target_perm))

    root_node: Literal = Literal(config.prefix).runs(lambda src: show_help(src))

    children: List[AbstractNode] = [
        permed_literal('reload').runs(lambda src: reload_self(src))
    ]

    debug_nodes: List[AbstractNode] = []

    if DEBUG:
        children += debug_nodes

    for node in children:
        root_node.then(node)

    gl_server.register_command(root_node)
