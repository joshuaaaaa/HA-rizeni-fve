"""
solar_manager.py — řízení FVE, baterie SolaX a spotřebičů podle přebytku a spotové ceny
AppDaemon app pro Home Assistant

INSTALACE (podrobně viz README.md)
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
from collections import deque
import appdaemon.plugins.hass.hassapi as hass


# ════════════════════════════════════════════════════════════════════
#  KONFIGURACE
# ════════════════════════════════════════════════════════════════════

DRY_RUN = True   # True = jen loguje, False = opravdu spíná

CFG = {
    # ── Hlavní vypínač (volitelný) ───────────────────────────────────
    # input_boolean v HA; když je vypnutý, skript nic neřídí.
    # Vytvoř ho v Nastavení → Zařízení a služby → Pomocníci → Přepínač,
    # název „solar_manager“. Nech prázdné "", pokud ho nechceš.
    "master_switch":    "input_boolean.solar_manager",

    # ── Spotová cena (integrace „Spotové ceny energií v Česku“) ──────
    "spot_price":       "sensor.current_spot_electricity_price",   # Kč/kWh
    "price_unit":       "czk_kwh",   # "czk_kwh" nebo "eur_mwh"
    "eur_czk_rate":     25.0,        # jen pro eur_mwh

    # Cenová pásma [Kč/kWh, čistá spotová cena]
    "price_negative":   0.0,   # pod tím = záporná cena
    "price_cheap":      1.5,   # pod tím = levná
    "price_expensive":  4.5,   # nad tím = drahá

    # ── SolaX (integrace solax_modbus) ───────────────────────────────
    "pv_power":         "sensor.solax_pv_power_total",        # výroba FVE [W]
    "battery_soc":      "sensor.solax_battery_capacity",      # SOC [%]
    "battery_power":    "sensor.solax_battery_power_charge",  # [W] + nabíjí / − vybíjí
    "grid_power":       "sensor.solax_measured_power",        # [W] + export / − odběr ze sítě
    # Znaménka ověřena z tvého výpisu (export 2378 W = +2378, baterie nabíjí = +976)
    "battery_invert":   False,
    "grid_invert":      False,
    # Výkon na jednotlivých fázích ze sítě [W] — z nich se počítá proud přes hlavní jistič.
    # (SolaX nemá proudy ze sítě, jen proudy střídače, ty pro jistič nejsou použitelné.)
    "phase_powers": [
        "sensor.solax_measured_power_l1",
        "sensor.solax_measured_power_l2",
        "sensor.solax_measured_power_l3",
    ],
    "phase_currents":   [],   # alternativa: senzory proudu fází ze sítě [A]

    # ── Řízení baterie ───────────────────────────────────────────────
    # False = skript baterii NEŘÍDÍ (máš na to vlastní automatizace).
    # Zapni až po vypnutí automatizací „Solax – řízení podle spot ceny“,
    # „Nabíjení baterie start/stop“, „Vybíjení baterie do sítě…“, „Sync … export limit“.
    "battery_control":  False,
    # Co nastavit v normálním režimu (pořadí = pořadí odeslání)
    "normal_selects": [
        ("select.solax_charger_use_mode", "Self Use Mode"),
    ],
    # Co nastavit pro nabíjení ze sítě
    "grid_charge_selects": [
        ("select.solax_manual_mode_select", "Force Charge"),
        ("select.solax_charger_use_mode", "Manual Mode"),
    ],
    # Limit přetoků do sítě: "" = skript ho NIKDY nemění (number.solax_export_control_user_limit
    # zůstává pod kontrolou tvých automatizací).
    "export_limit":     "",
    "export_limit_max": 15000,   # použije se jen při vyplněném export_limit [W]

    # ── Baterie (23 kWh dle BMS) ─────────────────────────────────────
    "battery_capacity_kwh":   23,
    "battery_soc_min":        30,    # jen informace — nastaveno ve střídači (Selfuse Discharge Min SOC)
    "grid_charge_enabled":    True,  # nabíjet ze sítě v levných hodinách
    "grid_charge_max_soc":    90,    # do kolika % nabíjet ze sítě
    "battery_priority_soc":   80,    # pod tím má baterie přednost před spotřebiči

    # ── Hlavní jistič ────────────────────────────────────────────────
    "fuse_limit_a":     25,    # hlavní jistič 3×25 A
    "fuse_reserve_a":   2,

    # ── Chování smyčky ───────────────────────────────────────────────
    "loop_interval_s":  30,    # jak často přepočítat
    "hysteresis_w":     200,   # pásmo necitlivosti
    "min_on_s":         300,   # spotřebič běží aspoň 5 min
    "min_off_s":        300,   # a je vypnutý aspoň 5 min (žádné cvakání)

    # ── Spotřebiče v pořadí priority (první = nejdůležitější) ───────
    # type:
    #   "switch"        — obyčejné relé / zásuvka (bojler)
    #   "ev"            — wallbox; "current" = number/select entita proudu, nebo None (jen zap/vyp)
    #   "select_mode"   — přepnutí volby v select entitě (TČ přes SG Ready):
    #                     zapnuto = "on_option", vypnuto = "off_option"
    #   "climate_boost" — TČ přes climate entitu: zvedne teplotu o "boost" °C
    # cheap: True = v levných a záporných hodinách zapnout i ze sítě
    # phases: 1 nebo 3 — kolik fází spotřebič zatěžuje
    "loads": [
        {
            "id": "boiler", "name": "Bojler", "type": "switch",
            "entity": "switch.boiler_jistic",        # Tuya jistič, ~2,4 kW na 1 fázi
            "power_w": 2400, "phases": 1,
            "cheap": True, "enabled": True,
        },
        {
            # SPRSUN přes SG Ready: SG+ zvedne žádanou teplotu topení (+4 °C)
            # a nádrže (+2 °C), Normal vrátí zpět. Vyžaduje zapnutý
            # switch.sprsun_sg_ready_function (zapni ručně v HA).
            "id": "hp", "name": "TČ SPRSUN (SG Ready)", "type": "select_mode",
            "entity": "select.sprsun_sg_ready_mode",
            "on_option": "SG+", "off_option": "Normal",
            "power_w": 2500, "phases": 3,
            "cheap": True, "enabled": True,
        },
        {
            "id": "ev_china", "name": "Nabíječka doma China (Tuya)", "type": "ev",
            "switch": "switch.nabijecka_doma_china",
            "current": "number.nabijecka_doma_china_max_current",
            "min_a": 6, "max_a": 14, "phases": 3,    # 3f, 10 kW ≈ 14 A na fázi
            "cheap": True, "enabled": True,
        },
        {
            # Olife přes Modbus (hub „olife_energy“ v configuration.yaml).
            # Nemá vypínač — proud se zapisuje do registru 2106 „Current Limit Control“,
            # 0 A = nenabíjet. Zpětně se čte senzor „Current Limit Control“.
            "id": "ev_olife", "name": "Wallbox Olife (Modbus)", "type": "ev",
            "modbus": {
                "hub": "olife_energy", "slave": 0, "register": 2106,
                "readback": "sensor.current_limit_control",
                "off_amps": 0,
            },
            "min_a": 6, "max_a": 16, "phases": 3,            # ZMĚŇ max. proud / fáze
            "cheap": True, "enabled": True,
        },
    ],
}

# ════════════════════════════════════════════════════════════════════
#  KONEC KONFIGURACE — níže nemusíš nic měnit
# ════════════════════════════════════════════════════════════════════

VOLTAGE = 230


class SolarManager(hass.Hass):

    def initialize(self):
        self.history = deque(maxlen=20)   # posledních 20 řádků logu pro dashboard
        self.info = {}
        self.c = CFG
        self.loads = [l for l in self.c["loads"] if l.get("enabled")]
        self.sent = {}   # poslední odeslané příkazy (proti opakování)
        self.st = {}     # stav spotřebičů: on, t (čas změny), amps
        self.paused = False

        self.check_entities()

        for l in self.loads:
            amps = l.get("min_a", 0)
            if self.adjustable(l):
                real = self.num(self.amps_entity(l))
                if real is not None:
                    amps = int(real)
            self.st[l["id"]] = {"on": self.real_on(l) or False, "t": 0.0, "amps": amps}

        self.run_every(self.loop, "now", self.c["loop_interval_s"])
        mode = "NA SUCHO (nic nespíná)" if DRY_RUN else "OSTRÝ PROVOZ"
        names = ", ".join(l["name"] for l in self.loads) or "žádné"
        self.log(f"SolarManager spuštěn ✓  režim: {mode}  |  spotřebiče: {names}")

    def check_entities(self):
        """Při startu upozorní na entity, které v HA neexistují (překlep v názvu)."""
        ents = [self.c["spot_price"], self.c["pv_power"], self.c["battery_soc"],
                self.c["battery_power"], self.c["grid_power"], self.c["master_switch"],
                self.c["export_limit"] if self.c["battery_control"] else "",
                *self.c["phase_currents"], *self.c["phase_powers"]]
        if self.c["battery_control"]:
            ents += [e for e, _ in self.c["normal_selects"] + self.c["grid_charge_selects"]]
        for l in self.loads:
            ents += [l.get("entity"), l.get("switch"), l.get("current"),
                     l.get("modbus", {}).get("readback")]
        for e in ents:
            if e and not self.entity_exists(e):
                self.log(f"✗ Entita {e} v HA neexistuje — zkontroluj název v konfiguraci",
                         level="WARNING")
        if not self.c["phase_currents"] and not self.c["phase_powers"]:
            self.log("Nejsou zadané proudy fází — spotřebiče se nebudou zapínat "
                     "(nelze hlídat jistič)", level="WARNING")

    # ── Hlavní smyčka ────────────────────────────────────────────────

    # ── Log a stav pro dashboard ─────────────────────────────────────

    def log(self, msg, *args, **kwargs):
        super().log(msg, *args, **kwargs)
        if hasattr(self, "history") and not msg.startswith("cena "):
            msg = msg.strip()
            if self.history and self.history[0].endswith(msg):
                return   # stejná hláška pořád dokola (např. chybí data) — neopakovat
            self.history.appendleft(f"{time.strftime('%d.%m. %H:%M:%S')}  {msg}")

    def loop(self, kwargs):
        self.info = {"stav": "chyba dat"}
        try:
            self.step()
        finally:
            self.publish()

    def why(self, text):
        """Vysvětlení rozhodnutí v tomto kroku (pro kartu „Co se právě děje“)."""
        self.info.setdefault("duvody", []).append(text)

    def publish(self):
        """Zapíše do HA senzory sensor.solar_manager a sensor.solar_manager_prebytek."""
        loads, detail = [], []
        for l in self.loads:
            s = self.st[l["id"]]
            txt = "ZAP" if s["on"] else "vyp"
            if s["on"] and self.adjustable(l):
                txt += f" {s['amps']} A"
            loads.append(f"{l['name']}: {txt}")
            detail.append({
                "nazev": l["name"],
                "zapnuto": s["on"],
                "proud_a": s["amps"] if s["on"] and self.adjustable(l) else None,
                "od": time.strftime("%d.%m. %H:%M", time.localtime(s["t"])) if s["t"] else "",
                "proc": s.get("reason", ""),
            })
        attrs = dict(self.info)
        attrs.update({
            "friendly_name": "Solar Manager",
            "icon": "mdi:solar-power-variant",
            "rezim": "NA SUCHO" if DRY_RUN else "OSTRÝ PROVOZ",
            "rizeni_baterie": self.c["battery_control"],
            "aktualizovano": time.strftime("%d.%m. %H:%M:%S"),
            "spotrebice": loads,
            "spotrebice_detail": detail,
            "log": list(self.history),
        })
        try:
            self.set_state("sensor.solar_manager", state=self.info.get("stav", "?"),
                           attributes=attrs)
        except Exception as e:
            super().log(f"Nelze zapsat sensor.solar_manager: {e}", level="WARNING")
        surplus = self.info.get("prebytek_w")
        try:
            self.set_state("sensor.solar_manager_prebytek",
                           state="unknown" if surplus is None else round(surplus),
                           attributes={"friendly_name": "Solar Manager přebytek",
                                       "icon": "mdi:solar-power",
                                       "unit_of_measurement": "W",
                                       "device_class": "power",
                                       "state_class": "measurement"})
        except Exception as e:
            super().log(f"Nelze zapsat sensor.solar_manager_prebytek: {e}", level="WARNING")

    def step(self):
        master = self.c["master_switch"]
        if master and self.get_state(master) == "off":
            self.info = {"stav": "pozastaveno"}
            self.why("Hlavní vypínač je vypnutý – skript nic neřídí, vše je v ručním režimu.")
            if not self.paused:
                self.log("Hlavní vypínač je vypnutý — neřídím nic")
                self.paused = True
            return
        if self.paused:
            self.log("Hlavní vypínač zapnut — pokračuji")
            self.paused = False
            self.sent.clear()

        price = self.price()
        grid = self.num(self.c["grid_power"])
        if price is None or grid is None:
            self.log("Chybí cena nebo výkon sítě — tento krok přeskakuji", level="WARNING")
            self.why("Chybí data: " + ", ".join(
                n for n, v in (("spotová cena", price), ("výkon sítě", grid)) if v is None)
                + " je nedostupná – nic neměním.")
            return
        if self.c["grid_invert"]:
            grid = -grid

        soc = self.num(self.c["battery_soc"]) or 0.0
        batt = self.num(self.c["battery_power"]) or 0.0
        if self.c["battery_invert"]:
            batt = -batt
        pv = self.num(self.c["pv_power"]) or 0.0

        self.sync_states()

        currents = self.phase_amps()
        phase_ok = bool(currents) and all(a is not None for a in currents)
        max_a = max(abs(a) for a in currents) if phase_ok else None
        limit_a = self.c["fuse_limit_a"] - self.c["fuse_reserve_a"]

        band = self.band(price)
        self.info = {
            "stav": band, "cena": round(price, 2), "fve_w": round(pv), "sit_w": round(grid),
            "baterie_soc": round(soc), "baterie_w": round(batt),
            "faze_max_a": None if max_a is None else round(max_a, 1), "limit_a": limit_a,
        }
        self.log(
            f"cena {price:.2f} Kč ({band}) | FVE {pv:.0f} W | síť {grid:+.0f} W | "
            f"baterie {soc:.0f} % {batt:+.0f} W | fáze max "
            f"{'?' if max_a is None else f'{max_a:.1f}'} A"
        )

        # 1) Ochrana jističe — má přednost před vším
        if phase_ok and max_a > limit_a:
            self.info["stav"] = "jistič!"
            self.why(f"Ochrana jističe: fáze má {max_a:.1f} A, limit je {limit_a} A – vypínám spotřebič s nejnižší prioritou.")
            self.log(f"⚠ Fáze {max_a:.1f} A > {limit_a} A — odlehčuji", level="WARNING")
            self.shed(force=True)
            return

        # 2) Baterie a přetoky
        self.battery(band, soc)

        # 3) Spotřebiče
        free_a = (limit_a - max_a) if phase_ok else 0.0
        if band in ("záporná", "levná"):
            self.info["prebytek_w"] = grid + batt
            self.why(f"Cena {price:.2f} Kč/kWh je {band} – spotřebiče se zapínají i ze sítě, "
                     f"pokud to dovolí jistič (volných {free_a:.1f} A).")
            self.cheap_mode(free_a, phase_ok)
        else:
            # Přebytek = co jde do sítě. Vybíjení baterie se počítá jako odběr,
            # jinak by v režimu Self Use baterie potichu „krmila“ bojler/auto.
            surplus = grid + min(batt, 0.0)
            if soc >= self.c["battery_priority_soc"] and batt > 0:
                surplus += batt   # nabíjení plné baterie můžeme přesměrovat do spotřebičů
            self.info["prebytek_w"] = surplus
            if soc < self.c["battery_priority_soc"] and batt > 0:
                self.why(f"Baterie má přednost (SOC {soc:.0f} % < {self.c['battery_priority_soc']} %), "
                         f"nabíjí se {batt:.0f} W – to se do přebytku nepočítá.")
            if batt < 0:
                self.why(f"Baterie se vybíjí {-batt:.0f} W – počítá se jako odběr.")
            self.solar_mode(surplus, free_a, phase_ok, band)

    def sync_states(self):
        """Převezme skutečný stav spínačů z HA (ruční zapnutí/vypnutí, výpadek)."""
        if DRY_RUN:
            return   # na sucho se nic nespíná, držíme si simulovaný stav
        for l in self.loads:
            real = self.real_on(l)
            s = self.st[l["id"]]
            if real is not None and real != s["on"]:
                self.log(f"{l['name']}: změněno mimo skript → {'zapnuto' if real else 'vypnuto'}")
                s["on"] = real
                s["t"] = time.time()
                s["reason"] = "změněno ručně / mimo skript"

    def phase_amps(self):
        """Proudy fází přes hlavní jistič [A] — ze senzorů proudu, nebo z výkonu fází."""
        if self.c["phase_currents"]:
            return [self.num(e) for e in self.c["phase_currents"]]
        out = []
        for e in self.c["phase_powers"]:
            w = self.num(e)
            out.append(None if w is None else abs(w) / VOLTAGE)
        return out

    def real_on(self, l):
        if l.get("modbus"):
            amps = self.num(l["modbus"]["readback"])
            return None if amps is None else amps >= l["min_a"]
        ent = l.get("switch") if l["type"] == "ev" else l.get("entity")
        if l["type"] == "climate_boost" or not ent:
            return None
        state = self.get_state(ent)
        if l["type"] == "select_mode":
            if state == l["on_option"]:
                return True
            return False if state == l["off_option"] else None
        if state in ("on", "off"):
            return state == "on"
        return None

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
        if not self.c["battery_control"]:
            self.why("Baterie: skript ji neřídí (řídí ji tvoje automatizace).")
            return
        if band == "záporná":
            # Nevyvážet (za export bychom platili), baterii nabíjet ze sítě
            self.set_export(0)
            if soc < self.c["grid_charge_max_soc"]:
                self.set_mode(self.c["grid_charge_selects"])
                self.why("Baterie: záporná cena – nabíjení ze sítě.")
            else:
                self.set_mode(self.c["normal_selects"])
        elif (band == "levná" and self.c["grid_charge_enabled"]
              and soc < self.c["grid_charge_max_soc"]):
            self.set_export(self.c["export_limit_max"])
            self.set_mode(self.c["grid_charge_selects"])
            self.why(f"Baterie: nabíjení ze sítě (levná cena, SOC {soc:.0f} % < {self.c['grid_charge_max_soc']} %).")
        else:
            # Normální a drahá: baterie kryje spotřebu domu (Self Use)
            self.why("Baterie: normální režim (Self Use).")
            self.set_export(self.c["export_limit_max"])
            self.set_mode(self.c["normal_selects"])

    def set_mode(self, selects):
        for ent, option in selects:
            self.send(ent, option, "select/select_option", entity_id=ent, option=option)

    def set_export(self, watts):
        ent = self.c["export_limit"]
        if ent:
            self.send(ent, watts, "number/set_value", entity_id=ent, value=watts)

    # ── Levná / záporná cena: zapnout co jde, i ze sítě ──────────────

    def cheap_mode(self, free_a, phase_ok):
        if not phase_ok:
            self.log("Neznám proudy fází — v levném režimu nic nepřidávám", level="WARNING")
            self.why("Neznám proudy fází – bez hlídání jističe nic nezapínám.")
            return
        for l in self.loads:
            if not l.get("cheap"):
                continue
            s = self.st[l["id"]]
            if not s["on"]:
                need = self.need_a(l)
                if free_a < need:
                    self.why(f"{l['name']}: potřebuje {need:.1f} A, na jističi zbývá jen {free_a:.1f} A.")
                    continue
                if not self.can_change(l, True):
                    self.why(f"{l['name']}: čeká na minimální dobu vypnutí (ještě {self.wait_s(l, True)} s).")
                    continue
                self.why(f"Levná cena – zapínám {l['name']}.")
                self.turn(l, True, "levná cena")
                if self.adjustable(l):
                    self.set_amps(l, min(l["max_a"], l["min_a"] + free_a - need))
                return  # jeden spotřebič za krok (rozběhové proudy)
            elif self.adjustable(l):
                # běžící wallbox přidat až na maximum, co jistič dovolí
                if free_a >= 2 and s["amps"] < l["max_a"]:
                    self.why(f"Levná cena – přidávám proud: {l['name']}.")
                    self.set_amps(l, min(l["max_a"], s["amps"] + free_a - 1))
                    return
        if not any(self.st[l["id"]]["on"] is False and l.get("cheap") for l in self.loads):
            self.why("Všechny spotřebiče pro levnou cenu už běží.")

    # ── Normální / drahá cena: jen z přebytku ────────────────────────

    def solar_mode(self, surplus, free_a, phase_ok, band):
        hyst = self.c["hysteresis_w"]

        # Odběr ze sítě / z baterie → nejdřív ubrat proud wallboxům, pak vypnout nejméně důležité
        if surplus < -hyst:
            for l in reversed(self.loads):
                s = self.st[l["id"]]
                if s["on"] and self.adjustable(l) and s["amps"] > l["min_a"]:
                    step = -surplus / (VOLTAGE * l["phases"])
                    self.why(f"Chybí {-surplus:.0f} W – ubírám proud: {l['name']}.")
                    self.set_amps(l, max(l["min_a"], s["amps"] - max(1, step)))
                    return
            on = [l for l in self.loads if self.st[l["id"]]["on"]]
            if not on:
                self.why(f"Odběr {-surplus:.0f} W pokrývá dům sám – žádný řízený spotřebič neběží.")
            elif not self.shed(force=False, reason=f"odběr ze sítě/baterie ({band})"):
                self.why(f"Chybí {-surplus:.0f} W, ale {on[-1]['name']} musí ještě běžet "
                         f"{self.wait_s(on[-1], False)} s (ochrana proti cvakání).")
            else:
                self.why(f"Chybí {-surplus:.0f} W – vypínám spotřebič s nejnižší prioritou.")
            return

        # Běžící wallboxy doladit podle přebytku
        for l in self.loads:
            s = self.st[l["id"]]
            if s["on"] and self.adjustable(l) and surplus > hyst \
                    and s["amps"] < l["max_a"]:
                step = (surplus - hyst) / (VOLTAGE * l["phases"])
                if step >= 1 and (not phase_ok or free_a >= step):
                    self.why(f"Přebytek {surplus:.0f} W – přidávám proud: {l['name']}.")
                    self.set_amps(l, min(l["max_a"], s["amps"] + step))
                    return

        # Dost přebytku → zapnout další spotřebič podle priority
        if not phase_ok:
            self.why("Neznám proudy fází – bez hlídání jističe nic nezapínám.")
            return
        for l in self.loads:
            s = self.st[l["id"]]
            if s["on"]:
                continue
            need_w = self.power_w(l) + hyst
            if surplus < need_w:
                self.why(f"Přebytek {max(surplus, 0):.0f} W nestačí na další spotřebič – "
                         f"{l['name']} potřebuje {need_w:.0f} W.")
            elif free_a < self.need_a(l):
                self.why(f"{l['name']}: přebytek by stačil, ale na jističi zbývá jen {free_a:.1f} A.")
            elif not self.can_change(l, True):
                self.why(f"{l['name']}: čeká na minimální dobu vypnutí (ještě {self.wait_s(l, True)} s).")
            else:
                self.why(f"Přebytek {surplus:.0f} W – zapínám {l['name']}.")
                self.turn(l, True, f"přebytek {surplus:.0f} W")
                if self.adjustable(l):
                    self.set_amps(l, l["min_a"])
            return  # nepřeskakovat priority
        self.why("Všechny řízené spotřebiče běží.")

    # ── Odlehčení ────────────────────────────────────────────────────

    def shed(self, force, reason="jistič"):
        for l in reversed(self.loads):
            if self.st[l["id"]]["on"] and (force or self.can_change(l, False)):
                self.turn(l, False, reason)
                return True
        return False

    # ── Spínání ──────────────────────────────────────────────────────

    def turn(self, l, on, reason):
        s = self.st[l["id"]]
        self.log(f"{'▶ ZAPÍNÁM' if on else '■ VYPÍNÁM'} {l['name']} — {reason}")
        if l["type"] == "switch":
            self.switch(l["entity"], on)
        elif l.get("modbus"):
            # Modbus wallbox nemá vypínač: zapnout = poslat min. proud, vypnout = 0 A
            self.modbus_write(l, l["min_a"] if on else l["modbus"].get("off_amps", 0))
            s["amps"] = l["min_a"]
        elif l["type"] == "ev":
            self.switch(l["switch"], on)
        elif l["type"] == "select_mode":
            opt = l["on_option"] if on else l["off_option"]
            self.send(l["entity"], opt, "select/select_option", entity_id=l["entity"], option=opt)
        elif l["type"] == "climate_boost":
            temp = l["base_temp"] + (l["boost"] if on else 0)
            self.send(l["entity"], temp, "climate/set_temperature",
                      entity_id=l["entity"], temperature=temp)
        s["on"] = on
        s["t"] = time.time()
        s["reason"] = reason

    def switch(self, entity, on):
        # homeassistant/turn_on funguje pro switch, input_boolean, light i fan
        svc = "homeassistant/turn_on" if on else "homeassistant/turn_off"
        self.send(entity, on, svc, entity_id=entity)

    def set_amps(self, l, amps):
        amps = int(max(l["min_a"], min(l["max_a"], amps)))
        s = self.st[l["id"]]
        if amps == s["amps"]:
            return
        self.log(f"{l['name']}: proud {s['amps']} → {amps} A")
        s["amps"] = amps
        if l.get("modbus"):
            self.modbus_write(l, amps)
            return
        ent = l["current"]
        if ent.startswith("select."):
            self.send(ent, str(amps), "select/select_option", entity_id=ent, option=str(amps))
        else:
            self.send(ent, amps, "number/set_value", entity_id=ent, value=amps)

    def modbus_write(self, l, amps):
        m = l["modbus"]
        self.send(m["readback"], int(amps), "modbus/write_register",
                  hub=m["hub"], slave=m.get("slave", 0), address=m["register"], value=int(amps))

    def adjustable(self, l):
        """Wallbox, kterému jde nastavovat proud."""
        return l["type"] == "ev" and bool(l.get("current") or l.get("modbus"))

    def amps_entity(self, l):
        return l["modbus"]["readback"] if l.get("modbus") else l["current"]

    def send(self, key, desired, service, **kw):
        # pozor: parametr se nesmí jmenovat „value“ — koliduje s value=… pro number/set_value
        if DRY_RUN:
            if self.sent.get(key) == desired:
                return
            self.sent[key] = desired
            self.log(f"   [NA SUCHO] by poslal: {self.describe(service, kw)}")
            return
        # Ostrý provoz: posílat jen když se skutečný stav v HA liší
        # (když někdo hodnotu změní ručně, skript ji vrátí).
        if self.matches(key, desired):
            self.sent[key] = desired
            return
        if self.sent.get(key) == desired and self.sent.get(key + "#t", 0) > time.time() - 60:
            return   # právě odesláno, zařízení ještě nestihlo potvrdit
        self.sent[key] = desired
        self.sent[key + "#t"] = time.time()
        self.log(f"   → posílám: {self.describe(service, kw)}")
        try:
            self.call_service(service, **kw)
        except Exception as e:
            self.log(f"Chyba při {service} {kw}: {e}", level="ERROR")
            self.sent.pop(key, None)

    @staticmethod
    def describe(service, kw):
        """Srozumitelný popis příkazu pro log."""
        if service == "modbus/write_register":
            return f"Modbus {kw['hub']} registr {kw['address']} = {kw['value']}"
        ent = kw.get("entity_id", "?")
        if service.endswith("turn_on"):
            return f"{ent} → ZAPNOUT"
        if service.endswith("turn_off"):
            return f"{ent} → VYPNOUT"
        for k in ("option", "value", "temperature"):
            if k in kw:
                return f"{ent} → {kw[k]}"
        return f"{service} {kw}"

    def matches(self, entity, value):
        try:
            if entity.startswith("climate."):
                cur = self.get_state(entity, attribute="temperature")
                return cur is not None and abs(float(cur) - float(value)) < 0.05
            state = self.get_state(entity)
            if isinstance(value, bool):
                return state == ("on" if value else "off")
            if isinstance(value, (int, float)):
                return abs(float(state) - float(value)) < 0.5
            return str(state) == str(value)
        except (ValueError, TypeError):
            return False

    # ── Pomocné výpočty ──────────────────────────────────────────────

    def wait_s(self, l, to_on):
        wait = self.c["min_off_s"] if to_on else self.c["min_on_s"]
        return max(0, int(wait - (time.time() - self.st[l["id"]]["t"])))

    def can_change(self, l, to_on):
        wait = self.c["min_off_s"] if to_on else self.c["min_on_s"]
        return time.time() - self.st[l["id"]]["t"] >= wait

    def power_w(self, l):
        if l["type"] == "ev":
            return l["min_a"] * VOLTAGE * l["phases"]
        return l.get("power_w", 2000)

    def need_a(self, l):
        """Kolik ampér na nejzatíženější fázi spotřebič přidá."""
        if l["type"] == "ev":
            return l["min_a"]
        return l.get("power_w", 2000) / (VOLTAGE * l.get("phases", 1))

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
