"""Errors safe to expose through the chat protocol."""


class ModelError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
