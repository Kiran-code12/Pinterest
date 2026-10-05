import logging


class SecretRedactingFilter(logging.Filter):
    """Masks known secret values in every log record."""

    def __init__(self, secrets_list: list[str]):
        super().__init__()
        self.secrets = sorted(set(secrets_list), key=len, reverse=True)

    def filter(self, record: logging.LogRecord) -> bool:
        if self.secrets:
            msg = record.getMessage()
            for s in self.secrets:
                msg = msg.replace(s, "***")
            record.msg, record.args = msg, None
        return True


def setup_logging(secret_values: list[str], level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for h in root.handlers:
        if not any(isinstance(f, SecretRedactingFilter) for f in h.filters):
            h.addFilter(SecretRedactingFilter(secret_values))
