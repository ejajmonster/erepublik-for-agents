# eRepublik-for-agents — STAN PROJEKTU (kierunek kontynuacji)

> **Dlaczego ten plik istnieje:** sesja może stracić kontekst. Ten plik mówi,
> na czym stoimy, co jest gotowe, co jest w toku i co robić dalej.
> Aktualizuj go KAŻDEGO RAZU po istotnym kroku. Ostatnia aktualizacja: 2026-10-04 13:35 CEST (sezon 6 live, tempo secesji dostrojone).

## 0. Orientacja w 30 sekund

- **SEZON 6 LIVE (od 04.10 12:59 CEST):** engine10, **v10 on**, endless, seed 20261004, 5000 seatów.
  Sezon 5 (engine9) zamknięty i archiwizowany w `archive/season5/`. Klucze realnych agentów przeniesione.
  **Tempo secesji dostrojone 04.10 13:35:** `SECEDER_COST` 80→60, pula `SPARE` 20→66 nazw.
  Symulacja 5000 seatów: **5→17 nacji w 60 tur** (przy 80: 4/60 tur). Restart serwera bezpieczny —
  zero secedes w logu = stała nie rusza żadnego seal-a. `/api/verify` zielone (6/6 seals).
- **Mój seat:** cid 0, `czlonkek` (qwen3.8 via OpenClaw), Aurelia (naród 0). Klucz w `keys.json`.
  `my_turn.py` v10: najpierw kopal najdroższy rzadki depozyt (stock<8), potem grain→research→culture→work.
- **Inni realni agenci:** cid 1 `pomocnik` (qwen3vl-8b-cpu, OpenClaw worker), cid 2 `zcode_glm` (GLM-5.3 via ZCode), cid 3 `head-of-engineering`.
- **Sezon 3 (engine4) i wcześniejsze — archiwum.** Sezon 3 się domknął; archiwum w `archive/`.

## 1. Mapa plików

| Plik | Status | Uwagi |
|---|---|---|
| `engine10.py` | **LIVE (sezon 6)** | v10: 4 rzadkie zasoby (copper/spices/gems/uranium) z nierównomiernymi depozytami, akcja `mine`, secede + cap 65. `SECEDER_COST=60` (04.10: 80→70→60 — tempo 5→17 nacji/60 tur na 5000 seatów). Testy 8/8 + probe_resources 7/7 + `probe_backcompat10.py` (czyta flagi z live stanu, nie hardcoduje v10=False). |
| `engine8.py` | gotowy (sezon 5) | v8: living economy (market impact, mean reversion, war shock, credit inflation). 10/10 zielone. |
| `engine7.py` | gotowy (sezon 4) | v7: misje, pakte obronne, trade_offer z escrowem, okupacja→wasal, war_log. `test_engine7_features.py` = 12/12 zielone. |
| `my_turn.py` | LIVE | autonomiczny gracz cid 0: grain→research→culture→work; idempotentny. Cron `czlonkek-turn` co :03. |
| `engine4.py` / `bots4.py` | sezon 3 (ukończony) | |
| `server.py` | **LIVE** | state.json = cache; verify replays z seed+log (linia join niosą `persona`). **Patched: `server_config.json` jako persistent fallback gdy env vars resetowane.** |
| `server_config.json` | **LIVE (NOWY)** | `{SEASON, SEED, SEATS, SEASON_START}` — trwały fallback config. Serwer czyta go po odczycie env vars i nadpisuje wartościami z pliku. Naprawia root cause: systemd user scope resetuje środowisko po restarcie hosta. |
| `play_turn.py` | LIVE | dogra botów (batche po 500 na `/api/actions`); realni grają sami. |
| `post_head.py` | LIVE | head po close na 1f916 (post 6178); tracker `head-posted.json`. |
| `start_season3.py` | razowe (LIVE) | launch sezonu 3; **NAPRAWIÓN 26.09: persona w linii join**. |
| `start_season5.py` | razowe | launch sezonu 5 (seed 20261029, 5000 seats, engine9, endless=True). |
| `archive/season2/`, `archive/seed1/` | kotwice | zamrożone stany+logi. |
| `archive/season4/` | archiwum | sezon 4 (engine7, turn 29, verify=true). |
| `archive/season5-corrupt/` | archiwum | zepsuty stan z 29.09 17:25 (reset env vars, 75 stale turnów). |
| `season3-proof.json` | artifact | dowód benchmarku: 80 tur × 5000 seatów = 111.8s, verify ok. |

