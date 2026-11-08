"""Install app handlers so exactly one copy of each is live.

Handlers are `@persistent`, so a copy left by a reload (F3, addon update) would survive and run
twice per frame. Matching on name and module catches the stale copy, which `in` cannot.
"""


def _same(handler, function):
    return (getattr(handler, "__name__", None) == function.__name__
            and getattr(handler, "__module__", None) == function.__module__)


def install(handlers, function):
    """Put `function` in `handlers`, removing any earlier copy of it first."""
    for handler in [h for h in handlers if _same(h, function)]:
        handlers.remove(handler)
    handlers.append(function)


def remove(handlers, function):
    """Take `function` and any earlier copy of it out of `handlers`."""
    for handler in [h for h in handlers if _same(h, function)]:
        handlers.remove(handler)
