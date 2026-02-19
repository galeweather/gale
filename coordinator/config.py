"""Gale Coordinator — Configuration from environment variables."""

import os


class Settings:
    """All config from env vars. No defaults for secrets."""

    # R2 / S3 credentials
    R2_ACCOUNT_ID: str = os.environ.get("R2_ACCOUNT_ID", "")
    R2_ACCESS_KEY_ID: str = os.environ.get("R2_ACCESS_KEY_ID", "")
    R2_SECRET_ACCESS_KEY: str = os.environ.get("R2_SECRET_ACCESS_KEY", "")
    R2_BUCKET: str = os.environ.get("R2_BUCKET", "gale-tiles")
    R2_PUBLIC_URL: str = os.environ.get(
        "R2_PUBLIC_URL",
        "https://pub-9975b1cfcbf9480f9e1333c7e208a6ce.r2.dev",
    )

    # Coordinator secret — workers present this to register
    COORDINATOR_SECRET: str = os.environ.get("COORDINATOR_SECRET", "")

    # Database
    DB_PATH: str = os.environ.get("DB_PATH", "/data/gale.db")

    # HRRR
    NOMADS_BASE: str = (
        "https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl"
    )
    HRRR_CHECK_INTERVAL_SECONDS: int = int(
        os.environ.get("HRRR_CHECK_INTERVAL_SECONDS", "600")
    )

    # Packet settings
    ASSIGNMENT_TIMEOUT_SECONDS: int = int(
        os.environ.get("ASSIGNMENT_TIMEOUT_SECONDS", "900")
    )
    PRESIGNED_URL_EXPIRY_SECONDS: int = int(
        os.environ.get("PRESIGNED_URL_EXPIRY_SECONDS", "1800")
    )
    CREDITS_PER_PACKET: int = int(
        os.environ.get("CREDITS_PER_PACKET", "10")
    )

    @property
    def r2_endpoint(self) -> str:
        return f"https://{self.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"


settings = Settings()
