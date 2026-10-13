"""Simulační testy solar_manager.py bez Home Assistantu.

Spuštění:  python3 tests/test_solar_manager.py
"""
import os
import sys
import types
import unittest

# ── Falešný AppDaemon ────────────────────────────────────────────────
pkg = types.ModuleType("appdaemon")
plugins = types.ModuleType("appdaemon.plugins")
hassmod = types.ModuleType("appdaemon.plugins.hass")
hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")


class FakeHass:
    def __init__(self, states):
        self.states = states
        self.calls = []
        self.logs = []

    def get_state(self, entity, attribute=None):
        v = self.states.get(entity)
        if attribute:
            return v.get(attribute) if isinstance(v, dict) else None
        return v["state"] if isinstance(v, dict) else v

    def entity_exists(self, e):
        return e in self.states

    def call_service(self, service, **kw):
        self.calls.append((service, kw))
        e = kw["entity_id"]
        if service.endswith("turn_on"):
            self.states[e] = "on"
        elif service.endswith("turn_off"):
            self.states[e] = "off"
        elif "value" in kw:
            self.states[e] = str(float(kw["value"]))
        elif "option" in kw:
            self.states[e] = kw["option"]
        elif "temperature" in kw:
            self.states[e] = {"state": "heat", "temperature": kw["temperature"]}

    def run_every(self, *a, **k):
        pass

    def log(self, msg, level="INFO"):
        self.logs.append(msg)


hassapi.Hass = FakeHass
sys.modules.update({"appdaemon": pkg, "appdaemon.plugins": plugins,
                    "appdaemon.plugins.hass": hassmod,
                    "appdaemon.plugins.hass.hassapi": hassapi})
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "apps"))
import solar_manager as sm  # noqa: E402

sm.DRY_RUN = False
sm.CFG["battery_control"] = True
for l in sm.CFG["loads"]:
    l["enabled"] = l["id"] != "ev_olife"

BOILER = "switch.boiler_jistic"
HP = "select.sprsun_sg_ready_mode"
EV_SW = "switch.nabijecka_doma_china"
EV_A = "number.nabijecka_doma_china_max_current"
MODE = "select.solax_charger_use_mode"
EXPORT = "number.solax_export_control_user_limit"


def make(**over):
    st = {
        "input_boolean.solar_manager": "on",
        "sensor.current_spot_electricity_price": "3.0",
        "sensor.solax_pv_power_total": "0",
        "sensor.solax_battery_capacity": "50",
        "sensor.solax_battery_power_charge": "0",
        "sensor.solax_measured_power": "0",
        "sensor.solax_measured_power_l1": "-1150",   # 5 A
        "sensor.solax_measured_power_l2": "-1150",
        "sensor.solax_measured_power_l3": "-1150",
        MODE: "Self Use Mode",
        "select.solax_manual_mode_select": "Stop Charge and Discharge",
        EXPORT: "15000",
        BOILER: "off",
        HP: "Normal",
        EV_SW: "off", EV_A: "6",
    }
    st.update({k.replace("__", "."): v for k, v in over.items()})
    app = sm.SolarManager.__new__(sm.SolarManager)
    FakeHass.__init__(app, st)
    app.initialize()
    return app


def services(app):
    return [(s, kw.get("entity_id")) for s, kw in app.calls]


GRID = "sensor__solax_measured_power"
SOC = "sensor__solax_battery_capacity"
BATT = "sensor__solax_battery_power_charge"
PRICE = "sensor__current_spot_electricity_price"


