import logging
import sys
from datetime import datetime
from pathlib import Path


class LogStream:
    def __init__(self, original_stream, logger, level):
        self.original_stream = original_stream
        self.logger = logger
        self.level = level
        self.buffer = ""

    def write(self, value):
        self.original_stream.write(value)
        self.buffer += value
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if line.strip():
                self.logger.log(self.level, line.rstrip())

    def flush(self):
        self.original_stream.flush()
        if self.buffer.strip():
            self.logger.log(self.level, self.buffer.rstrip())
        self.buffer = ""


def configure_logging(project_root, logs_directory="logs"):
    directory = Path(project_root) / logs_directory
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = directory / f"execution_{timestamp}.log"
    logger = logging.getLogger("bet365_bot")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(handler)
    sys.stdout = LogStream(sys.__stdout__, logger, logging.INFO)
    sys.stderr = LogStream(sys.__stderr__, logger, logging.ERROR)
    logger.info("Log da execução iniciado: %s", log_file)
    return logger, log_file


def get_logger():
    return logging.getLogger("bet365_bot")
