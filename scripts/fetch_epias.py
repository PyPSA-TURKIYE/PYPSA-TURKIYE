# SPDX-FileCopyrightText: 2026 The PyPSA-Türkiye Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
EPİAŞ Şeffaflık 2.0 saatlik veri indirici (PyPSA-TR için).

Kullanım:
    pip install eptr2 python-dotenv pandas pyarrow
    cp .env.example .env   # ve içine EPTR_USERNAME / EPTR_PASSWORD yaz
    python scripts/fetch_epias.py
    python scripts/fetch_epias.py --start 2018-01-01 --end 2024-12-31

Çıktılar (data/epias/ altına .parquet olarak):
    consumption_YYYY.parquet     - Saatlik gerçek tüketim (MWh)
    generation_YYYY.parquet      - Saatlik gerçek üretim, kaynak bazında
    mcp_YYYY.parquet             - Gün öncesi piyasa takas fiyatı (TL/MWh)

Rate limit koruması:
    - EPİAŞ limit: 100 istek/dk, 10 istek/sn (kullanıcı başına).
    - Script: aylık parçalar + her istek arası 0.8 sn bekleme → ~75 istek/dk.
    - HTTP 429 / rate-limit yanıtlarında exponential backoff (1, 2, 4, 8, 16, 32 sn).
    - Üst güvenlik: dakika başına en fazla MAX_REQ_PER_MIN istek (sürgülü pencere).
    - TGT 8 saat geçerli; eptr2 otomatik yeniler.
    - 2018'den önceki veriler için EPİAŞ kapsama eksik olabilir.
