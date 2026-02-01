from dataclasses import dataclass
from typing import List

@dataclass
class User:
    username: str
    roles: List[str]

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles
