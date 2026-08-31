import json

import pytest
from geopy.exc import GeocoderTimedOut
from requests.exceptions import ConnectionError as RequestsConnectionError
from unittest.mock import Mock

import weatherTool_V2
from weatherTool import CityNotFoundError, WeatherLookupError
from weatherTool_V2 import get_city_forecast, _DAILY_VARIABLES, _describe_weather_code


class FakeNominatim:
    def __init__(self, geocode_result=None, geocode_error=None):
        self._geocode_result = geocode_result
        self._geocode_error = geocode_error
        self.geocode = Mock(side_effect=self._geocode)

    def _geocode(self, _query):
        if self._geocode_error is not None:
            raise self._geocode_error
        return self._geocode_result


class FakeOpenMeteoClient:
    def __init__(self, weather_api_result=None, weather_api_error=None):
        self._weather_api_result = weather_api_result
        self._weather_api_error = weather_api_error
        self.weather_api = Mock(side_effect=self._weather_api)

    def _weather_api(self, _url, params=None):
        if self._weather_api_error is not None:
            raise self._weather_api_error
        return self._weather_api_result


def make_fake_response(day_values: dict, timezone_name: str = "Atlantic/Reykjavik"):
    """day_values maps a variable name (from _DAILY_VARIABLES) to a list of
    per-day floats, all the same length."""
    num_days = len(next(iter(day_values.values())))
    start = 1798761600  # 2027-01-01T00:00:00Z
    interval = 86400

    daily = Mock()
    daily.Time.return_value = start
    daily.TimeEnd.return_value = start + interval * num_days
    daily.Interval.return_value = interval

    def variables(i):
        name = _DAILY_VARIABLES[i]
        day_list = day_values[name]
        var = Mock()
        var.ValuesLength.return_value = len(day_list)
        var.Values.side_effect = lambda j, _values=day_list: _values[j]
        return var

    daily.Variables.side_effect = variables

    response = Mock()
    response.Daily.return_value = daily
    response.Timezone.return_value = timezone_name
    return response


def default_day_values(num_days: int = 2) -> dict:
    return {name: [float(i) for i in range(num_days)] for name in _DAILY_VARIABLES}


@pytest.fixture(autouse=True)
def stub_network_dependencies(monkeypatch):
    monkeypatch.setattr(weatherTool_V2, "requests_cache", Mock(CachedSession=Mock(return_value=Mock())))
    monkeypatch.setattr(weatherTool_V2, "retry", Mock(return_value=Mock()))


def patch_geocoder(monkeypatch, geolocator):
    monkeypatch.setattr(weatherTool_V2, "Nominatim", Mock(return_value=geolocator))


def patch_openmeteo(monkeypatch, client):
    monkeypatch.setattr(weatherTool_V2.openmeteo_requests, "Client", Mock(return_value=client))


def test_returns_forecast_grouped_by_day(monkeypatch):
    location = Mock(latitude=64.1466, longitude=-21.9426)
    geolocator = FakeNominatim(geocode_result=location)
    patch_geocoder(monkeypatch, geolocator)

    day_values = default_day_values(num_days=2)
    day_values["weather_code"] = [0.0, 61.0]
    client = FakeOpenMeteoClient(weather_api_result=[make_fake_response(day_values)])
    patch_openmeteo(monkeypatch, client)

    result = get_city_forecast("Reykjavik, Iceland", days=2)
    data = json.loads(result)

    assert data["city"] == "Reykjavik, Iceland"
    assert data["latitude"] == 64.1466
    assert data["longitude"] == -21.9426
    assert data["timezone"] == "Atlantic/Reykjavik"
    assert len(data["forecast"]) == 2

    day0 = data["forecast"][0]
    assert day0["date"] == "2027-01-01"
    assert day0["temperature_c"] == {"max": 0.0, "min": 0.0, "mean": 0.0}
    assert day0["humidity_pct"] == {"max": 0.0, "min": 0.0, "mean": 0.0}
    assert day0["precipitation"] == {
        "total_mm": 0.0, "rain_mm": 0.0, "showers_mm": 0.0,
        "snowfall_cm": 0.0, "hours": 0.0,
    }
    assert day0["wind"] == {"speed_max_kmh": 0.0, "gusts_max_kmh": 0.0, "direction_dominant_deg": 0.0}
    assert day0["sky"] == {"weather_code": 0, "condition": "Clear/Sunny"}

    day1 = data["forecast"][1]
    assert day1["date"] == "2027-01-02"
    assert day1["sky"] == {"weather_code": 61, "condition": "Rain"}

    _, kwargs = client.weather_api.call_args
    assert kwargs["params"]["latitude"] == 64.1466
    assert kwargs["params"]["forecast_days"] == 2
    assert kwargs["params"]["timezone"] == "auto"


