"""Settings, and which of them change the answer.

Three do: the identity scheme, the conflict policy, and the page size. None is a measurement, and
`doctor` says so. No list-typed fields -- Pydantic parses those as JSON, so `MSA_SKIP=a,b` fails
with a decode error that tells the user nothing.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import ConflictPolicy, Identity


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MSA_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    manifest: str = "manifest.json"

    identity: Identity = Identity.SHARED_KEY
    policy: ConflictPolicy = ConflictPolicy.FLAG_FOR_REVIEW
    page_size: int = Field(default=3, ge=1)
    skip: str = ""

    @property
    def manifest_path(self) -> Path:
        return self.data_dir / self.manifest

    @property
    def skipped(self) -> frozenset[str]:
        return frozenset(s.strip() for s in self.skip.split(",") if s.strip())


def provenance() -> list[tuple[str, str, str]]:
    """(setting, where its value comes from, what goes wrong if it is off)."""
    return [
        (
            "identity",
            "CHOSEN, never discovered",
            "the count of distinct records changes with it, and there is no scheme the data "
            "prefers -- SHARED_KEY merges across sources, NATIVE_ID never does",
        ),
        (
            "policy",
            "CHOSEN policy, not a measurement",
            "different policies return different winners on the same claims; the default declines "
            "to pick, which is the only one that invents nothing",
        ),
        (
            "page_size",
            "CHOSEN, and usually dictated by the source",
            "it changes WHICH rows a mid-read mutation lands on, so the same mutation loses "
            "different records at different page sizes",
        ),
        (
            "skip",
            "CHOSEN",
            "a skipped source is NEVER_ASKED, which makes the total a floor rather than shrinking "
            "it -- nothing here can say how much it was holding",
        ),
    ]


def what_is_never_measured() -> list[str]:
    return [
        "How many records each source actually holds. Only what it handed over, and what it "
        "claims about itself -- and both come from the source.",
        "How many records a FAILED source was holding. At least zero, at most unbounded.",
        "Whether a record that appeared mid-read should have been in this run at all.",
        "Which conflict policy is correct for a given field.",
    ]