## 2. Serwisy (systemd --user)

| Service | Rola |
|---|---|
| `erepublik-server` | HTTP :8451, **engine9, sezon 5 endless**. Env: `EREP_ENGINE=engine9`, `EREP_SEED=20261029`, `EREP_SEASON=season5`, `EREP_SEATS=5000`, `EREP_SEASON_START=1790701200` (29.09 17:00 UTC). **Fallback: `server_config.json`.** |
| `erepublik-play` | timer :55; `play_turn.py` dogra botów przed close. |
| `erepublik-head` | timer :03; `post_head.py` po close. |
| `erepublik-www` + `.timer` | sync → Pages (co 5 min + PathChanged; **push przy zmianie URLA LUB content hash**). |
| `erepublik-tunnel` | quick cloudflared — tunel znów na quick URL (tunnel-url.txt; 28.09: `preceding-rising-trips-algorithm.trycloudflare.com`). |
| `czlonkek-turn` | timer :03; `my_turn.py` autonomiczny ruch cid 0. |

## 3. Incydent 26.09 (verify=false) — WYRZĘBIONE

- Serwer manualny (po restarcie hosta ~18:18 CEST) padł; łańcuch tury 0 sezonu 3
  miał verify=false. Przyczyna: `start_season3.py` nadpisywał `persona` seatów
  0/1/2 w stanie, ale linie join w logu nie nosiły `persona` → replay nie potrafił
  odtworzyć stanu → seal tury 0 się nie zgadzał.
- **Naprawa w miejscu (bez resetu — head tury 0 = komentarz 81218 był publiczny):**
  1. `log.jsonl`: 3 linie join wzbogacone o pole `persona` (wartości ze stanu).
  2. `server.py` `/api/verify`: replay czyta `persona` z linii join.
  3. `server.py` `/api/citizens` (join): linia join zapisuje `persona`.
  4. `start_season3.py`: rejestracja realnych agentów zapisuje `persona` w linii join.
  5. Unit `erepublik-server.service` zaktualizowany (engine4, 5000 seatów,
     SEASON_START=1790438400) — wcześniej wskazywał engine3/season2.
- Po restarcie unit: serwer domknął turn 1 (catch-up), **verify=true, turn 2 otwarty**.

## 3a. Incydent 29.09 17:25 (reset env vars) — NAPRAWIONE

- Po restarcie hosta ~17:25 CEST systemd user scope zresetował środowisko.
  `EREP_SEASON_START` wrócił do defaultu 1790431200 (26.09 14:00 UTC).
  `close_stale_turns()` przy bootcie zobaczył 75 stale turnów i chciał je wszystkie
  domknąć naraz z 5000 pendingów. Stan był w połowie mutacji.
- **Naprawa:**
  1. Archiwizacja zepsutego stanu → `archive/season5-corrupt/` (state, log, keys).
  2. Reset do czystego turn 0: `turn=0, pending={}, seals=[], winner=None, log_index=0`.
     3 join-y realnych agentów w log.jsonl. 5000 kluczy nienaruszone.
  3. **`server_config.json`**: nowy plik konfiguracyjny (SEASON, SEED, SEATS, SEASON_START=1790701200).
  4. **`server.py`**: patch — po odczycie env vars, nadpisuje je wartościami z `server_config.json`.
  5. Serwer restart: verify=true, replay_ok=True, joins=3, seals=0, pending=0.
- **Root cause:** systemd user scope resetuje środowisko po restarcie hosta. `Environment=` w unit file nie działa po reboot. Trwała naprawa: `server_config.json` jako persistent config.

## 3b. Incydent 30.09 11:49 (verify=false od t5) — NAPRAWIONE

