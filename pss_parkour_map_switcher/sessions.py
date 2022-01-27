import os
import shutil
import threading
import time

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
        self.display_styles: Styles = styles

    @property
    def display_text(self):
        text = self.display_name if isinstance(self.display_name, RTextBase) else RText(self.actual_name)
        if self.display_styles is not None:
            text.set_styles(self.display_styles)
        return text.c(RAction.run_command, f'{config.primary_prefix} choose {self.actual_name}').h(
                tr('hover.vote', option=self.display_name)
        ).set_color(self.display_color)


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
    __session_global_lock = Lock()

    def main(self, *args, thread_name: Optional[str] = None, **kwargs) -> Union[Thread, Any]:
        if thread_name is not None:
            @new_thread(f"MapSwitcher_{str(thread_name)}")
            def wrapper():
                self._main(*args, **kwargs)
            return wrapper()

        else:
            return self._main(*args, **kwargs)

    def _main(self, *args, **kwargs):
        with self.__session_global_lock:
            try:
                self.__main(*args, *kwargs)
            except Exception as exc:
                gl_server.logger.exception('Error occurred in VoteSession thread of MapSwitcher')
                self.on_error(exc)

    def __main(self, *args, **kwargs):
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


class LoadSlotSession(AbstractSession):
    current_slot: Optional[str] = None

    def __init__(self, folder: str, handle_exc: bool = True):
        self.slot_name = folder
        self.slot_dir_path = storage.get_slot_full_dir(folder)
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

    def __main(self, *args, **kwargs):
        if not gl_server.is_on_executor_thread():
            raise RuntimeError('This function can only be called on TaskExecutor thread')
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

        self.current_slot = self.slot_name
        current_info = storage.get_slots_info().get(self.slot_name, default=SlotInfo.get_default())
        debug_log(f'Set used time for slot {self.slot_name}')
        current_info.last_used = time.time()
        current_info.save(self.slot_name)
        gl_server.start()
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


class VoteSession(AbstractSession):
    def __init__(self, initiator: str, vote_options: List[VoteOption], result_handler: Callable[[VoteOption], Any],
                 allow_draw: bool = False, handle_on_executor: bool = True):
        self.initiator = initiator
        self.voted: Dict[str, VoteOption] = dict()
        self.__vote_options: List[VoteOption] = vote_options
        self.__original_options: List[VoteOption] = vote_options
        self.overtime: int = 0
        self.result_handler = result_handler
        self.handle_on_executor = handle_on_executor
        self.allow_draw = allow_draw
        self.thread: Thread = self.main(thread_name='VoteSession')
        self.__terminated = False

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

    def __main(self, *args, **kwargs):
        if not self.is_available():
            raise RuntimeError('There is already a processing vote')
        if self.__session_global_lock.locked():
            raise RuntimeError('Session busy')

        self.set_session()
        self.__wait_and_handle()

    def start_overtime(self, options: List[str]):
        if threading.current_thread() != self.thread:
            raise RuntimeError("Can't be called outside VoteSession thread")
        for item in options:
            if item not in self.actual_vote_options:
                raise IndexError('Illegal overtime option {}: all the options must be included in the former vote')
        self.__vote_options = options
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
    def vote_result(self):
        result = {}
        for item in self.__vote_options:
            result[item] = 0
        for value in self.voted.values():
            result[value] += 1
        sorted_result: List[Tuple[VoteOption, int]] = sorted(list(result.items()), key=lambda x: x[1], reverse=True)
        return {item[0]: item[1] for item in sorted_result}    # fuck u pycharm

    def result_text(self, winners):
        text_list, num = [tr('msg.vote.result', ', '.join([f"§a{item}§r" for item in winners]))], 0
        for option in self.__vote_options:
            num += 0
            color = 'a' if option in winners else 'c§m'
            text_list.append(f'[{num}] §{color}{option.display_name} {self.vote_result[option]}§r')
        for option in self.__original_options:
            num += 0
            text_list.append(f'[{num}] §7§m{option.display_name}')
        return RText.join('\n', text_list)

    @property
    def display_text(self):
        num = 0
        overtime_text = tr('msg.overtime', self.overtime) if self.overtime > 0 else ''
        to_display = [
            tr('msg.vote_headline', player=self.initiator, vote_time=config.vote_time_limit, overtime=overtime_text)
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
        if self.__terminated:
            return

        # Handle result
        max_value, winners = list(self.vote_result.values())[0], []
        for key, value in self.vote_result:
            if value == max_value:
                winners.append(key)

        gl_server.say(self.result_text(winners))

        time.sleep(0.5)

        if len(winners) == 1 or self.allow_draw:
            self.clear()
            if self.handle_on_executor:
                handler = ExecutorScheduleHandler(self.result_handler, *winners)
                gl_server.schedule_task(handler.run)
            else:
                self.result_handler(*winners)
            self.result_handler(*winners)
        elif len(winners) > 1:
            self.start_overtime(winners)
        else:
            raise RuntimeError('Result handle error: Empty winner')

    def interrupt(self):
        self.__terminated = True
        self.clear()

    def on_error(self, exc: Exception):
        gl_server.say(tr('err.during_vote', str(exc)))
        self.interrupt()


class AutoMapRollingSession(AbstractSession):
    __last_rolling_start: Optional[datetime] = None
    __next_rolling: Optional[datetime] = None

    def __init__(self, roller: Callable[[], Any], handle_on_executor: bool = True):
        self.__roller = roller
        self.__handle_on_executor = handle_on_executor
        self.__scheduler = BackgroundScheduler(daemon=True)

        self.__remind_jobs: Job = self.__scheduler.add_job(
            self.remind, IntervalTrigger(minutes=round(config.remind_rolling_interval))
        )
        self.__last_rolling_start = datetime.now()
        self.__next_rolling = self.__last_rolling_start + timedelta(minutes=config.map_rolling_interval)
        self.__roll_job: Job = self.__scheduler.add_job(
            self.__roll, DateTrigger(run_date=self.__next_rolling, timezone=self.__next_rolling.tzinfo),
            kwargs=dict(thread_name='MapRollingSession'),
        )
        self.__scheduler.start()

    def __roll(self):
        self.interrupt(clear=False)
        if self.__handle_on_executor:
            gl_server.schedule_task(self.main)
        else:
            self.main()

    def delay(self, minutes: int):
        if not self.is_running:
            raise RuntimeError("Can't delay a finished session")
        self.__next_rolling += timedelta(minutes=minutes)
        self.__roll_job = self.__roll_job.modify(
            trigger=DateTrigger(run_date=self.__next_rolling, timezone=self.__next_rolling.tzinfo)
        )

    def __main(self, *args, **kwargs):
        self.__roller()
        self.restart()

    def remind(self):
        gl_server.say(tr('msg.remind', round(self.get_remaining_time() / 60)))

    def get_remaining_time(self) -> float:
        return (self.__next_rolling - datetime.now()).total_seconds()

    @property
    def is_running(self):
        return self.__scheduler.running

    def interrupt(self, clear=True):
        self.stop_scheduler()
        if clear:
            self.clear()

    def restart(self):
        if not self.is_running:
            self.interrupt()
            AutoMapRollingSession(self.__roller, self.__handle_on_executor).set_session()
        else:
            raise RuntimeError('Former session not exited')

    def stop_scheduler(self):
        self.__scheduler.shutdown()

    def on_error(self, exc: Exception):
        self.interrupt()
        self.restart()
