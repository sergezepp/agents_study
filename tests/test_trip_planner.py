import json
import time
from unittest.mock import Mock

import pytest

import trip_planner
from trip_planner import (
    SubagentError,
    TripPlanningError,
    activities_subagent,
    packing_subagent,
    plan_trip,
    report_synthesizer,
    scheduler_subagent,
    weather_subagent,
)


def make_weather(num_days: int = 2) -> dict:
    return {
        "city": "Lisbon, Portugal",
        "latitude": 38.7,
        "longitude": -9.1,
        "timezone": "Europe/Lisbon",
        "forecast": [
            {
                "date": f"2027-01-{i + 1:02d}",
                "temperature_c": {"max": 15.0 + i, "min": 8.0 + i, "mean": 11.0 + i},
                "humidity_pct": {"max": 80.0, "min": 50.0, "mean": 65.0},
                "precipitation": {
                    "total_mm": 0.0 if i == 0 else 5.0,
                    "rain_mm": 0.0 if i == 0 else 5.0,
                    "showers_mm": 0.0,
                    "snowfall_cm": 0.0,
                    "hours": 0.0 if i == 0 else 3.0,
                },
                "wind": {"speed_max_kmh": 10.0, "gusts_max_kmh": 20.0, "direction_dominant_deg": 180.0},
                "sky": {"weather_code": 0 if i == 0 else 61, "condition": "Clear/Sunny" if i == 0 else "Rain"},
            }
            for i in range(num_days)
        ],
    }


def make_activities() -> list[dict]:
    return [
        {"name": "Castle Tour", "type": "outdoor", "duration_hours": 3, "area": "Alfama", "description": "Historic castle."},
        {"name": "Museum Visit", "type": "indoor", "duration_hours": 2, "area": "Belem", "description": "Art museum."},
    ]


def make_schedule() -> dict:
    return {
        "days": [
            {
                "date": "2027-01-01",
                "weather_condition": "Clear/Sunny",
                "planned_activities": [{"name": "Castle Tour", "duration_hours": 3, "type": "outdoor"}],
                "notes": "Great day for sightseeing.",
            },
            {
                "date": "2027-01-02",
                "weather_condition": "Rain",
                "planned_activities": [{"name": "Museum Visit", "duration_hours": 2, "type": "indoor"}],
                "notes": "Bring an umbrella.",
            },
        ]
    }


def make_packing() -> dict:
    return {
        "categories": {
            "clothing": ["Light jacket", "Rain jacket"],
            "gear": ["Umbrella"],
            "toiletries": ["Sunscreen"],
            "other": ["Camera"],
        },
        "notes": "Layer for variable temperatures.",
    }


def test_weather_subagent_returns_parsed_forecast(monkeypatch):
    weather = make_weather()
    monkeypatch.setattr(trip_planner, "get_city_forecast", Mock(return_value=json.dumps(weather)))

    result = weather_subagent("Lisbon, Portugal", days=2)

    assert result == weather
    trip_planner.get_city_forecast.assert_called_once_with("Lisbon, Portugal", 2)


def test_activities_subagent_returns_list(monkeypatch):
    activities = make_activities()
    monkeypatch.setattr(trip_planner, "_ask_claude", Mock(return_value=activities))

    result = activities_subagent("Lisbon, Portugal")

    assert result == activities


def test_activities_subagent_raises_on_non_list(monkeypatch):
    monkeypatch.setattr(trip_planner, "_ask_claude", Mock(return_value={"not": "a list"}))

    with pytest.raises(SubagentError):
        activities_subagent("Lisbon, Portugal")


def test_activities_subagent_propagates_subagent_error(monkeypatch):
    monkeypatch.setattr(trip_planner, "_ask_claude", Mock(side_effect=SubagentError("bad json")))

    with pytest.raises(SubagentError):
        activities_subagent("Lisbon, Portugal")


def test_scheduler_subagent_returns_schedule(monkeypatch):
    schedule = make_schedule()
    ask_claude = Mock(return_value=schedule)
    monkeypatch.setattr(trip_planner, "_ask_claude", ask_claude)

    result = scheduler_subagent("Lisbon, Portugal", make_weather(), make_activities())

    assert result == schedule
    _, user_content = ask_claude.call_args[0]
    payload = json.loads(user_content)
    assert payload["city"] == "Lisbon, Portugal"
    assert len(payload["daily_weather"]) == 2
    assert payload["activities"] == make_activities()


def test_scheduler_subagent_raises_on_missing_days_key(monkeypatch):
    monkeypatch.setattr(trip_planner, "_ask_claude", Mock(return_value={"oops": True}))

    with pytest.raises(SubagentError):
        scheduler_subagent("Lisbon, Portugal", make_weather(), make_activities())


