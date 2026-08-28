from unittest.mock import Mock

import pytest
from geopy.exc import GeocoderTimedOut
from requests.exceptions import ConnectionError as RequestsConnectionError

import weatherTool
from weatherTool import CityNotFoundError, WeatherLookupError, get_city_temperature


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


def make_fake_response(temperature: float):
    response = Mock()
    current = Mock()
    current.Variables.return_value.Value.return_value = temperature
    response.Current.return_value = current
    return response


@pytest.fixture(autouse=True)
def stub_network_dependencies(monkeypatch):
    monkeypatch.setattr(weatherTool, "requests_cache", Mock(CachedSession=Mock(return_value=Mock())))
    monkeypatch.setattr(weatherTool, "retry", Mock(return_value=Mock()))


def patch_geocoder(monkeypatch, geolocator):
    monkeypatch.setattr(weatherTool, "Nominatim", Mock(return_value=geolocator))


def patch_openmeteo(monkeypatch, client):
    monkeypatch.setattr(weatherTool.openmeteo_requests, "Client", Mock(return_value=client))


def test_returns_temperature_on_success(monkeypatch):
    location = Mock(latitude=64.1466, longitude=-21.9426)
    geolocator = FakeNominatim(geocode_result=location)
    patch_geocoder(monkeypatch, geolocator)

    client = FakeOpenMeteoClient(weather_api_result=[make_fake_response(21.5)])
    patch_openmeteo(monkeypatch, client)

    result = get_city_temperature("Reykjavik, Iceland")

    assert result == 21.5
    _, kwargs = client.weather_api.call_args
    assert kwargs["params"]["latitude"] == 64.1466
    assert kwargs["params"]["longitude"] == -21.9426


@pytest.mark.parametrize("blank_input", ["", "   "])
def test_raises_value_error_on_blank_input(monkeypatch, blank_input):
    geolocator = FakeNominatim(geocode_result=Mock())
    patch_geocoder(monkeypatch, geolocator)

    with pytest.raises(ValueError):
        get_city_temperature(blank_input)

    geolocator.geocode.assert_not_called()


def test_raises_city_not_found_when_location_is_none(monkeypatch):
    geolocator = FakeNominatim(geocode_result=None)
    patch_geocoder(monkeypatch, geolocator)

    with pytest.raises(CityNotFoundError):
        get_city_temperature("asdkfjhaskdjfh")


def test_raises_city_not_found_on_geocoder_service_error(monkeypatch):
    original_error = GeocoderTimedOut("timed out")
    geolocator = FakeNominatim(geocode_error=original_error)
    patch_geocoder(monkeypatch, geolocator)

    with pytest.raises(CityNotFoundError) as exc_info:
        get_city_temperature("Reykjavik, Iceland")

    assert exc_info.value.__cause__ is original_error


def test_raises_weather_lookup_error_on_empty_responses(monkeypatch):
    location = Mock(latitude=64.1466, longitude=-21.9426)
    geolocator = FakeNominatim(geocode_result=location)
    patch_geocoder(monkeypatch, geolocator)

    client = FakeOpenMeteoClient(weather_api_result=[])
    patch_openmeteo(monkeypatch, client)

    with pytest.raises(WeatherLookupError):
        get_city_temperature("Reykjavik, Iceland")


def test_raises_weather_lookup_error_on_request_exception(monkeypatch):
    location = Mock(latitude=64.1466, longitude=-21.9426)
    geolocator = FakeNominatim(geocode_result=location)
    patch_geocoder(monkeypatch, geolocator)

    original_error = RequestsConnectionError("connection refused")
    client = FakeOpenMeteoClient(weather_api_error=original_error)
    patch_openmeteo(monkeypatch, client)

    with pytest.raises(WeatherLookupError) as exc_info:
        get_city_temperature("Reykjavik, Iceland")

    assert exc_info.value.__cause__ is original_error