"""

from __future__ import annotations

import argparse
import collections
import logging
import os
import random
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

try:
    from eptr2 import EPTR2
    from eptr2.composite import get_hourly_consumption_and_forecast_data
except ImportError:
    sys.stderr.write("eptr2 kurulu değil. Kur: pip install eptr2 python-dotenv pyarrow\n")
    sys.exit(1)


def _make_direct_fetcher(call_key: str):
    """eptr.call(key, ...) için fetcher wrapper üret.

    Composite fonksiyonlar bazen iç mantıkla parametre kontrolü yapar
    (ör. get_hourly_production_data santral ID'si ister); direkt çağrı
    daha öngörülebilirdir ve Türkiye toplamı için doğru olandır.
    """

    def _fetcher(start_date: str, end_date: str, eptr: EPTR2, **_) -> pd.DataFrame:
        return eptr.call(call_key, start_date=start_date, end_date=end_date)

    _fetcher.__name__ = f"call_{call_key}"
    return _fetcher


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fetch_epias")


SCRIPT_DIR = Path(__file__).resolve().parent
# .env arama sırası: script ile aynı klasör → bir üst klasör (repo kökü) → cwd.
# İlk bulunan kullanılır; yoksa "yok" hatası.
_ENV_CANDIDATES = [
    SCRIPT_DIR / ".env",
    SCRIPT_DIR.parent / ".env",
    Path.cwd() / ".env",
]


def _find_env() -> Path | None:
    for p in _ENV_CANDIDATES:
        if p.exists():
            return p
    return None


_ENV_PATH = _find_env()
# Çıktı klasörü: .env nerede bulunduysa onun yanına data/epias/ koy.
# Bulunamazsa script ile aynı klasör + data/epias/.
_BASE_DIR = _ENV_PATH.parent if _ENV_PATH else SCRIPT_DIR
OUTPUT_DIR = _BASE_DIR / "data" / "epias"

REQUEST_INTERVAL_S = 1.0       # her istek arası taban bekleme (~55 istek/dk)
MAX_REQ_PER_MIN = 70           # EPİAŞ gerçek limit 80/dk (log'dan doğrulandı); ~%12 pay
MAX_RETRIES = 6                # 429 / geçici hata için yeniden deneme sayısı
BACKOFF_BASE_S = 1.0           # exponential backoff: 1, 2, 4, 8, 16, 32 sn


_request_times: collections.deque = collections.deque()


def _throttle() -> None:
    """Sürgülü pencere: son 60 sn içinde MAX_REQ_PER_MIN'i aşmasın."""
    now = time.monotonic()
    while _request_times and now - _request_times[0] > 60.0:
        _request_times.popleft()
    if len(_request_times) >= MAX_REQ_PER_MIN:
        wait = 60.0 - (now - _request_times[0]) + 0.5
        log.info("  rate-limit penceresi dolu, %.1f sn bekleniyor", wait)
        time.sleep(wait)
    _request_times.append(time.monotonic())


def _is_rate_limit_error(exc: Exception) -> bool:
    """eptr2 hatasından 429 / 'too many requests' / 'rate' tespit et."""
    msg = str(exc).lower()
    return any(k in msg for k in ("429", "too many", "rate limit", "rate-limit", "throttl"))


def safe_call(fetcher, eptr, **kwargs) -> pd.DataFrame | None:
    """Rate-limit'e saygılı + retry'lı tekil çağrı.

    eptr2 composite imzası: (start_date, end_date, eptr=None, ...) — eptr keyword.
    """
    for attempt in range(MAX_RETRIES):
        _throttle()
        try:
            df = fetcher(eptr=eptr, **kwargs)
            time.sleep(REQUEST_INTERVAL_S + random.uniform(0, 0.2))  # taban bekleme + jitter
            return df
        except Exception as exc:
            if _is_rate_limit_error(exc) and attempt < MAX_RETRIES - 1:
                wait = BACKOFF_BASE_S * (2 ** attempt) + random.uniform(0, 1.0)
                log.warning("  429 / rate-limit (deneme %d/%d) — %.1f sn bekleniyor: %s",
                            attempt + 1, MAX_RETRIES, wait, exc)
                time.sleep(wait)
                continue
            if attempt < MAX_RETRIES - 1:
                wait = BACKOFF_BASE_S * (2 ** attempt)
                log.warning("  geçici hata (deneme %d/%d), %.1f sn sonra: %s",
                            attempt + 1, MAX_RETRIES, wait, exc)
                time.sleep(wait)
                continue
            log.error("  başarısız (%d deneme): %s", MAX_RETRIES, exc)
            return None
    return None


def month_ranges(start: date, end: date):
    """Tarih aralığını ay ay parçalara böl (API yükünü azaltmak için)."""
    cur = date(start.year, start.month, 1)
    while cur <= end:
        next_month = date(cur.year + (cur.month // 12), ((cur.month % 12) + 1), 1)
        chunk_start = max(cur, start)
        chunk_end = min(next_month - pd.Timedelta(days=1).to_pytimedelta(), end)
        yield chunk_start, chunk_end
        cur = next_month


def fetch_yearly(eptr: EPTR2, fetcher, year: int, label: str) -> pd.DataFrame:
    """Bir yılı aylık parçalar hâlinde çek, birleştirip döndür."""
    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)
    frames: list[pd.DataFrame] = []
    for chunk_start, chunk_end in month_ranges(year_start, year_end):
        log.info(
            "  %s: %s → %s çekiliyor", label, chunk_start.isoformat(), chunk_end.isoformat()
        )
        df = safe_call(
            fetcher,
            eptr,
            start_date=chunk_start.isoformat(),
            end_date=chunk_end.isoformat(),
        )
        if df is None or df.empty:
            log.warning("  boş yanıt / başarısız: %s", chunk_start)
            continue
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def save_parquet(df: pd.DataFrame, path: Path) -> None:
    if df.empty:
        log.warning("boş — yazılmadı: %s", path.name)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, compression="zstd")
    log.info("yazıldı: %s (%d satır)", path, len(df))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="EPİAŞ Şeffaflık 2.0 veri indirici")
    p.add_argument("--start", default="2018-01-01", help="Başlangıç (YYYY-MM-DD)")
    p.add_argument("--end", default="2024-12-31", help="Bitiş (YYYY-MM-DD)")
    p.add_argument(
        "--skip",
        nargs="*",
        default=[],
        choices=["consumption", "generation", "mcp"],
        help="Atlanacak veri kümeleri",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    if _ENV_PATH is None:
        log.error(".env bulunamadı. Şu yerlerden birine koy:")
        for c in _ENV_CANDIDATES:
            log.error("    %s", c)
        sys.exit(2)
    log.info(".env okundu: %s", _ENV_PATH)
    load_dotenv(_ENV_PATH, override=True)

    user = os.getenv("EPTR_USERNAME")
    pwd = os.getenv("EPTR_PASSWORD")
    if not user or not pwd:
        log.error("%s içinde EPTR_USERNAME ve EPTR_PASSWORD tanımlı değil.", _ENV_PATH)
        sys.exit(2)

    log.info("EPİAŞ'a bağlanılıyor (kullanıcı: %s)…", user)
    # use_dotenv=False: eptr2 kendi .env okumasın (Windows'ta cp1254 encoding çakışması).
    # Kimlikler zaten yukarıda load_dotenv ile env'e yüklendi + parametre olarak veriliyor.
    eptr = EPTR2(username=user, password=pwd, use_dotenv=False)

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    datasets = {
        "consumption": ("Tüketim (saatlik)", get_hourly_consumption_and_forecast_data),
        "generation": ("Üretim (saatlik, kaynak bazında)", _make_direct_fetcher("rt-gen")),
        "mcp": ("MCP / PTF (gün öncesi)", _make_direct_fetcher("mcp")),
    }

    for key, (label, fetcher) in datasets.items():
        if key in args.skip:
            log.info("ATLANDI: %s", label)
            continue
        log.info("İndiriliyor: %s", label)
        for year in range(start.year, end.year + 1):
            out_path = OUTPUT_DIR / f"{key}_{year}.parquet"
            if out_path.exists():
                log.info("  zaten var, atlanıyor: %s", out_path.name)
                continue
            df = fetch_yearly(eptr, fetcher, year, label)
            save_parquet(df, out_path)

    log.info("Bitti. Dosyalar: %s", OUTPUT_DIR)


if __name__ == "__main__":
    main()
