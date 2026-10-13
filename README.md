# Řízení FVE a spotřeby v Home Assistantu (`solar_manager.py`)

AppDaemon aplikace, která každých 30 s rozhoduje, kam poslat elektřinu:

| Situace | Co skript udělá |
|---|---|
| **Přebytek z FVE** (jde do sítě) | Zapne spotřebiče podle priority: bojler → wallbox 1 → wallbox 2 → TČ. Wallboxům s nastavitelným proudem plynule přidává/ubírá ampéry. |
| **Levná spotová cena** (pod 1,5 Kč/kWh) | Nabíjí baterii ze sítě (do 90 %) a zapne spotřebiče označené `cheap` i ze sítě. |
| **Záporná cena** | Vypne přetoky do sítě (export 0 W), nabíjí baterii, zapíná spotřebiče. |
| **Normální / drahá cena** | Baterie kryje dům (Self Use), spotřebiče běží jen z přebytku. Jakmile začnou brát ze sítě **nebo z baterie**, vypínají se (od nejméně důležitého). |
| **Hrozí vypadnutí hlavního jističe** (fáze > 23 A) | Okamžitě vypne nejméně důležitý spotřebič. |

Spotřebu domu skript neřídí, jen ji vidí v měření sítě.

---

## Návod krok za krokem

### 1. Nainstaluj AppDaemon
1. **Nastavení → Doplňky → Obchod s doplňky** → vyhledej **AppDaemon** → Instalovat.
2. Zapni **Spustit při startu** a **Watchdog** → **Spustit**.

### 2. Nahraj soubory
Nejjednodušeji doplňkem **File editor** nebo **Studio Code Server** (taky z Obchodu s doplňky).

* `apps/solar_manager.py` → ulož do `/addon_configs/a0d7b954_appdaemon/apps/solar_manager.py`
* obsah `apps/apps.yaml` → přidej na konec souboru `/addon_configs/a0d7b954_appdaemon/apps/apps.yaml`

> U starších instalací je složka `/config/appdaemon/apps/`.

### 3. Vytvoř hlavní vypínač
**Nastavení → Zařízení a služby → Pomocníci → Vytvořit pomocníka → Přepínač**, název `solar_manager`.
Vznikne `input_boolean.solar_manager`. Když ho vypneš, skript přestane cokoliv řídit (např. dovolená, servis).
Nechceš ho? V konfiguraci dej `"master_switch": ""`.

### 4. Najdi názvy svých entit
**Nástroje pro vývojáře → Stavy**, do filtru piš:

| Co hledáš | Filtr | Do konfigurace |
|---|---|---|
| Aktuální spotová cena (Kč/kWh) | `spot` | `spot_price` |
| Výroba FVE celkem (W) | `solax pv power` | `pv_power` |
| Stav baterie (%) | `solax battery capacity` | `battery_soc` |
| Výkon baterie (W) | `solax battery power` | `battery_power` |
| Výkon do/ze sítě (W) | `solax measured power` (nebo `grid`) | `grid_power` |
| Proudy fází **ze sítě** (A) | `solax current` | `phase_currents` |
| Pracovní režim střídače | `solax charger use mode` | `work_mode_select` |
| Limit přetoků (W) | `solax export` | `export_limit` |
| Bojler (relé/Shelly) | `bojler`, `shelly` | `loads → boiler → entity` |
| Wallbox 1 – zapnutí nabíjení | název wallboxu | `ev1 → switch` |
| Wallbox 1 – nabíjecí proud | název wallboxu + `current` | `ev1 → current` |
| Wallbox 2 (Tuya Local) | název wallboxu | `ev2 → switch`, `current` |
| Tepelné čerpadlo | `climate.` | `hp → entity` |

Pak v `solar_manager.py` přepiš všechny řádky označené `# ZMĚŇ`. Spotřebič zapneš do řízení přes `"enabled": True`.

Tipy:
* **Wallbox**: když jde proud jen přepínat ze seznamu (Tuya to tak často má), je to entita `select.…` – skript to umí. Když jde jen zapnout/vypnout, dej `"current": None`.
* **Fáze** (`"phases"`): bojler bývá na 1 fázi, wallbox a TČ na 3. Hodnota se používá pro hlídání jističe.
* **TČ** se řídí přes `climate` entitu: při přebytku/levné ceně zvedne požadovanou teplotu o `boost` °C, jinak vrátí `base_temp`. Pokud tvoje integrace TČ nemá `climate`, ale třeba `switch` (např. SG Ready), změň `type` na `"switch"`.
* **Řízení baterie** je ve výchozím stavu vypnuté (`"battery_control": False`), protože na baterii máš vlastní automatizace. Nabíjení ze sítě se dělá přepnutím `select.solax_charger_use_mode` na *Manual Mode* a `select.solax_manual_mode_select` na *Force Charge* (seznam `grid_charge_selects`). Než ho zapneš, vyzkoušej to ručně v HA a ověř, že baterie opravdu nabíjí ze sítě.
* **Minimální SOC baterie** nastav přímo ve střídači (Selfuse Discharge Min SOC). Skript ho nepřepisuje.

