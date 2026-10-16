# gemini2api-argon-proxy — audit + opravy ("smooth Argon" bez zaspávania agenta)

**Repo:** `badmarsh/gemini2api-argon-proxy` (fork upstreamu `xwteam/gemini2api`)
**Base commit:** `0499f80` (fork, v1.7.1)
**Patch:** `patches/gemini2api-argon-agent-loop.patch` (29 files, +1718 / −405)
**Testy:** `988 passed` (unit) + `97 passed, 1 skipped` (offline evals, `evals/`) + `ruff check .` clean
**Overené:** `python -m pytest -q`, `python -m pytest -q evals`, patch aplikovaný na čistý klon `0499f80` → 979 passed

---

## 0. TL;DR

Tvoj problém nebol v tom, že by Argon "smooth" nebol — bol v tom, že **tri veci sa správali potichu a zle**:

1. **`ARGON_SYSTEM_PROMPT` sa Codexu nikdy neposlal.** Podmienka v `app/routers/openai.py` (commit `2d06316`) ho vkladala len keď klient neposlal žiadny system/developer prompt — Codex posiela vždy developer prompt ⇒ pravidlá pre Argon boli pre jediného klienta, pre ktorého boli napísané, **vypnuté**.
2. **Agent zaspí v tool-loope.** Gemini Web je chat model: po 1–2 volaniach nástrojov odpovie *"Pracujem na tom, dám ti vedieť"* namiesto `tool_call`. Harness vidí text, uzavrie kolo (`phase: final_answer` / `finish_reason: stop`) a ide idle. Proxy na to nemala **nič** — ani pripomienku na konci promptu, ani detekciu, ani korekciu.
3. **Fallback na Flash bol tichý a rozbitý na štyri spôsoby.** Každý router (openai ×3, claude, gemini, responses) mal vlastnú kópiu pravidiel Argon→Flash; keď zlyhali obe vetvy, endpoint vrátil **prázdnu odpoveď 200**. A klient sa nikdy nedozvedel, že odpovedá Flash a nie Argon.

Po oprave: Argon pravidlá sa **mergujú** pred developer prompt (konfigurovateľné), na konci každého tool promptu je povinná pripomienka „teraz spusti nástroj, žiadne prísľuby", zaspávanie sa **detekuje a opraví** jedným korekčným pokusom, a Argon→Flash je **vždy viditeľný** (hlavičky + frame v streame + `/admin/status`).

---

## 1. Nájdené chyby (s dôkazmi)

