"""Paid media generation, done by the Hub so that provider keys, spend caps and the cost record live
in one place. Agents ask for a shot or a voiceover; they never hold the keys.

Keys are entered by a reviewer under Settings (stored encrypted), per project or for the whole Hub;
environment variables of the same names still work as a fallback:
  XAI_API_KEY           Grok Imagine stills and video
  OPENROUTER_API_KEY    Seedance video (model from CMSBOT_SEEDANCE_MODEL, or the newest Seedance listed)
  ELEVENLABS_API_KEY    voiceover with word timings (voice from the brand kit or ELEVENLABS_VOICE_ID)

Rates used for the spend check are estimates unless the provider reports a cost; override them with
the rate settings.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from cmsbot import video
from cmsbot.hub.media import media_key
from cmsbot.hub.service import AGENT_EDITABLE, Actor, Hub, HubError

DEFAULT_ITEM_CAP_USD = 10.0
POLL_SECONDS, POLL_LIMIT_SECONDS = 5, 600
MAX_CLIP_SECONDS = 12


Config = Callable[[str], "str | None"]  # looks a setting up: the project's value, the Hub's, then the environment


def _rate(cfg: Config, name: str, default: float) -> float:
    try:
        return float(cfg(f"CMSBOT_RATE_{name}") or default)
    except ValueError:
        return default


@dataclass
class Result:
    data: bytes
    suffix: str            # ".mp4", ".png", ".mp3"
    content_type: str
    cost_usd: float | None = None   # None = the provider did not report one; the estimate is recorded
    extra: dict[str, Any] | None = None


class ProviderError(Exception):
    pass


def _check(response: httpx.Response, what: str) -> Any:
    if response.status_code >= 400:
        raise ProviderError(f"{what} refused the request ({response.status_code}): {response.text[:300]}")
    return response.json()


def _poll(fetch: Callable[[], dict[str, Any]], done: tuple[str, ...], failed: tuple[str, ...], what: str) -> dict[str, Any]:
    deadline = time.monotonic() + POLL_LIMIT_SECONDS
    while time.monotonic() < deadline:
        state = fetch()
        status = str(state.get("status", "")).lower()
        if status in done:
            return state
        if status in failed:
            raise ProviderError(f"{what} reported '{status}': {json.dumps(state)[:300]}")
        time.sleep(POLL_SECONDS)
    raise ProviderError(f"{what} did not finish within {POLL_LIMIT_SECONDS // 60} minutes.")


# ---- providers --------------------------------------------------------------------------------

def xai_image(prompt: str, *, cfg: Config, **_: Any) -> Result:
    key = cfg("XAI_API_KEY")
    with httpx.Client(timeout=180) as client:
        data = _check(client.post("https://api.x.ai/v1/images/generations", headers={"Authorization": f"Bearer {key}"},
                                  json={"model": os.environ.get("CMSBOT_XAI_IMAGE_MODEL", "grok-imagine-image-pro"),
                                        "prompt": prompt, "n": 1, "response_format": "b64_json"}), "Grok Imagine")
    return Result(base64.b64decode(data["data"][0]["b64_json"]), ".png", "image/png")


def xai_video(prompt: str, *, cfg: Config, seconds: int, first_frame_url: str = "", **_: Any) -> Result:
    key = cfg("XAI_API_KEY")
    headers = {"Authorization": f"Bearer {key}"}
    body: dict[str, Any] = {"model": os.environ.get("CMSBOT_XAI_VIDEO_MODEL", "grok-imagine-video-1.5"),
                            "prompt": prompt, "duration": seconds}
    if first_frame_url:
        body["image"] = {"url": first_frame_url}
    with httpx.Client(timeout=120) as client:
        request_id = _check(client.post("https://api.x.ai/v1/videos/generations", headers=headers, json=body), "Grok Imagine")["request_id"]
        state = _poll(lambda: _check(client.get(f"https://api.x.ai/v1/videos/{request_id}", headers=headers), "Grok Imagine"),
                      ("done",), ("failed", "expired"), "Grok Imagine")
        clip = client.get(state["video"]["url"], timeout=300, follow_redirects=True)
        clip.raise_for_status()
    return Result(clip.content, ".mp4", "video/mp4")


def seedance_video(prompt: str, *, cfg: Config, seconds: int, first_frame_url: str = "", **_: Any) -> Result:
    key = cfg("OPENROUTER_API_KEY")
    headers = {"Authorization": f"Bearer {key}"}
    base = "https://openrouter.ai/api/v1/videos"
    with httpx.Client(timeout=120) as client:
        model = cfg("CMSBOT_SEEDANCE_MODEL") or ""
        if not model:
            listed = _check(client.get(f"{base}/models", headers=headers), "OpenRouter")
            entries = listed.get("data", listed) if isinstance(listed, dict) else listed
            names = sorted((str(m.get("id", "")) for m in entries if isinstance(m, dict) and "seedance" in str(m.get("id", "")).lower()),
                           reverse=True)
            if not names:
                raise ProviderError("OpenRouter lists no Seedance video model; set CMSBOT_SEEDANCE_MODEL.")
            model = names[0]
        body: dict[str, Any] = {"model": model, "prompt": prompt, "duration": seconds, "aspect_ratio": "9:16",
                                "resolution": os.environ.get("CMSBOT_SEEDANCE_RESOLUTION", "720p")}
        if first_frame_url:
            body["frame_images"] = [{"type": "image_url", "image_url": {"url": first_frame_url}, "frame_type": "first_frame"}]
        job = _check(client.post(base, headers=headers, json=body), "OpenRouter")
        state = _poll(lambda: _check(client.get(f"{base}/{job['id']}", headers=headers), "OpenRouter"),
                      ("completed",), ("failed", "cancelled", "expired"), "Seedance")
        clip = client.get(f"{base}/{job['id']}/content", params={"index": 0}, headers=headers, timeout=300, follow_redirects=True)
        clip.raise_for_status()
    cost = (state.get("usage") or {}).get("cost")
    return Result(clip.content, ".mp4", "video/mp4", float(cost) if isinstance(cost, (int, float)) else None, {"model": model})


def words_from_alignment(alignment: dict[str, Any]) -> list[dict[str, Any]]:
    """ElevenLabs returns a time per character; captions need a time per word."""
    words: list[dict[str, Any]] = []
    current, start, end = "", 0.0, 0.0
    for char, begin, finish in zip(alignment.get("characters", []), alignment.get("character_start_times_seconds", []),
                                   alignment.get("character_end_times_seconds", [])):
        if char.isspace():
            if current:
                words.append({"word": current, "start": round(start, 3), "end": round(end, 3)})
                current = ""
            continue
        if not current:
            start = float(begin)
        current += char
        end = float(finish)
    if current:
        words.append({"word": current, "start": round(start, 3), "end": round(end, 3)})
    return words


def elevenlabs_voice(text: str, *, cfg: Config, voice_id: str, **_: Any) -> Result:
    key = cfg("ELEVENLABS_API_KEY")
    with httpx.Client(timeout=300) as client:
        data = _check(client.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps",
                                  headers={"xi-api-key": key}, params={"output_format": "mp3_44100_128"},
                                  json={"text": text, "model_id": os.environ.get("CMSBOT_ELEVENLABS_MODEL", "eleven_multilingual_v2")}),
                      "ElevenLabs")
    return Result(base64.b64decode(data["audio_base64"]), ".mp3", "audio/mpeg", None,
                  {"words": words_from_alignment(data.get("alignment") or {})})


PROVIDERS: dict[str, dict[str, Any]] = {
    "grok-image": {"kind": "image", "env": "XAI_API_KEY", "run": xai_image, "category": "image", "vendor": "xai",
                   "estimate": lambda cfg, seconds, chars: _rate(cfg, "XAI_IMAGE", 0.07)},
    "grok-video": {"kind": "video", "env": "XAI_API_KEY", "run": xai_video, "category": "video", "vendor": "xai",
                   "estimate": lambda cfg, seconds, chars: seconds * _rate(cfg, "XAI_VIDEO_PER_SECOND", 0.50)},
    "seedance": {"kind": "video", "env": "OPENROUTER_API_KEY", "run": seedance_video, "category": "video", "vendor": "seedance",
                 "estimate": lambda cfg, seconds, chars: seconds * _rate(cfg, "SEEDANCE_PER_SECOND", 0.50)},
    "elevenlabs": {"kind": "voice", "env": "ELEVENLABS_API_KEY", "run": elevenlabs_voice, "category": "audio", "vendor": "elevenlabs",
                   "estimate": lambda cfg, seconds, chars: chars / 1000 * _rate(cfg, "ELEVENLABS_PER_1K_CHARS", 0.30)},
}


def _config(hub: Hub, project: str) -> Config:
    return lambda name: hub.setting(name, project)


def configured(cfg: Config, kind: str | None = None) -> list[str]:
    return [name for name, spec in PROVIDERS.items() if cfg(spec["env"]) and (kind is None or spec["kind"] == kind)]


def test_key(hub: Hub, actor: Actor, name: str, project: str | None = None) -> dict[str, Any]:
    """Make one free, read-only call with a stored key, so a wrong key is found now and not mid-render."""
    hub._manageable(actor, project or None)
    probes = {
        "XAI_API_KEY": ("https://api.x.ai/v1/models", lambda key: {"Authorization": f"Bearer {key}"}),
        "OPENROUTER_API_KEY": ("https://openrouter.ai/api/v1/key", lambda key: {"Authorization": f"Bearer {key}"}),
        "ELEVENLABS_API_KEY": ("https://api.elevenlabs.io/v1/user", lambda key: {"xi-api-key": key}),
    }
    if name not in probes:
        raise HubError("Only provider keys can be tested.")
    key = hub.setting(name, project)
    if not key:
        raise HubError("That key is not set.", 404)
    url, headers = probes[name]
    try:
        status = httpx.get(url, headers=headers(key), timeout=20).status_code
    except httpx.HTTPError as exc:
        return {"name": name, "ok": False, "detail": f"Could not reach the provider: {exc}"}
    return {"name": name, "ok": status < 300,
            "detail": "The provider accepted the key." if status < 300 else f"The provider answered {status}; the key is probably wrong or lacks access."}


# ---- spend rules ------------------------------------------------------------------------------

def options(hub: Hub, actor: Actor, item_id: int) -> dict[str, Any]:
    """What can be generated for this item right now, and how much room is left."""
    item = hub.get_item(actor, item_id)
    settings = hub._project(item["project"])["settings"]
    cap = float(settings.get("item_media_cap_usd", DEFAULT_ITEM_CAP_USD))
    spend = hub.spend(actor, item["project"])
    return {
        "providers": {name: {"kind": PROVIDERS[name]["kind"]} for name in configured(_config(hub, item["project"]))},
        "item_cap_usd": cap, "item_spent_usd": item["cost_usd"], "item_remaining_usd": round(cap - item["cost_usd"], 2),
        "project_month": spend,
        "script_approved": bool(item["audit"].get("script_approved")),
        "note": "Costs are estimates unless the provider reports one. Preview with render_video before generating footage.",
    }


def _authorise(hub: Hub, actor: Actor, item: dict[str, Any], estimate: float) -> None:
    if actor.role not in ("admin", "generator"):
        raise HubError("This token may not generate media.", 403)
    if actor.role == "generator":
        if item["status"] not in AGENT_EDITABLE:
            raise HubError(f"Item {item['id']} is '{item['status']}' and is with the reviewer.", 409)
        if item["kind"] == "short_video" and not item["audit"].get("script_approved"):
            raise HubError("The script has not been approved yet. Nothing is generated before a person approves the words.", 409)
    room = options(hub, actor, item["id"])
    if estimate > room["item_remaining_usd"]:
        raise HubError(f"This would cost about ${estimate:.2f}, but only ${room['item_remaining_usd']:.2f} of this item's "
                       f"${room['item_cap_usd']:.2f} media cap is left. A reviewer can raise the cap in the project settings.", 402)
    month = room["project_month"]
    if month["over_budget"] or (month["remaining_usd"] is not None and estimate > month["remaining_usd"]):
        raise HubError(f"The project's monthly budget (${month['monthly_budget_usd']}) has no room for about ${estimate:.2f}.", 402)


def _pick(cfg: Config, kind: str, provider: str) -> tuple[str, dict[str, Any]]:
    available = configured(cfg, kind)
    if provider:
        if provider not in PROVIDERS or PROVIDERS[provider]["kind"] != kind:
            raise HubError(f"'{provider}' is not a {kind} provider. Options: {', '.join(n for n, s in PROVIDERS.items() if s['kind'] == kind)}")
        if provider not in available:
            raise HubError(f"{provider} has no key on this Hub. A reviewer can add it under Settings.", 503)
        return provider, PROVIDERS[provider]
    if not available:
        raise HubError(f"No {kind} provider has a key on this Hub. A reviewer can add one under Settings.", 503)
    return available[0], PROVIDERS[available[0]]


def _store(hub: Hub, storage, actor: Actor, item: dict[str, Any], role: str, result: Result) -> str:
    url = storage.put(media_key(item["project"], item["id"], f"{role}{result.suffix}"), result.data, result.content_type)
    current = hub.get_item(actor, item["id"])
    media = [m for m in current["media"] if not (isinstance(m, dict) and m.get("role") == role)]
    hub.update_item(actor, item["id"], media=[*media, {"url": url, "role": role, "name": f"{role}{result.suffix}"}],
                    note=f"generated {role}")
    return url


def _absolute(hub: Hub, url: str) -> str:
    if url.startswith("/"):
        if not hub.public_url:
            raise HubError("Set CMSBOT_HUB_PUBLIC_URL so the video provider can fetch the first frame.", 503)
        return hub.public_url + url
    return url


def generate_footage(hub: Hub, storage, actor: Actor, item_id: int, *, shot: int, prompt: str, kind: str = "video",
                     seconds: int = 5, provider: str = "", from_still: bool = True) -> dict[str, Any]:
    """Generate the clip or still for one shot and attach it as shot-NN. A video starts from the shot's
    existing still when there is one, which is what keeps a look consistent from shot to shot."""
    item = hub.get_item(actor, item_id)
    if item["kind"] != "short_video":
        raise HubError("Footage is generated for short_video items.")
    shots = video.normalize(item["body"])
    if not 1 <= shot <= len(shots):
        raise HubError(f"This script has shots 1 to {len(shots)}.")
    if kind not in ("video", "image"):
        raise HubError("kind must be 'video' or 'image'.")
    if not prompt.strip():
        raise HubError("Write the prompt for this shot.")
    seconds = max(2, min(int(seconds), MAX_CLIP_SECONDS))
    cfg = _config(hub, item["project"])
    name, spec = _pick(cfg, kind, provider)
    estimate = round(spec["estimate"](cfg, seconds, 0), 4)
    _authorise(hub, actor, item, estimate)
    role = f"shot-{shot:02d}"
    still = next((m["url"] for m in item["media"] if isinstance(m, dict) and m.get("role") == role
                  and re.search(r"\.(png|jpe?g|webp)$", str(m.get("url", "")), re.IGNORECASE)), "")
    try:
        result = spec["run"](prompt.strip(), cfg=cfg, seconds=seconds,
                             first_frame_url=_absolute(hub, still) if (kind == "video" and from_still and still) else "")
    except ProviderError as exc:
        raise HubError(str(exc), 502) from exc
    except httpx.HTTPError as exc:
        raise HubError(f"{name} could not be reached: {exc}", 502) from exc
    cost = result.cost_usd if result.cost_usd is not None else estimate
    url = _store(hub, storage, actor, item, role, result)
    hub.record_cost(actor, item["project"], amount_usd=cost, category=spec["category"], vendor=spec["vendor"], item_id=item_id,
                    note=f"{role} {kind} via {name}" + ("" if result.cost_usd is not None else " (estimated)"))
    return {"item_id": item_id, "shot": shot, "role": role, "url": url, "provider": name, "cost_usd": cost,
            "cost_is_estimate": result.cost_usd is None, **options(hub, actor, item_id)}


def generate_voiceover(hub: Hub, storage, actor: Actor, item_id: int, *, voice_id: str = "", provider: str = "") -> dict[str, Any]:
    """Speak the script and attach it as `voiceover`, with word timings as `timing` so captions sync exactly."""
    item = hub.get_item(actor, item_id)
    if item["kind"] != "short_video":
        raise HubError("Voiceover is generated for short_video items.")
    brand = hub.brand(item["project"])
    voice_id = voice_id or ((brand.get("voiceover") or {}).get("voice_id") if isinstance(brand.get("voiceover"), dict) else "") \
        or hub.setting("ELEVENLABS_VOICE_ID", item["project"]) or ""
    if not voice_id:
        raise HubError("No voice chosen. A reviewer can set the ElevenLabs voice id under Settings.")
    text = " ".join(s["narration"] for s in video.normalize(item["body"]) if s["narration"])
    cfg = _config(hub, item["project"])
    name, spec = _pick(cfg, "voice", provider)
    estimate = round(spec["estimate"](cfg, 0, len(text)), 4)
    _authorise(hub, actor, item, estimate)
    try:
        result = spec["run"](text, cfg=cfg, voice_id=voice_id)
    except ProviderError as exc:
        raise HubError(str(exc), 502) from exc
    except httpx.HTTPError as exc:
        raise HubError(f"{name} could not be reached: {exc}", 502) from exc
    url = _store(hub, storage, actor, item, "voiceover", result)
    words = (result.extra or {}).get("words") or []
    if words:
        _store(hub, storage, actor, item, "timing",
               Result(json.dumps({"words": words}).encode("utf-8"), ".json", "application/json"))
    cost = result.cost_usd if result.cost_usd is not None else estimate
    hub.record_cost(actor, item["project"], amount_usd=cost, category=spec["category"], vendor=spec["vendor"], item_id=item_id,
                    note=f"voiceover via {name}" + ("" if result.cost_usd is not None else " (estimated)"))
    return {"item_id": item_id, "url": url, "provider": name, "words_timed": len(words), "cost_usd": cost,
            "cost_is_estimate": result.cost_usd is None,
            "disclosure": "A synthetic voice carries this narration; apply the platform's AI-content label when posting.",
            **options(hub, actor, item_id)}
