"""Errores públicos sin valores de entrada ni excepciones de dependencias."""


class APIError(Exception):
    def __init__(self, code: str, status: int, message: str = "Request rejected.") -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.message = message


def unavailable() -> APIError:
    return APIError("TEMPORARY_UNAVAILABLE", 503, "Service temporarily unavailable.")
