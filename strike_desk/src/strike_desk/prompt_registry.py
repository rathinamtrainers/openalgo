"""PromptOps: versioned prompt artifacts, collapsed to one stamped set version."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .errors import PromptNotFound

logger = logging.getLogger(__name__)

_FRONT_MATTER = re.compile(r"\A---\s*\n(?P<body>.*?)\n---\s*\n", re.DOTALL)


@dataclass(frozen=True)
class PromptArtifact:
    name: str
    version: str
    content: str
    digest: str


def _parse(path: Path) -> PromptArtifact:
    raw = path.read_text(encoding="utf-8")
    match = _FRONT_MATTER.match(raw)
    version = "v0"
    body = raw
    if match:
        for line in match.group("body").splitlines():
            key, separator, value = line.partition(":")
            if separator and key.strip() == "version":
                version = value.strip() or "v0"
        body = raw[match.end() :]
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return PromptArtifact(name=path.stem, version=version, content=body, digest=digest)


class PromptRegistry:
    """Immutable view of the prompt artifacts loaded at startup."""

    def __init__(self, artifacts: dict[str, PromptArtifact]) -> None:
        self._artifacts = dict(artifacts)
        fingerprint = "\n".join(
            f"{name}:{artifact.version}:{artifact.digest}"
            for name, artifact in sorted(self._artifacts.items())
        )
        self._set_version = "ps-" + hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:12]

    @classmethod
    def load(cls, directory: Path) -> PromptRegistry:
        artifacts: dict[str, PromptArtifact] = {}
        if directory.is_dir():
            for path in sorted(directory.glob("*.md")):
                artifact = _parse(path)
                artifacts[artifact.name] = artifact
        registry = cls(artifacts)
        logger.info(
            "loaded %d prompt artifact(s) from %s as %s",
            len(artifacts),
            directory,
            registry.set_version,
        )
        return registry

    @property
    def set_version(self) -> str:
        return self._set_version

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._artifacts))

    def get(self, name: str) -> PromptArtifact:
        try:
            return self._artifacts[name]
        except KeyError as exc:
            raise PromptNotFound(f"no prompt artifact named {name!r}") from exc

    def __len__(self) -> int:
        return len(self._artifacts)
