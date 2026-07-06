"""Settings loader with environment variable substitution.

Reads `config/settings.yaml`, substitutes `${VAR:default}` patterns, and
returns a typed config object. Used by every entrypoint.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

CONFIG_PATH = Path(__file__).parent / "settings.yaml"
_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)(?::([^}]*))?\}")


def _substitute(value: Any) -> Any:
    if isinstance(value, str):
        def repl(m: re.Match) -> str:
            var, default = m.group(1), m.group(2) or ""
            return os.environ.get(var, default)
        return _ENV_RE.sub(repl, value)
    if isinstance(value, list):
        return [_substitute(v) for v in value]
    if isinstance(value, dict):
        return {k: _substitute(v) for k, v in value.items()}
    return value


class SNMPConfig(BaseModel):
    community: str
    version: str = "v2c"
    v3_user: str = ""
    v3_auth_key: str = ""
    v3_priv_key: str = ""
    timeout_s: int = 2
    retries: int = 2


class ONVIFConfig(BaseModel):
    ws_discovery_multicast: str
    ws_discovery_port: int
    probe_timeout_s: int = 5


class BioStarConfig(BaseModel):
    base_url: str
    username: str = ""
    password: str = ""


class HoneywellConfig(BaseModel):
    export_dir: str


class Tier1Config(BaseModel):
    snmp: SNMPConfig
    onvif: ONVIFConfig
    biostar: BioStarConfig
    honeywell: HoneywellConfig


class Tier2Config(BaseModel):
    failure_window_minutes: int
    delta_minutes: int
    min_lift: float
    min_sequence_support: int


class DiscoveryConfig(BaseModel):
    tier1: Tier1Config
    tier2: Tier2Config


class PostgresConfig(BaseModel):
    host: str
    port: int
    user: str
    password: str
    database: str


class Neo4jConfig(BaseModel):
    uri: str
    user: str
    password: str
    database: str = "neo4j"


class SparkConfig(BaseModel):
    app_name: str
    master: str
    executor_memory: str
    shuffle_partitions: int = 200


class ConfidenceConfig(BaseModel):
    auto_accept: float
    auto_flag: float
    suggest: float
    reject: float


class RefreshConfig(BaseModel):
    cadence: str
    drift_alert_webhook: str = ""
    drift_lift_threshold: float = 5.0


class LoggingConfig(BaseModel):
    level: str = "INFO"
    json: bool = True


class Settings(BaseModel):
    postgres: PostgresConfig
    neo4j: Neo4jConfig
    spark: SparkConfig
    discovery: DiscoveryConfig
    confidence: ConfidenceConfig
    refresh: RefreshConfig
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


def load_settings(path: Path | None = None) -> Settings:
    p = path or CONFIG_PATH
    raw = yaml.safe_load(p.read_text())
    return Settings.model_validate(_substitute(raw))
