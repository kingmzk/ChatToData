"""Centralized configuration and environment settings for Talk2Data."""
from __future__ import annotations
import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


def get_db_path() -> str:
    """Return configured SQLite database file path or default."""
    return os.getenv("TALK2DATA_DB_PATH", "talk2data.db")


def get_env_mode() -> str:
    """Return environment mode: 'development' or 'production'."""
    return os.getenv("TALK2DATA_ENV", "development").lower()


def get_auth_secret() -> Optional[str]:
    """Return HMAC secret key for production bearer token verification."""
    return os.getenv("TALK2DATA_AUTH_SECRET")


def get_canonical_model() -> str:
    """Return canonical model identifier with compatibility alias support.
    
    Precedence:
    1. TALK2DATA_MODEL (canonical)
    2. GEMINI_MODEL (compatibility alias)
    3. 'gemini-3.5-flash' (default)
    """
    return os.getenv("TALK2DATA_MODEL") or os.getenv("GEMINI_MODEL") or "gemini-3.5-flash"


def get_gemini_api_key() -> Optional[str]:
    """Return Gemini API key if configured."""
    return os.getenv("GEMINI_API_KEY")