class Tests(unittest.TestCase):
    def test_no_unknown_entities(self):
        app = make()
        self.assertFalse([m for m in app.logs if "neexistuje" in m and "olife" not in m], app.logs)

    def test_surplus_turns_on_boiler(self):
        app = make(**{GRID: "3000"})
        app.loop({})
        self.assertIn(("homeassistant/turn_on", BOILER), services(app))

    def test_no_surplus_nothing(self):
        app = make()
        app.loop({})
        self.assertEqual(app.calls, [])

    def test_battery_discharge_sheds_load(self):
        app = make(**{"switch__boiler_jistic": "on", BATT: "-2000"})
        app.loop({})
        self.assertIn(("homeassistant/turn_off", BOILER), services(app))

    def test_full_battery_charge_counts_as_surplus(self):
        app = make(**{SOC: "90", BATT: "2800"})
        app.loop({})
        self.assertIn(("homeassistant/turn_on", BOILER), services(app))

    def test_low_battery_charge_has_priority(self):
        app = make(**{SOC: "40", BATT: "2800"})
        app.loop({})
        self.assertNotIn(("homeassistant/turn_on", BOILER), services(app))

    def test_second_load_is_heat_pump_sg(self):
        app = make(**{"switch__boiler_jistic": "on", GRID: "3000"})
        app.loop({})
        self.assertIn(("select/select_option", HP), services(app))
        self.assertEqual(app.states[HP], "SG+")

    def test_fuse_protection_from_phase_power(self):
        # 7130 W na L1 = 31 A > 30 A
        app = make(**{"switch__boiler_jistic": "on", "sensor__solax_measured_power_l1": "-7130"})
        app.loop({})
        self.assertIn(("homeassistant/turn_off", BOILER), services(app))

    def test_cheap_grid_charge_and_boiler(self):
        app = make(**{PRICE: "1.0"})
        app.loop({})
        s = services(app)
        self.assertEqual(app.states[MODE], "Manual Mode")
        self.assertEqual(app.states["select.solax_manual_mode_select"], "Force Charge")
        self.assertIn(("homeassistant/turn_on", BOILER), s)

    def test_battery_control_off_leaves_inverter(self):
        sm.CFG["battery_control"] = False
        try:
            app = make(**{PRICE: "-1.0"})
            app.loop({})
            self.assertNotIn(MODE, [e for _, e in services(app)])
            self.assertNotIn(EXPORT, [e for _, e in services(app)])
        finally:
            sm.CFG["battery_control"] = True

    def test_negative_price_export_zero(self):
        app = make(**{PRICE: "-0.5"})
        app.loop({})
        self.assertEqual(app.states[EXPORT], "0.0")

    def test_manual_change_restored(self):
        app = make(**{PRICE: "1.0"})
        app.loop({})
        app.states[MODE] = "Self Use Mode"  # někdo přepnul ručně
        app.calls.clear()
        app.sent.clear()
        app.loop({})
        self.assertIn(("select/select_option", MODE), services(app))

    def test_no_repeat_calls(self):
        app = make(**{PRICE: "1.0"})
        app.loop({})
        app.loop({})
        self.assertEqual(len(set(map(str, app.calls))), len(app.calls))

    def test_master_switch_off(self):
        app = make(**{"input_boolean__solar_manager": "off", GRID: "5000"})
        app.loop({})
        self.assertEqual(app.calls, [])

    def test_missing_data_skips(self):
        app = make(**{GRID: "unavailable"})
        app.loop({})
        self.assertEqual(app.calls, [])

    def test_ev_ramps_down_on_import(self):
        app = make(**{"switch__boiler_jistic": "on", HP.replace(".", "__"): "SG+",
                      EV_SW.replace(".", "__"): "on", EV_A.replace(".", "__"): "12",
                      GRID: "-1500"})
        app.loop({})
        self.assertLess(float(app.states[EV_A]), 12)

    def test_dry_run_calls_nothing(self):
        sm.DRY_RUN = True
        try:
            app = make(**{PRICE: "1.0"})
            app.loop({})
            self.assertEqual(app.calls, [])
            self.assertTrue(any("NA SUCHO" in m for m in app.logs))
        finally:
            sm.DRY_RUN = False

    def test_manual_switch_on_detected(self):
        app = make()
        app.states[BOILER] = "on"
        app.loop({})
        self.assertTrue(app.st["boiler"]["on"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