def test_packing_subagent_returns_packing_list(monkeypatch):
    packing = make_packing()
    ask_claude = Mock(return_value=packing)
    monkeypatch.setattr(trip_planner, "_ask_claude", ask_claude)

    result = packing_subagent(make_schedule())

    assert result == packing
    _, user_content = ask_claude.call_args[0]
    assert json.loads(user_content) == make_schedule()


def test_packing_subagent_raises_on_missing_categories_key(monkeypatch):
    monkeypatch.setattr(trip_planner, "_ask_claude", Mock(return_value={"oops": True}))

    with pytest.raises(SubagentError):
        packing_subagent(make_schedule())


def test_report_synthesizer_contains_expected_content():
    report = report_synthesizer(
        "Lisbon, Portugal", make_weather(), make_activities(), make_schedule(), make_packing()
    )

    assert "# Trip Plan: Lisbon, Portugal" in report
    assert "2027-01-01" in report
    assert "2027-01-02" in report
    assert "Castle Tour" in report
    assert "Museum Visit" in report
    assert "Great day for sightseeing." in report
    assert "## Packing List" in report
    assert "Rain jacket" in report
    assert "Layer for variable temperatures." in report


def test_plan_trip_orchestrates_subagents_in_order(monkeypatch, tmp_path):
    weather = make_weather()
    activities = make_activities()
    schedule = make_schedule()
    packing = make_packing()
    report_text = "# Trip Plan"

    weather_mock = Mock(return_value=weather)
    activities_mock = Mock(return_value=activities)
    scheduler_mock = Mock(return_value=schedule)
    packing_mock = Mock(return_value=packing)
    report_mock = Mock(return_value=report_text)

    monkeypatch.setattr(trip_planner, "weather_subagent", weather_mock)
    monkeypatch.setattr(trip_planner, "activities_subagent", activities_mock)
    monkeypatch.setattr(trip_planner, "scheduler_subagent", scheduler_mock)
    monkeypatch.setattr(trip_planner, "packing_subagent", packing_mock)
    monkeypatch.setattr(trip_planner, "report_synthesizer", report_mock)

    output_path = tmp_path / "trip_plan.md"
    result = plan_trip("Lisbon, Portugal", days=2, output_path=str(output_path))

    assert result == report_text
    weather_mock.assert_called_once_with("Lisbon, Portugal", 2)
    activities_mock.assert_called_once_with("Lisbon, Portugal")
    scheduler_mock.assert_called_once_with("Lisbon, Portugal", weather, activities)
    packing_mock.assert_called_once_with(schedule)
    report_mock.assert_called_once_with("Lisbon, Portugal", weather, activities, schedule, packing)
    assert output_path.read_text() == report_text


def test_plan_trip_fails_fast_when_weather_subagent_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(trip_planner, "weather_subagent", Mock(side_effect=RuntimeError("geocoding failed")))
    activities_mock = Mock(return_value=make_activities())
    scheduler_mock = Mock()
    packing_mock = Mock()
    report_mock = Mock()
    monkeypatch.setattr(trip_planner, "activities_subagent", activities_mock)
    monkeypatch.setattr(trip_planner, "scheduler_subagent", scheduler_mock)
    monkeypatch.setattr(trip_planner, "packing_subagent", packing_mock)
    monkeypatch.setattr(trip_planner, "report_synthesizer", report_mock)

    with pytest.raises(TripPlanningError) as exc_info:
        plan_trip("Nowhere", days=2, output_path=str(tmp_path / "trip_plan.md"))

    assert "weather_subagent failed" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    scheduler_mock.assert_not_called()
    packing_mock.assert_not_called()
    report_mock.assert_not_called()


def test_plan_trip_runs_weather_and_activities_in_parallel(monkeypatch, tmp_path):
    def slow_weather(city, days):
        time.sleep(0.2)
        return make_weather()

    def slow_activities(city):
        time.sleep(0.2)
        return make_activities()

    monkeypatch.setattr(trip_planner, "weather_subagent", slow_weather)
    monkeypatch.setattr(trip_planner, "activities_subagent", slow_activities)
    monkeypatch.setattr(trip_planner, "scheduler_subagent", Mock(return_value=make_schedule()))
    monkeypatch.setattr(trip_planner, "packing_subagent", Mock(return_value=make_packing()))
    monkeypatch.setattr(trip_planner, "report_synthesizer", Mock(return_value="report"))

    start = time.monotonic()
    plan_trip("Lisbon, Portugal", days=2, output_path=str(tmp_path / "trip_plan.md"))
    elapsed = time.monotonic() - start

    assert elapsed < 0.35
