# Gale

Free, open-source weather visualization for the United States. Real-time radar, satellite imagery, HRRR forecasts, and NWS alerts on a single dark map. No accounts, no API keys, no tracking.

**[galeweather.github.io/gale](https://galeweather.github.io/gale/)**

## What It Does

- **MRMS Radar** — 2-minute refresh with 50-minute animation timeline
- **GOES Satellite** — visible imagery layer
- **HRRR Temperature** — color-ramped forecast overlay from 3km model data
- **NWS Alerts** — severe weather polygons color-coded by severity
- **Point Forecast** — click anywhere for instant temperature reading + hourly NWS forecast
- **Search** — find any US location by name

Wind speed and precipitation layers are coming soon.

## Tech

Single HTML file. No build step. No backend.

MapLibre GL JS renders the map with CARTO Dark basemap and OpenFreeMap vector labels. Weather data comes from free public APIs (Iowa State Mesonet, NWS, NOAA). HRRR temperature tiles are pre-rendered and served from Cloudflare R2.

## Run Locally

Open `frontend/index.html` in a browser. That's it.

## Project Structure

```
frontend/index.html     — the app (~1800 lines)
frontend/sw.js          — service worker for tile caching
docs/                   — GitHub Pages mirror (auto-synced from frontend/)
scripts/                — HRRR/GFS tile generation pipelines
```

## Data Sources

All free, no API keys required:

| Source | Data |
|--------|------|
| Iowa State Mesonet | MRMS radar, GOES satellite |
| NWS API | Alerts, point forecasts |
| Cloudflare R2 | HRRR temperature tiles |
| Nominatim | Geocoding search |
| OpenFreeMap | Vector place labels |

## License

MIT
