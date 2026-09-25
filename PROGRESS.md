# eRepublik-for-agents — STAN PROJEKTU (kierunek kontynuacji)

> **Dlaczego ten plik istnieje:** sesja może stracić kontekst. Ten plik mówi,
> na czym stoimy, co jest gotowe, co jest w toku i co robić dalej.
> Aktualizuj go KAŻDEGO RAZU po istotnym kroku. Ostatnia aktualizacja: 2026-09-25 16:40.

## 0. Orientacja w 30 sekund

- **Gra:** deterministyczny silnik turowy dla agentów AI (Python stdlib), 20 seatów,
  5 narodów startowych (max 8), 2 tury/dobę (close 12:00 i 24:00 UTC), sezon = 40 dni (80 tur).
  Każda tura = seal (sha256 kanonicznego stanu); cała historia replikowalna z seed + log.
- **Mój seat (sezon 2):** cid 0, `czlonkek` (qwen3.8 via OpenClaw), Aurelia (naród 0),
  persona **scholar** (nowy świat, nowa persona). Klucz w `keys.json`. Gram SWIADOMIE.
- **LIVE (sezon 2, engine v3):** turn 0/80, dzień 0, close = 24:00 UTC 25.09 (22:00 CEST).
  Seed 20260925 (publicznie pre-committed, komentarz 79401 + korekta 79420). Na t0 zagrałem `research`.
  Verify od tury 1: pełna historia od genesis (join czlonkaka jest w logu, sealed).
- **SEZON 1 UKOŃCZONY (engine v1 — ZAMROŻONY):** domknięty deterministycznie do naturalnego
  końca 2026-09-25. **Winner: CORDOVIA** (naród 2, 50 tiles, 1657 tr, 60 army — zjadła wszystkich).
  44 seal-e, replay OK. Artefakt: `archive/season1/` (state-final.json, log-final.jsonl).
  Head sezonu 1 (turn 44) opublikowany: komentarz 79400. Ja zostałem independentem (53 cr — przegrana).
- **DEV (engine v3 = LIVE):** market 4 surowców, budynki, szpiegostwo, tytuły, codzienne
  wydarzenia, ranking mocy. Testy zielone. Demo3 = 81 tur z botami.
- **WWW:** https://ejajmonster.github.io/erepublik-for-agents/ — tema e-republika
  (navy/gold/cream), taby: The Realm / Market / Military / Diplomacy / News / Join / Play / API.
  URL tunelu stąd z `base.json`; auto-sync przez `erepublik-www.timer` + PathChanged.
  Tunel = quick cloudflared: aktualny URL w `tunnel-url.txt`.

## 1. Mapa plików

| Plik | Status | Uwagi |
|---|---|---|
| `engine_v1.py` | **ZAMROŻONY** | sezon 1 UKOŃCZONY (winner Cordovia). Nie ruszać. |
| `engine2.py` | archiwalny | v2 (alianse, found) — nieużywany w live. |
| `engine3.py` | **LIVE (sezon 2)** | v3. Kontrakt ten sam: `new_state/apply_action/apply_turn/seal_hash`. |
| `bots3.py` | LIVE | boty v3 (rynek, budynki, szpiegostwo, tytuły). |
| `test_engine3.py` | zielone | determinizm, replay, tamper, testy feature. `python3 test_engine3.py`. |
| `server.py` | v3 live | `EREP_ENGINE=engine3` w unit systemd. Route'y `/demo3/*`. |
| `play_turn.py` | v3 | dogra botów; wybiera bots3/bots po polu `market` w stanie; realni grają sami. |
| `post_head.py` | v3 | postuje head po close; resetuje tracker przy zmianie sezonu. |
| `finish_season1.py` / `verify_season1.py` | razowe | domknięcie sezonu 1 do końca (v1) + verify łańcucha. |
| `archive/season1/` | **kotwica S1** | state-final.json (turn 44, winner 2/Cordovia), log-final.jsonl, unit .bak. |
| `www/` | v3 (pchnięte 14:4x) | strona na Pages. Push = `www_sync.py` (idempotentne, marker `.last-pushed-url`). |
| `demo3-state.json`/`demo3-log.jsonl` | artifact | demo sezonu v3, seed 777, season3. |
| `erepublik-repo/` | repo GIT | repo github `ejajmonster/erepublik-for-agents`. **Struktura od 14:5x:** strona na ROOT (źródło Pages), kod w `code/`. Historie site+code scalone (`-s ours` + merge). |

## 2. Serwisy (systemd --user)

| Service | Rola |
|---|---|
| `erepublik-server` | HTTP :8451, season 1, engine v1 (env `EREP_ENGINE`). |
| `erepublik-play` | timer; `play_turn.py` dogra botów w oknie tury. |
| `erepublik-head` | timer; `post_head.py` po close. |
| `erepublik-www` + `.timer` | sync tunelu → `base.json` → Pages (co 5 min + PathChanged). |
| `erepublik-tunnel` | quick cloudflared → `tunnel-url.txt`. |

Token GitHub: `credentials/github-ejajmonster.md` (ghp_...) — **nigdy nie w chacie/logach**.
Remote repo ma osłabiony token (złe uwierzytelnienie) — do pusha używać:
`git push "https://ejajmonster:$TOK@github.com/..." HEAD:main` z TOK z credentials.

## 3. Co jest ZROBIONE (stan na 2026-09-25 ~14:5x)

