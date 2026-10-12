"""
solar_manager.py — řízení FVE, baterie SolaX a spotřebičů podle přebytku a spotové ceny
AppDaemon app pro Home Assistant

INSTALACE
  1. Ulož tento soubor do složky apps AppDaemonu:
       /addon_configs/a0d7b954_appdaemon/apps/solar_manager.py
     (u starších instalací: /config/appdaemon/apps/solar_manager.py)
  2. Do souboru apps.yaml ve stejné složce přidej:
       solar_manager:
         module: solar_manager
         class: SolarManager
  3. AppDaemon appku načte sám (případně restartuj doplněk AppDaemon).
     Co dělá, uvidíš v logu AppDaemonu.

BEZPEČNOST
  DRY_RUN = True  → nic nespíná, jen píše do logu, co BY udělal.
  Až budeš s rozhodováním spokojený, přepni na False.

CO VYPLNIT
  Všechno, co je označené  # ZMĚŇ  — názvy entit najdeš v HA:
  Nástroje pro vývojáře → Stavy → filtr.
"""

import time
import appdaemon.plugins.hass.hassapi as hass


# ════════════════════════════════════════════════════════════════════
#  KONFIGURACE
# ════════════════════════════════════════════════════════════════════

DRY_RUN = True   # True = jen loguje, False = opravdu spíná

CFG = {
    # ── Spotová cena (integrace „Spotové ceny energií v Česku“) ──────
    "spot_price":       "sensor.current_spot_electricity_price",  # ZMĚŇ podle svého HA
    "price_unit":       "czk_kwh",   # "czk_kwh" nebo "eur_mwh"
    "eur_czk_rate":     25.0,        # jen pro eur_mwh

    # Cenová pásma [Kč/kWh, čistá spotová cena]
    "price_negative":   0.0,   # pod tím = záporná cena
    "price_cheap":      1.5,   # pod tím = levná
    "price_expensive":  4.5,   # nad tím = drahá

    # ── SolaX (integrace solax_modbus) ───────────────────────────────
    "pv_power":         "sensor.solax_pv_power_total",       # ZMĚŇ  výroba FVE [W]
    "battery_soc":      "sensor.solax_battery_capacity",     # ZMĚŇ  SOC [%]
    "battery_power":    "sensor.solax_battery_power_charge", # ZMĚŇ  [W] + nabíjí / − vybíjí
    "grid_power":       "sensor.solax_measured_power",       # ZMĚŇ  [W] + export / − odběr ze sítě
    "phase_currents": [                                      # ZMĚŇ  proudy fází [A]
        "sensor.solax_current_l1",
        "sensor.solax_current_l2",
        "sensor.solax_current_l3",
    ],

    # Ovládání střídače (nech prázdné "", pokud zatím nechceš baterii řídit)
    "work_mode_select": "select.solax_charger_use_mode",     # ZMĚŇ
    "mode_normal":      "Self Use Mode",                     # ZMĚŇ přesný text volby
    "mode_grid_charge": "Force Time Use",                    # ZMĚŇ přesný text volby
    "export_limit":     "number.solax_export_control_user_limit",  # ZMĚŇ [W]
    "export_limit_max": 10000,   # ZMĚŇ běžný limit přetoků [W]

    # ── Baterie (20 kWh) ─────────────────────────────────────────────
    "battery_capacity_kwh":   20,
    "battery_soc_min":        20,    # pod tím nevybíjet
    "grid_charge_enabled":    True,  # nabíjet ze sítě v levných hodinách
    "grid_charge_max_soc":    90,    # do kolika % nabíjet ze sítě
    "battery_priority_soc":   80,    # pod tím má baterie přednost před spotřebiči

    # ── Hlavní jistič ────────────────────────────────────────────────
    "fuse_limit_a":     32,
    "fuse_reserve_a":   2,

    # ── Chování smyčky ───────────────────────────────────────────────
    "loop_interval_s":  30,    # jak často přepočítat
    "hysteresis_w":     200,   # pásmo necitlivosti
    "min_on_s":         300,   # spotřebič běží aspoň 5 min
    "min_off_s":        300,   # a je vypnutý aspoň 5 min (žádné cvakání)

    # ── Spotřebiče v pořadí priority (první = nejdůležitější) ───────
    # type:
    #   "switch"        — obyčejné relé / zásuvka (bojler)
    #   "ev"            — wallbox; "current" = number entita proudu, nebo None (jen zap/vyp)
    #   "climate_boost" — TČ přes climate entitu: v levné době / při přebytku
    #                     zvedne teplotu o "boost" °C, jinak vrátí "base_temp"
    # cheap: True = v levných a záporných hodinách zapnout i ze sítě
    "loads": [
        {
            "id": "boiler", "name": "Bojler", "type": "switch",
            "entity": "switch.bojler",                       # ZMĚŇ
            "power_w": 2000, "cheap": True, "enabled": True,
        },
        {
            "id": "ev1", "name": "Wallbox 1 (Modbus)", "type": "ev",
            "switch": "switch.wallbox1_charging",            # ZMĚŇ
            "current": "number.wallbox1_max_current",        # ZMĚŇ (nebo None)
            "min_a": 6, "max_a": 16, "phases": 3,            # ZMĚŇ fáze 1 nebo 3
            "cheap": True, "enabled": False,                 # zapni, až vyplníš
        },
        {
            "id": "ev2", "name": "Wallbox 2 (Tuya)", "type": "ev",
            "switch": "switch.wallbox2",                     # ZMĚŇ
            "current": None,                                 # ZMĚŇ pokud jde nastavit proud
            "min_a": 6, "max_a": 16, "phases": 3,
            "cheap": True, "enabled": False,
        },
        {
            "id": "hp", "name": "Tepelné čerpadlo", "type": "climate_boost",
            "entity": "climate.tepelne_cerpadlo",            # ZMĚŇ
            "base_temp": 21.0, "boost": 1.5,
            "power_w": 3000, "cheap": True, "enabled": False,
        },
    ],
}

