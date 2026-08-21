"""Fetches Helsinki weather from FMI open data for the edition's Sää page.

Three stored queries feed one report: the Kumpula station's latest
observations, FMI's edited hourly forecast (the one their own app shows),
and HARMONIE for gusts — the edited model returns NaN for WindGust.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from lxml import etree

log = logging.getLogger(__name__)

HELSINKI = ZoneInfo("Europe/Helsinki")

WFS_URL = "https://opendata.fmi.fi/wfs"
TIMEOUT = 15

OBSERVATION_QUERY = "fmi::observations::weather::timevaluepair"
FORECAST_QUERY = "fmi::forecast::edited::weather::scandinavia::point::timevaluepair"
GUST_QUERY = "fmi::forecast::harmonie::surface::point::timevaluepair"

OBSERVATION_PARAMS = "t2m,ws_10min,wg_10min,wd_10min,rh,p_sea,n_man,vis,td"
FORECAST_PARAMS = (
    "Temperature,WindSpeedMS,WindDirection,Precipitation1h,SmartSymbol,"
    "sunrise,sunset,daylength"
)

WML2 = "{http://www.opengis.net/waterml/2.0}"
GML = "{http://www.opengis.net/gml/3.2}"

Fetcher = Callable[[str, dict], bytes | None]


@dataclass(frozen=True)
class Observation:
    """The station's most recent reading of each measurement."""

    time: datetime
    temperature: float | None
    dew_point: float | None
    humidity: float | None
    wind_speed: float | None
    wind_gust: float | None
    wind_direction: float | None
    pressure: float | None
    cloud_cover: float | None
    visibility: float | None


@dataclass(frozen=True)
class ForecastHour:
    time: datetime
    temperature: float | None
    wind_speed: float | None
    wind_gust: float | None
    wind_direction: float | None
    precipitation: float | None
    symbol: int | None


@dataclass(frozen=True)
class WeatherReport:
    place: str | None
    observation: Observation
    hours: list[ForecastHour]
    sunrise: datetime | None
    sunset: datetime | None
    day_length: int | None


# --- Parsing -----------------------------------------------------------------


def parse_series(xml: bytes) -> dict[str, list[tuple[datetime, str]]]:
    """Read a WFS timevaluepair response into {parameter: [(time, value)]}.

    Values stay as strings because FMI mixes numbers with timestamps —
    `sunrise` rides in the same envelope as `Temperature`.
    """
    try:
        root = etree.fromstring(xml)
    except etree.XMLSyntaxError as e:
        log.warning("FMI returned unparseable XML: %s", e)
        return {}

    series: dict[str, list[tuple[datetime, str]]] = {}
    for timeseries in root.iter(f"{WML2}MeasurementTimeseries"):
        # Observations are keyed "obs-obs-1-1-t2m", forecasts "mts-1-1-Temperature".
        name = (timeseries.get(f"{GML}id") or "").rsplit("-", 1)[-1]
        if not name:
            continue
        points = []
        for tvp in timeseries.iter(f"{WML2}MeasurementTVP"):
            time = tvp.findtext(f"{WML2}time")
            value = tvp.findtext(f"{WML2}value")
            if time is None or value is None:
                continue
            points.append((_parse_time(time), value.strip()))
        series[name] = points
    return series


def parse_place(xml: bytes) -> str | None:
    """The station name FMI labels the response with, e.g. "Helsinki Kumpula"."""
    try:
        root = etree.fromstring(xml)
    except etree.XMLSyntaxError:
        return None
    for name in root.iter(f"{GML}name"):
        text = (name.text or "").strip()
        # The same element carries numeric region codes; only names are useful.
        if text and not text.lstrip("-").isdigit():
            return text
    return None


def parse_position(xml: bytes) -> str | None:
    """The station's own coordinates, as "lat,lon" for a forecast query.

    Taking these from the observation response keeps the station the single
    thing to configure: the forecast always lands where the readings do.
    """
    try:
        root = etree.fromstring(xml)
    except etree.XMLSyntaxError:
        return None
    for position in root.iter(f"{GML}pos"):
        parts = (position.text or "").split()
        if len(parts) >= 2:
            return f"{parts[0]},{parts[1]}"
    return None


