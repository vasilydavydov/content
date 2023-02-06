import demistomock as demisto  # noqa: F401
from CommonServerPython import *  # noqa: F401

query = demisto.context().get('uniqueRegionCount')
data = {
    "Type": 17,
    "ContentsFormat": "number",
    "Contents": {
        "stats": query,
        "params": {
            "layout": "horizontal",
            "name": "Unique Region Count",
            "sign": "",
            "colors": {
                "items": {
                    "#00cd33": {
                        "value": -1
                    },
                    "#f57d00": {
                        "value": 1
                    }
                }
            },
            "type": "above"
        }
    }
}

demisto.results(data)
