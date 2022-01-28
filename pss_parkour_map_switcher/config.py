from mcdreforged.api.utils import Serializable
from mcdreforged.api.types import ServerInterface, PluginServerInterface
from typing import Union, List, Optional

gl_server: PluginServerInterface = ServerInterface.get_instance().as_plugin_server_interface()


class PermissionRequirements(Serializable):
    reload: int = 3
    vote_for_next: int = 3
    select: int = 1
    list: int = 1
    info: int = 1


class Configuration(Serializable):
    command_prefix: Union[List[str]] = ['!!pms', '!!mapswitch']
    backup_path: str = './pre_saved_maps'
    server_path: str = './server'
    countdown_time: int = 5
    max_slots: int = 10
    vote_time_limit: float = 2.0  # min(s)
    map_rolling_interval: float = 60.0  # min(s)
    remind_rolling_interval: float = 10  # min(s)
    slots_percentage_allowed_in_random: float = 50.0  # %
    restore_temp_folder: str = 'temp'
    default_delay_single_time: int = 10
    world_names: List[str] = [
        'world'
    ]
    ignored_files: List[str] = [
        'session.lock'
    ]
    current_slot: Optional[str] = None
    permission_requirements: PermissionRequirements = PermissionRequirements.get_default()

    __debug_perm = 4
    __debug_nodes = ['session-status']

    @property
    def prefix(self) -> List[str]:
        return list(set(self.command_prefix)) if isinstance(self.command_prefix, list) else [self.command_prefix]

    @property
    def primary_prefix(self) -> str:
        return self.prefix[0]

    def get_prem(self, literal: str) -> int:
        if literal in self.__debug_nodes:
            return self.__debug_perm
        return self.permission_requirements.serialize().get(literal, 1)

    @classmethod
    def load(cls) -> 'Configuration':
        default, illegal_item = cls.get_default(), []
        cfg = gl_server.load_config_simple(default_config=default.serialize(), target_class=cls)
        if cfg.max_slots <= 0:
            cfg.max_slots = default.max_slots
            illegal_item.append('slot amount (must >0)')
        if not 0 < cfg.slots_percentage_allowed_in_random <= 100:
            cfg.slots_percentage_allowed_in_random = default.slots_percentage_allowed_in_random
            illegal_item.append('random percentage (0-100, can\'t include 0)')
        if cfg.countdown_time <= 0:
            cfg.slots_percentage_allowed_in_random = default.slots_percentage_allowed_in_random
            illegal_item.append('count down time (must >0)')

        if len(illegal_item) != 0:
            cfg.save()
            for item in illegal_item:
                gl_server.logger.error(f'Illegal {item}, using default value')

        return cfg

    def save(self):
        gl_server.save_config_simple(self)

    def is_file_ignored(self, file_name: str) -> bool:
        for item in self.ignored_files:
            if len(item) > 0:
                if item[0] == '*' and file_name.endswith(item[1:]):
                    return True
                if item[-1] == '*' and file_name.startswith(item[:-1]):
                    return True
                if file_name == item:
                    return True
        return False


config: Configuration = Configuration.load()
