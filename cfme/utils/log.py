"""Logging framework

This module creates the cfme logger, for use throughout the project. This logger only captures log
messages explicitly sent to it, not logs emitted by other components (such as selenium). To capture
those, consider using the pytest-capturelog plugin.

Example Usage
^^^^^^^^^^^^^

.. code-block:: python

    from utils.log import logger

    logger.debug('debug log message')
    logger.info('info log message')
    logger.warning('warning log message')
    logger.error('error log message')
    logger.critical('critical log message')

The above will result in the following output in ``cfme_tests/logs/cfme.log``::

    1970-01-01 00:00:00,000 [D] debug log message (filename.py:3)
    1970-01-01 00:00:00,000 [I] info log message (filename.py:4)
    1970-01-01 00:00:00,000 [W] warning log message (filename.py:5)
    1970-01-01 00:00:00,000 [E] error log message (filename.py:6)
    1970-01-01 00:00:00,000 [C] fatal log message (filename.py:7)

Additionally, if ``log_error_to_console`` is True (see below), the following will be
written to stderr::

    [E] error (filename.py:6)
    [C] fatal (filename.py:7)

Log Message Source
^^^^^^^^^^^^^^^^^^

We have added a custom log record attribute that can be used in log messages: ``%(source)s`` This
attribute is included in the default 'cfme' logger configuration.

This attribute will be generated by default and include the filename and line number from where the
log message was emitted. It will attempt to convert file paths to be relative to cfme_tests, but use
the absolute file path if a relative path can't be determined.

When writting generic logging facilities, it is sometimes helpful to override
those source locations to make the resultant log message more useful. To do so, pass the extra
``source_file`` (str) and ``source_lineno`` (int) to the log emission::

    logger.info('info log message', extra={'source_file': 'somefilename.py', 'source_lineno': 7})

If ``source_lineno`` is ``None`` and ``source_file`` is included, the line number will be omitted.
This is useful in cases where the line number can't be determined or isn't necessary.

Configuration
^^^^^^^^^^^^^

.. code-block:: yaml

    # in env.yaml
    logging:
        # Can be one of DEBUG, INFO, WARNING, ERROR, CRITICAL
        level: INFO
        # Maximum logfile size, in bytes, before starting a new logfile
        # Set to 0 to disable log rotation
        max_logfile_size: 0
        # Maximimum backup copies to make of rotated log files (e.g. cfme.log.1, cfme.log.2, ...)
        # Set to 0 to keep no backups
        max_logfile_backups: 0
        # If True, messages of level ERROR and CRITICAL are also written to stderr
        errors_to_console: False
        # Default file format
        file_format: "%(asctime)-15s [%(levelname).1s] %(message)s (%(source)s)"
        # Default format to console if errors_to_console is True
        stream_format: "[%(levelname)s] %(message)s (%(source)s)"

Additionally, individual logger configurations can be overridden by defining nested configuration
values using the logger name as the configuration key. Note that the name of the logger objects
exposed by this module don't obviously line up with their key in ``cfme_data``. The 'name' attribute
of loggers can be inspected to get this value::

    >>> utils.log.logger.name
    'cfme'
    >>> utils.log.perflog.logger.name
    'perf'

Here's an example of those names being used in ``env.local.yaml`` to configure loggers
individually:

.. code-block:: yaml

    logging:
        cfme:
            # set the cfme log level to debug
            level: DEBUG
        perf:
            # make the perflog a little more "to the point"
            file_format: "%(message)s"

Notes:

* The ``cfme`` and ``perf`` loggers are guaranteed to exist when using this module.
* The name of a logger is used to generate its filename, and will usually not have the word
  "log" in it.

  * ``perflog``'s logger name is ``perf`` for this reason, resulting in ``log/perf.log``
    instead of ``log/perflog.log``.
  * Similarly, ``logger``'s' name is ``cfme``, to prevent having ``log/logger.log``.

.. warning::

    Creating a logger with the same name as one of the default configuration keys,
    e.g. ``create_logger('level')`` will cause a rift in space-time (or a ValueError).

    Do not attempt.

Message Format
^^^^^^^^^^^^^^

    ``year-month-day hour:minute:second,millisecond [Level] message text (file:linenumber)``

``[Level]``:

    One letter in square brackets, where ``[I]`` corresponds to INFO, ``[D]`` corresponds to
    DEBUG, and so on.

``(file:linenumber)``:

    The relative location from which this log message was emitted. Paths outside

Members
^^^^^^^

"""
import inspect
import logging
import sys
import warnings
from time import time
from traceback import extract_tb, format_tb

