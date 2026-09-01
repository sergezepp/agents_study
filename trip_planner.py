"""Trip Planning Assistant: a coordinator-subagent multi-agent system.

Coordinator (`plan_trip`) system intent: "you are a helpful trip planning
assistant and you need to coordinate subagents to produce a trip schedule
plan for a given location for the next N days." The coordination itself is
deterministic Python rather than a further LLM tool-use loop (see `agent.py`
for that pattern) because the delegation order here is fixed and known ahead
of time — there's nothing for an LLM to decide about *which* subagent to call
next.

Subagents:
  - weather_subagent: wraps weatherTool_V2.get_city_forecast().
  - activities_subagent: asks Claude for a list of activities for the city,
    drawn from the model's own training knowledge — there is no places/search
    API configured in this project, so activity data may be dated or generic
    for lesser-known cities.
  - scheduler_subagent: assigns activities to days based on weather.
  - packing_subagent: derives a packing list from the finalized schedule
    (weather + planned activities per day).
  - report_synthesizer: pure Python Markdown templating (no LLM call) over
    all of the above.

plan_trip() runs weather_subagent and activities_subagent in parallel (both
are I/O-bound calls), then scheduler_subagent, then packing_subagent, then
report_synthesizer, matching the requested coordinator order. If either of
the parallel subagents fails, the whole pipeline aborts immediately
(TripPlanningError) rather than proceeding with partial data.
"""

import json
from concurrent.futures import ThreadPoolExecutor

from anthropic import Anthropic

from weatherTool_V2 import get_city_forecast

DEFAULT_MODEL = "claude-sonnet-5"


class TripPlanningError(RuntimeError):
    """Raised when the coordinator cannot complete the trip planning pipeline."""


class SubagentError(RuntimeError):
    """Raised when a subagent's LLM call returns output that isn't valid JSON."""


def _ask_claude(system: str, user_content: str) -> dict:
    """Send one completion request to Claude and parse its reply as JSON.

    Raises SubagentError if the reply isn't valid JSON (after stripping an
    optional markdown code fence).
    """
    client = Anthropic()
    message = client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": user_content}],
    )
    text_blocks = [block.text for block in message.content if block.type == "text"]
    text = "\n".join(text_blocks).strip()

    if message.stop_reason == "max_tokens":
        raise SubagentError(
            "Claude response was truncated (hit max_tokens) before producing complete JSON"
        )

    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SubagentError(f"Claude did not return valid JSON: {exc}") from exc


def weather_subagent(city: str, days: int = 15) -> dict:
    """Return the parsed forecast dict for `city` over the next `days` days."""
    return json.loads(get_city_forecast(city, days))


def activities_subagent(city: str) -> list[dict]:
    """Return a list of candidate activities for `city`, from Claude's own knowledge."""
    system = (
        "You are a local activities expert. Given a city, respond with ONLY a "
        "JSON array (no prose, no markdown fences) of 15 to 25 activities a "
        "visitor could do there. Each element must be an object with keys: "
        '"name" (string), "type" ("indoor" or "outdoor"), "duration_hours" '
        '(number), "area" (a neighborhood/district name), and "description" '
        "(one sentence). Include a realistic mix of indoor and outdoor options."
    )
    result = _ask_claude(system, f"City: {city}")
    if not isinstance(result, list):
        raise SubagentError("Expected activities_subagent to return a JSON array")
    return result


def scheduler_subagent(city: str, weather: dict, activities: list[dict]) -> dict:
    """Assign activities to each forecast day based on that day's weather."""
    daily_weather = [
        {
            "date": day["date"],
            "temperature_c": day["temperature_c"],
            "precipitation_mm": day["precipitation"]["total_mm"],
            "condition": day["sky"]["condition"],
        }
        for day in weather["forecast"]
    ]

    system = (
        "You are a trip scheduling expert. You will be given a city, a "
        "day-by-day weather forecast, and a list of candidate activities. "
        "Build a day-by-day schedule: prefer outdoor activities on clear/"
        "partly-cloudy days, prefer indoor activities on rain/snow/"
        "thunderstorm days, avoid scheduling the same activity twice, and "
        "keep total scheduled hours per day reasonable (roughly 4-8 hours). "
        "Respond with ONLY JSON (no prose, no markdown fences) of the shape: "
        '{"days": [{"date": "...", "weather_condition": "...", '
        '"planned_activities": [{"name": "...", "duration_hours": 0, '
        '"type": "indoor|outdoor"}], "notes": "..."}]}. Include one entry '
        "in \"days\" for every day in the provided forecast, in order."
    )
    user_content = json.dumps(
        {"city": city, "daily_weather": daily_weather, "activities": activities}
    )
    result = _ask_claude(system, user_content)
    if "days" not in result:
        raise SubagentError("Expected scheduler_subagent output to contain a 'days' key")
    return result


