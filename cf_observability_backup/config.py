import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


@dataclass(frozen=True)
class Settings:
    account_id: str
    api_token: str = field(repr=False)
    workers: tuple[str, ...]
    db: Path
    initial_hours: int = 24


def load_settings(path: Path) -> Settings:
    config = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    for key in ("account_id", "api_token", "db"):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be a non-empty string")

    workers = config.get("workers")
    if (
            not isinstance(workers, list)
            or not workers
            or any(not isinstance(worker, str) or not worker.strip() for worker in workers)
    ):
        raise ValueError("workers must be a non-empty list of Worker names")
    if len(set(workers)) != len(workers):
        raise ValueError("workers must not contain duplicate names")

    initial_hours = config.get("initial_hours", 24)
    if type(initial_hours) is not int or initial_hours < 1:
        raise ValueError("initial_hours must be a positive integer")

    return Settings(
        account_id=config["account_id"],
        api_token=config["api_token"],
        workers=tuple(workers),
        db=path.parent / config["db"],
        initial_hours=initial_hours,
    )