| # | Chyba | Kde | Dopad |
|---|---|---|---|
| 1 | `ARGON_SYSTEM_PROMPT` blokovaný vlastným system/developer promptom klienta | `app/routers/openai.py:294` (`not any(m.get("role") in ("system","developer"))`), `app/routers/claude.py:107` | Argon pravidlá (verifikácia, tool-use, žiadne halucinácie) **nikdy** neaplikované na Codex/Cline/OpenCode |
| 2 | Žiadna pripomienka na **konci** promptu (len na začiatku) | `app/utils/tools.py::build_tool_prompt` | model po 1–2 tool calloch "zabudne", že je v slučke → konverzačný prísľub → harness idle (tvoja presná skúsenosť) |
| 3 | Žiadna detekcia/korekcia zaspanej odpovede | — | každé zaspávanie = manuálne "pokračuj" od používateľa |
| 4 | 4 nezávislé kópie Argon→Flash pravidiel | `openai.py` (non-stream + buffered + true-stream), `claude.py`, `gemini.py`, `responses.py` | rozdielne správanie na tom istom zlyhaní; každá oprava sa musí spraviť 4× |
| 5 | Zlyhanie **aj** Flash fallbacku ⇒ prázdna kompletizácia `200 OK` | `openai.py:425`, `responses.py:196` (`logger.error` + pokračovanie) | klient dostane "úspech" bez obsahu — presne to, čo harness nikdy nemá vidieť |
| 6 | Fallback úplne **tichý** (žiadna hlavička/frame) | celý fallback reťazec | nedá sa zistiť, či odpovedal Argon alebo Flash 3.8; ladenie bolo hádanie |
| 7 | Argon→Flash bez markerа v streame | `_stream_response*` | streaming klient (Codex) si myslí, že číta Argon |
| 8 | Stall breaker neviditeľný, bez manuálneho resetu | `app/core/argon_breaker.py` | po incidente sa čaká `ARGON_STALL_BREAKER_SECONDS`, nedá sa zavrieť ručne; v `/admin/status` o ňom nič |
| 9 | Prvý chunk streamu čakal **240 s** rovnako ako medzera uprostred | `gemini_client.py:1475` | "Argon zaspal" = 4 minúty ticha bez jediného bajtu pre klienta |
| 10 | `inner[2]` v 81-prvkovom payloade je `DEFAULT_METADATA` ⇒ `conversation_id` sa **nikdy neposiela** | `gemini_client.py::_encode_payload_thinking` (call sites posielajú `gemini_conv_id`) | na "thinking" ceste sa ticho stratí serverové pokračovanie konverzácie (multi-turn sa opiera len o klientsku históriu) |
| 11 | `_build_model_header_thinking(model, session_uuid)` je mŕtvy kód, deleguje na `_build_model_header` | `gemini_client.py:321` | zmätočný povrch protokolu, nikto nevie čo je "to pravé" hlavičkové pole |
| 12 | Exitné testy **kódovali chybu #1** ako kontrakt | `tests/unit/test_argon_system_prompt.py` (`assert _SYS not in prompt`) | regresia by nebola odhalená; testy bránili správnej oprave |
| 13 | `ARGON_TIMEOUT` neexistoval (len konštanta v kóde) | `gemini_client.py` | nedá sa zladiť timeout s prostredím (proxy/gateway limity) bez rebuildu |
| 14 | Breaker držal stav len v `_open_until`, bez dôvodu/počítadiel | `argon_breaker.py` | "prečo je to teraz na Flashi?" sa nedalo zistiť |
| 15 | **Companion UUID sa nezhoduje s payloadom** — `_encode_payload_thinking` vygeneruje UUID (uloží do `inner[59]`) a vráti ho, ale obe volania ho zahodia (`encoded, _ = …`) a do hlavičky `x-goog-ext-525005358-jspb` vygenerujú **iné** UUID | `gemini_client.py` (2 miesta) | protokol definuje jednu per-request UUID; upstream ich spároval, fork nie — riziko tichých odmietnutí/black-hole na strane Googlu |
| 16 | **Viacťahový kontext sa ticho strácal** — 81-prvkový payload nemá slot pre pokračovanie konverzácie (`inner[2]` = metadata, spec §4.3), ale router pri uloženom `gemini_conv_id` posielal **len poslednú správu** | `openai.py` + `_encode_payload_thinking` | klient dostal "conversation_id akceptované" a model odpovedal **bez histórie** (multi-turn tvrdenie v README bolo pre týchto klientov nepravdivé) |
| 17 | Mŕtvy `_build_model_header_thinking` + nezdokumentovaný "legacy" `_encode_payload` | `gemini_client.py` | zbytočný povrch, nikto nevie čo je kanonické |

> Poznámka k tvojej otázke o "smooth": v kóde **žiadny `smooth` identifikátor neexistuje** (0 výskytov). "Smooth" je UX metafora pre tie tiché prechody — presne chyby #5, #6, #7 a #9.

## 2. Nový kontrakt: "smooth" = viditeľné, nie tiché

| Vrstva | Predtým | Teraz |
|---|---|---|
| System prompt | skip, ak klient poslal svoj | **`ARGON_SYSTEM_PROMPT_MODE`**: `merge` (default, Argon pravidlá **pred** developer prompt), `only_when_none` (legacy), `replace` |
| Tool loop | len úvodné inštrukcie | **`TOOL_LOOP_REMINDER`** na konci promptu (vypnuté pri `tool_choice="none"`, `TOOL_LOOP_REMINDER=false`) |
| Zaspávanie | nič | **`agent_loop.guard_tool_loop`**: detekcia prísľubu (EN/SK, bez code-fence/tool payloadu, max 1200 znakov) → **1** korekčný pokus s `[MANDATORY CORRECTION]`; pri zlyhaní sa **zachová prvý text** (`TOOL_LOOP_STALL_GUARD=false`) |
| Argon→Flash | 4 kópie, tiché | **jedna implementácia** `app/core/argon_fallback.py`; marker v `result["argon_fallback"]`, hlavičky `X-Gemini2API-Argon-Served-By` / `-Fallback-Reason`, v streame `: gemini2api argon_fallback served_by=… reason=…` |
| Prázdna odpoveď | `200` s prázdnym textom | **jeden retry na Argon** (non-stream) → potom Flash; ak zlyhá aj Flash, **propaguje sa pôvodná chyba** (nikdy tichý prázdny výsledok). Po odoslaní obsahu sa už neprepína (žiadne miešanie dvoch odpovedí) |
| Stall breaker | neviditeľný | `snapshot()` (`reason`, `note`, `seconds_left`, `trips`, `last_trip_at`) v `/admin/status` + **`POST /admin/argon/reset`** |
| Timeouty | 240 s prvý chunk, 360 s POST (konštanta) | **150 s** prvý chunk (nič sa nestráca — pred prvým bajtom nie je čo poslať), 240 s medzera **uprostred** zostáva (rt=c; predčasné ukončenie = tiché odrezanie odpovede / rozbité tool JSON), `ARGON_TIMEOUT` konfigurovateľné |
| Panel | — | nové pole „Argon & agent loop" (mode select, timeout, oba toggly) v 5 jazykoch + hinty |

