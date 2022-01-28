import re

from typing import Union, Iterable, List, Optional
from mcdreforged.api.types import CommandSource, PlayerCommandSource
from mcdreforged.api.rtext import *
from mcdreforged.api.command import *

from .storage import storage
from .utils import gl_server, tr, DEBUG, src_name, debug_log
from .sessions import AbstractSession, LoadSlotSession, VoteSession, VoteOption, AutoMapRollingSession
from .config import config


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


def show_help(source: CommandSource):
    meta = gl_server.get_self_metadata()
    source.reply(
        tr('help.detailed', prefix=config.primary_prefix, name=meta.name, ver=str(meta.version)
    ).set_translator(htr))


def show_available_votes(source: CommandSource):
    source.reply(tr('help.vote', prefix=config.primary_prefix).set_translator(htr))


def start_vote_to_switch(source: CommandSource):
    def switch_handler(*result: VoteOption):
        if len(result) != 1:
            raise IndexError('Vote has multiple results')
        result = result[0]
        if result.actual_name == 'keep':
            gl_server.say(tr('msg.kept'))
            return
        current: LoadSlotSession = LoadSlotSession.get_instance()
        if current is not None:
            current.interrupt()
        current: AutoMapRollingSession = AutoMapRollingSession.get_instance()
        if current is not None:
            current.interrupt()
        load_session = LoadSlotSession(result.actual_name, handle_exc=False, should_lock=False)
        load_session.start()

    slots = list(storage.get_slots_info().keys())
    if LoadSlotSession.current_slot in slots:
        slots.remove(LoadSlotSession.current_slot)
    options = [VoteOption(item) for item in slots]
    options.append(VoteOption('keep', tr('msg.switch_options.keep'), color=RColor.gold))
    VoteSession(src_name(source), options, switch_handler, tr('msg.switch_options.target'))


def start_vote_to_delay_rolling(source: CommandSource, delay_time: Optional[int] = None):
    delay_time = config.default_delay_single_time if delay_time is None else delay_time

    def delay_handler(*result: VoteOption):
        if len(result) != 1:
            raise IndexError('Vote has multiple results')
        result = result[0]
        if result.actual_name == 'delay':
            rolling: AutoMapRollingSession = AutoMapRollingSession.get_instance()
            rolling.delay(delay_time)
            gl_server.say(tr('msg.delay.delayed', delay_time))
        elif result.actual_name == 'keep':
            gl_server.say(tr('msg.delay.not_delayed'))

    options = [
        VoteOption('delay', tr('msg.delay_options.delay_for', delay_time)),
        VoteOption('keep', tr('msg.delay_options.keep'), color=RColor.gold)
    ]
    VoteSession(src_name(source), options, delay_handler, tr('msg.delay_options.target'))


def reload_self(source: CommandSource):
    gl_server.reload_plugin(gl_server.get_self_metadata().id)
    source.reply(tr('msg.reloaded'))


def list_worlds(source: CommandSource):
    slots = storage.get_slots_info()
    slots_amount, num = len(slots), 0
    text_list = [tr('msg.list.title', slots_amount)]
    for slot_name in slots.keys():
        num += 1
        text_list.append(
            RText(
                f'[§7{num}] §b{slot_name}§r'
            ).h(
                tr('hover.list.info', slot_name)
            ).c(
                RAction.run_command, f"{config.primary_prefix} info {slot_name}"
            )
        )
    source.reply(RTextBase.join('\n', text_list))


def info_slot(source: CommandSource, slot_name: str):
    def format_size(size: int):
        if size < 2 ** 30:
            return f'{round(size / 2 ** 20, 2)} §6MB'
        else:
            return f'{round(size / 2 ** 30, 2)} §6GB'

    slot_info = storage.get_slots_info().get(slot_name)
    source.reply(
        tr('msg.info', slot_name=slot_name, slot_info=slot_info, size=format_size(storage.get_slot_size(slot_name)))
    )


def select_option(source: PlayerCommandSource, option_name: str):
    vote: VoteSession = VoteSession.get_instance()
    vote.vote(source, option_name)
    option = vote.get_option(option_name)
    source.reply(tr('msg.chosen', option.colored_display_name).c(
        RAction.suggest_command, f'{config.primary_prefix} vote ').h(
        tr('hover.vote_other')
    ))