def test_nan_values_are_reported_as_null(monkeypatch):
    """Open-Meteo returns NaN for outer days of an extended forecast that
    fall outside a variable's model coverage; the client shouldn't crash."""
    location = Mock(latitude=64.1466, longitude=-21.9426)
    geolocator = FakeNominatim(geocode_result=location)
    patch_geocoder(monkeypatch, geolocator)

    day_values = default_day_values(num_days=2)
    for name in _DAILY_VARIABLES:
        day_values[name][1] = float("nan")
    client = FakeOpenMeteoClient(weather_api_result=[make_fake_response(day_values)])
    patch_openmeteo(monkeypatch, client)

    result = get_city_forecast("Reykjavik, Iceland", days=2)
    data = json.loads(result)

    day1 = data["forecast"][1]
    assert day1["temperature_c"] == {"max": None, "min": None, "mean": None}
    assert day1["sky"] == {"weather_code": None, "condition": "Unknown"}


@pytest.mark.parametrize("blank_input", ["", "   "])
def test_raises_value_error_on_blank_input(monkeypatch, blank_input):
    geolocator = FakeNominatim(geocode_result=Mock())
    patch_geocoder(monkeypatch, geolocator)

    with pytest.raises(ValueError):
        get_city_forecast(blank_input)

    geolocator.geocode.assert_not_called()


@pytest.mark.parametrize("bad_days", [0, -1, 17])
def test_raises_value_error_on_out_of_range_days(monkeypatch, bad_days):
    geolocator = FakeNominatim(geocode_result=Mock())
    patch_geocoder(monkeypatch, geolocator)

    with pytest.raises(ValueError):
        get_city_forecast("Reykjavik, Iceland", days=bad_days)

    geolocator.geocode.assert_not_called()


def test_raises_city_not_found_when_location_is_none(monkeypatch):
    geolocator = FakeNominatim(geocode_result=None)
    patch_geocoder(monkeypatch, geolocator)

    with pytest.raises(CityNotFoundError):
        get_city_forecast("asdkfjhaskdjfh")


def test_raises_city_not_found_on_geocoder_service_error(monkeypatch):
    original_error = GeocoderTimedOut("timed out")
    geolocator = FakeNominatim(geocode_error=original_error)
    patch_geocoder(monkeypatch, geolocator)

    with pytest.raises(CityNotFoundError) as exc_info:
        get_city_forecast("Reykjavik, Iceland")

    assert exc_info.value.__cause__ is original_error


def test_raises_weather_lookup_error_on_empty_responses(monkeypatch):
    location = Mock(latitude=64.1466, longitude=-21.9426)
    geolocator = FakeNominatim(geocode_result=location)
    patch_geocoder(monkeypatch, geolocator)

    client = FakeOpenMeteoClient(weather_api_result=[])
    patch_openmeteo(monkeypatch, client)

    with pytest.raises(WeatherLookupError):
        get_city_forecast("Reykjavik, Iceland")


def test_raises_weather_lookup_error_on_request_exception(monkeypatch):
    location = Mock(latitude=64.1466, longitude=-21.9426)
    geolocator = FakeNominatim(geocode_result=location)
    patch_geocoder(monkeypatch, geolocator)

    original_error = RequestsConnectionError("connection refused")
    client = FakeOpenMeteoClient(weather_api_error=original_error)
    patch_openmeteo(monkeypatch, client)

    with pytest.raises(WeatherLookupError) as exc_info:
        get_city_forecast("Reykjavik, Iceland")

    assert exc_info.value.__cause__ is original_error


def test_describe_weather_code_unknown_code():
    assert _describe_weather_code(9999) == "Unknown"
