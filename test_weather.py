import io
import json
import unittest
from unittest.mock import patch
import weather

class WeatherTests(unittest.TestCase):
    def test_compact_cache(self):
        now=1800000000
        raw={"current":{"time":now,"temperature_2m":5,"weather_code":3},
             "hourly":{"time":[now-7200,now,now+3600,now+172900],
                       "temperature_2m":[1,5,6,9],"weather_code":[0,3,2,1],
                       "precipitation_probability":[0,30,40,10]}}
        with patch.object(weather.time,"time",return_value=now),patch.object(weather.urllib.request,"urlopen",return_value=io.BytesIO(json.dumps(raw).encode())):
            data=weather.fetch({"location":"Podolsk","latitude":55,"longitude":37})
        self.assertEqual(len(data["hours"]),2)
        self.assertEqual(data["current"]["rain"],30)
        self.assertLess(len(json.dumps(data)),8192)

if __name__=="__main__":unittest.main()

class MetWeatherTests(unittest.TestCase):
    def test_met_cached_forecast(self):
        now=1800000000
        raw={"properties":{"timeseries":[
            {"time":"2027-01-15T08:00:00Z","data":{"instant":{"details":{"air_temperature":-5}}}},
            {"time":"2027-01-15T09:00:00Z","data":{"instant":{"details":{"air_temperature":-4}}}}
        ]}}
        weather.MET_CACHE[("55.4242","37.5547")]={"raw":raw,"expires":now+3600}
        with patch.object(weather.time,"time",return_value=now),patch.object(weather.urllib.request,"urlopen") as network:
            result=weather.fetch_met({"location":"Podolsk","latitude":55.42419,"longitude":37.55472})
        network.assert_not_called()
        self.assertEqual(result["current"]["rain"],-1)
        self.assertTrue(result["source"].startswith("MET Norway"))
        weather.MET_CACHE.clear()
