import json
import math
from datetime import datetime, timezone

import openmeteo_requests
import requests_cache
from geopy.exc import GeopyError
from geopy.geocoders import Nominatim
from requests.exceptions import RequestException
from retry_requests import retry

from weatherTool import CityNotFoundError, WeatherLookupError

_DAILY_VARIABLES = [
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "relative_humidity_2m_max",
    "relative_humidity_2m_min",
    "relative_humidity_2m_mean",
    "precipitation_sum",
    "rain_sum",
    "showers_sum",
    "snowfall_sum",
    "precipitation_hours",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
    "wind_direction_10m_dominant",
    "weather_code",
]

_WMO_WEATHER_CODES = {
    0: "Clear/Sunny",
    1: "Mainly Clear",
    2: "Partly Cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Fog",
    51: "Drizzle",
    53: "Drizzle",
    55: "Drizzle",
    56: "Drizzle",
    57: "Drizzle",
    61: "Rain",
    63: "Rain",
    65: "Rain",
    66: "Rain",
    67: "Rain",
    71: "Snow",
    73: "Snow",
    75: "Snow",
    77: "Snow",
    80: "Rain Showers",
    81: "Rain Showers",
    82: "Rain Showers",
    85: "Snow Showers",
    86: "Snow Showers",
    95: "Thunderstorm",
    96: "Thunderstorm",
    99: "Thunderstorm",
}


def _describe_weather_code(code: int) -> str:
    return _WMO_WEATHER_CODES.get(code, "Unknown")


def _clean(value):
    """Open-Meteo returns NaN for days beyond a variable's model coverage
    (e.g. the outer days of a 16-day forecast) — map that to None/null."""
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def get_city_forecast(city_name_country: str, days: int = 15) -> str:
    """Return a multi-day forecast for a "City, Country" string as a JSON string.

    Each day in the "forecast" list groups temperature, humidity,
    precipitation (rain/showers/snow), wind, and sky condition.

    Raises:
        ValueError: `city_name_country` is empty/blank, or `days` is not in 1..16.
        CityNotFoundError: the location could not be geocoded.
        WeatherLookupError: the weather API call failed or returned no data.
    """
    if not city_name_country or not city_name_country.strip():
        raise ValueError("city_name_country must be a non-empty string")
    if not 1 <= days <= 16:
        raise ValueError("days must be between 1 and 16")

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
        "daily": ",".join(_DAILY_VARIABLES),
        "forecast_days": days,
        "timezone": "auto",
    }

    try:
        responses = openmeteo.weather_api(
            "https://api.open-meteo.com/v1/forecast", params=params
        )
    except RequestException as exc:
        raise WeatherLookupError(f"Weather API request failed: {exc}") from exc

    if not responses:
        raise WeatherLookupError(f"No weather data returned for '{city_name_country}'")

    response = responses[0]
    daily = response.Daily()

    values = {}
    for i, name in enumerate(_DAILY_VARIABLES):
        variable = daily.Variables(i)
        values[name] = [variable.Values(j) for j in range(variable.ValuesLength())]

    dates = [
        datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat()
        for t in range(daily.Time(), daily.TimeEnd(), daily.Interval())
    ]

    forecast = []
    for day_index, date in enumerate(dates):
        raw_weather_code = _clean(values["weather_code"][day_index])
        weather_code = int(raw_weather_code) if raw_weather_code is not None else None
        condition = _describe_weather_code(weather_code) if weather_code is not None else "Unknown"
        forecast.append(
            {
                "date": date,
                "temperature_c": {
                    "max": _clean(values["temperature_2m_max"][day_index]),
                    "min": _clean(values["temperature_2m_min"][day_index]),
                    "mean": _clean(values["temperature_2m_mean"][day_index]),
                },
                "humidity_pct": {
                    "max": _clean(values["relative_humidity_2m_max"][day_index]),
                    "min": _clean(values["relative_humidity_2m_min"][day_index]),
                    "mean": _clean(values["relative_humidity_2m_mean"][day_index]),
                },
                "precipitation": {
                    "total_mm": _clean(values["precipitation_sum"][day_index]),
                    "rain_mm": _clean(values["rain_sum"][day_index]),
                    "showers_mm": _clean(values["showers_sum"][day_index]),
                    "snowfall_cm": _clean(values["snowfall_sum"][day_index]),
                    "hours": _clean(values["precipitation_hours"][day_index]),
                },
                "wind": {
                    "speed_max_kmh": _clean(values["wind_speed_10m_max"][day_index]),
                    "gusts_max_kmh": _clean(values["wind_gusts_10m_max"][day_index]),
                    "direction_dominant_deg": _clean(values["wind_direction_10m_dominant"][day_index]),
                },
                "sky": {
                    "weather_code": weather_code,
                    "condition": condition,
                },
            }
        )

    response_timezone = response.Timezone()
    if isinstance(response_timezone, bytes):
        response_timezone = response_timezone.decode()

    result = {
        "city": city_name_country,
        "latitude": location.latitude,
        "longitude": location.longitude,
        "timezone": response_timezone,
        "forecast": forecast,
    }
    return json.dumps(result, indent=2)