from cfme.utils import conf, safe_string
from cfme.utils.path import get_rel_path, log_path, project_path

import os

MARKER_LEN = 80

# set logging defaults
_default_conf = {
    'level': 'INFO',
    'errors_to_console': False,
    'to_console': False,
}

# let logging know we made a TRACE level
logging.TRACE = 5
logging.addLevelName(logging.TRACE, 'TRACE')


class logger_wrap(object):
    """ Sets up the logger by default, used as a decorator in utils.appliance

    If the logger doesn't exist, sets up a sensible alternative
    """
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def __call__(self, func):
        def newfunc(*args, **kwargs):
            cb = kwargs.get('log_callback')
            if not cb:
                cb = logger.info
            kwargs['log_callback'] = lambda msg: cb(self.args[0].format(msg))
            return func(*args, **kwargs)
        return newfunc


class TraceLogger(logging.Logger):
    """A trace-loglevel-aware :py:class:`Logger <python:logging.Logger>`"""
    def trace(self, msg, *args, **kwargs):
        """
        Log 'msg % args' with severity 'TRACE'.

        """
        if self.isEnabledFor(logging.TRACE):
            self._log(logging.TRACE, msg, args, **kwargs)


logging._loggerClass = TraceLogger


class TraceLoggerAdapter(logging.LoggerAdapter):
    """A trace-loglevel-aware :py:class:`LoggerAdapter <python:logging.LoggerAdapter>`"""
    def trace(self, msg, *args, **kwargs):
        """
        Delegate a trace call to the underlying logger, after adding
        contextual information from this adapter instance.
        """
        msg, kwargs = self.process(msg, kwargs)
        self.logger.trace(msg, *args, **kwargs)


class PrefixAddingLoggerFilter(logging.Filter):
    def __init__(self, prefix=None):
        self.prefix = prefix

    def filter(self, record):
        if self.prefix:
            record.msg = "{0}{1}".format(safe_string(self.prefix), safe_string(record.msg))
        return True


class NamedLoggerAdapter(TraceLoggerAdapter):
    """An adapter that injects a name into log messages"""
    def process(self, message, kwargs):
        return '({}) {}'.format(self.extra, message), kwargs


def _load_conf(logger_name=None):
    # Reload logging conf from env, then update the logging_conf
    try:
        del(conf['env'])
    except KeyError:
        # env not loaded yet
        pass

    logging_conf = _default_conf.copy()

    yaml_conf = conf.env.get('logging', {})
    # Update the defaults with values from env yaml
    logging_conf.update(yaml_conf)
    # Additionally, look in the logging conf for file-specific loggers
    if logger_name in logging_conf:
        logging_conf.update(logging_conf[logger_name])

    return logging_conf


class _RelpathFilter(logging.Filter):
    """Adds the relpath attr to records

    Not actually a filter, this was the least ridiculous way to add custom dynamic
    record attributes and reduce it all down to the ``source`` record attr.

    looks for 'source_file' and 'source_lineno' on the log record, falls back to builtin
    record attributes if they aren't found.

    """
    def filter(self, record):
        record.pathname = get_rel_path(record.pathname)
        return True


class WarningsRelpathFilter(logging.Filter):
    """filter to modify warnings messages, to use relative paths in the project"""
    def filter(self, record):
        if record.args:
            new_record = record.args[0].replace(project_path.strpath, '.')
            record.args = (new_record,) + record.args[1:]
        return True


class WarningsDeduplicationFilter(object):
    """
    this filter is needed since something in the codebase causes the warnings
    once filter to be reset, so we need to deduplicate on our own

    there is no indicative codepath that is clearly at fault
    so this low implementation cost solution was choosen to deduplicate off-band
    """
    def __init__(self):
        self.seen = set()

    def filter(self, record):
        msg = record.args[0].splitlines()[0].split(': ', 1)[-1]
        if msg in self.seen:
            return False
        else:
            self.seen.add(msg)
            return True


