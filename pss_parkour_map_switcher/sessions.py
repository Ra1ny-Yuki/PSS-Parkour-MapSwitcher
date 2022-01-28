import os
import shutil
import threading
import time

from abc import ABC
from datetime import datetime, timedelta
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.job import Job
from apscheduler.schedulers.background import BackgroundScheduler
from typing import Dict, List, Callable, Any, Optional, Union, Iterable, Tuple
from threading import Lock, Thread
from mcdreforged.api.rtext import *
from mcdreforged.api.types import PlayerCommandSource, CommandSource
from mcdreforged.api.decorator import new_thread

from .utils import debug_log, gl_server, stop_and_wait, rm, cp, tr
from .storage import storage, SLOT_INFO_FILE, SlotInfo
from .config import config


VoteOptionDisplayText = Union[str, RTextBase]
Styles = Union[None, RStyle, Iterable[RStyle]]


class VoteOption:
    def __init__(self, actual_name: str, display_name: Optional[VoteOptionDisplayText] = None,
                 color: Optional[RColor] = None, styles: Styles = None):
        self.actual_name: str = actual_name
        self.display_name: VoteOptionDisplayText = actual_name if display_name is None else display_name
        self.display_color: RColor = color if isinstance(color, RColor) else RColor.aqua
        self.colored_display_name = RText(str(self.display_name), self.display_color)
        self.display_styles: Styles = styles

    @property
    def display_text(self):
        text = self.colored_display_name.copy()
        if self.display_styles is not None:
            text.set_styles(self.display_styles)
        return text.c(
            RAction.run_command, f'{config.primary_prefix} choose {self.actual_name}'
        ).h(
            tr('hover.vote', option=self.colored_display_name)
        )


class ExecutorScheduleHandler:
    def __init__(self, handler: Callable[[VoteOption], Any], *args, **kwargs):
        self.__handler = handler
        self.__args = args
        self.__kwargs = kwargs

    def run(self):
        self.__handler(*self.__args, **self.__kwargs)

    def schedule(self):
        gl_server.schedule_task(self.run)


class AbstractSession:
    __running_sessions = {}
    session_global_lock = Lock()

    def __init__(self, should_lock: bool = True):
        self.should_lock = should_lock
        self.terminated = False

    def main(self, *args, thread_name: Optional[str] = None, **kwargs) -> Thread:
        @new_thread(f"MapSwitcher_{str(thread_name) if thread_name is not None else 'BeforeSession'}")
        def wrapper():
            def wrap():
                handler = ExecutorScheduleHandler(self.actual_main, *args, **kwargs)
                if thread_name is None:
                    gl_server.schedule_task(handler.run)
                else:
                    handler.run()

            try:
                if self.should_lock:
                    with self.session_global_lock:
                        if self.terminated:
                            return
                        wrap()
                else:
                    wrap()

            except Exception as exc:
                gl_server.logger.exception('Error occurred in VoteSession thread of MapSwitcher')
                gl_server.say(tr('error.in_session', RText(str(exc), RColor.dark_red)).set_color(RColor.red))
                self.on_error(exc)

        return wrapper()

    def actual_main(self, *args, **kwargs):
        raise NotImplementedError

    def on_error(self, exc: Exception):
        raise NotImplementedError

    @classmethod
    def clear(cls):
        if cls in cls.__running_sessions.keys():
            cls.__running_sessions[cls] = None

    @classmethod
    def clear_all(cls):
        cls.__running_sessions = {}

    def interrupt(self):
        self.terminated = True
        self.clear()

    @classmethod
    def is_all_empty(cls):
        return all([session is None for session in cls.__running_sessions.values()])

    def set_session(self):
        self.__running_sessions[self.__class__] = self

    @classmethod
    def get_instance(cls):
        return cls.__running_sessions.get(cls)

    @classmethod
    def is_available(cls):
        return cls.get_instance() is None

    @classmethod
    def all_sessions(cls):
        return cls.__running_sessions.copy()

    @classmethod
    def on_unload(cls):
        for session in cls.__running_sessions.values():
            if session is not None:
                session.interrupt()


