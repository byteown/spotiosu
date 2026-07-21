"""pp calculation and mod handling.

Uses rosu-pp-py (a binding to the same performance algorithm osu! uses) to
compute pp from a downloaded .osu file. If the library is unavailable or a
calculation fails, callers fall back to star-rating-only output.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("spotiosu.pp")

try:  # rosu-pp-py is optional; the bot still works (star-only) without it.
    from rosu_pp_py import Beatmap, Performance  # type: ignore

    _HAS_ROSU = True
except Exception:  # noqa: BLE001
    _HAS_ROSU = False


# Classic osu! mod bitflags (only the ones that affect pp / are commonly used).
MOD_BITS = {
    "NF": 1, "EZ": 2, "TD": 4, "HD": 8, "HR": 16, "SD": 32,
    "DT": 64, "RX": 128, "HT": 256, "NC": 512, "FL": 1024,
    "SO": 4096, "PF": 16384,
}

DEFAULT_ACCS = (95.0, 98.0, 99.0, 100.0)


def parse_mods(mod_string: str | None) -> tuple[list[str], int]:
    """Turn a string like 'hdhr' or 'HD DT' into (acronyms, bitflag)."""
    if not mod_string:
        return [], 0
    tokens = re.findall(r"[A-Za-z]{2}", mod_string.upper())
    acronyms: list[str] = []
    bits = 0
    for tok in tokens:
        if tok in MOD_BITS and tok not in acronyms:
            acronyms.append(tok)
            bits |= MOD_BITS[tok]
    # NC implies DT for the difficulty/rate change.
    if "NC" in acronyms and "DT" not in acronyms:
        bits |= MOD_BITS["DT"]
    return acronyms, bits


def format_mods(acronyms: list[str]) -> str:
    return "+" + "".join(acronyms) if acronyms else "+NoMod"


class PpCalculator:
    """Computes pp for a beatmap at several accuracies for the given mods."""

    available = _HAS_ROSU

    def compute(
        self,
        osu_file_text: str,
        mods_bits: int = 0,
        accs: tuple[float, ...] = DEFAULT_ACCS,
    ) -> dict[float, float] | None:
        """Return {accuracy: pp} or None if calculation is not possible."""
        if not _HAS_ROSU:
            return None
        try:
            bmap = Beatmap(content=osu_file_text)
        except Exception:  # noqa: BLE001
            log.debug("Failed to parse .osu content", exc_info=True)
            return None
        results: dict[float, float] = {}
        for acc in accs:
            try:
                perf = Performance(accuracy=acc, mods=mods_bits)
                attrs = perf.calculate(bmap)
                results[acc] = round(float(attrs.pp), 1)
            except Exception:  # noqa: BLE001
                log.debug("pp calc failed at acc=%s", acc, exc_info=True)
                return None
        return results

    def stars(self, osu_file_text: str, mods_bits: int = 0) -> float | None:
        if not _HAS_ROSU:
            return None
        try:
            bmap = Beatmap(content=osu_file_text)
            attrs = Performance(mods=mods_bits).calculate(bmap)
            return round(float(attrs.difficulty.stars), 2)
        except Exception:  # noqa: BLE001
            return None