class Perflog(object):
    """Performance logger, useful for timing arbitrary events by name

    Logged events will be written to ``log/perf.log`` by default, unless
    a different log file name is passed to the Perflog initializer.

    Usage:

        from cfme.utils.log import perflog
        perflog.start('event_name')
        # do stuff
        seconds_taken = perflog.stop('event_name')
        # seconds_taken is also written to perf.log for later analysis

    """
    tracking_events = {}

    def __init__(self, perflog_name='perf'):
        self.logger, _ = setup_logger(logging.getLogger(perflog_name))

    def start(self, event_name):
        """Start tracking the named event

        Will reset the start time if the event is already being tracked

        """
        if event_name in self.tracking_events:
            self.logger.warning('"%s" event already started, resetting start time', event_name)
        else:
            self.logger.debug('"%s" event tracking started', event_name)
        self.tracking_events[event_name] = time()

    def stop(self, event_name):
        """Stop tracking the named event

        Returns:
            A float value of the time passed since ``start`` was last called, in seconds,
            *or* ``None`` if ``start`` was never called.

        """
        if event_name in self.tracking_events:
            seconds_taken = time() - self.tracking_events.pop(event_name)
            self.logger.info('"%s" event took %f seconds', event_name, seconds_taken)
            return seconds_taken
        else:
            self.logger.error('"%s" not being tracked, call .start first', event_name)
            return None


def make_file_handler(filename, root=log_path.strpath, level=None, **kw):
    filename = os.path.join(root, filename)
    handler = logging.FileHandler(filename, **kw)
    formatter = logging.Formatter(
        '%(asctime)-15s [%(levelname).1s] [%(name)s] %(message)s (%(pathname)s:%(lineno)s)')
    handler.setFormatter(formatter)
    if level is not None:
        handler.setLevel(level)
    return handler


def console_handler(level):
    formatter = logging.Formatter(
        '[%(levelname)s] [%(name)s] %(message)s (%(pathname)s:%(lineno)s)')
    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(formatter)
    return handler


def setup_logger(logger, file_handler=None):
    # prevent the root logger effective level from affecting us
    # this is a hack
    logger.setLevel(1)
    # prevent root logger handlers from triggering (its sad that we need this)
    logger.propagate = False
    # Grab the logging conf
    conf = _load_conf(logger.name)

    # log_file is dynamic, so we can't used logging.config.dictConfig here without creating
    # a custom RotatingFileHandler class. At some point, we should do that, and move the
    # entire logging config into env.yaml

    if not file_handler:
        file_handler = make_file_handler(logger.name + '.log', level=conf['level'])
    logger.addHandler(file_handler)

    if conf['errors_to_console']:
        logger.addHandler(console_handler(logging.ERROR))
    if conf['to_console']:
        logger.addHandler(console_handler(conf['to_console']))

    logger.addFilter(_RelpathFilter())
    return logger, file_handler


def create_sublogger(logger_sub_name):
    return NamedLoggerAdapter(logger, logger_sub_name)


def format_marker(mstring, mark="-"):
    """ Creates a marker in log files using a string and leader mark.

    This function uses the constant ``MARKER_LEN`` to determine the length of the marker,
    and then centers the message string between padding made up of ``leader_mark`` characters.

    Args:
        mstring: The message string to be placed in the marker.
        leader_mark: The marker character to use for leading and trailing.

    Returns: The formatted marker string.

    Note: If the message string is too long to fit one character of leader/trailer and
        a space, then the message is returned as is.
    """
    if len(mstring) <= MARKER_LEN - 2:
        # Pad with spaces
        mstring = ' {} '.format(mstring)
        # Format centered, surrounded the leader_mark
        format_spec = '{{:{leader_mark}^{marker_len}}}'\
            .format(leader_mark=mark, marker_len=MARKER_LEN)
        mstring = format_spec.format(mstring)
    return mstring


