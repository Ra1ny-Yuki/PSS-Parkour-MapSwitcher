from mcdreforged.api.utils import Serializable
from typing import Union, List

from .utils import gl_server


class PermissionRequirements(Serializable):
    reload: int = 3


class Configuration(Serializable):
    command_prefix: Union[List[str]] = '!!template'
    permission_requirements: PermissionRequirements = PermissionRequirements.get_default()

    @property
    def prefix(self) -> List[str]:
        return list(set(self.command_prefix)) if isinstance(self.command_prefix, list) else [self.command_prefix]

    @property
    def primary_prefix(self) -> str:
        return self.prefix[0]

    def get_prem(self, cmd: str) -> int:
        return self.permission_requirements.serialize().get(cmd, 1)

    @classmethod
    def load(cls) -> 'Configuration':
        return gl_server.load_config_simple(default_config=cls.get_default(), target_class=cls)

    def save(self):
        gl_server.save_config_simple(self)


config: Configuration = Configuration.load()
