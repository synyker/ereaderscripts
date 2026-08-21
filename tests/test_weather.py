from datetime import datetime, timezone
from pathlib import Path

from weather import (
    FORECAST_QUERY,
    GUST_QUERY,
    OBSERVATION_QUERY,
    direction_name,
    fetch_weather,
    latest_value,
    parse_place,
    parse_position,
    parse_series,
    sky_description,
    symbol_label,
    wind_arrow,
)

FIXTURES = Path(__file__).parent / "fixtures"

# The fixtures were recorded for Kumpula on 21.8.2026: observations run to
# 04:10Z, the forecast covers 05:00Z-10:00Z (08-13 local).
NOW = datetime(2026, 8, 21, 4, 20, tzinfo=timezone.utc)


def fixture_fetcher(calls: list | None = None):
    """A fetcher that answers each stored query from its recorded response."""
    bodies = {
        OBSERVATION_QUERY: (FIXTURES / "fmi_observation.xml").read_bytes(),
        FORECAST_QUERY: (FIXTURES / "fmi_forecast.xml").read_bytes(),
        GUST_QUERY: (FIXTURES / "fmi_gusts.xml").read_bytes(),
    }

    def fetch(url: str, params: dict) -> bytes | None:
        if calls is not None:
            calls.append(params)
        return bodies.get(params["storedquery_id"])

    return fetch


def failing_fetcher(*, query: str):
    """A fetcher where one stored query fails and the rest succeed."""
    working = fixture_fetcher()

    def fetch(url: str, params: dict) -> bytes | None:
        if params["storedquery_id"] == query:
            return None
        return working(url, params)

    return fetch


def report(fetcher=None, **kwargs):
    return fetch_weather(
        fmisid=101004,
        hours=kwargs.pop("hours", 6),
        now=kwargs.pop("now", NOW),
        fetcher=fetcher or fixture_fetcher(),
    )


# --- Parsing -----------------------------------------------------------------

SERIES_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0"
    xmlns:gml="http://www.opengis.net/gml/3.2"
    xmlns:wml2="http://www.opengis.net/waterml/2.0">
  <wfs:member>
    <wml2:MeasurementTimeseries gml:id="obs-obs-1-1-r_1h">
      <wml2:point><wml2:MeasurementTVP>
        <wml2:time>2026-08-21T03:50:00Z</wml2:time><wml2:value>0.4</wml2:value>
      </wml2:MeasurementTVP></wml2:point>
      <wml2:point><wml2:MeasurementTVP>
        <wml2:time>2026-08-21T04:00:00Z</wml2:time><wml2:value>NaN</wml2:value>
      </wml2:MeasurementTVP></wml2:point>
    </wml2:MeasurementTimeseries>
  </wfs:member>
</wfs:FeatureCollection>
"""


def test_series_are_keyed_by_their_parameter_name():
    assert list(parse_series(SERIES_XML)) == ["r_1h"]


def test_series_values_keep_their_observation_times():
    times = [t for t, _ in parse_series(SERIES_XML)["r_1h"]]

    assert times[0] == datetime(2026, 8, 21, 3, 50, tzinfo=timezone.utc)


def test_latest_value_ignores_nan_samples():
    """FMI reports r_1h only on the hour, filling the gaps with NaN."""
    assert latest_value(parse_series(SERIES_XML)["r_1h"]) == 0.4


def test_latest_value_is_none_when_every_sample_is_nan():
    assert latest_value([(NOW, "NaN")]) is None


def test_latest_value_is_none_for_an_empty_series():
    assert latest_value([]) is None


# --- Current conditions ------------------------------------------------------


def test_observation_uses_the_most_recent_sample():
    assert report().observation.temperature == 12.1


def test_observation_carries_wind_speed_and_gust():
    observation = report().observation

    assert observation.wind_speed == 3.3
    assert observation.wind_gust == 5.5


def test_observation_carries_pressure_humidity_and_dew_point():
    observation = report().observation

    assert observation.pressure == 1003.8
    assert observation.humidity == 93.0
    assert observation.dew_point == 10.9


def test_observation_time_is_local_helsinki_time():
    """04:10Z is 07:10 in Helsinki in August."""
    assert report().observation.time.strftime("%H:%M") == "07:10"


def test_sky_description_reads_cloud_cover_in_octas():
    assert sky_description(0) == "selkeää"
    assert sky_description(5) == "puolipilvistä"
    assert sky_description(8) == "pilvistä"


def test_sky_description_is_none_when_cloud_cover_is_unavailable():
    """Octa 9 is FMI's 'sky obscured / not observed' code."""
    assert sky_description(9) is None
    assert sky_description(None) is None


# --- Forecast ----------------------------------------------------------------


def test_forecast_has_one_entry_per_hour():
    assert len(report().hours) == 6


def test_forecast_hours_are_local_and_consecutive():
    assert [h.time.strftime("%H") for h in report().hours] == ["08", "09", "10", "11", "12", "13"]


def test_forecast_hour_carries_temperature_wind_and_precipitation():
    first = report().hours[0]

    assert first.temperature == 13.31
    assert first.wind_speed == 2.8
    assert first.precipitation == 0.0


def test_gusts_come_from_the_harmonie_model():
    """The edited model returns NaN for WindGust, so gusts need their own query."""
    assert [h.wind_gust for h in report().hours[:3]] == [5.8, 5.6, 5.3]


def test_forecast_keeps_its_symbol_code():
    assert report().hours[0].symbol == 4


