"""Kite and futures-paper settings. No broker order credentials."""

import logging

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s",
)
logger = logging.getLogger("FuturesPaper.Config")


class Settings(BaseSettings):
    ZERODHA_API_KEY: str = Field(..., description="Kite Connect app API key")
    ZERODHA_API_SECRET: str = Field(..., description="Kite Connect app API secret")
    ZERODHA_REDIRECT_URL: str = Field(
        "http://127.0.0.1:7860/api/v1/auth/callback",
        description="Must match the redirect URL registered in the Kite developer console",
    )
    TOKEN_CACHE_FILE: str = Field(
        "token_store.json", description="JSON file caching the daily Kite access token"
    )

    NIFTY_FUT_SYMBOL: str = Field("", description="Fallback NIFTY futures symbol. Empty resolves from Kite.")
    NIFTY_FUT_AUTO_RESOLVE: bool = Field(True)
    NIFTY_FUT_LOT_SIZE: int = Field(65, ge=1)
    NIFTY_OPTION_SYMBOL: str = Field("")
    NIFTY_OPTION_TYPE: str = Field("CE")
    NIFTY_OPTION_EXPIRY_DATE: str = Field("")
    NIFTY_OPTION_STRIKE: float = Field(0)
    NIFTY_OPTION_LOT_SIZE: int = Field(65, ge=1)
    NIFTY_OPTION_AUTO_RESOLVE: bool = Field(True)

    STRATEGY_QTY: int = Field(0, ge=0)
    STRATEGY_LOTS: int = Field(1, ge=1)
    STRATEGY_TIMEFRAME_MINUTES: int = Field(1, ge=1)
    STRATEGY_WARMUP_DAYS: int = Field(5, ge=1, le=30)
    FUT_SIGNAL_HOLD_SECONDS: float = Field(15.0, ge=0)
    FUT_STOP_POINTS: float = Field(15.0, ge=0)
    TV_WEBHOOK_ENABLED: bool = Field(False)

    GROQ_API_KEY: str = Field("", description="Optional. Converts pasted Pine Script. Set as a Space secret.")
    GROQ_BASE_URL: str = Field("https://api.groq.com/openai/v1")
    GROQ_MODEL: str = Field("openai/gpt-oss-120b")
    NVIDIA_API_KEY: str = Field("")
    NVIDIA_BASE_URL: str = Field("https://integrate.api.nvidia.com/v1")
    NVIDIA_MODEL: str = Field("deepseek-ai/deepseek-v4.1-flash")

    ORDERS_ALLOWED_EXCHANGE: str = Field("NFO")
    ORDERS_ALLOWED_EXCHANGES: str = Field("NSE,NFO")
    ORDERS_ALLOWED_SYMBOLS: str = Field("NIFTY 50")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("ZERODHA_API_KEY", "ZERODHA_API_SECRET")
    @classmethod
    def kite_not_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Credential value cannot be empty.")
        return value.strip()

    @property
    def allowed_symbols(self) -> set[str]:
        symbols = {s.strip().upper() for s in self.ORDERS_ALLOWED_SYMBOLS.split(",") if s.strip()}
        fut = self.NIFTY_FUT_SYMBOL.strip().upper()
        if fut:
            symbols.add(fut)
        opt = self.NIFTY_OPTION_SYMBOL.strip().upper()
        if opt:
            symbols.add(opt)
        return symbols

    @property
    def allowed_exchanges(self) -> set[str]:
        raw = self.ORDERS_ALLOWED_EXCHANGES or self.ORDERS_ALLOWED_EXCHANGE
        return {e.strip().upper() for e in raw.split(",") if e.strip()}

    def order_quantity_for(self, *, is_fo: bool) -> int:
        if self.STRATEGY_QTY and self.STRATEGY_QTY > 0:
            return int(self.STRATEGY_QTY)
        if is_fo:
            lot = int(self.NIFTY_OPTION_LOT_SIZE or self.NIFTY_FUT_LOT_SIZE)
            return int(self.STRATEGY_LOTS) * lot
        return max(1, int(self.STRATEGY_LOTS))


try:
    settings = Settings()
    logger.info("Configuration loaded successfully.")
except Exception as err:
    logger.critical("Startup aborted — invalid configuration: %s", err)
    raise SystemExit(1)
