from enum import StrEnum


class TransportMode(StrEnum):
    LOCAL_BUS = "local_bus"
    REGIONAL_BUS = "regional_bus"
    TRAIN = "train"
    TRAM = "tram"
    TROLLEYBUS = "trolleybus"
    SUBWAY = "subway"
    LIGHT_RAIL = "light_rail"
    MONORAIL = "monorail"
    FUNICULAR = "funicular"
    CABLE_CAR = "cable_car"
    FERRY = "ferry"