- **Stan:** turn 16 otwarty, `replay_ok: false` od tury 5 (seal mismatch, log line 29765).
  T0–t4: replay identyczny z live. T5: log ma 4858 akcji, ale część utracona w środku tury
  (rejestr w trakcie tury) → seal t5 nie do odtworzenia → łańcuch od t5 nie do zweryfikowania.
- **Decyzja Piotra:** soft reset do t4 (truncate log do seal t4, state do t5 otwartej).
- **Naprawa:**
  1. Archiwizacja zepsutego stanu (t17) → `archive/season5-softreset-20260930-121102/`.
  2. Log przycięty do t4 (24907 linii, łącznie z seal t4).
  3. State zbudowany od seed via apply_action (one by one) — seal chain t0–t4 = log ✓.
  4. `server_config.json`: SEASON_START → next hour boundary (t5 otwarta od teraz).
  5. Serwer restart → **verify: replay_ok=true, seals 5, turn 5 otwarta** (close 13:00 CEST).
- **Utracone:** t5 (~4858 akcji). Seals t0–t4 nienaruszone.
- **Root cause:** rejestr hosta w środku tury t5 → część akcji utracona z logu.

## 4. Co jest ZROBIONE

- [x] **engine10 (v10) DOKOŃCZONE (04.10 00:16):** secede + cap 65 (wariant A) + **mechanika surowców** — 4 rzadkie zasoby `copper/spices/gems/uranium` (bazy 14/18/22/30) rozłożone nierównomiernie (`_gen_deposits(seed)`, per-nacja rng, p=0.33), nowa akcja `mine` (wydobywa z depozytu; bez argu = najdroższy cenowo), ceny nowych surowców dryfują/cap `_pcap` (baza+8), embargo/treaty/market walidują na `_v10_resources`. Flaga `v10` off domyślnie; `v10` implikuje `v9`.
      - **BACK-COMPAT GATE PRZEBITY:** `probe_backcompat10.py` = engine10 z `v10=off` replikuje **żywy sezon 5 1:1** (seed+log.jsonl, 83 seal-e na 04.10 00:15). Stary łańcuch pozostaje reprodukowalny na wspólnym kodzie; aktywacja sezonu 6 = `EREP_ENGINE=engine10` + `v10=True` w `_engine_flags`.
      - Testy: `test_engine10_features.py` 8/8 (v10-off==v9, secede, leadership, guards, cap 65/8, determinism, replay, tamper); `probe_resources10.py` 7/7 (nierównomierne depozyty, mine+auto-select, refund v9, driftem cen+cap, embargo na rzadkim, determinism z mine).
      - `bots10.py` (z bots7, engine10): boty górniczą depozyty (merchant = najdroższy; inni = brakujący, stock<5), `found` używa `_max_nations`. Smoke 4 tury: deterministyczny, zero odrzuceń, `mine` 168×/50 obywateli.
      - `server.py`/`play_turn.py`: `v10` w `_engine_flags`/`_replay_state`/`pick_bots`.
      - **LIVE server NIEDOTKNIĘTY:** dalej engine9, season5, endless. Przejście na engine10 = przy starcie sezonu 6 (nowy seed, `EREP_ENGINE=engine10`).

- [x] **Sezon 4 zaarchiwizowany** do `archive/season4/` (state, log, keys; turn 29, verify=true).
- [x] **Sezon 5 LIVE (engine9 endless, wariant 1b):** launch 29.09 14:26 UTC, turn 0 otwarty
      (close 15:00 UTC), 3 realnych agentów (czlonkek, pomocnik, zcode_glm). engine9 `_check_end`
      respektuje `endless` — bez twardego końca; 1 nacja = zapisana jako `winner`, gra trwa dalej.
- [x] **WWW panel bohaterów + endless UI** (29.09 16:55, commit `cd4f0be`): zakładka Realm ma
      panel Heroes (nazwa/naród/skill/loyalty bar + akcje `bribe{id}`/`assassinate{id}`), licznik
      tury w endless bez "/80" (pasek pełny). Serwer podaje `heroes` w public_state (15 bohaterów
      na start, 3/naród). Push na Pages: hash `b273e3151bef` (repo `d8e9991`); tunel
      `airfare-titanium-fundamentals-ion.trycloudflare.com` (strona = Pages, tunel służy API).
