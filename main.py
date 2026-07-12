import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from src.logging_service import configure_logging


PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")


def get_bool(name, default=False):
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "sim"}


@dataclass(frozen=True)
class AppConfig:
    cdp_port: int
    headless: bool
    close_chrome: bool
    reset_tabs: bool
    feed_timeout: int
    max_restarts: int
    model_file: Path
    logs_directory: str

    @classmethod
    def from_environment(cls):
        config = cls(
            cdp_port=int(os.getenv("BET365_CDP_PORT", "9222")),
            headless=get_bool("BET365_HEADLESS"),
            close_chrome=get_bool("BET365_CLOSE_ALL_CHROME", True),
            reset_tabs=get_bool("BET365_RESET_TABS", True),
            feed_timeout=int(os.getenv("BET365_STALL_TIMEOUT_SECONDS", "60")),
            max_restarts=int(os.getenv("BET365_MAX_RESTARTS", "3")),
            model_file=PROJECT_ROOT / "v12_sniper_final.pkl",
            logs_directory=os.getenv("LOGS_DIRECTORY", "logs"),
        )
        config.validate()
        return config

    def validate(self):
        if not 1 <= self.cdp_port <= 65535:
            raise ValueError("BET365_CDP_PORT deve estar entre 1 e 65535.")
        if self.feed_timeout < 1:
            raise ValueError("BET365_STALL_TIMEOUT_SECONDS deve ser maior que zero.")
        if self.max_restarts < 0:
            raise ValueError("BET365_MAX_RESTARTS não pode ser negativo.")


class Application:
    def __init__(self, config):
        self.config = config

    def show_summary(self):
        mode = "completo (V12)" if self.config.model_file.exists() else "coleta Bet365 (sem modelo V12)"
        interface = "headless" if self.config.headless else "visível"
        print("=" * 50)
        print(f"🚀 Robô | modo: {mode}")
        print(f"🌐 Chrome: {interface} | CDP: {self.config.cdp_port}")
        print(f"🛡️ Watchdog: {self.config.feed_timeout}s | reinícios: {self.config.max_restarts}")
        print("=" * 50)

    def run(self):
        logger, log_file = configure_logging(PROJECT_ROOT, self.config.logs_directory)
        logger.info("Aplicação iniciada.")
        self.show_summary()
        try:
            from src.monitor import start_monitor
            start_monitor()
        except Exception:
            logger.exception("Erro não tratado na execução do robô.")
            raise
        finally:
            logger.info("Aplicação finalizada. Log salvo em: %s", log_file)


def main():
    Application(AppConfig.from_environment()).run()


if __name__ == "__main__":
    main()