# ════════════════════════════════════════════════════════════════════
#  KONEC KONFIGURACE — níže nemusíš nic měnit
# ════════════════════════════════════════════════════════════════════

VOLTAGE = 230


class SolarManager(hass.Hass):

    def initialize(self):
        self.c = CFG
        self.loads = [l for l in self.c["loads"] if l.get("enabled")]
        self.sent = {}   # poslední odeslané příkazy (proti opakování)
        self.st = {}     # stav spotřebičů: on, t (čas změny), amps

        for l in self.loads:
            on = False
            if l["type"] == "switch":
                on = self.get_state(l["entity"]) == "on"
            elif l["type"] == "ev":
                on = self.get_state(l["switch"]) == "on"
            self.st[l["id"]] = {"on": on, "t": 0.0, "amps": l.get("min_a", 0)}

        self.run_every(self.loop, "now", self.c["loop_interval_s"])
        mode = "NA SUCHO (nic nespíná)" if DRY_RUN else "OSTRÝ PROVOZ"
        names = ", ".join(l["name"] for l in self.loads) or "žádné"
        self.log(f"SolarManager spuštěn ✓  režim: {mode}  |  spotřebiče: {names}")

    # ── Hlavní smyčka ────────────────────────────────────────────────

    def loop(self, kwargs):
        price = self.price()
        grid = self.num(self.c["grid_power"])
        if price is None or grid is None:
            self.log("Chybí cena nebo výkon sítě — tento krok přeskakuji", level="WARNING")
            return

        soc = self.num(self.c["battery_soc"]) or 0.0
        batt = self.num(self.c["battery_power"]) or 0.0
        pv = self.num(self.c["pv_power"]) or 0.0

        currents = [self.num(e) for e in self.c["phase_currents"]]
        phase_ok = all(a is not None for a in currents)
        max_a = max(abs(a) for a in currents) if phase_ok else None
        limit_a = self.c["fuse_limit_a"] - self.c["fuse_reserve_a"]

        band = self.band(price)
        self.log(
            f"cena {price:.2f} Kč ({band}) | FVE {pv:.0f} W | síť {grid:+.0f} W | "
            f"baterie {soc:.0f} % {batt:+.0f} W | fáze max "
            f"{'?' if max_a is None else f'{max_a:.1f}'} A"
        )

        # 1) Ochrana jističe — má přednost před vším
        if phase_ok and max_a > limit_a:
            self.log(f"⚠ Fáze {max_a:.1f} A > {limit_a} A — odlehčuji", level="WARNING")
            self.shed(force=True)
            return

        # 2) Baterie a přetoky
        self.battery(band, soc)

        # 3) Spotřebiče
        free_a = (limit_a - max_a) if phase_ok else 0.0
        if band in ("záporná", "levná"):
            self.cheap_mode(free_a, phase_ok)
        else:
            surplus = grid
            if soc >= self.c["battery_priority_soc"] and batt > 0:
                surplus += batt   # nabíjení plné baterie můžeme přesměrovat do spotřebičů
            self.solar_mode(surplus, free_a, phase_ok, band)

    # ── Pásma ceny ───────────────────────────────────────────────────

    def band(self, price):
        if price < self.c["price_negative"]:
            return "záporná"
        if price < self.c["price_cheap"]:
            return "levná"
        if price >= self.c["price_expensive"]:
            return "drahá"
        return "normální"

    # ── Baterie ──────────────────────────────────────────────────────

    def battery(self, band, soc):
        if band == "záporná":
            # Nevyvážet (za export bychom platili), baterii nabíjet ze sítě
            self.set_export(0)
            if soc < self.c["grid_charge_max_soc"]:
                self.set_mode(self.c["mode_grid_charge"])
            else:
                self.set_mode(self.c["mode_normal"])
        elif (band == "levná" and self.c["grid_charge_enabled"]
              and soc < self.c["grid_charge_max_soc"]):
            self.set_export(self.c["export_limit_max"])
            self.set_mode(self.c["mode_grid_charge"])
        else:
            # Normální a drahá: baterie kryje spotřebu domu (Self Use)
            self.set_export(self.c["export_limit_max"])
            self.set_mode(self.c["mode_normal"])

    def set_mode(self, option):
        ent = self.c["work_mode_select"]
        if ent and option:
            self.send(ent, option, "select/select_option", entity_id=ent, option=option)

    def set_export(self, watts):
        ent = self.c["export_limit"]
        if ent:
            self.send(ent, watts, "number/set_value", entity_id=ent, value=watts)

    # ── Levná / záporná cena: zapnout co jde, i ze sítě ──────────────

    def cheap_mode(self, free_a, phase_ok):
        if not phase_ok:
            self.log("Neznám proudy fází — v levném režimu nic nepřidávám", level="WARNING")
            return
        for l in self.loads:
            if not l.get("cheap"):
                continue
            s = self.st[l["id"]]
            if not s["on"]:
                need = self.need_a(l)
                if free_a >= need and self.can_change(l, True):
                    self.turn(l, True, "levná cena")
                    if l["type"] == "ev" and l.get("current"):
                        self.set_amps(l, min(l["max_a"], l["min_a"] + free_a - need))
                    return  # jeden spotřebič za krok (rozběhové proudy)
            elif l["type"] == "ev" and l.get("current"):
                # běžící wallbox přidat až na maximum, co jistič dovolí
                self.set_amps(l, min(l["max_a"], s["amps"] + max(0.0, free_a - 1)))

    # ── Normální / drahá cena: jen z přebytku ────────────────────────

    def solar_mode(self, surplus, free_a, phase_ok, band):
        hyst = self.c["hysteresis_w"]

        # Odběr ze sítě → nejdřív ubrat proud wallboxům, pak vypnout nejméně důležité
        if surplus < -hyst:
            for l in reversed(self.loads):
                s = self.st[l["id"]]
                if s["on"] and l["type"] == "ev" and l.get("current") and s["amps"] > l["min_a"]:
                    step = -surplus / (VOLTAGE * l["phases"])
                    self.set_amps(l, max(l["min_a"], s["amps"] - step))
                    return
            self.shed(force=False, reason=f"odběr ze sítě ({band})")
            return

        # Běžící wallboxy doladit podle přebytku
        for l in self.loads:
            s = self.st[l["id"]]
            if s["on"] and l["type"] == "ev" and l.get("current") and surplus > hyst:
                step = (surplus - hyst) / (VOLTAGE * l["phases"])
                if step >= 1 and (not phase_ok or free_a >= step):
                    self.set_amps(l, min(l["max_a"], s["amps"] + step))
                    return

        # Dost přebytku → zapnout další spotřebič podle priority
        if not phase_ok:
            return
        for l in self.loads:
            s = self.st[l["id"]]
            if s["on"]:
                continue
            if surplus >= self.power_w(l) + hyst and free_a >= self.need_a(l) and self.can_change(l, True):
                self.turn(l, True, f"přebytek {surplus:.0f} W")
                if l["type"] == "ev" and l.get("current"):
                    self.set_amps(l, l["min_a"])
                return
            break  # nepřeskakovat priority

    # ── Odlehčení ────────────────────────────────────────────────────

    def shed(self, force, reason="jistič"):
        for l in reversed(self.loads):
            if self.st[l["id"]]["on"] and (force or self.can_change(l, False)):
                self.turn(l, False, reason)
                return

    # ── Spínání ──────────────────────────────────────────────────────

    def turn(self, l, on, reason):
        s = self.st[l["id"]]
        self.log(f"{'▶ ZAPÍNÁM' if on else '■ VYPÍNÁM'} {l['name']} — {reason}")
        if l["type"] == "switch":
            self.switch(l["entity"], on)
        elif l["type"] == "ev":
            self.switch(l["switch"], on)
        elif l["type"] == "climate_boost":
            temp = l["base_temp"] + (l["boost"] if on else 0)
            self.send(l["entity"], temp, "climate/set_temperature",
                      entity_id=l["entity"], temperature=temp)
        s["on"] = on
        s["t"] = time.time()

    def switch(self, entity, on):
        svc = "switch/turn_on" if on else "switch/turn_off"
        self.send(entity, on, svc, entity_id=entity)

    def set_amps(self, l, amps):
        amps = int(max(l["min_a"], min(l["max_a"], amps)))
        s = self.st[l["id"]]
        if amps == s["amps"]:
            return
        self.log(f"{l['name']}: proud {s['amps']} → {amps} A")
        s["amps"] = amps
        self.send(l["current"], amps, "number/set_value", entity_id=l["current"], value=amps)

    def send(self, key, value, service, **kw):
        if self.sent.get(key) == value:
            return
        self.sent[key] = value
        if DRY_RUN:
            self.log(f"   [NA SUCHO] {service} {kw}")
            return
        try:
            self.call_service(service, **kw)
        except Exception as e:
            self.log(f"Chyba při {service} {kw}: {e}", level="ERROR")
            self.sent.pop(key, None)

    # ── Pomocné výpočty ──────────────────────────────────────────────

    def can_change(self, l, to_on):
        wait = self.c["min_off_s"] if to_on else self.c["min_on_s"]
        return time.time() - self.st[l["id"]]["t"] >= wait

    def power_w(self, l):
        if l["type"] == "ev":
            return l["min_a"] * VOLTAGE * l["phases"]
        return l.get("power_w", 2000)

    def need_a(self, l):
        """Kolik ampér na nejzatíženější fázi spotřebič přidá (jednofázové = celý výkon na 1 fázi)."""
        if l["type"] == "ev":
            return l["min_a"]
        return l.get("power_w", 2000) / VOLTAGE

    def price(self):
        raw = self.num(self.c["spot_price"])
        if raw is None:
            return None
        if self.c["price_unit"] == "eur_mwh":
            return raw * self.c["eur_czk_rate"] / 1000
        return raw

    def num(self, entity):
        try:
            v = self.get_state(entity)
            if v in (None, "unavailable", "unknown", ""):
                return None
            return float(v)
        except (ValueError, TypeError):
            self.log(f"Nelze číst {entity}", level="WARNING")
            return None