def packing_subagent(schedule: dict) -> dict:
    """Derive a packing list from the finalized day-by-day schedule."""
    system = (
        "You are a travel packing expert. You will be given a day-by-day "
        "trip schedule that includes each day's weather condition and "
        "planned activities. Respond with ONLY JSON (no prose, no markdown "
        'fences) of the shape: {"categories": {"clothing": [...], "gear": '
        '[...], "toiletries": [...], "other": [...]}, "notes": "..."}. Base '
        "clothing/gear choices on both the weather conditions present and "
        "the specific activities planned (e.g. hiking implies boots, a "
        "beach day implies swimwear)."
    )
    result = _ask_claude(system, json.dumps(schedule))
    if "categories" not in result:
        raise SubagentError("Expected packing_subagent output to contain a 'categories' key")
    return result


def report_synthesizer(
    city: str,
    weather: dict,
    activities: list[dict],
    schedule: dict,
    packing: dict,
) -> str:
    """Render the final trip plan as a Markdown document (pure function, no LLM call)."""
    days = weather["forecast"]
    start_date = days[0]["date"]
    end_date = days[-1]["date"]
    temps_max = [d["temperature_c"]["max"] for d in days if d["temperature_c"]["max"] is not None]
    temps_min = [d["temperature_c"]["min"] for d in days if d["temperature_c"]["min"] is not None]

    lines = [f"# Trip Plan: {city}", ""]
    lines.append(f"**Dates:** {start_date} to {end_date} ({len(days)} days)")
    if temps_max and temps_min:
        lines.append(f"**Temperature range:** {min(temps_min):.1f}°C to {max(temps_max):.1f}°C")
    lines.append(f"**Candidate activities considered:** {len(activities)}")
    lines.append("")

    lines.append("## Day-by-Day Schedule")
    lines.append("")
    for day in schedule["days"]:
        lines.append(f"### {day['date']} — {day['weather_condition']}")
        planned = day.get("planned_activities", [])
        if planned:
            for activity in planned:
                name = activity.get("name", "Unnamed activity")
                duration = activity.get("duration_hours", "?")
                activity_type = activity.get("type", "")
                lines.append(f"- **{name}** ({activity_type}, {duration}h)")
        else:
            lines.append("- Free day / rest day")
        notes = day.get("notes")
        if notes:
            lines.append(f"\n_Notes: {notes}_")
        lines.append("")

    lines.append("## Packing List")
    lines.append("")
    for category, items in packing["categories"].items():
        lines.append(f"### {category.title()}")
        for item in items:
            lines.append(f"- {item}")
        lines.append("")
    packing_notes = packing.get("notes")
    if packing_notes:
        lines.append(f"_Notes: {packing_notes}_")
        lines.append("")

    return "\n".join(lines)


def plan_trip(city: str, days: int = 15, output_path: str = "trip_plan.md") -> str:
    """Coordinate all subagents to produce a full trip plan, writing it to `output_path`.

    Runs weather_subagent and activities_subagent in parallel, then
    scheduler_subagent, then packing_subagent, then report_synthesizer.
    Raises TripPlanningError if any step fails; the pipeline fails fast and
    does not proceed with partial data.
    """
    with ThreadPoolExecutor(max_workers=2) as executor:
        weather_future = executor.submit(weather_subagent, city, days)
        activities_future = executor.submit(activities_subagent, city)

        try:
            weather = weather_future.result()
        except Exception as exc:
            activities_future.cancel()
            raise TripPlanningError(f"weather_subagent failed: {exc}") from exc

        try:
            activities = activities_future.result()
        except Exception as exc:
            raise TripPlanningError(f"activities_subagent failed: {exc}") from exc

    try:
        schedule = scheduler_subagent(city, weather, activities)
    except Exception as exc:
        raise TripPlanningError(f"scheduler_subagent failed: {exc}") from exc

    try:
        packing = packing_subagent(schedule)
    except Exception as exc:
        raise TripPlanningError(f"packing_subagent failed: {exc}") from exc

    try:
        report = report_synthesizer(city, weather, activities, schedule, packing)
    except Exception as exc:
        raise TripPlanningError(f"report_synthesizer failed: {exc}") from exc

    with open(output_path, "w") as f:
        f.write(report)

    return report
