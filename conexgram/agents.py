"""Agent profile primitives for future multi-agent routing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class AgentProfile:
    """Configuration for one Codex-backed Telegram agent persona."""

    name: str
    description: str = ""
    working_dir: Optional[Path] = None
    model: Optional[str] = None
    reasoning_effort: Optional[str] = None
    mode: Optional[str] = None
    base_prompt: str = ""


@dataclass
class AgentRegistry:
    """Small in-memory registry. The gateway currently uses the default agent."""

    profiles: dict[str, AgentProfile] = field(default_factory=dict)
    default_name: str = "default"

    def register(self, profile: AgentProfile) -> None:
        self.profiles[profile.name] = profile

    def get(self, name: Optional[str] = None) -> Optional[AgentProfile]:
        return self.profiles.get(name or self.default_name)
