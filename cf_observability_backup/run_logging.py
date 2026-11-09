import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


@contextmanager
def run_logging(directory: Path) -> Iterator[None]:
    rotated = directory / "logs"
    rotated.mkdir(exist_ok=True)
    file_handler = TimedRotatingFileHandler(
        directory / "backup.log", when="midnight", encoding="utf-8", errors="backslashreplace"
    )
    file_handler.namer = lambda name: str(rotated / Path(name).name)
    console_handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S %z"
    )

    logger = logging.getLogger(__package__)
    old_level, old_propagate = logger.level, logger.propagate
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handlers = (console_handler, file_handler)
    for handler in handlers:
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    try:
        yield
    finally:
        for handler in handlers:
            logger.removeHandler(handler)
            handler.close()
        logger.setLevel(old_level)
        logger.propagate = old_propagate