### 4b. Tvoje instalace – co je už nastavené

| Co | Entita | Poznámka |
|---|---|---|
| Spotová cena | `sensor.current_spot_electricity_price` | Kč/kWh |
| FVE / baterie / síť | `sensor.solax_pv_power_total`, `…battery_capacity`, `…battery_power_charge`, `…measured_power` | znaménka ověřena, invert není potřeba |
| Hlídání jističe | `sensor.solax_measured_power_l1..l3` | proud se dopočítá z výkonu fází (W ÷ 230) |
| Bojler | `switch.boiler_jistic` | ~2,4 kW, 1 fáze |
| TČ SPRSUN | `select.sprsun_sg_ready_mode` | SG+ při přebytku nebo levné ceně, jinak Normal. **Musí být zapnutý `switch.sprsun_sg_ready_function`.** |
| Wallbox China (Tuya) | `switch.nabijecka_doma_china` + `number.nabijecka_doma_china_max_current` | 3 fáze, 10 kW, 6–14 A |
| Wallbox Olife (Modbus) | zápis do registru 2106 (hub `olife_energy`), zpětně `sensor.current_limit_control` | 0 A = nenabíjí, 6–16 A = nabíjí. Žádný vypínač nemá. |

**Automatizace, které se se skriptem perou.** Před ostrým provozem je vypni (Nastavení → Automatizace), jinak budou dvě věci ovládat totéž:

* pro oba wallboxy: `EV Charging – Enhanced Dynamic Control`, `EV Charging – Morning Reset / Night Stop`, `Spuštění nabíjení`, `Start/Stop nabíjení …` a `Ukončení nabíjení`, pokud ovládají tuto nabíječku,
* pro baterii (jen když zapneš `battery_control`): `Solax – řízení podle spot ceny`, `Nabíjení baterie start/stop`, `Vybíjení baterie do sítě podle spotu START/STOP`, `Sync … export limit` a `Nastavení doby trvání režimu Solax`.

**Olife – ověření ručně (jednou, před ostrým provozem):** v **Nástroje pro vývojáře → Akce** spusť

```yaml
action: modbus.write_register
data:
  hub: olife_energy
  slave: 0
  address: 2106
  value: 8
```

Do 15 s by měl `sensor.current_limit_control` ukázat 8. S připojeným autem by mělo začít nabíjet a `sensor.charging_power` by měl ukázat zhruba 5,5 kW při 3 fázích. Potom pošli `value: 0`, nabíjení by se mělo zastavit. Když HA hlásí chybu u `slave`, přepiš ho na `device_address` (novější HA). Stejně pak uprav i řádek `slave=` ve skriptu.

### 5. Kontrola znamének (důležité!)
Skript počítá s tím, že:
* `grid_power` je **kladný při přetoku do sítě**, záporný při odběru,
* `battery_power` je **kladný při nabíjení**, záporný při vybíjení.

Ověř to v poledne v **Nástroje pro vývojáře → Stavy**: když panely vyrábí a posíláš do sítě, musí být `grid_power` kladný. Když je to obráceně, nastav `"grid_invert": True` (u baterie `"battery_invert": True`).

### 6. Zkušební provoz „na sucho“
V souboru je `DRY_RUN = True` → skript **nic nespíná**, jen píše do logu, co by udělal.
Log najdeš v **Nastavení → Doplňky → AppDaemon → Log**. Uvidíš např.:

```
SolarManager spuštěn ✓  režim: NA SUCHO (nic nespíná)  |  spotřebiče: Bojler
cena 3.12 Kč (normální) | FVE 5200 W | síť +2900 W | baterie 95 % +0 W | fáze max 6.1 A
▶ ZAPÍNÁM Bojler — přebytek 2900 W
   [NA SUCHO] homeassistant/turn_on {'entity_id': 'switch.bojler'}
```

Kdyby se v logu objevilo `✗ Entita … v HA neexistuje`, máš překlep v názvu.
Nech to tak běžet 2–3 dny a kontroluj, že rozhodnutí dávají smysl.

> Pozor: na sucho se spotřebiče ve skutečnosti nezapnou, takže měření sítě se nezmění – skript si je jen „pamatuje“ jako zapnuté.

### 7. Ostrý provoz
Přepni `DRY_RUN = False` a ulož. AppDaemon soubor načte sám. Začni s jedním spotřebičem (bojler), další přidávej postupně přes `"enabled": True`.

