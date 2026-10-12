# Řízení FVE a spotřeby v Home Assistantu (`solar_manager.py`)

AppDaemon aplikace, která každých 30 s rozhoduje, kam poslat elektřinu:

| Situace | Co skript udělá |
|---|---|
| **Přebytek z FVE** (jde do sítě) | Zapne spotřebiče podle priority: bojler → wallbox 1 → wallbox 2 → TČ. Wallboxům s nastavitelným proudem plynule přidává/ubírá ampéry. |
| **Levná spotová cena** (pod 1,5 Kč/kWh) | Nabíjí baterii ze sítě (do 90 %) a zapne spotřebiče označené `cheap` i ze sítě. |
| **Záporná cena** | Vypne přetoky do sítě (export 0 W), nabíjí baterii, zapíná spotřebiče. |
| **Normální / drahá cena** | Baterie kryje dům (Self Use), spotřebiče běží jen z přebytku. Jakmile začnou brát ze sítě **nebo z baterie**, vypínají se (od nejméně důležitého). |
| **Hrozí vypadnutí hlavního jističe** (fáze > 30 A) | Okamžitě vypne nejméně důležitý spotřebič. |

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
* **Režimy SolaXu**: texty v `mode_normal` a `mode_grid_charge` musí přesně odpovídat volbám v `select.…charger_use_mode` (rozklikni entitu a podívej se). Režim *Force Time Use* nabíjí ze sítě jen v časech nastavených ve střídači (`charger start/end time`) – nastav je na celý den a povol nabíjení ze sítě (`grid charge`).
* **Minimální SOC baterie** nastav přímo ve střídači (Self Use min SOC) – skript ho nepřepisuje.

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

---

## Nastavení, se kterými si můžeš hrát

| Klíč | Výchozí | Význam |
|---|---|---|
| `price_cheap` | 1,5 | Pod touto cenou (Kč/kWh, čistý spot) = levná hodina |
| `price_expensive` | 4,5 | Nad touto cenou = drahá hodina |
| `grid_charge_max_soc` | 90 | Do kolika % nabíjet baterii ze sítě |
| `battery_priority_soc` | 80 | Pod tímto SOC má baterie přednost před spotřebiči |
| `fuse_limit_a` / `fuse_reserve_a` | 32 / 2 | Hlavní jistič a rezerva |
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