def test_requests_only_the_hours_that_will_be_shown():
    calls: list[dict] = []
    report(fetcher=fixture_fetcher(calls), hours=6)

    forecast = next(c for c in calls if c["storedquery_id"] == FORECAST_QUERY)
    # 04:20Z rounds up to the next full hour, and six hours run through 10:00Z
    assert forecast["starttime"] == "2026-08-21T05:00:00Z"
    assert forecast["endtime"] == "2026-08-21T10:00:00Z"


def test_a_shorter_window_asks_for_fewer_hours():
    calls: list[dict] = []
    report(fetcher=fixture_fetcher(calls), hours=3)

    forecast = next(c for c in calls if c["storedquery_id"] == FORECAST_QUERY)
    assert forecast["endtime"] == "2026-08-21T07:00:00Z"


# --- Sun ---------------------------------------------------------------------


def test_sunrise_and_sunset_are_local_times():
    """02:47Z / 17:57Z are 05:47 and 20:57 in Helsinki."""
    assert report().sunrise.strftime("%H:%M") == "05:47"
    assert report().sunset.strftime("%H:%M") == "20:57"


def test_day_length_is_reported_in_minutes():
    assert report().day_length == 910


# --- Symbols -----------------------------------------------------------------


def test_symbol_labels_are_short_enough_for_a_narrow_column():
    assert all(len(symbol_label(code)) <= 5 for code in range(1, 108))


def test_symbol_labels_name_the_condition():
    assert symbol_label(1) == "Selk"
    assert symbol_label(4) == "Ppilv"
    assert symbol_label(7) == "Pilv"
    assert symbol_label(24) == "Kuuro"
    assert symbol_label(34) == "Sade"
    assert symbol_label(74) == "Lumi"


def test_night_symbols_reuse_their_daytime_label():
    """SmartSymbol adds 100 for the night variant of the same weather."""
    assert symbol_label(104) == symbol_label(4)


def test_unknown_symbol_falls_back_to_a_dash():
    assert symbol_label(None) == "-"
    assert symbol_label(999) == "-"


# --- Place and wind direction ------------------------------------------------


def test_station_coordinates_come_from_the_observation_response():
    """One fmisid configures the whole page: the forecast follows the station."""
    assert parse_position((FIXTURES / "fmi_observation.xml").read_bytes()) == "60.20307,24.96131"


def test_position_is_none_when_the_response_carries_no_coordinates():
    assert parse_position(b"<wfs:FeatureCollection xmlns:wfs='http://www.opengis.net/wfs/2.0'/>") is None


def test_forecast_is_requested_at_the_station_coordinates():
    calls: list[dict] = []
    report(fetcher=fixture_fetcher(calls))

    forecast = next(c for c in calls if c["storedquery_id"] == FORECAST_QUERY)
    assert forecast["latlon"] == "60.20307,24.96131"


def test_no_report_when_the_station_has_no_coordinates():
    def positionless(url: str, params: dict) -> bytes:
        if params["storedquery_id"] == OBSERVATION_QUERY:
            return b"<wfs:FeatureCollection xmlns:wfs='http://www.opengis.net/wfs/2.0'/>"
        return fixture_fetcher()(url, params)

    assert report(fetcher=positionless) is None


def test_place_is_the_station_fmi_names_in_the_response():
    assert parse_place((FIXTURES / "fmi_observation.xml").read_bytes()) == "Helsinki Kumpula"


def test_place_is_none_when_the_response_names_no_station():
    assert parse_place(b"<wfs:FeatureCollection xmlns:wfs='http://www.opengis.net/wfs/2.0'/>") is None


def test_report_carries_the_station_name():
    assert report().place == "Helsinki Kumpula"


def test_wind_direction_names_where_the_wind_blows_from():
    assert direction_name(0) == "pohjoisesta"
    assert direction_name(90) == "idästä"
    assert direction_name(180) == "etelästä"
    assert direction_name(270) == "lännestä"


def test_wind_direction_rounds_to_the_nearest_of_eight_points():
    assert direction_name(292) == "lännestä"
    assert direction_name(315) == "luoteesta"
    assert direction_name(359) == "pohjoisesta"


def test_wind_direction_is_unnamed_when_missing():
    assert direction_name(None) is None


def test_wind_arrow_points_the_way_the_wind_is_going():
    """FMI reports the direction wind comes from; the arrow shows where it heads."""
    assert wind_arrow(270) == "→"  # from the west, blowing east
    assert wind_arrow(0) == "↓"  # from the north, blowing south


def test_wind_arrow_is_blank_when_direction_is_missing():
    assert wind_arrow(None) == ""


# --- Failure handling --------------------------------------------------------


def test_no_report_when_the_forecast_is_unavailable():
    assert report(fetcher=failing_fetcher(query=FORECAST_QUERY)) is None


def test_no_report_when_the_observation_is_unavailable():
    assert report(fetcher=failing_fetcher(query=OBSERVATION_QUERY)) is None


def test_forecast_survives_a_failed_gust_request():
    """Gusts are a nice-to-have; losing them must not cost us the whole page."""
    result = report(fetcher=failing_fetcher(query=GUST_QUERY))

    assert result is not None
    assert [h.wind_gust for h in result.hours] == [None] * 6
    assert result.hours[0].temperature == 13.31


def test_no_report_when_the_response_is_not_xml():
    def broken(url: str, params: dict) -> bytes:
        return b"<html>502 Bad Gateway</html>"

    assert report(fetcher=broken) is None