- [x] **Sezon 5 RESET (29.09 17:37 CEST):** incydent 17:25 — archiwizacja, reset do czystego
      turn 0, `server_config.json` jako persistent fallback, patch server.py. Verify=true.
- [x] **X/Twitter promocja (29.09 17:45–17:50 CEST):** 2 posty na @ejajmonster —
      SEASON 5: ENDLESS (2104960854961664267) + 3 AI agents deterministic server (2104962536575545435).
- [x] **Tura 0: real agents + komentarze (29.09 18:10–18:15 CEST):** czlonkek/pomocnik/zcode_glm
      research; komentarze pod postami (pomocnik pod post 1, zcode_glm pod post 2). Replies=1 na obu.
- [x] **Sezon 4 (engine7):** verify=true od genesis przez 29 tur; mój gracz autonomiczny (cron `czlonkek-turn`).
- [x] **Mapa-kula na WWW domknięta** (28.09): brakujące stałe `GX/GY/GR/S`, `defs`
      (stars, gclip, vig-vinjeta), wyśrodkowanie świata (translate − S·320/− S·235),
      kolizje klas `card`→`cardx`/`capcard`, wireframe ortograficzny (grat3d),
      rim, scale bar pod limbą; CSS `.sector/.sname/.grat/.cardx/.capcard/.grat3d/.rim`.
      Test node harness: 12 sektorów, karty, flaga wasala, linie wojny, zero NaN.
      Push na Pages zweryfikowany (hash b3fd7d56ea4e). Commit workspace `bbef834`.
- [x] Sezon 3: 5000 seatów, engine4 — ukończony, archiwum.

## 5. Co jest W TOKU / DO ZROBIENIA (kolejność = priorytet)

1. **Sezon 5 trwa (endless, po soft reset 30.09).** Verify=replay_ok=true.
   Mój ruch każdej tury: cron `czlonkek-turn` (co :03) gra `my_turn.py`.
   **KOLEJNA WIELKA RZECZ:** sezon 6 = engine10 (v10: surowce+depozyty, secede, cap 65) — silnik gotowy i przetestowany; do zrobienia: decyzja Piotra (start w naturalnym końcu / na sygnał), `start_season6.py` (nowy seed, `EREP_ENGINE=engine10`, flaga `v10`), WWW (panel depozytów/górników, nazwa surowców), ogłoszenie.
2. **Tunel:** quick cloudflared (URL zmienne) — monitorować `tunnel-url.txt`, `www_sync` sam wypycha.
3. **Promocja:** kolejne posty co kilka tur, udostępnianie linków, hashtagi.
   **1f916 (29.09):** OPEN CALL opublikowany — post **7181** + komentarze pomocnik-2.
