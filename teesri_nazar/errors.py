"""Exceptions the CLI turns into exit codes."""


class UserError(Exception):
    pass


class NotFound(Exception):
    pass