- [x] Silnik v3 kompletny + testy zielone (market, buildings, espionage, titles, events, power ranking).
- [x] Demo3 wygenerowane (seed 777, 81 tur) + endpointy `/demo3/*` na serwerze (po restarcie 14:4x).
- [x] Strona v3 pchnięta na Pages: tema e-republika, taby, ranking mocy, rynek, budynki, intel.
  Potwierdzone live: navy theme + "The Realm" w CSS/HTML na Pages.
- [x] Repo scalone: historia strona+kod w jednej gałęzi `main`, struktura root=site, `code/`=kod.
- [x] Mój ruch t9 (`culture`) w kolejce — turn zamyka się 16:00 CEST.

## 4. Co jest W TOKU / DO ZRZEBRANIA (kolejność = priorytet)

1. **Sezon 2 trwa** (start 25.09 15:4x CEST, engine v3, seed 20260925). Najbliższe:
   turn 0 zamyka się 24:00 UTC (22:00 CEST) — play-turn dogra botów o ~21:50 (timer 21:50/09:50 CEST),
   head polecia o ~22:03. **Mój ruch t0: research (queued).** Dalej: obserwować, grać świadomie.
2. **Kompletność v3 jak e-republika** — Piotr: "niech wygląda jak e-republika". Lista
   kandydatów do v3.1 (do uzgodnienia; UWAGA: zmiany engine3 w trakcie sezonu 2 = NIE —
   season 2 musi dobiec do końca na obecnym kodzie, żeby verify działał. v3.1 = sezon 3):
   - **dyplomacja głębsza:** traktaty (wymiana surowców), pakt nieagresji vs sojusz, misja dyplomatyczna;
   - **wojna realna:** atak na kafelek, oblężenie (culture/tech zmienia wynik), okup po wojnie;
   - **ekonomia głębsza:** handel między narodami, podatki (lider stawia stawkę), handel wewnętrzny;
   - **kultura/infra:** landmarky, święta narodowe, muzea (obrona przed banditami);
   - **cykle rządowe:** typy rządu (demokracja/dyktatura/oligarchia), kandydaci na lidera;
   - **UI:** profil narodu, historia wojen, wykresy siły (SVG), feed na żywo (poll co 30s).
3. **Tunel stały:** quick tunnel = URL pływający. Stały Cloudflare tunnel wymaga logina
   Piotra (account) — dostęp przez host (nie w chacie). Wtedy named tunnel `erepublik`.
4. **Boty v3 lepsze** (sezon 3): persony (merchant kupuje tanio/sprzedaje drogo, militarist
   barracks+sabotaż, scholar research+university, diplomat aliansy, populist culture).
5. **Verify:** `/api/verify` od tury 1 sezonu 2 — pełna historia od genesis (join czlonkaka
   jest w logu i sealed). Utrata logu tury 1 sezonu 1 (79212) nie dotyczy nowego sezonu.

## 5. Jak kontynuować (jeśli kontekst przepadł)

1. **Czytaj ten plik** + `README.md` (kontrakt API + mechaniki) + ostatni wpis w
   `memory/YYYY-MM-DD.md` (co wydarzyło się od ostatniej edycji tego pliku).
2. **Sprawdź live:** `curl localhost:8451/api/turn` (która tura), `curl localhost:8451/api/verify`
   (replay ok?), `tail -30 log.jsonl`, `curl -s $(cat tunnel-url.txt)/api/turn` (tunel żywy?).
3. **Zanim coś zmienisz w engine v3:** `python3 test_engine3.py` (musi przejść), a po zmianach
   przebiegnij `test_determinism` + `test_replay` + `test_tamper`.
4. **Nie ruszaj** `engine_v1.py`/`bots_v1.py` (zamrożone) ani live `state.json`/`log.jsonl`
   sezonu 1 (gra trwa).
5. **Po zmianach na stronie www:** `python3 www_sync.py` (albo timer to zrobi) — push idempotentny.
   Weryfikacja: `curl -s https://ejajmonster.github.io/erepublik-for-agents/index.html`.
6. **Po zmianach w code repo:** commit + push z tokenem z credentials (patrz §2), gałąź `main`.
7. **Mój ruch:** jak otwiera się nowa tura (current wzrasta) i nie ma mojej akcji w
   `pending`, gram świadomie przez `/api/action` (klucz cid 0 z `keys.json`).
   Logika gry: patrz `state.json` → credits, wojny, rynek (jeśli v3), podejmij decyzję.

## 6. Decyzje i zobowiązania (dla ciągłości)

- **Zobowiązanie 72268: WYKONANE** — pre-commit sezonu 2 opublikowany 25.09 (79401):
  seed 20260925, engine v3, nowi świat/seaty. Korekta gridu tury: 79420 (12:00/24:00 UTC).
- **Zgłoszenie 79212:** utrata logu tury 1 (reboot hosta ~12:00 UTC 25.09) — dotyczy tylko
  sezonu 1; sezon 2 ma pełną historię od genesis.
- **Head 79400:** sealed head końca sezonu 1 (turn 44, winner Cordovia).
- Piotr: "skup się na rozwoju gry, nie na starych turach"; "niech gra będzie kompletna
  jak e-republika"; "zamknij sezon 1" (25.09 ~15:00) → sezon 1 domknięty deterministycznie,
  sezon 2 odpalony na v3.

## 7. Szybki health-check (odpalić przy każdej kontynuacji)

```bash
cd /home/piotr/.openclaw/workspace/erepublik
curl -s localhost:8451/api/turn          # która tura, kiedy close
curl -s localhost:8451/api/verify        # replay_ok: true?
curl -s $(cat tunnel-url.txt)/api/turn   # tunel żywy?
tail -5 log.jsonl                        # ostatnie zamknięcia tur
systemctl --user status erepublik-server erepublik-tunnel --no-pager
```
