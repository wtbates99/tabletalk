"""A bounded local executor for independent eval trials."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TypeVar

Input = TypeVar("Input")
Output = TypeVar("Output")


@dataclass(frozen=True)
class LocalExecutor:
    max_workers: int = 1

    def __post_init__(self) -> None:
        if not 1 <= self.max_workers <= 32:
            raise ValueError("Local executor workers must be from 1 through 32")

    def map(
        self,
        function: Callable[[Input], Output],
        values: Iterable[Input],
    ) -> tuple[Output, ...]:
        """Run bounded tasks and preserve the input order."""
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            return tuple(executor.map(function, values))