**Dizajn "fail-safe" (zámer):** guard, reminder aj fallback môžu len **pridať** pokus — nikdy nezmenia dobrú odpoveď na chybu. Každý zásah je v logu (WARNING) a v počítadlách: `agent_loop.stats()` (`detected`/`corrected`), `count_argon_salvaged_results()`.

## 3. Prečo to rieši presne tvoj scenár (Codex → OpenCodeX → proxy → Argon)

1. Codex pošle `developer` prompt ⇒ pravidlá sa teraz **mergeujú pred neho** (predtým vypnuté).
2. Codex pošle `tools` ⇒ prompt končí pripomienkou „**oDPOVEDZ TOOL_CALLOM, ŽIADNY PRÍSĽUB**".
3. Ak Argon aj tak odpovie prísľubom ⇒ proxy ho **detekuje a spýta sa znova** s korekciou; ak ani to nepomôže, odpoveď sa pošle tak ako je (nič sa nerozbije).
4. Ak Argon **zaspí na 360 s** ⇒ (a) timeout sa už neplytvá na druhý účet, (b) po prvom zaspávaní sa na `ARGON_STALL_BREAKER_SECONDS` obsluhuje Flash, (c) klient to vidí v hlavičke/frame a operátor v paneli, (d) `POST /admin/argon/reset` to hneď vráti na Argon.

## 4. Ako nasadiť (u teba, Docker)

```bash
# v klone gemini2api-argon-proxy
git apply /cesta/k/patches/gemini2api-argon-agent-loop.patch   # alebo: git am -3
docker compose up -d --build
docker logs --tail 30 gemini2api

# nové .env voliče (defaulty sú nastavené tak, aby to hneď fungovalo)
ARGON_SYSTEM_PROMPT_MODE=merge     # merge | only_when_none | replace
TOOL_LOOP_REMINDER=true            # pripomienka na konci promptu
TOOL_LOOP_STALL_GUARD=true         # korekcia zaspanej odpovede
ARGON_TIMEOUT=360                  # 0 = interný default
```

**Odporúčaná verifikácia po nasadení** (2 minúty):
```bash
# 1) marker fallbacku prežije do klienta (žiadna hlavička = odpovedal Argon)
curl -si -H "Authorization: Bearer $API_KEY" -H 'Content-Type: application/json' \
  -d '{"model":"gemini-4-argon","messages":[{"role":"developer","content":"be terse"},
       "role":"user","content":"Say pong"}]}' -o /dev/null -D - \
  http://127.0.0.1:5918/v1/chat/completions | grep -i argon

# 2) panel: stav breakeru + agent-loop počítadlá
curl -s -H "X-Admin-Key: $ADMIN_API_KEY" http://127.0.0.1:5918/admin/status | python -m json.tool | sed -n '/argon/,/}/p'

# 3) reset po incidente
curl -s -X POST -H "X-Admin-Key: $ADMIN_API_KEY" http://127.0.0.1:5918/admin/argon/reset
```

## 5. Orchestrátor (tvoja otázka „chcem aby bol orchestrator")

Proxy vie **vynútiť slučku** (spustiť nástroj namiesto prísľubu), ale **nedokáže vymyslieť workerov** — Gemini Web nemá spúšťanie na pozadí. Model, ktorý napíše *"Spustil som realizáciu, riadim nasadenie"*, nemá za sebou žiadny proces; je to divadlo. Reálne možnosti sú dve:

1. **Skutočný multi-agent orchestrátor** — orchestrátor deleguje cez **reálne nástroje** (`create_thread` pre každého workera, potom `wait_threads`, audit cez browser/vision). Každé delegovanie je `tool_call`, takže proxy slučka ho nezastaví.
2. **Fázový orchestrátor** — jeden agent, jedna fáza = jedno kolo nástrojov (implement → test → audit → ďalšia fáza). Robustnejšie, bez fiktívnych workerov.

Praktické pravidlo (funguje aj s opraveným proxy): **zakázať fiktívne statusy**. Buď spustíš `create_thread`, alebo píšeš kód. Text bez `tool_call` = koniec kola. Presne to vynucuje `TOOL_LOOP_REMINDER` + guard.

## 6. Druhé kolo: multi-turn kontext a párovanie UUID

Dva nálezy, ktoré menia **wire-adjacent** správanie — preto s dôkazom a s jasnou cestou späť.

### 6.1 Párovanie UUID (#15) — opravené, `inner[59]` == hlavička

`_encode_payload_thinking` mintuje jednu per-request UUID, ukladá ju do `inner[59]` a **vracia** ju. Obe volania ju zahadzovali a pre hlavičku `x-goog-ext-525005358-jspb` si vygenerovali druhú. Upstream (referenčná implementácia tohto protokolu) používal **tú istú** UUID na oboch miestach; forkova verzia je dôsledok refaktoru, nie zdokumentované rozhodnutie. Vrátené späť.