def latest_value(points: list[tuple[datetime, str]]) -> float | None:
    """The newest numeric sample, skipping the NaNs FMI pads gaps with."""
    for _time, value in reversed(points):
        number = _to_float(value)
        if number is not None:
            return number
    return None


def _parse_time(text: str) -> datetime:
    return datetime.fromisoformat(text.strip().replace("Z", "+00:00"))


def _to_float(value: str | None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number  # NaN != NaN


def _latest_time(points: list[tuple[datetime, str]]) -> datetime | None:
    for time, value in reversed(points):
        if _to_float(value) is not None:
            return time
    return None


def _parse_stamp(value: str | None) -> datetime | None:
    """FMI writes sun times as a compact 20260821T024743 stamp in UTC."""
    try:
        naive = datetime.strptime(value, "%Y%m%dT%H%M%S")
    except (TypeError, ValueError):
        return None
    return naive.replace(tzinfo=timezone.utc).astimezone(HELSINKI)


# --- Describing --------------------------------------------------------------

SYMBOL_LABELS = {
    1: "Selk", 2: "Selk",
    4: "Ppilv", 5: "Ppilv",
    6: "Pilv", 7: "Pilv",
    9: "Sumu",
    11: "Tihku", 14: "Tihku",
    17: "Jäätä",
    21: "Kuuro", 24: "Kuuro", 27: "Kuuro",
    31: "Sade", 34: "Sade", 37: "Sade",
    41: "Ränt", 44: "Ränt", 47: "Ränt",
    51: "Ränt", 54: "Ränt", 57: "Ränt",
    61: "Lumi", 64: "Lumi", 67: "Lumi",
    71: "Lumi", 74: "Lumi", 77: "Lumi",
    81: "Rae", 84: "Rae", 87: "Rae",
    91: "Utu", 92: "Sumu",
}


def symbol_label(code: int | None) -> str:
    """A column-width abbreviation for a SmartSymbol code."""
    if code is None:
        return "-"
    if code > 100:  # night variants are the daytime code plus 100
        code -= 100
    return SYMBOL_LABELS.get(code, "-")


DIRECTION_NAMES = (
    "pohjoisesta", "koillisesta", "idästä", "kaakosta",
    "etelästä", "lounaasta", "lännestä", "luoteesta",
)
# The X4's font has no arrow glyphs — it draws them as an empty box — so the
# forecast names the direction the wind blows from with the English compass
# abbreviation instead.
DIRECTION_LETTERS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def _sector(degrees: float | None) -> int | None:
    """Which of the eight compass points a bearing rounds to."""
    if degrees is None:
        return None
    return round(degrees / 45) % 8


def direction_name(degrees: float | None) -> str | None:
    """Name the direction the wind blows from, as Finnish forecasts do."""
    sector = _sector(degrees)
    return None if sector is None else DIRECTION_NAMES[sector]


def direction_letter(degrees: float | None) -> str:
    """The same direction as `direction_name`, abbreviated for a narrow line."""
    sector = _sector(degrees)
    return "" if sector is None else DIRECTION_LETTERS[sector]


def sky_description(octas: float | None) -> str | None:
    """Name the cloud cover the station reports in eighths of the sky."""
    if octas is None or octas > 8:  # 9 means the sky could not be observed
        return None
    if octas < 1:
        return "selkeää"
    if octas <= 2:
        return "enimmäkseen selkeää"
    if octas <= 5:
        return "puolipilvistä"
    if octas <= 7:
        return "enimmäkseen pilvistä"
    return "pilvistä"


# --- Fetching ----------------------------------------------------------------


def _get(url: str, params: dict) -> bytes | None:
    try:
        response = requests.get(url, params=params, timeout=TIMEOUT)
        response.raise_for_status()
        return response.content
    except requests.RequestException as e:
        log.warning("FMI query %s failed: %s", params.get("storedquery_id"), e)
        return None


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _next_full_hour(now: datetime) -> datetime:
    return (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)


def fetch_weather(
    *,
    fmisid: int,
    hours: int,
    now: datetime,
    fetcher: Fetcher | None = None,
) -> WeatherReport | None:
    """Build one weather report, or None if FMI can't be reached or understood."""
    fetch = fetcher or _get
    start = _next_full_hour(now)
    end = start + timedelta(hours=hours - 1)
    window = {
        "timestep": "60",
        "starttime": _stamp(start),
        "endtime": _stamp(end),
    }

    observation_xml = fetch(WFS_URL, {
        **_BASE_PARAMS,
        "storedquery_id": OBSERVATION_QUERY,
        "fmisid": str(fmisid),
        "parameters": OBSERVATION_PARAMS,
        "starttime": _stamp(now - timedelta(hours=1)),
    }) or b""

    observation = _build_observation(parse_series(observation_xml))
    latlon = parse_position(observation_xml)
    if observation is None or latlon is None:
        log.warning("Station %s gave no usable observations; leaving the Sää page out", fmisid)
        return None

    forecast = parse_series(
        fetch(WFS_URL, {
            **_BASE_PARAMS,
            "storedquery_id": FORECAST_QUERY,
            "latlon": latlon,
            "parameters": FORECAST_PARAMS,
            **window,
        }) or b""
    )
    if "Temperature" not in forecast:
        log.warning("FMI returned no forecast for %s; leaving the Sää page out", latlon)
        return None

    gusts = parse_series(
        fetch(WFS_URL, {
            **_BASE_PARAMS,
            "storedquery_id": GUST_QUERY,
            "latlon": latlon,
            "parameters": "WindGust",
            **window,
        }) or b""
    )

    return WeatherReport(
        place=parse_place(observation_xml),
        observation=observation,
        hours=_build_hours(forecast, gusts),
        sunrise=_parse_stamp(_first(forecast, "sunrise")),
        sunset=_parse_stamp(_first(forecast, "sunset")),
        day_length=_int_or_none(_first(forecast, "daylength")),
    )


_BASE_PARAMS = {
    "service": "WFS",
    "version": "2.0.0",
    "request": "getFeature",
}


def _first(series: dict[str, list[tuple[datetime, str]]], name: str) -> str | None:
    """Sun times repeat on every timestep, so any one of them will do."""
    points = series.get(name) or []
    return points[0][1] if points else None


def _int_or_none(value: str | None) -> int | None:
    number = _to_float(value)
    return None if number is None else int(number)


def _build_observation(series: dict[str, list[tuple[datetime, str]]]) -> Observation | None:
    time = _latest_time(series.get("t2m") or [])
    if time is None:
        return None
    return Observation(
        time=time.astimezone(HELSINKI),
        temperature=latest_value(series.get("t2m") or []),
        dew_point=latest_value(series.get("td") or []),
        humidity=latest_value(series.get("rh") or []),
        wind_speed=latest_value(series.get("ws_10min") or []),
        wind_gust=latest_value(series.get("wg_10min") or []),
        wind_direction=latest_value(series.get("wd_10min") or []),
        pressure=latest_value(series.get("p_sea") or []),
        cloud_cover=latest_value(series.get("n_man") or []),
        visibility=latest_value(series.get("vis") or []),
    )


def _build_hours(
    forecast: dict[str, list[tuple[datetime, str]]],
    gusts: dict[str, list[tuple[datetime, str]]],
) -> list[ForecastHour]:
    def by_time(series: dict, name: str) -> dict[datetime, str]:
        return dict(series.get(name) or [])

    temperature = by_time(forecast, "Temperature")
    wind = by_time(forecast, "WindSpeedMS")
    direction = by_time(forecast, "WindDirection")
    rain = by_time(forecast, "Precipitation1h")
    symbol = by_time(forecast, "SmartSymbol")
    gust = by_time(gusts, "WindGust")

    return [
        ForecastHour(
            time=time.astimezone(HELSINKI),
            temperature=_to_float(temperature.get(time)),
            wind_speed=_to_float(wind.get(time)),
            wind_gust=_to_float(gust.get(time)),
            wind_direction=_to_float(direction.get(time)),
            precipitation=_to_float(rain.get(time)),
            symbol=_int_or_none(symbol.get(time)),
        )
        for time in sorted(temperature)
    ]
