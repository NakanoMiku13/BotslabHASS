"""Constants for the Botslab Vacuum integration."""
from __future__ import annotations

DOMAIN = "botslab_vacuum"
DEFAULT_SCAN_INTERVAL = 30  # seconds

# Config entry keys
CONF_Q = "q"
CONF_T = "t"
CONF_SID = "sid"
CONF_M2 = "m2"
CONF_PRODUCT_KEY = "product_key"
CONF_DEVICE_NAME = "device_name"
CONF_SIGN_SECRET = "sign_secret"
CONF_REGION = "region"

# TSL WorkingStatus enum -> Home Assistant VacuumActivity value
# (from the product DSL: WorkingStatus / TEST_WorkingStatus)
WORKING_STATUS = {
    0: "idle",        # StandBy
    1: "cleaning",    # DefaultClean
    2: "cleaning",    # CleanInAreas
    3: "cleaning",    # CleanInZones
    4: "paused",      # Paused
    5: "returning",   # GotoChargeBase
    6: "docked",      # Charging
    7: "docked",      # ChargeDone
    8: "idle",        # Dormant
    9: "error",       # InTrouble
    10: "cleaning",   # InRemoteControl
    11: "docked",     # InDustCollecting
}
WORKING_STATUS_NAME = {
    0: "StandBy", 1: "DefaultClean", 2: "CleanInAreas", 3: "CleanInZones",
    4: "Paused", 5: "GotoChargeBase", 6: "Charging", 7: "ChargeDone",
    8: "Dormant", 9: "InTrouble", 10: "InRemoteControl", 11: "InDustCollecting",
}

# Fan speed <-> SuctionPowLevel (SetSuctionPowLevelWithMode "Level")
FAN_SPEEDS = ["Quiet", "Normal", "Strong", "MAX"]  # index == Level int
SUCTION_MODE_DEFAULT = 0  # SetSuctionPowLevelWithMode "Mode" (0 = OnDefaultPage)

# TSL service / attribute identifiers used by this integration
SVC_START = "StartDefaultClean"         # input {"CleanTimes": 1}
SVC_PAUSE = "PauseRobot"                # input {"TargetAction": 0}
SVC_RESUME = "ResumeWork"               # no input
SVC_RETURN = "ReturnChargeBase"         # no input
SVC_LOCATE = "SeekRobot"                # no input
SVC_SUCTION = "SetSuctionPowLevelWithMode"  # input {"Mode": m, "Level": l}

ATTR_BATTERY = "BatteryLevel"
ATTR_STATUS = "WorkingStatus"
ATTR_STATUS_TEST = "TEST_WorkingStatus"
ATTR_ERROR = "ErrorCode"
ATTR_SUCTION = "SuctionPowLevel"