def show_status(source: CommandSource):
    rolling: AutoMapRollingSession = AutoMapRollingSession.get_instance()
    remaining_time: float = round(rolling.get_remaining_time() / 60, 1) if rolling is not None else 'N/A'
    current_slot = LoadSlotSession.current_slot if LoadSlotSession is not None else 'N/A'
    source.reply(tr('msg.status', remain=remaining_time, current=current_slot))


def roller():
    slot_name, slot_info = storage.random_a_slot(LoadSlotSession.current_slot)
    if AbstractSession.session_global_lock.locked():
        gl_server.broadcast(tr('msg.paused'))
    load_session = LoadSlotSession(slot_name, handle_exc=False)
    load_session.start()


def debug_session_status():
    for cls, inst in AbstractSession.all_sessions().items():
        gl_server.logger.info('[Debug] ' + RText(cls.__name__, RColor.green) + ': ' + RText(str(inst), RColor.yellow))


def debug_start_rolling(source: CommandSource):
    rolling: Optional[AutoMapRollingSession] = AutoMapRollingSession.get_instance()
    if rolling is not None:
        source.reply('[Debug]§c Rolling is already running')
        return
    AutoMapRollingSession(roller).set_session()
    source.reply('[Debug] Rolling §astarted§r')


def debug_stop_rolling(source: CommandSource):
    rolling: AutoMapRollingSession = AutoMapRollingSession.get_instance()
    if rolling is None:
        source.reply('[Debug]§c Rolling is not running')
        return
    rolling.interrupt()
    source.reply('[Debug] Rolling §cstopped§r')


def debug_randomables(source: CommandSource):
    source.reply(f'[Debug] {", ".join(list(storage.get_random_slots().keys()))}')


def register_command():
    # Node requirements
    def permed_literal(literals: Union[str, Iterable[str]]) -> Literal:
        literals = {literals} if isinstance(literals, str) else set(literals)
        perm = 1
        for item in literals:
            target_perm = config.get_prem(item)
            if target_perm > perm:
                perm = target_perm
        return Literal(literals).requires(lambda src: src.has_permission(target_perm))

    def vote_literal(literals: Union[str, Iterable[str]]):
        return Literal(literals).requires(lambda: VoteSession.get_instance() is None, lambda: tr('error.vote_running_already'))

    def map_quotable_text(name: str):
        return QuotableText(name).requires(
                lambda src, ctx: ctx['map'] in storage.get_slots_info().keys(), lambda: tr('error.slot_not_found')
            )

    def vote_option_quotable_text(name: str):
        return QuotableText(name).requires(
            lambda src, ctx: VoteSession.is_available_option(ctx[name]),
            lambda: tr('error.invalid_vote_option')
        )

    # nodes
    root_node: Literal = Literal(config.prefix).runs(lambda src: show_help(src))

    children: List[AbstractNode] = [
        permed_literal('reload').runs(
            lambda src: reload_self(src)
        ),
        permed_literal('list').runs(
            lambda src: list_worlds(src)
        ),
        permed_literal('info').then(
            map_quotable_text('map').runs(lambda src, ctx: info_slot(src, ctx['map']))
        ),
        permed_literal('vote').runs(lambda src: show_available_votes(src)).then(
            vote_literal('switch').runs(lambda src: start_vote_to_switch(src))
        ).then(
            vote_literal('delay').runs(lambda src: start_vote_to_delay_rolling(src)).then(
                Integer('seconds').runs(lambda src, ctx: start_vote_to_delay_rolling(src, ctx['seconds']))
            )
        ),
        permed_literal('choose').requires(
            lambda src: src.is_player, lambda: tr('error.player_only')
        ).then(
            vote_option_quotable_text('option').runs(lambda src, ctx: select_option(src, ctx['option']))
        ),
        permed_literal('status').runs(lambda src: show_status(src))
    ]

    debug_nodes: List[AbstractNode] = [
        permed_literal('session-status').runs(debug_session_status),
        permed_literal('start').runs(lambda src: debug_start_rolling(src)),
        permed_literal('stop').runs(lambda src: debug_stop_rolling(src)),
        permed_literal('randomables').runs(lambda src: debug_randomables(src))
    ]

    if DEBUG:
        children += debug_nodes

    for node in children:
        root_node.then(node)

    gl_server.register_command(root_node)
