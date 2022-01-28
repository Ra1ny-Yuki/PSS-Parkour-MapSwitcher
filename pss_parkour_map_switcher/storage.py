import datetime
import json
import os
import random

from typing import Optional, Dict, Tuple
from threading import RLock
from mcdreforged.api.utils import Serializable

from .config import config
from .utils import gl_server


SLOT_INFO_FILE = 'info.json'


class SlotInfo(Serializable):
    last_used: Optional[float] = None
    comment: str = ''

    @property
    def last_used_time(self) -> int:
        return 0 if self.last_used is None else self.last_used

    @property
    def last_used_formatted(self) -> str:
        return datetime.datetime.fromtimestamp(self.last_used_time).strftime('%Y-%m-%d %H:%M:%S')

    def save(self, folder_name: str):
        gl_server.save_config_simple(
            self, file_name=os.path.join(config.backup_path, folder_name, SLOT_INFO_FILE), in_data_folder=False
        )

    @classmethod
    def load(cls, folder_name: str) -> Optional['SlotInfo']:
        folder_path = os.path.join(config.backup_path, folder_name)
        if not os.path.isdir(folder_path):
            return None
        if SLOT_INFO_FILE not in os.listdir(folder_path):
            return None
        try:
            with open(os.path.join(folder_path, SLOT_INFO_FILE), 'r', encoding='UTF-8') as f:
                return SlotInfo.deserialize(json.load(f))
        except:
            return None


class StorageManager:
    def __init__(self):
        self.__lock = RLock()

    @staticmethod
    def get_backup_dir():
        if not os.path.isdir(config.backup_path):
            os.makedirs(config.backup_path)
        return config.backup_path

    def get_slot_full_dir(self, folder: str):
        return os.path.join(self.get_backup_dir(), folder)

    def get_slots_info(self, reverse: bool = False) -> Dict[str, SlotInfo]:
        with self.__lock:
            slot_info_mapping = dict()
            for folder in os.listdir(self.get_backup_dir()):
                this_slot_dir = self.get_slot_full_dir(folder)
                if os.path.isdir(this_slot_dir) and SLOT_INFO_FILE in os.listdir(this_slot_dir):
                    this_slot_info = SlotInfo.load(folder)
                    if this_slot_info is not None:
                        slot_info_mapping[folder] = this_slot_info
            slot_info_mapping = {
                item[0]: item[1] for item in sorted(
                    list(slot_info_mapping.copy().items()), key=lambda item: item[1].last_used_time, reverse=reverse
                )
            }
            return slot_info_mapping

    def get_slots_amount(self):
        with self.__lock:
            return len(self.get_slots_info())

    def get_random_slots_amount(self) -> int:
        with self.__lock:
            slots_raw = self.get_slots_amount() * config.slots_percentage_allowed_in_random / 100
            slots_raw = int(slots_raw) + 1 if slots_raw % 1 != 0 else int(slots_raw)
            return int(slots_raw) if slots_raw < config.max_slots else config.max_slots

    def get_random_slots(self):
        with self.__lock:
            slots_info = self.get_slots_info()
            if self.get_slots_amount() <= self.get_random_slots_amount():
                return slots_info
            return {item: slots_info[item] for item in list(slots_info.keys())[:self.get_random_slots_amount()]}

    def get_slot_size(self, slot_name: str):
        dir_ = self.get_slot_full_dir(slot_name)
        size = 0
        for root, dirs, files in os.walk(dir_):
            size += sum([os.path.getsize(os.path.join(root, name)) for name in files])
        return size

    def random_a_slot(self, *except_slots: str) -> Tuple[str, SlotInfo]:
        with self.__lock:
            slots = self.get_random_slots().copy()
            for item in except_slots:
                if item in slots.keys():
                    del slots[item]
            return random.choice(list(slots.items()))  # fuck u pycharm


storage = StorageManager()
