"""The user-facing error type.

One exception class, carrying an exit code. Its whole purpose is to let a command
handler say *what went wrong and what it means for the process* without knowing
anything about ``sys.exit`` -- which is what keeps handlers testable in-process.
"""

from __future__ import annotations

from .exit_codes import ExitCode

__all__ = ["TaskRunnerError"]


class TaskRunnerError(Exception):
    """An error caused by the user's input or environment, not by a bug.

    Distinguishing this from an unexpected exception is the whole point of the
    case study's error model:

    * ``TaskRunnerError`` -> ``prog: error: <message>`` and a specific exit code.
    * anything else       -> traceback, exit 1.

    Bugs must stay loud. A handler that catches ``Exception`` and re-raises as
    ``TaskRunnerError`` turns a real defect into a mystifying "error: something
    went wrong", and it will never be found.
    """

    def __init__(self, message: str, code: ExitCode = ExitCode.DATA) -> None:
        super().__init__(message)
        self.message = message
        self.code = code

    def __str__(self) -> str:
        return self.message
