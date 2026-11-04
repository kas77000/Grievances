"""FIX-tag decoding for orders.

`target.fixmsg` (and `execution.fixtags`) is a string of `tag=value` pairs. From the Argo q
code (parentorders.q / f_fixtag) the top-level delimiter is ';' and some tags carry a nested
sub-message on '^'. The BCORE_Tags CSV (from the other database) maps tag numbers to meaning;
it is versioned, so we keep the latest version per tag.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

# Minimal built-in fallback dictionary so the tag inspector is useful before the real
# BCORE_Tags CSV is loaded. Superseded by the CSV when present.
BUILTIN_TAGS = {
    "9001": ("algorithm", "Specifies the algorithm of the order."),
    "9012": ("algorithm_override", "Algorithm via 9012; overrides 9001 if present."),
    "9006": ("pcap", "PCAP participation cap parameter."),
    "9007": ("tmx", "TMX venue parameter."),
    "9010": ("would_good_price", "I-would / would-good-price level."),
    "9603": ("clordid", "Client order id."),
    "82":   ("rel_limit", "Relative limit (inside 9012)."),
    "63":   ("settl_type", "Settlement type."),
    "64":   ("settl_date", "Settlement date."),
    "115":  ("onbehalf", "On-behalf-of / sender comp id."),
    "10100": ("asc_vol_min", "Scheduling: minimum volume."),
    "10101": ("asc_vol_max", "Scheduling: maximum volume."),
    "10106": ("asc_vol_target", "Scheduling: target volume."),
    "10102": ("asc_rel_price_limit", "Scheduling: relative price limit."),
    "10108": ("asc_exec_algo", "Scheduling: execution algorithm."),
    "15000": ("asc_ioi", "IOI indicator."),
}


def parse_fixmsg(fixmsg: Optional[str], sep: str = ";") -> dict[str, str]:
    """Split a fixmsg string into {tag: value}. Robust to None / empty / stray separators."""
    out: dict[str, str] = {}
    if not fixmsg or not isinstance(fixmsg, str):
        return out
    for item in fixmsg.split(sep):
        item = item.strip()
        if "=" in item:
            tag, val = item.split("=", 1)
            tag = tag.strip()
            if tag:
                out[tag] = val
    return out


def load_tag_dictionary(csv_path: str | Path) -> Optional[pd.DataFrame]:
    """Load BCORE_Tags CSV → one row per TagNumber (latest version). Returns None on failure.

    Expected columns (from the photographed header):
        TagNumber, version, tagName, interface, comments, availability, implemented,
        example, modifiedBy, modifiedTime, oldTagInfo, clientTagNumber, description,
        parametersField, validValues
    """
    path = Path(csv_path)
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    except Exception:
        return None
    if "TagNumber" not in df.columns:
        return None
    df["TagNumber"] = df["TagNumber"].astype(str).str.strip()
    if "modifiedTime" in df.columns:
        df["_mtime"] = pd.to_datetime(df["modifiedTime"], errors="coerce")
        df = df.sort_values("_mtime")
    latest = df.groupby("TagNumber", as_index=False).last()
    return latest.set_index("TagNumber")


def decode_order_tags(
    fixmsg: Optional[str],
    tagdict: Optional[pd.DataFrame] = None,
    sep: str = ";",
) -> pd.DataFrame:
    """Return a tidy table: tag, name, value, description, applies_to, client_tag."""
    rows = []
    for tag, val in parse_fixmsg(fixmsg, sep=sep).items():
        name, desc, applies, client = "(unknown)", "", "", ""
        if tagdict is not None and tag in tagdict.index:
            r = tagdict.loc[tag]
            name = r.get("tagName", "") or "(unnamed)"
            desc = r.get("description", "") or r.get("comments", "")
            applies = r.get("availability", "")
            client = r.get("clientTagNumber", "")
        elif tag in BUILTIN_TAGS:
            name, desc = BUILTIN_TAGS[tag]
        rows.append({
            "tag": tag,
            "name": name,
            "value": val,
            "description": desc,
            "applies_to": applies,
            "client_tag": client,
        })
    cols = ["tag", "name", "value", "description", "applies_to", "client_tag"]
    return pd.DataFrame(rows, columns=cols)
