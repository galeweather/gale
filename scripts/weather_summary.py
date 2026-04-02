#!/usr/bin/env python3
"""
Fetch NWS 4-day forecast for Twin Cities, MN → send to Haiku for a
charismatic weatherperson summary → output JSON to scripts/output/summary.json
for upload to R2.

NWS grid point for Minneapolis–Saint Paul: MSP office, grid 107,71
Source: https://api.weather.gov/points/44.9778,-93.2650
"""

import json, os, sys, urllib.request, datetime

# ── NWS forecast for Twin Cities ──────────────────────────────────
NWS_FORECAST_URL = "https://api.weather.gov/gridpoints/MPX/107,71/forecast"
NWS_HEADERS = {"User-Agent": "Gale Weather (galeweather.com)", "Accept": "application/geo+json"}

def fetch_nws_forecast():
    """Fetch 7-day NWS forecast, return first 8 periods (~4 days, day+night each)."""
    req = urllib.request.Request(NWS_FORECAST_URL, headers=NWS_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    periods = data["properties"]["periods"][:8]  # 4 days × 2 (day + night)
    return periods

def build_forecast_text(periods):
    """Format NWS periods into readable text for Haiku."""
    lines = []
    for p in periods:
        lines.append(f"{p['name']}: {p['temperature']}°{p['temperatureUnit']}, "
                     f"wind {p['windSpeed']} {p['windDirection']}. "
                     f"{p['detailedForecast']}")
    return "\n".join(lines)

# ── Haiku summary generation ─────────────────────────────────────
def generate_summary(forecast_text):
    """Call Claude Haiku to write a charismatic weather summary."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    prompt = f"""You are a charismatic, knowledgeable TV weatherperson delivering the 4-day forecast for the Twin Cities metro area in Minnesota. You have real personality — think a mix of confident and warm, someone people tune in for.

Write a 3-4 paragraph summary covering the next 4 days. Include:
- What's happening RIGHT NOW (today/tonight)
- The trend over the next few days (warming? cooling? storm system moving in?)
- Any notable weather to prepare for (rain, wind, extreme temps, etc.)
- A closing line with personality

Style rules:
- Conversational and engaging, NOT robotic or generic
- Use specific temperatures and conditions from the data
- Reference "the Twin Cities" or "the metro" naturally
- No bullet points, no headers — flowing paragraphs only
- Keep it under 250 words
- Do NOT start with "Good morning" or any time-of-day greeting (this is displayed all day)
- Do NOT use emojis

Here is the NWS forecast data:

{forecast_text}"""

    body = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 400,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
    )

    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())

    return result["content"][0]["text"]

# ── Main ─────────────────────────────────────────────────────────
def main():
    print("Fetching NWS forecast for Twin Cities...")
    periods = fetch_nws_forecast()
    forecast_text = build_forecast_text(periods)

    print("Generating summary with Haiku...")
    summary = generate_summary(forecast_text)

    # Build output JSON
    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    output = {
        "summary": summary,
        "generated_at": now,
        "location": "Twin Cities, MN",
        "periods": len(periods),
        "source": "NWS Minneapolis/St. Paul (MPX) gridpoint forecast"
    }

    # Write to output directory (same pattern as tile pipeline)
    os.makedirs("scripts/output/summary", exist_ok=True)
    out_path = "scripts/output/summary/forecast.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Written to {out_path}")
    print(f"Generated at: {now}")
    print(f"Summary length: {len(summary)} chars")

if __name__ == "__main__":
    main()
