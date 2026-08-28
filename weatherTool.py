import openmeteo_requests
import requests_cache
from geopy.exc import GeopyError
from geopy.geocoders import Nominatim
from requests.exceptions import RequestException
from retry_requests import retry


class CityNotFoundError(ValueError):
    """Raised when the given city/country string cannot be geocoded."""


class WeatherLookupError(RuntimeError):
    """Raised when the Open-Meteo API call fails or returns no data."""


def get_city_temperature(city_name_country: str) -> float:
    """Return the current temperature (°C) for a "City, Country" string.

    Raises:
        ValueError: `city_name_country` is empty or blank.
        CityNotFoundError: the location could not be geocoded.
        WeatherLookupError: the weather API call failed or returned no data.
    """
    if not city_name_country or not city_name_country.strip():
        raise ValueError("city_name_country must be a non-empty string")

    geolocator = Nominatim(user_agent="my_city_finder")
    try:
        location = geolocator.geocode(city_name_country)
    except GeopyError as exc:
        raise CityNotFoundError(
            f"Failed to geocode '{city_name_country}': {exc}"
        ) from exc

    if location is None:
        raise CityNotFoundError(f"Could not find location for '{city_name_country}'")

    cache_session = requests_cache.CachedSession(".cache", expire_after=3600)
    retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
    openmeteo = openmeteo_requests.Client(session=retry_session)

    params = {
        "latitude": location.latitude,
        "longitude": location.longitude,
        "current": "temperature_2m",
    }

    try:
        responses = openmeteo.weather_api(
            "https://api.open-meteo.com/v1/forecast", params=params
        )
    except RequestException as exc:
        raise WeatherLookupError(f"Weather API request failed: {exc}") from exc

    if not responses:
        raise WeatherLookupError(f"No weather data returned for '{city_name_country}'")

    current = responses[0].Current()
    return current.Variables(0).Value()
