# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a small experimentation project for learning the Anthropic Python SDK — basic chat, multi-turn conversation, and tool use (function calling). There is no build step or package manifest (no `requirements.txt`/`pyproject.toml`); dependencies were installed ad hoc into `.venv`. A pytest suite exists for `weatherTool.py` and `weatherTool_V2.py` under `tests/`.

## Environment

- Python virtualenv at `.venv/` (activate with `source .venv/bin/activate`).
- Secrets live in `.env` (loaded via `python-dotenv`), currently just `ANTHROPIC_API_KEY`. Notebooks call `load_dotenv(override=True)` so `.env` always wins over a stale `ANTHROPIC_API_KEY` left in the shell/kernel environment.
- Run notebooks with Jupyter (`jupyter lab` or open in PyCharm) — `notebook.ipynb` and `weatherAPI_Sandbox.ipynb` are the two experiment notebooks.
- `.cache.sqlite` is an HTTP response cache created by `requests-cache` (used by `weatherTool.py`/`weatherTool_V2.py`) — safe to delete/regenerate.
- `pytest` (plus `pluggy`/`iniconfig`) is installed ad hoc into `.venv` — it isn't tracked anywhere else, so if `.venv` is ever recreated, reinstall with `.venv/bin/pip install pytest`.
- `numpy` is intentionally NOT installed — `weatherTool_V2.py` reads Open-Meteo daily arrays via the scalar `.Values(j)`/`.ValuesLength()` accessors instead of `.ValuesAsNumpy()`, which would require `numpy` and isn't available.

## Architecture

- `tools.py` — thin Anthropic chat helpers: `add_user_message`/`add_assistant_message` mutate a shared `messages` list in place (Anthropic's message format), and `chat(messages)` calls `client.messages.create(...)` with model `claude-sonnet-5` and returns the first text block. Notebooks build conversation history by repeatedly calling these two helpers then `chat`.
- `weatherTool.py` — `get_city_temperature(city_name_country)` geocodes a "City, Country" string via `geopy`'s Nominatim, then queries Open-Meteo's forecast API (through `openmeteo_requests`, with `requests_cache` + `retry_requests` for caching/retries) for current temperature. Raises `CityNotFoundError` if the location can't be geocoded and `WeatherLookupError` if the weather API call fails or returns no data.
- `weatherTool_V2.py` — `get_city_forecast(city_name_country, days=15)` geocodes the same "City, Country" string, then requests a multi-day `daily` forecast block from Open-Meteo (temperature max/min/mean, humidity max/min/mean, precipitation/rain/showers/snowfall, wind speed/gusts/direction, and `weather_code`) and returns a JSON string with one entry per day. `days` must be 1-16 (Open-Meteo's `forecast_days` range) and raises `ValueError` outside that range. Reuses `CityNotFoundError`/`WeatherLookupError` from `weatherTool.py` for the same failure modes. Sky condition (e.g. "Overcast", "Rain", "Thunderstorm") is derived from the WMO `weather_code` via the module-level `_WMO_WEATHER_CODES` lookup (`_describe_weather_code`). Open-Meteo doesn't always have model coverage for the outer days of an extended forecast and returns `NaN` for every variable on those days — `_clean()` maps `NaN` to `None`/JSON `null` (and `weather_code`/`condition` become `null`/`"Unknown"`) instead of letting `int(nan)` raise.
- `agent.py` — `run_agent(messages, tools, tool_functions, ...)` is the shared Anthropic tool-use loop: it calls `messages.create` with `tool_choice="auto"`, dispatches any `tool_use` blocks to the matching callable in `tool_functions` (catching exceptions and reporting them back to Claude as the `tool_result` content instead of raising), and loops until Claude returns a final text-only answer (or `max_iterations` is hit). Both notebooks use this instead of hand-rolling the tool-use round trip.
- `notebook.ipynb` — progression of examples: (1) raw `Anthropic().messages.create` call, (2) same flow using the `tools.py` helpers for multi-turn context, (3) a weather-assistant tool-use example built on `agent.run_agent` and `weatherTool.get_city_temperature`.
- `weatherAPI_Sandbox.ipynb` — direct `weatherTool.get_city_temperature` call for a sample city, followed by the same `agent.run_agent` weather-assistant pattern as `notebook.ipynb`.
- `tests/test_weather_tool.py` — pytest suite for `weatherTool.py`'s success/error paths, using `monkeypatch` to stub out `Nominatim`, `requests_cache`, and the Open-Meteo client (no real network/disk I/O).
- `tests/test_weather_tool_v2.py` — mirrors `test_weather_tool.py`'s mocking style for `weatherTool_V2.get_city_forecast`: success path asserting the JSON is grouped by day with the right fields, blank-input and out-of-range `days` `ValueError`s, `CityNotFoundError`/`WeatherLookupError` paths, and a NaN-handling regression test (`test_nan_values_are_reported_as_null`) for the extended-forecast-coverage gap described above. The fake `Daily()` mock stubs `.Time()`/`.TimeEnd()`/`.Interval()` and per-variable `.Values(j)`/`.ValuesLength()`.
- Run the whole suite with `.venv/bin/pytest tests/ -v`. `conftest.py` at the repo root exists solely so pytest can resolve the top-level module imports.

When extending this repo, follow the existing pattern: keep the low-level Anthropic/tool logic in `.py` modules and drive experiments/conversation flow from the notebooks.
