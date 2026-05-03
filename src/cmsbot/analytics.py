"""Unified analytics storage keyed by asset_set_id and slot_id."""

from __future__ import annotations

import json
import csv
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def performance_index_path(output_root: str | Path) -> Path:
    return Path(output_root) / "calendar" / "performance-index.json"


def load_performance_index(output_root: str | Path) -> list[dict[str, Any]]:
    path = performance_index_path(output_root)
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        records = payload.get("records", [])
        return records if isinstance(records, list) else []
    return payload if isinstance(payload, list) else []


def save_performance_index(output_root: str | Path, records: list[dict[str, Any]]) -> Path:
    path = performance_index_path(output_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(),
        "records": records,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def upsert_performance_record(output_root: str | Path, record: dict[str, Any]) -> tuple[dict[str, Any], Path]:
    output_root = Path(output_root)
    records = load_performance_index(output_root)
    key = _record_key(record)

    replaced = False
    for idx, existing in enumerate(records):
        if _record_key(existing) == key:
            merged = dict(existing)
            merged.update(record)
            merged["updated_at"] = datetime.now().isoformat()
            records[idx] = merged
            record = merged
            replaced = True
            break

    if not replaced:
        record = dict(record)
        record.setdefault("created_at", datetime.now().isoformat())
        record["updated_at"] = datetime.now().isoformat()
        records.append(record)

    index_path = save_performance_index(output_root, records)
    _write_asset_set_analytics(output_root, record)
    return record, index_path


def record_social_metrics(
    output_root: str | Path,
    *,
    asset_set_id: str,
    platform: str,
    format: str,
    slot_id: str | None = None,
    pillar: str | None = None,
    topic: str | None = None,
    hook: str | None = None,
    posted_date: str | None = None,
    source_blog: str | None = None,
    views_24h: int = 0,
    views_48h: int = 0,
    views_7d: int = 0,
    impressions: int = 0,
    saves: int = 0,
    shares: int = 0,
    comments: int = 0,
    profile_visits: int = 0,
    link_clicks: int = 0,
    conversions: int = 0,
    performance_score: float | None = None,
    notes: str | None = None,
) -> tuple[dict[str, Any], Path]:
    score = performance_score if performance_score is not None else _score_social_record(
        views_7d=views_7d,
        impressions=impressions,
        saves=saves,
        shares=shares,
        comments=comments,
        link_clicks=link_clicks,
        conversions=conversions,
    )
    record = {
        "asset_set_id": asset_set_id,
        "slot_id": slot_id or "",
        "platform": platform,
        "format": format,
        "content_type": "social",
        "pillar": pillar or "",
        "topic": topic or "",
        "hook": hook or "",
        "source_blog": source_blog or "",
        "posted_date": posted_date or datetime.now().strftime("%Y-%m-%d"),
        "views_24h": views_24h,
        "views_48h": views_48h,
        "views_7d": views_7d,
        "impressions": impressions,
        "saves": saves,
        "shares": shares,
        "comments": comments,
        "profile_visits": profile_visits,
        "link_clicks": link_clicks,
        "conversions": conversions,
        "performance_score": round(float(score), 2),
        "notes": notes or "",
    }
    return upsert_performance_record(output_root, record)


def import_seo_evaluation(
    output_root: str | Path,
    report_path: str | Path,
) -> tuple[int, Path]:
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    scorecards = report.get("contentScorecards", [])
    if not isinstance(scorecards, list):
        raise ValueError("No contentScorecards found in SEO evaluation report")

    imported = 0
    index_path = performance_index_path(output_root)
    for card in scorecards:
        if not isinstance(card, dict):
            continue

        slug = str(card.get("slug") or "").strip()
        if not slug:
            continue
        traffic = card.get("traffic") if isinstance(card.get("traffic"), dict) else {}
        ranking_keywords = card.get("rankingKeywords") if isinstance(card.get("rankingKeywords"), list) else []
        total_impressions = sum(int((kw or {}).get("impressions", 0) or 0) for kw in ranking_keywords if isinstance(kw, dict))
        total_clicks = sum(int((kw or {}).get("clicks", 0) or 0) for kw in ranking_keywords if isinstance(kw, dict))
        topic = str(card.get("title") or slug)
        record = {
            "asset_set_id": slug,
            "slot_id": f"{slug}:blog",
            "platform": "blog",
            "format": "seo_content",
            "content_type": "blog",
            "pillar": "",
            "topic": topic,
            "hook": "",
            "source_blog": slug,
            "posted_date": _safe_date(str(card.get("publishedAt") or "")),
            "views_24h": 0,
            "views_48h": 0,
            "views_7d": int(traffic.get("pageviews", 0) or 0),
            "impressions": total_impressions,
            "saves": 0,
            "shares": 0,
            "comments": 0,
            "profile_visits": 0,
            "link_clicks": total_clicks,
            "conversions": int(traffic.get("conversions", 0) or 0),
            "performance_score": float(card.get("performanceScore", 0) or 0),
            "notes": f"Imported from SEO evaluation {report.get('generatedAt') or ''}".strip(),
            "seo": {
                "url": card.get("url") or "",
                "organic_sessions": int(traffic.get("organicTraffic", 0) or 0),
                "unique_users": int(traffic.get("uniqueUsers", 0) or 0),
                "bounce_rate": float(traffic.get("bounceRate", 0) or 0),
                "avg_session_duration": float(traffic.get("avgSessionDuration", 0) or 0),
                "opportunities": card.get("opportunities", []),
            },
        }
        _, index_path = upsert_performance_record(output_root, record)
        imported += 1

    return imported, index_path


def import_social_csv(
    output_root: str | Path,
    csv_path: str | Path,
    *,
    platform: str,
    format: str,
) -> tuple[int, Path]:
    """Import a flexible social analytics CSV into the shared performance index."""
    imported = 0
    index_path = performance_index_path(output_root)
    with Path(csv_path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for raw_row in reader:
            row = {_normalize_header(key): value for key, value in raw_row.items() if key is not None}
            asset_set_id = _first_text(row, "asset_set_id", "asset set id", "asset", "slug", "post_id", "post id")
            if not asset_set_id:
                continue
            record, index_path = record_social_metrics(
                output_root,
                asset_set_id=asset_set_id,
                platform=_first_text(row, "platform") or platform,
                format=_first_text(row, "format", "content_format", "content format") or format,
                slot_id=_first_text(row, "slot_id", "slot id"),
                pillar=_first_text(row, "pillar"),
                topic=_first_text(row, "topic"),
                hook=_first_text(row, "hook"),
                source_blog=_first_text(row, "source_blog", "source blog"),
                posted_date=_first_text(row, "posted_date", "posted date", "date"),
                views_24h=_first_int(row, "views_24h", "views 24h"),
                views_48h=_first_int(row, "views_48h", "views 48h"),
                views_7d=_first_int(row, "views_7d", "views 7d", "views", "plays"),
                impressions=_first_int(row, "impressions", "reach"),
                saves=_first_int(row, "saves"),
                shares=_first_int(row, "shares", "reposts"),
                comments=_first_int(row, "comments", "replies"),
                profile_visits=_first_int(row, "profile_visits", "profile visits"),
                link_clicks=_first_int(row, "link_clicks", "link clicks", "clicks"),
                conversions=_first_int(row, "conversions"),
                performance_score=_first_float_or_none(row, "performance_score", "performance score", "score"),
                notes=_first_text(row, "notes"),
            )
            imported += 1
    return imported, index_path


def summarize_recent_signals(
    records: list[dict[str, Any]],
    *,
    lookback_days: int = 90,
) -> dict[str, Any]:
    cutoff = datetime.now() - timedelta(days=lookback_days)
    filtered = [
        record
        for record in records
        if _parse_date(record.get("posted_date")) >= cutoff
    ]
    if not filtered:
        return {"top_pillars": [], "top_formats": [], "top_hooks": []}

    pillar_scores: dict[str, list[float]] = defaultdict(list)
    format_scores: dict[str, list[float]] = defaultdict(list)
    hook_scores: dict[str, list[float]] = defaultdict(list)
    for record in filtered:
        score = float(record.get("performance_score", 0) or 0)
        pillar = str(record.get("pillar") or "").strip()
        fmt = str(record.get("format") or "").strip()
        hook = str(record.get("hook") or "").strip()
        if pillar:
            pillar_scores[pillar].append(score)
        if fmt:
            format_scores[fmt].append(score)
        if hook:
            hook_scores[hook].append(score)

    return {
        "top_pillars": _rank_scores(pillar_scores),
        "top_formats": _rank_scores(format_scores),
        "top_hooks": _rank_scores(hook_scores, limit=5),
    }


def render_performance_report(records: list[dict[str, Any]], *, lookback_days: int = 30) -> dict[str, Any]:
    cutoff = datetime.now() - timedelta(days=lookback_days)
    filtered = [
        record
        for record in records
        if _parse_date(record.get("posted_date")) >= cutoff
    ]
    totals = {
        "records": len(filtered),
        "views_7d": sum(int(record.get("views_7d", 0) or 0) for record in filtered),
        "impressions": sum(int(record.get("impressions", 0) or 0) for record in filtered),
        "saves": sum(int(record.get("saves", 0) or 0) for record in filtered),
        "conversions": sum(int(record.get("conversions", 0) or 0) for record in filtered),
    }
    return {
        "lookback_days": lookback_days,
        "totals": totals,
        "signals": summarize_recent_signals(filtered, lookback_days=lookback_days),
    }


def _record_key(record: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(record.get("asset_set_id") or ""),
        str(record.get("slot_id") or ""),
        str(record.get("platform") or ""),
        str(record.get("format") or ""),
    )


def _normalize_header(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _first_text(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(_normalize_header(key))
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _first_int(row: dict[str, Any], *keys: str) -> int:
    text = _first_text(row, *keys)
    if not text:
        return 0
    try:
        return int(float(text.replace(",", "")))
    except ValueError:
        return 0


def _first_float_or_none(row: dict[str, Any], *keys: str) -> float | None:
    text = _first_text(row, *keys)
    if not text:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def _parse_date(value: Any) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        return datetime.min
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(raw[:10], "%Y-%m-%d")
        except ValueError:
            return datetime.min


def _safe_date(value: str) -> str:
    parsed = _parse_date(value)
    if parsed == datetime.min:
        return datetime.now().strftime("%Y-%m-%d")
    return parsed.strftime("%Y-%m-%d")


def _score_social_record(
    *,
    views_7d: int,
    impressions: int,
    saves: int,
    shares: int,
    comments: int,
    link_clicks: int,
    conversions: int,
) -> float:
    score = 0.0
    score += min(25.0, views_7d / 2000)
    score += min(20.0, impressions / 5000) if impressions else 0.0
    score += min(20.0, saves / 20)
    score += min(10.0, shares / 10)
    score += min(10.0, comments / 10)
    score += min(10.0, link_clicks / 10)
    score += min(15.0, conversions * 3)
    return score


def _rank_scores(score_map: dict[str, list[float]], *, limit: int = 10) -> list[dict[str, Any]]:
    ranked = []
    for key, scores in score_map.items():
        if not scores:
            continue
        ranked.append(
            {
                "key": key,
                "average_score": round(sum(scores) / len(scores), 2),
                "count": len(scores),
            }
        )
    ranked.sort(key=lambda item: (item["average_score"], item["count"]), reverse=True)
    return ranked[:limit]


def _write_asset_set_analytics(output_root: Path, record: dict[str, Any]) -> None:
    asset_set_id = str(record.get("asset_set_id") or "").strip()
    if not asset_set_id:
        return
    asset_set_dir = _find_asset_set_dir(output_root, asset_set_id)
    if asset_set_dir is None:
        return
    analytics_dir = asset_set_dir / "analytics"
    analytics_dir.mkdir(parents=True, exist_ok=True)
    performance_path = analytics_dir / "performance.json"
    if performance_path.exists():
        payload = json.loads(performance_path.read_text(encoding="utf-8"))
        entries = payload.get("records", []) if isinstance(payload, dict) else []
    else:
        entries = []

    key = _record_key(record)
    replaced = False
    for idx, existing in enumerate(entries):
        if _record_key(existing) == key:
            entries[idx] = record
            replaced = True
            break
    if not replaced:
        entries.append(record)

    payload = {
        "asset_set_id": asset_set_id,
        "updated_at": datetime.now().isoformat(),
        "records": entries,
    }
    performance_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _find_asset_set_dir(output_root: Path, asset_set_id: str) -> Path | None:
    asset_sets_root = output_root / "asset-sets"
    if not asset_sets_root.exists():
        return None
    matches = list(asset_sets_root.glob(f"*/*/{asset_set_id}"))
    if matches:
        return matches[0]
    return None