class LoadSlotSession(AbstractSession, ABC):
    current_slot: Optional[str] = None

    def __init__(self, slot: str, handle_exc: bool = True, should_lock: bool = True):
        super(LoadSlotSession, self).__init__(should_lock)
        self.slot_name = slot
        self.slot_dir_path = storage.get_slot_full_dir(slot)
        self.backed_up = []
        self.moved = []
        self.temp_folder = os.path.join(config.server_path, config.restore_temp_folder)
        self.finished_backup = False
        self.handle_exc = handle_exc
        if not os.path.isdir(self.slot_dir_path):
            raise FileNotFoundError('This slot is not found')

    def start(self):
        self.set_session()
        self.main()

    def actual_main(self, *args, **kwargs):
        if not gl_server.is_on_executor_thread():
            raise RuntimeError('This function can only be called on TaskExecutor thread')
        gl_server.broadcast(tr('msg.next_map', self.slot_name))
        gl_server.broadcast(tr('msg.before_load', config.countdown_time))
        stop_and_wait(config.countdown_time)
        if not os.path.isdir(self.temp_folder):
            os.makedirs(self.temp_folder)
            debug_log('Generated temp folder')

        # back world files up
        for item in config.world_names:
            cp(os.path.join(config.server_path, item), os.path.join(self.temp_folder, item))

        # remove current world file
        self.finished_backup = True
        for item in config.world_names:
            rm(os.path.join(config.server_path, item))

        # copy file to server directory
        for item in os.listdir(self.slot_dir_path):
            if item != SLOT_INFO_FILE:
                cp(os.path.join(self.slot_dir_path, item), os.path.join(config.server_path, item))

        shutil.rmtree(self.temp_folder)

        LoadSlotSession.current_slot = self.slot_name
        debug_log(f'Current slot: {self.current_slot}')
        current_info = storage.get_slots_info().get(self.slot_name, SlotInfo.get_default())
        debug_log(f'Set used time for slot {self.slot_name}')
        current_info.last_used = time.time()
        current_info.save(self.slot_name)
        gl_server.start()
        rolling: Optional[AutoMapRollingSession] = AutoMapRollingSession.get_instance()
        if rolling is not None:
            AutoMapRollingSession.get_instance().restart()
        self.clear()

    def on_error(self, exc: Exception):
        if self.finished_backup:
            for item in self.moved:
                rm(os.path.join(config.server_path, item))
            for item in self.backed_up:
                cp(os.path.join(self.temp_folder, item), os.path.join(config.server_path, item))
        if os.path.isdir(self.temp_folder):
            shutil.rmtree(self.temp_folder)
        if not gl_server.is_server_running():
            gl_server.start()
        self.clear()
        if not self.handle_exc:
            raise exc