*Dôkaz, že test je skutočný:* dočasným vrátením pôvodnej línie testy spadnú —
`test_send_request_echoes_the_payload_uuid_in_the_companion_header`, `test_generate_stream_echoes_...` → `2 failed`; po obnovení `5 passed`.
*Ak by tvoje probe merania tvrdili opak:* sú to dve línie (`headers["x-goog-ext-525005358-jspb"] = f'["{request_uuid}",1]'` v `_send_request` a v `generate_stream`), vrátenie = jedna zmena na každej.

### 6.2 Kontext konverzácie (#16) — default je teraz plná história

Overené v kóde: `_encode_payload` (starý 4-prvkový payload, ktorý `conversation_id` **vie** prenášať v `inner[2]`) sa v runtime **už nikde nepoužíva** — všetko ide cez 81-prvkový thinking payload, ktorý pokračovanie nepodporuje. Router napriek tomu pri uloženom `gemini_conv_id` posielal iba poslednú správu → model bez kontextu, bez chyby, bez logu.

Riešenie: `SERVER_SIDE_CONTINUATION` (default `false`) — plná história sa posiela vždy, `conversation_id` sa zahodí (nič hore ho nevie použiť a jeho prenášanie držalo nažive aj zbytočnú "expired → resend" vetvu, ktorá by len zopakovala ten istý request). Zahodenie sa **raz** zaloguje (bez hodnoty). Ak protokol raz pokračovanie znovu umožní: `SERVER_SIDE_CONTINUATION=true` obnoví pôvodnú optimalizáciu (posledná správa + ID).

**Overené:** `test_openai_keeps_the_full_history_when_the_server_cannot_continue`, `test_continuation_flag_restores_last_message_only`, `test_thinking_payload_has_no_conversation_slot`, `test_dropped_conversation_is_reported_once`; hardening testy (`non-str block.text`, image-only latest) prepnuté na opt-in vetvu, aby ostali platné.

### 6.3 Čistý povrch (#17)

`_build_model_header_thinking` (17-prvkový variant, mŕtvy — len delegoval na kanonickú 15-prvkovú verziu) odstránený; testy volajú `_build_model_header`. `_encode_payload` ostáva, ale s docstringom "referencia + byte-identity guard, nie runtime" a s testom, ktorý stráži, že sa nevráti na hot path.

---

## 7. Zmenené súbory

```text
app/core/agent_loop.py        (nový)  detekcia + korekcia zaspávania, počítadlá
app/core/argon_fallback.py    (prepísaný) jedna implementácia Argon->Flash + serve_marker + markery
app/core/argon_breaker.py     (prepísaný) dôvod/počítadlá/snapshot + enabled()
app/core/gemini_client.py     150s prvý chunk, ARGON_TIMEOUT, párovanie UUID, continuation guard,
                              count_argon_salvaged_results(), mŕtvy 17-prvkový header preč
app/utils/tools.py            TOOL_LOOP_REMINDER na konci promptu
app/utils/prompt.py           argon_system_text() + apply_argon_system() (merge/only_when_none/replace)
app/routers/openai.py, claude.py, gemini.py, responses.py   jedna cesta generovania, markery, guard
app/routers/admin.py          /admin/status rozšírený, POST /admin/argon/reset
app/routers/settings.py, app/core/settings_overrides.py, static/app/settings.js, static/app/i18n.js
CHANGELOG.md, .env.example    nové voliče a záznam zmien
tests/unit/  (nové, 33 testov): test_agent_loop_guard.py, test_argon_fallback_serving.py,
             test_tool_loop_reminder.py, test_admin_argon_status.py, test_argon_request_uuid_pairing.py
```

## 8. Overenie (spusti si to sám)

```text
$ python -m pytest -q
988 passed, 1 warning in 12.68s

$ python -m pytest -q evals
97 passed, 1 skipped in 57.86s

$ ruff check .
All checks passed!

$ git clone <repo> /tmp/check && cd /tmp/check && git checkout 0499f80
$ git apply patches/gemini2api-argon-agent-loop.patch && python -m pytest -q tests/unit
979 passed
```

Evaly (`evals/`) odhalili aj jednu moju vlastnú regresiu: skrátený chunk-timeout **uprostred** streamu odrezal odpoveď v scenári `gap200` (200 s medzera, rt=c). Preto je 240 s medzera po prvom obsahu **zámerne** nezmenená a je na to komentár + eval-guard.

Pri párovaní UUID (#15) je dôkaz opačný — dočasným vrátením pôvodnej línie kódu testy **spadnú** (`2 failed`), po obnove `5 passed`.
