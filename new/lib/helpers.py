import logging
from logging.handlers import RotatingFileHandler
import os
import re
import shlex
import subprocess

import sys
from time import monotonic

project_dir: str = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))


def quote_all(*args):
    quoted = []
    for arg in args:
        quoted.append(shlex.quote(arg))
    return tuple(quoted)


def execute(command, timeout: int = None, passthrough=False, passthrough_prefix=None, **kwargs):
    if passthrough:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.STDOUT

    if "stdout" not in kwargs:
        kwargs["stdout"] = subprocess.PIPE
    if "stderr" not in kwargs:
        kwargs["stderr"] = subprocess.STDOUT
    if "shell" not in kwargs:
        kwargs["shell"] = True

    process = subprocess.Popen(command, **kwargs)
    if passthrough:
        file_log_handler = find_file_log_handler()
        buffer = TerminalLineBuffer()
        stdout = process.stdout
        deadline = monotonic() + timeout if timeout is not None else None
        while process.poll() is None and (deadline is None or deadline < monotonic()):
            # noinspection PyTypeChecker
            value: bytes = stdout.read(1)
            sys.stdout.buffer.write(value)

            if file_log_handler is not None:
                buffer.feed(value)
                if buffer.is_complete():
                    line = buffer.get_line()
                    file_log_handler.handle(create_stdout_log_record(line, passthrough_prefix))

        if file_log_handler is not None:
            line = buffer.get_line()
            if line:
                file_log_handler.handle(create_stdout_log_record(line, passthrough_prefix))

        if deadline is not None and deadline >= monotonic() and process.poll() is None:
            process.kill()
            raise subprocess.TimeoutExpired(process.args, timeout)
    else:
        process.wait(timeout)
    exit_code = process.returncode

    if exit_code != 0:
        message = "Command '%s' failed, exit code: %s" % (command, exit_code)
        if not passthrough:
            # noinspection PyUnresolvedReferences
            message += ", output: %s" % process.stdout.read().decode("utf-8")
        raise ProcessException(message)

    if passthrough:
        return exit_code
    else:
        # noinspection PyUnresolvedReferences
        return process.stdout.read().decode("utf-8")


class ProcessException(Exception):
    pass


class TerminalLineBuffer:
    last_value: bytes

    def __init__(self):
        self.line_buffer = b""
        # ANSI & carriage return
        self.control_sequences_regex = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])|\x0D")

    def feed(self, value: bytes):
        self.last_value = value
        self.line_buffer += value

    def is_complete(self):
        return self.last_value == b"\n"

    def get_line(self):
        line = self.line_buffer.decode("utf-8")
        self.line_buffer = b""
        line = line.replace("\r\n", "\n")
        line = self.control_sequences_regex.sub("", line)
        return line


def create_stdout_log_record(message, passthrough_prefix=None, level=logging.INFO):
    message = message.rstrip()
    if passthrough_prefix is not None:
        message = "%s%s" % (passthrough_prefix, message)
    return logging.LogRecord("root", level, "", 0, message, None, None, None)


class LessThanLevelFilter(logging.Filter):
    def __init__(self, exclusive_maximum, name="LessThanLevelFilter"):
        super(LessThanLevelFilter, self).__init__(name)
        self.maximum_level = exclusive_maximum

    def filter(self, record):
        return 1 if record.levelno < self.maximum_level else 0


def setup_logging(name="test"):
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    stderr_level = logging.WARNING

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.INFO)
    stdout_handler.addFilter(LessThanLevelFilter(stderr_level))
    stdout_handler.setFormatter(formatter)
    logger.addHandler(stdout_handler)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(stderr_level)
    stderr_handler.setFormatter(formatter)
    logger.addHandler(stderr_handler)

    log_file = os.path.join(project_dir, "build", "%s.log" % name)
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=1048576 * 10,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.log_file = log_file
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    logger.addHandler(file_handler)


def find_file_log_handler():
    file_log_handler = None
    for handler in logging.getLogger().handlers:
        if isinstance(handler, RotatingFileHandler):
            file_log_handler = handler
            break
    return file_log_handler


def get_my_log_file():
    file_log_handler = find_file_log_handler()
    if file_log_handler is not None and hasattr(file_log_handler, "log_file"):
        return file_log_handler.log_file
    return "can't find it"


def refuse_root():
    if os.geteuid() == 0:
        logging.error(
            "ERROR: 'root' user detected, please don't run this script as root,"
            " run as any other regular user that has docker access (usermod -aG docker YOUR_USER),"
            " the root privileges would break some packages.")
        exit(1)