4. **Stabilność (#1) — ZAMKNIĘTE 29.09 18:55 CEST:** `/api/health` + `tunnel_watch.sh` + backup co close.
5. **Do rozważenia:** naprawa root cause — dlaczego rejestr hosta utracił akcje w środku tury?
   (Buffer log do batcha? fsync po każdej akcji? Czy to było OOM kill / crash serwera?)

## 9. Sezon 5 = ENDLESS na engine9 (Piotr, 29.09)

- **Sezon 4 = ostatni sezon z wygranym** (kończy się po 80 turach / 40 dniach).
- **Sezon 5 = endless na silniku 9** (engine9: bohaterowie jako postacie, łapówki, zamachy, defekcja).
- engine9 zielony (11/11 testów), deterministyczny, replay z seed+log działa.
- **Endless mode:** `_check_end` respektuje `state["endless"]` — bez twardego końca.
  1 nacja = zapisana jako `winner`, gra trwa dalej.
- **Start:** start_season5.py, seed 20261029, 5000 seatów, engine9 v9=True, archiwizacja
  sezonu 4 do archive/season4/, rejestracja realnych agentów, nowy SEASON_START=1790701200 (17:00 UTC),
  update systemd unit erepublik-server, WWW panel bohaterów (lojalność, skill, zamachy/łapówki).
- **Decyzje Piotra:** (1) wariant 1b — ostatnia nacja zostaje liderem, gra trwa dalej. (2) Start od razu.

## 10. Heartbeat 29.09 15:53 → 14:00 UTC

- Turn 28 sezonu 4 domknięty ~14:00:15 UTC (close_stale_turns race, ale serwer się ogarnął).
- Turn 29 otwarty, verify=true (seals 29). Mój ruch t29 = work (credits 7, grain 5).
- cron czlonkek-turn: przywrócony 15:14, next trigger 16:03 CEST. Działa.

## 8. Incydent 29.09 (cron czlonkek-turn zniknął) — NAPRAWIONE

- Timer+service `czlonkek-turn` (autonomiczny ruch cid 0, co :03) nie istniał na dysku
  (był opisany w tym pliku, ale unit zniknął — prawdopodobnie po restarcie hosta). Skutek:
  moje tury 18, 25, 27 sezonu 4 poszły pusto (brak akcji w logu).
- **Naprawa 29.09 15:14:** odtworzony `czlonkek-turn.service` + `.timer` (OnCalendar *:03, Europe/Warsaw,
  Persistent, my_turn.py), enabled+started. Next trigger: 16:03 CEST.
- Tura 28 sezonu 4: mój ruch `market_buy grain` w pending (cron zagrał przed moim interwencją).
- Stan 29.09 15:14: turn 28 otwarty, verify=true (seals 28), tunel quick
  `airfare-titanium-fundamentals-ion.trycloudflare.com`, head tury 28 opublikowany (comment 85439).

## 6. Jak kontynuować (jeśli kontekst przepadł)

1. Czytaj ten plik + ostatni wpis `memory/YYYY-MM-DD.md`.
2. Live: `curl localhost:8451/api/turn`, `curl localhost:8451/api/verify`,
   `tail -5 log.jsonl`, `curl -s https://erepublik.tail962662.ts.net/api/turn`.
3. State: `state.json` (cache), `log.jsonl` (seal chain), `keys.json` (5000 kluczy),
   `server_config.json` (persistent config fallback).
4. Mój ruch: jak otwiera się nowa tura i nie ma mojej akcji w `pending`, gram
   świadomie `/api/action` (klucz cid 0). Logika: state.json → credits, tech/culture
   caps, rynek, wojny → decyzja.

## 7. Decyzje i zobowiązania

- **Pre-commit sezonu 3:** seed 20260926, 5000 seatów, engine4 — publiczny.
- **Sezon 4:** seed 20260929, engine7, 5000 seatów, start 2026-09-28 09:00 UTC.
- **Sezon 5:** seed 20261029, engine9, 5000 seatów, start 2026-09-29 17:00 UTC (po resecie).
- Piotr: "skup się na rozwoju gry" — kolejne wersje silnika po sezonach.
- **28.09 20:30:** engine9 (bohaterowie) zielony + zcommitowany (workspace `99b98f7`); mirror engine5–9 + testy + my_turn na GitHubie (`f49b623`); server.py z flagami v8/v9 do sezonu 5; cron `czlonkek-turn` (co :03) gra moje tury autonomicznie. Tura 9 sezonu 4: kolejkowałem `accept_offer 15` przez pomyłkę (oferta od nas do narodu 1 — nie do nas) → ruch tury 9 padnie, od tury 10 gra cron.
- **29.09 17:00:** Piotr wybrał wariant 1b (ostatnia nacja zostaje liderem, gra trwa dalej) + start od razu.
- **29.09 17:37:** Reset stanu po incydencie (reset env vars, 75 stale turnów). Archiwizacja + reset do czystego turn 0 + `server_config.json` jako persistent fallback.

## 11. Stabilność — dodane 29.09 18:50 CEST

- `/api/health` endpoint (server.py): `ok`, `uptime_s`, `turn`, `seals`, `last_seal_utc`.
- `tunnel_watch.sh`: health-check serwera na początku — jeśli `/api/health` nie odpowiada, restart `erepublik-server.service` (15 × 2s wait). Potem standardowa logika tunelu.
- Serwer restart + verify: `/api/health` → `ok: true, uptime_s: 1, turn: 0, seals: 0`.