def _custom_excepthook(type, value, traceback):
    file, lineno, function, __ = extract_tb(traceback)[-1]
    text = ''.join(format_tb(traceback)).strip()
    logger.error('Unhandled %s', type.__name__)
    logger.error(text, extra={'source_file': file, 'source_lineno': lineno})
    _original_excepthook(type, value, traceback)


if '_original_excepthook' not in globals():
    # Guard the original excepthook against reloads so we don't hook twice
    _original_excepthook = sys.excepthook


def nth_frame_info(n):
    """
    Inspect the stack to determine the filename and lineno of the code running at the "n"th frame

    Args:
        n: Number of the stack frame to inspect

    Raises IndexError if the stack doesn't contain the nth frame (the caller should know this)

    Returns a frameinfo namedtuple as described in :py:func:`inspect <python:inspect.getframeinfo>`

    """
    # Inspect the stack with 1 line of context, looking at the "n"th frame to determine
    # the filename and line number of that frame
    return inspect.getframeinfo(inspect.stack(1)[n][0])


class ArtifactorHandler(logging.Handler):
    """Logger handler that hands messages off to the artifactor"""

    slaveid = artifactor = None

    def createLock(self):  # NOQA: false positive, base class override
        # opt out of locking since artifactor hook calling is threadsave
        self.lock = None

    def emit(self, record):
        if self.artifactor:
            self.artifactor.fire_hook(
                'log_message',
                log_record=record.__dict__,
                slaveid=self.slaveid,
            )


logger, cfme_file_handler = setup_logger(logging.getLogger('cfme'))
# Have wrapanapi log to the same FileHandler as cfme
wrapanapi_logger, _ = setup_logger(logging.getLogger('wrapanapi'), cfme_file_handler)
artifactor_handler = ArtifactorHandler()
logger.addHandler(artifactor_handler)
# Also have wrapanapi use the ArtifactorHandler to combine cfme+wrapanapi logging there
wrapanapi_logger.addHandler(artifactor_handler)

add_prefix = PrefixAddingLoggerFilter()
logger.addFilter(add_prefix)

perflog = Perflog()


def _configure_warnings():
    # Capture warnings
    warnings.simplefilter('once')
    # hack to avoid circular imports
    maybe_appliance = sys.modules.get('cfme.utils.appliance')
    if maybe_appliance is not None:
        # TODO opt-out, follow up with the location of configuring things
        # these currently cause warnings in case something bad happens
        # the followup will reposition the setup so it no longer incurrs the issues
        try:
            warnings.simplefilter(
                'ignore', maybe_appliance.NavigatableDeprecationWarning)
        except AttributeError:
            pass
        try:
            warnings.simplefilter('error',

                maybe_appliance.ApplianceSummoningWarning)
        except AttributeError:
            pass

    warnings.filterwarnings(
        'ignore', module='entrypoints',
        message=".*read_file.*", category=DeprecationWarning)
    logging.captureWarnings(True)
    wlog = logging.getLogger('py.warnings')
    wlog.addFilter(WarningsRelpathFilter())
    wlog.addFilter(WarningsDeduplicationFilter())
    wlog.addHandler(make_file_handler('py.warnings.log'))
    wlog.addHandler(console_handler(logging.INFO))
    wlog.propagate = False


def setup_for_worker(workername, loggers=('cfme', 'py.warnings', 'wrapanapi')):
    # this function is a bad hack, at some point we want a more ballanced setup
    for logger in loggers:
        log = logging.getLogger(logger)
        handler = next(x for x in log.handlers
                       if isinstance(x, logging.FileHandler))
        handler.close()
        base, name = os.path.split(handler.baseFilename)
        add_prefix.prefix = "({})".format(workername)
        handler.baseFilename = os.path.join(
            base, "{worker}-{name}".format(worker=workername, name=name))
        log.debug("worker log started")  # directly reopens the file


def add_stdout_handler(logger):
    """Look for a stdout handler in the logger, add one if not present"""
    for handle in logger.handlers:
        if isinstance(handle, logging.StreamHandler) and 'stdout' in handle.stream.name:
            break
    else:
        # Never found a stdout StreamHandler
        logger.addHandler(logging.StreamHandler(sys.stdout))


_configure_warnings()

# Register a custom excepthook to log unhandled exceptions
sys.excepthook = _custom_excepthook
