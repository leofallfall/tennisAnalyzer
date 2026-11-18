"""AIClient port and its adapters.

The application prepares an Evidence Pack, asks the configured client to analyse it, and
later collects ``analysis_result.json`` from the pack folder. Collection and validation
are identical for every adapter, so switching adapters changes neither the data model,
the pack, the validation nor the UI.

* ``ClaudeCodeAdapter`` (default, personal development mode): nothing is executed. The
  adapter returns instructions; the user runs Claude Code interactively in the project
  (``/analizza-sessione <pack>``) with their own subscription. No API key, no network
  call from the application, no automation of the subscription.
* ``AnthropicAPIAdapter`` (future): would send ``claude_instructions.md`` as the system
  prompt and the pack files/images as content, then write ``analysis_result.json``.
  Not implemented on purpose.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

from tennis_api.ai.pack import PackKind
from tennis_api.ai.result import RESULT_FILENAME
from tennis_api.ai.validation import ValidationOutcome, validate_result_file
from tennis_cv.common import StrictModel
from tennis_cv.errors import NotYetImplementedError

SKILL_COMMAND = "/analizza-sessione"


class Handoff(StrictModel):
    """What the user has to do for an interactive adapter."""

    adapter: str
    interactive: bool
    pack_path: str
    command: str
    result_path: str
    steps: list[str]


class AIClient(ABC):
    name: ClassVar[str]
    interactive: ClassVar[bool]

    @abstractmethod
    def request_analysis(self, pack_dir: Path, display_path: str, kind: PackKind) -> Handoff:
        """Start (or explain how to start) the analysis of a prepared pack."""

    def collect_result(self, pack_dir: Path) -> ValidationOutcome:
        """Read and validate ``analysis_result.json``. Same for every adapter."""
        return validate_result_file(pack_dir)


class ClaudeCodeAdapter(AIClient):
    name = "claude_code"
    interactive = True

    def request_analysis(self, pack_dir: Path, display_path: str, kind: PackKind) -> Handoff:
        what = "the session" if kind is PackKind.SESSION else "the selected stroke"
        command = f"{SKILL_COMMAND} {display_path}"
        return Handoff(
            adapter=self.name,
            interactive=True,
            pack_path=display_path,
            command=command,
            result_path=f"{display_path}/{RESULT_FILENAME}",
            steps=[
                "Apri un terminale nella cartella del progetto (dove si trova CLAUDE.md).",
                "Avvia Claude Code con il comando `claude` (usa il tuo abbonamento; "
                "nessuna API key).",
                f"Esegui `{command}` per analizzare {what}.",
                "Approva la scrittura del solo file analysis_result.json quando Claude Code "
                "lo chiede.",
                'Torna nell\'app e premi "Import AI analysis".',
            ],
        )


class AnthropicAPIAdapter(AIClient):
    name = "anthropic_api"
    interactive = False

    def request_analysis(self, pack_dir: Path, display_path: str, kind: PackKind) -> Handoff:
        raise NotYetImplementedError(
            "The Anthropic API adapter is not implemented yet. Use the Claude Code adapter "
            "(TPA_AI_ADAPTER=claude_code).",
            details={"adapter": self.name},
        )


ADAPTERS: dict[str, type[AIClient]] = {
    ClaudeCodeAdapter.name: ClaudeCodeAdapter,
    AnthropicAPIAdapter.name: AnthropicAPIAdapter,
}


def get_ai_client(name: str) -> AIClient:
    try:
        return ADAPTERS[name]()
    except KeyError:
        raise ValueError(f"Unknown AI adapter '{name}'. Known: {sorted(ADAPTERS)}") from None
