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
for l in sm.CFG["loads"]:
    l["enabled"] = True
EV1 = sm.CFG["loads"][1]


def make(**over):
    st = {
        "input_boolean.solar_manager": "on",
        "sensor.current_spot_electricity_price": "3.0",
        "sensor.solax_pv_power_total": "0",
        "sensor.solax_battery_capacity": "50",
        "sensor.solax_battery_power_charge": "0",
        "sensor.solax_measured_power": "0",
        "sensor.solax_current_l1": "5", "sensor.solax_current_l2": "5", "sensor.solax_current_l3": "5",
        "select.solax_charger_use_mode": "Self Use Mode",
        "number.solax_export_control_user_limit": "10000",
        "switch.bojler": "off",
        "switch.wallbox1_charging": "off", "number.wallbox1_max_current": "6",
        "switch.wallbox2": "off",
        "climate.tepelne_cerpadlo": {"state": "heat", "temperature": 21.0},
    }
    st.update(over)
    app = sm.SolarManager.__new__(sm.SolarManager)
    FakeHass.__init__(app, st)
    app.initialize()
    return app


def services(app):
    return [(s, kw.get("entity_id")) for s, kw in app.calls]


class Tests(unittest.TestCase):
    def test_surplus_turns_on_boiler(self):
        app = make(**{"sensor.solax_measured_power": "3000", "sensor.solax_pv_power_total": "5000"})
        app.loop({})
        self.assertIn(("homeassistant/turn_on", "switch.bojler"), services(app))

    def test_no_surplus_nothing(self):
        app = make()
        app.loop({})
        self.assertEqual(app.calls, [])

    def test_battery_discharge_sheds_load(self):
        # Bojler běží, síť 0, ale baterie vybíjí 2 kW → bojler musí vypnout
        app = make(**{"switch.bojler": "on", "sensor.solax_battery_power_charge": "-2000"})
        app.loop({})
        self.assertIn(("homeassistant/turn_off", "switch.bojler"), services(app))

    def test_full_battery_charge_counts_as_surplus(self):
        app = make(**{"sensor.solax_battery_capacity": "90",
                      "sensor.solax_battery_power_charge": "2500"})
        app.loop({})
        self.assertIn(("homeassistant/turn_on", "switch.bojler"), services(app))

    def test_low_battery_charge_has_priority(self):
        app = make(**{"sensor.solax_battery_capacity": "40",
                      "sensor.solax_battery_power_charge": "2500"})
        app.loop({})
        self.assertNotIn(("homeassistant/turn_on", "switch.bojler"), services(app))

    def test_fuse_protection(self):
        app = make(**{"switch.bojler": "on", "sensor.solax_current_l1": "31"})
        app.loop({})
        # vypíná se nejméně důležitý zapnutý spotřebič = bojler (jediný zapnutý)
        self.assertIn(("homeassistant/turn_off", "switch.bojler"), services(app))

    def test_cheap_grid_charge_and_boiler(self):
        app = make(**{"sensor.current_spot_electricity_price": "1.0"})
        app.loop({})
        s = services(app)
        self.assertIn(("select/select_option", "select.solax_charger_use_mode"), s)
        self.assertIn(("homeassistant/turn_on", "switch.bojler"), s)

    def test_negative_price_export_zero(self):
        app = make(**{"sensor.current_spot_electricity_price": "-0.5"})
        app.loop({})
        self.assertIn(("number/set_value", "number.solax_export_control_user_limit"), services(app))
        self.assertEqual(app.states["number.solax_export_control_user_limit"], "0.0")

    def test_manual_change_restored(self):
        app = make(**{"sensor.current_spot_electricity_price": "1.0"})
        app.loop({})
        app.states["select.solax_charger_use_mode"] = "Self Use Mode"  # někdo přepnul ručně
        app.calls.clear()
        app.sent.clear()
        app.loop({})
        self.assertIn(("select/select_option", "select.solax_charger_use_mode"), services(app))

    def test_no_repeat_calls(self):
        app = make(**{"sensor.current_spot_electricity_price": "1.0"})
        app.loop({})
        n = len(app.calls)
        app.loop({})
        # druhý krok může přidat další spotřebič, ale nesmí opakovat stejné příkazy
        self.assertEqual(len(set(map(str, app.calls))), len(app.calls))
        self.assertGreaterEqual(len(app.calls), n)

    def test_master_switch_off(self):
        app = make(**{"input_boolean.solar_manager": "off",
                      "sensor.solax_measured_power": "5000"})
        app.loop({})
        self.assertEqual(app.calls, [])

    def test_missing_data_skips(self):
        app = make(**{"sensor.solax_measured_power": "unavailable"})
        app.loop({})
        self.assertEqual(app.calls, [])

    def test_ev_ramps_down_on_import(self):
        app = make(**{"switch.bojler": "on", "switch.wallbox1_charging": "on",
                      "number.wallbox1_max_current": "12",
                      "sensor.solax_measured_power": "-1500"})
        app.loop({})
        self.assertIn(("number/set_value", "number.wallbox1_max_current"), services(app))
        self.assertLess(float(app.states["number.wallbox1_max_current"]), 12)

    def test_dry_run_calls_nothing(self):
        sm.DRY_RUN = True
        try:
            app = make(**{"sensor.current_spot_electricity_price": "1.0"})
            app.loop({})
            self.assertEqual(app.calls, [])
            self.assertTrue(any("NA SUCHO" in m for m in app.logs))
        finally:
            sm.DRY_RUN = False

    def test_manual_switch_on_detected(self):
        app = make()
        app.states["switch.bojler"] = "on"
        app.loop({})
        self.assertTrue(app.st["boiler"]["on"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