### 8. Stránka (dashboard) pro sledování a ovládání
Skript každých 30 s zapisuje do HA dva senzory:

* `sensor.solar_manager`: stav (záporná / levná / normální / drahá / jistič! / pozastaveno / chyba dat). V atributech je cena, přebytek, FVE, síť, baterie, fáze, stav spotřebičů a **posledních 20 řádků logu**.
* `sensor.solar_manager_prebytek`: přebytek pro spotřebiče ve W, aby šel kreslit do grafu.

Vložení stránky:
1. **Nastavení → Ovládací panely → Přidat ovládací panel → Nový ovládací panel od začátku**, název třeba „Řízení FVE“.
2. Otevři ho → vpravo nahoře **tužka** → **⋮** → **Editor nezpracované konfigurace**.
3. Smaž všechno a vlož celý soubor [`dashboard/solar_manager.yaml`](dashboard/solar_manager.yaml) → **Uložit**.

Na stránce je: hlavní vypínač, aktuální rozhodnutí skriptu a stav spotřebičů, log řízení, časová osa rozhodnutí za 24 h, výkony a fáze ze sítě, spotová cena a karta pro každý řízený spotřebič. Bojler, TČ i nabíječky jde odtud ovládat i ručně.

> Senzory `sensor.solar_manager…` existují jen za běhu skriptu. Po restartu HA se objeví do 30 s. Když karta hlásí „Skript neběží“, zkontroluj doplněk AppDaemon.

---

## Nastavení, se kterými si můžeš hrát

| Klíč | Výchozí | Význam |
|---|---|---|
| `price_cheap` | 1,5 | Pod touto cenou (Kč/kWh, čistý spot) = levná hodina |
| `price_expensive` | 4,5 | Nad touto cenou = drahá hodina |
| `grid_charge_max_soc` | 90 | Do kolika % nabíjet baterii ze sítě |
| `battery_priority_soc` | 80 | Pod tímto SOC má baterie přednost před spotřebiči |
| `fuse_limit_a` / `fuse_reserve_a` | 25 / 2 | Hlavní jistič a rezerva |
| `hysteresis_w` | 200 | Pásmo necitlivosti, aby to necvakalo |
| `min_on_s` / `min_off_s` | 300 | Minimální doba zapnutí/vypnutí spotřebiče |

Pořadí v `loads` = priorita. Co je výš, zapíná se dřív a vypíná později.

---

## Řešení problémů

| Problém | Příčina / řešení |
|---|---|
| `Chybí cena nebo výkon sítě` | Entita `spot_price` nebo `grid_power` je `unavailable` nebo špatně pojmenovaná. |
| `Neznám proudy fází` | Chybí `phase_currents` → skript z bezpečnostních důvodů nic nezapíná. |
| Spotřebiče se zapnou a hned vypnou | Obrácené znaménko sítě nebo baterie (krok 5). |
| `Chyba při select/select_option` | Text režimu v `mode_normal`/`mode_grid_charge` neodpovídá volbě ve střídači. |
| Ručně jsem změnil režim střídače a skript ho vrátil | Tak to má být. Chceš řídit ručně → vypni `input_boolean.solar_manager`. |

---

## Testy (pro vývoj)

```
python3 tests/test_solar_manager.py
```
Simulace bez Home Assistantu – ověřuje přebytek, vybíjení baterie, levnou/zápornou cenu, ochranu jističe, ruční zásahy, hlavní vypínač a režim na sucho.

---

## Co bylo opraveno oproti původní verzi

1. **Pád při nastavování čísel** – nastavení exportního limitu i proudu wallboxu končilo chybou `send() got multiple values for argument 'value'` při každém kroku. V ostrém provozu by skript nefungoval vůbec.
2. **Baterie potichu napájela spotřebiče** – v režimu Self Use kryje baterie odběr, takže síť ukazuje ~0 W a skript nepoznal, že bojler/auto jede z baterie. Teď se vybíjení baterie počítá jako odběr a spotřebiče se vypnou.
3. **Ruční zásahy** – skript si teď každý krok načte skutečný stav spínačů (ruční zapnutí, výpadek Wi-Fi/Tuya) a když někdo ručně změní režim střídače nebo limit, vrátí ho.
4. **Wallbox přes `select`** (typické pro Tuya Local) – proud jde nastavit i výběrem ze seznamu.
5. **Spínání přes `homeassistant.turn_on/off`** – funguje i pro `input_boolean`, `light` apod., nejen `switch`.
6. **Jističe a fáze** – spotřebiče mají `phases`, 3fázové TČ se už nepočítá jako 13 A na jedné fázi.
7. **Kontrola entit při startu**, **hlavní vypínač** a volby `grid_invert`/`battery_invert` pro obrácená znaménka.