class VoteSession(AbstractSession, ABC):
    def __init__(self, initiator: str, vote_options: List[VoteOption], result_handler: Callable[[VoteOption], Any],
                 target: Union[str, RTextBase], allow_draw: bool = False, handle_on_executor: bool = True):
        super(VoteSession, self).__init__()
        self.initiator = initiator
        self.voted: Dict[str, VoteOption] = dict()
        self.__vote_options: List[VoteOption] = vote_options
        self.__original_options: List[VoteOption] = vote_options
        self.overtime: int = 0
        self.result_handler = result_handler
        self.handle_on_executor = handle_on_executor
        self.allow_draw = allow_draw
        self.__thread_lock = Lock()
        self.target: Union[str, RTextBase] = target
        self.thread: Optional[Thread] = None

        if not self.session_global_lock.locked():
            with self.__thread_lock:
                self.thread = self.main(thread_name='VoteSession')
        else:
            gl_server.logger.warning('Already a session running!')
            self.terminated = True

    @classmethod
    def is_available_option(cls, actual_option_name: str):
        inst: VoteSession = cls.get_instance()
        if inst is None:
            return False
        return actual_option_name in inst.actual_vote_options

    @property
    def actual_vote_options(self):
        return list(self.option_mapping.keys())

    @property
    def vote_options_for_display(self):
        options = []
        for item in self.__original_options:
            if item in self.__vote_options:
                options.append(item.display_text)
            else:
                options.append(f"§7§m{item.display_name}§r")
        return sorted(options, key=lambda x: 1 if isinstance(x, RTextBase) else 0, reverse=True)

    def actual_main(self, *args, **kwargs):
        if not self.is_available():
            raise RuntimeError('There is already a processing vote')

        self.set_session()
        with self.__thread_lock:
            self.__wait_and_handle()

    def start_overtime(self, options: List[VoteOption]):
        debug_log('Overtime starting...')
        if threading.current_thread() != self.thread:
            raise RuntimeError("Can't be called outside VoteSession thread")
        for item in options:
            if item not in self.__vote_options:
                raise IndexError('Illegal overtime option {}: all the options must be included in the former vote')
        self.__vote_options = options
        self.voted = {}
        self.overtime += 1

        self.__wait_and_handle()

    @property
    def option_mapping(self) -> Dict[str, VoteOption]:
        return {item.actual_name: item for item in self.__vote_options}

    def get_option(self, option_name: str) -> Optional[VoteOption]:
        return self.option_mapping.get(option_name)

    def vote(self, source: PlayerCommandSource, option: str):
        if option not in self.actual_vote_options:
            raise KeyError('Illegal vote option')
        self.voted[source.player] = self.get_option(option)

    @property
    def vote_result(self) -> Dict[VoteOption, int]:
        result = {}
        for item in self.__vote_options:
            result[item] = 0
        for value in self.voted.values():
            result[value] += 1
        sorted_result: List[Tuple[VoteOption, int]] = sorted(list(result.items()), key=lambda x: x[1], reverse=True)
        return {item[0]: item[1] for item in sorted_result}    # fuck u pycharm

    def result_text(self, winners):
        text_list, num = [tr('msg.vote.result', ', '.join([f"§a{item.display_name}§r" for item in winners]))], 0
        for option, result in self.vote_result.items():
            num += 1
            c1, c2 = ('a', "2") if option in winners else ('c§m', "4§m")
            text_list.append(f'[§e{num}§r] §{c2}{result}§r §{c1}{option.display_name}§r')
        for option in self.__original_options:
            if option not in self.__vote_options:
                num += 1
                text_list.append(f'[§e{num}§r] §7§m-- {option.display_name}')
        return RText.join('\n', text_list)

    @property
    def display_text(self):
        num = 0
        overtime_text = tr('msg.vote.overtime', self.overtime) if self.overtime > 0 else ''
        to_display = [
            tr('msg.vote.headline', target=self.target,
               player=self.initiator, vote_time=config.vote_time_limit, overtime=overtime_text)
        ]

        for option in self.vote_options_for_display:
            num += 1
            to_display.append(f'[§3{num}§r] ' + option)

        return RTextBase.join('\n', to_display)

    def display_to_source(self, source: CommandSource):
        source.reply(self.display_text)

    def __wait_and_handle(self):
        # Ensure current thread
        if gl_server.is_on_executor_thread():
            raise RuntimeError("Vote can't start on TaskExecutor thread")
        if self.thread != threading.current_thread():
            raise RuntimeError("Unexpected call from other threads")

        # Announce vote start
        gl_server.say(self.display_text)

        # Wait for vote ends
        time.sleep(config.vote_time_limit * 60)


        # Handle result
        max_value, winners = list(self.vote_result.values())[0], []
        for key, value in self.vote_result.items():
            if value == max_value:
                winners.append(key)

        if sum(self.vote_result.values()) == 0:
            gl_server.say(tr('msg.vote.no_one').set_color(RColor.red))
            self.interrupt()

        if self.terminated:
            return

        gl_server.say(self.result_text(winners))

        if len(winners) == 1 or self.allow_draw:
            self.interrupt()
            if self.handle_on_executor:
                handler = ExecutorScheduleHandler(self.result_handler, *winners)
                gl_server.schedule_task(handler.run)
            else:
                self.result_handler(*winners)
        elif len(winners) > 1:
            self.start_overtime(winners)
        else:
            raise RuntimeError('Result handle error: Empty winner')

    def on_error(self, exc: Exception):
        self.interrupt()


class AutoMapRollingSession(AbstractSession, ABC):
    __last_rolling_start: Optional[datetime] = None
    __next_rolling: Optional[datetime] = None

    def __init__(self, roller: Callable[[], Any]):
        super(AutoMapRollingSession, self).__init__(False)
        self.__roller = roller
        self.__last_rolling_start = datetime.now()
        self.__next_rolling = self.__last_rolling_start + timedelta(minutes=config.map_rolling_interval)
        self.__scheduler: Optional[BackgroundScheduler] = None
        self.__remind_jobs: Optional[Job] = None
        self.__roll_job: Optional[Job] = None
        self.__init_scheduler()

    def __init_scheduler(self):
        try:
            self.__roll_job.remove()
        except:
            pass
        try:
            self.__remind_jobs.remove()
        except:
            pass
        try:
            self.__scheduler.shutdown()
        except:
            pass
        del self.__roll_job
        del self.__remind_jobs
        del self.__scheduler
        self.__scheduler = BackgroundScheduler(daemon=True)
        self.__remind_jobs = self.__scheduler.add_job(
            self.remind,
            IntervalTrigger(seconds=round(timedelta(minutes=config.remind_rolling_interval).total_seconds()))
        )
        self.__roll_job = self.__scheduler.add_job(
            self.__roll, DateTrigger(run_date=self.__next_rolling),
        )
        self.__scheduler.start()

    def __roll(self):
        gl_server.schedule_task(self.main)

    def delay(self, minutes: int):
        delay_time = timedelta(seconds=minutes * 60)
        debug_log(f'Delay value: {delay_time.total_seconds()} s')
        debug_log(f'Old next rolling time: {self.__next_rolling}')
        self.__next_rolling += delay_time
        debug_log(f'Current next rolling time: {self.__next_rolling}')

        if self.__next_rolling.timestamp() < time.time():
            gl_server.say(tr('msg.delay.not_enough'))
            return

        if not self.is_running:
            load_session = LoadSlotSession.get_instance()
            if load_session is None:
                raise RuntimeError("Can't delay a finished session")
            load_session.interrupt()
        self.__init_scheduler()

    def actual_main(self, *args, **kwargs):
        self.__remind_jobs.remove()
        if self.__scheduler.running:
            self.stop_scheduler()
        self.__roller()

    def remind(self):
        if abs((datetime.now() - self.__next_rolling).total_seconds()) < 1:
            return
        remaining = self.get_remaining_time()
        remaining = round(remaining / 60, 2)
        gl_server.say(tr('msg.remind', remaining))

    def get_remaining_time(self) -> float:
        return (self.__next_rolling - datetime.now()).total_seconds()

    @property
    def is_running(self):
        return self.__scheduler.running

    def interrupt(self):
        if self.__scheduler.running:
            self.stop_scheduler()
        super(AutoMapRollingSession, self).interrupt()

    def restart(self):
        if not self.is_running:
            self.interrupt()
            AutoMapRollingSession(self.__roller).set_session()
        else:
            raise RuntimeError('Former session not exited')

    def stop_scheduler(self):
        self.__scheduler.shutdown()

    def on_error(self, exc: Exception):
        self.interrupt()
        self.restart()
