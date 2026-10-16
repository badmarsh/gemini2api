# Slovenský audit a reprodukčná správa

Dátum auditu: **2026-10-16 UTC**
Repozitár: `badmarsh/gemini2api-argon-proxy`
Pracovná vetva: `arena/7d0e69e9-gemini2api-argon-proxy`

## Rozsah a bezpečnostné hranice

Audit pokryl `app/` (tool-call JSON, streamovanie, Argon → Flash fallback, autentizáciu, účty,
limity, perzistenciu a bezpečnostné hranice), statickú konzolu a offline evaluačný harness. Neboli
čítané ani menené `.env`, `data/`, cookies, `accounts.json`, `api-keys.json` ani
`docker-compose.override.yml`. Do logov a reportov sa neukladajú prompty, hlavičky s povereniami
ani identifikátory účtov; diagnostika používa nanajvýš krátky jednosmerný hash účtu.

## Baseline a finálne brány

| Brána | Baseline | Finálny stav |
|---|---:|---:|
| Unit/integration pytest | 991 passed, 1 warning | 1007 passed, 1 warning (`python 3.11` sandbox) |
| Ruff | čistý | čistý (`python -m ruff check .`) |
| Eval suite | 240 passed | 240 passed (`pytest evals -q -p no:cacheprovider`) |
| T0 | existujúce deterministické fake-upstream baseline súbory | 52 records, bez abortu; digest `d70108e0…`, report `evals/reports/20261016T225213Z-30667a.json` |
| Python 3.14 príkaz | v sandboxe chýba `py` launcher | použitá kontrolná Python 3.11; 3.14 brána zostáva explicitne otvorená pre CI/hostiteľský stroj |

Pôvodný `py -3.14 -m pytest -q` nebolo možné v tomto sandboxe vykonať, pretože launcher `py`
neexistuje. Toto nie je vydávané za úspešný Python 3.14 test.

## Zistenia a dôkaz RED → GREEN

### 1. Jednotný budget regenerácie za turn

**RED:** guard slučky a parser retry mali samostatné rozhodovanie, takže jeden logický turn mohol
spotrebovať dve drahé regenerácie.
**GREEN:** `RegenerationBudget` je zdieľaný všetkými štyrmi router rodinami a buffered aj
streaming cestami. Maximálne jeden token sa spotrebuje pred guardom alebo parser retry. Povolené
a zablokované pokusy a posledný dôvod sú v `/admin/status.regenerations`. Dôkaz: `tests/unit/test_regeneration_budget.py`
a router/eval testy.

### 2. Oprava tool-call JSON bez hádania

**RED:** úplná JSON hodnota s neescapovanou úvodzovkou v argumentovom stringu padala na syntax
chybe.
**GREEN:** stavový, ohraničený repair sa spustí iba pri kompletnom objekte a iba v argumentovom
stringu. Truncation nedopĺňa chýbajúce zátvorky ani úvodzovky; regex rescue nebol pridaný.
Existujúci kontrakt `tests/unit/test_tool_retry.py::test_truncated_json_is_never_repaired` zostal
zachovaný a dopĺňa ho `tests/unit/test_tool_json_quote_repair.py`.

Overené experimenty: escapovaný newline je platný; literálny control character je akceptovaný
wire/parser cestou s `strict=False`; neescapovaná úvodzovka sa opraví iba ak je objekt kompletný;
truncated JSON sa neopravuje. Fragment s dĺžkou 120 je iba `text[:120]` vo warningu, nie dôkaz
truncation.

### 3. Gemini wire hardening

**RED:** identifikované klientské `json.loads` v `app/core/gemini_client.py` používali strict
správanie pri upstream frame hodnotách s literálnymi control characters.
**GREEN:** všetky relevantné miesta majú `strict=False` v samostatnom commite
`05e7604 fix(gemini): accept literal control characters in wire frames`. Ide o toleranciu wire
frame, nie o opravu malformed tool argumentov.

### 4. Diagnostika a fallback

**RED:** existovali iba agregované live metriky; nebolo možné korelovať TTFT, inter-chunk gap,
serving model, fallback dôvod, error class a regeneráciu s jedným requestom.
**GREEN:** bounded event store a rozšírený `LogRecord` poskytujú tieto polia. `/admin/status`,
`/admin/debug-metrics`, `/admin/logs` a usage summary ich môžu zobraziť bez promptov/cookies.
Fallback zostáva viditeľný v existujúcich response hlavičkách a stream marker frames; proxy nikdy
potichu neprepne model po odoslaní obsahu.

### 5. Konzola

Dashboard má panel Request Diagnostics s TTFT histogramom, fallback/error breakdownom a počtom
regenerácií. Logs tabuľka zobrazuje TTFT a serving model. Playground má Tool JSON test mode:
fixture sa odošle na chránený debug endpoint, použije sa rovnaký bezpečný parser, zobrazí sa
failure class, dĺžka, truncation suspicion a prípadný bezpečný repair; test nespúšťa regeneráciu
ani upstream request. Lokalizačné bloky `zh-CN`, `en-US`, `ja-JP`, `ko-KR`, `zh-TW` majú zhodné
sady kľúčov.

Zachované sú existujúce streaming SSE comment-frame správanie, image-generation waiting state,
artifact cards, upload obrázkov, headless rendering a kontrastné CSS tokeny. Nové texty idú cez
I18N, hodnoty z backendu sa escapujú pred HTML vložením.

### 6. Lokálny self-tuning harness

`evals/tune.py` poskytuje:

- deterministický T0 fake-upstream matrix bez Google siete;
- opt-in T1/T2 HTTP spot checks cez explicitný `--base-url`;
- concurrency 2, Argon request cap a abort po troch po sebe idúcich upstream error/429/captcha;
- atomický `evals/state.json`, `--resume`, report JSON/Markdown a single-process lock;
- redakciu secret-like hodnôt, runtime `data/`, `.env`, cookies, promptov a raw response textu;
- exact-match-only worker edits: najviac jeden návrh, `old_text` musí existovať práve raz,
  `--apply` je explicitné a line endings sa pri patchi zachovávajú;
- kontrakt pre worker model `gemini-3.8-flash` bez implicitného volania hostovaného modelu.

T0 report je uložený v `evals/reports/20261016T225213Z-30667a.json` a stav v `evals/state.json`;
obsahuje 52 deterministických scenárov, 20 pozorovaných fallbackov, p95 1813 ms a žiadny abort.
Live tier sa nepovažuje za vykonaný bez explicitne dodaného test endpointu a poverenia mimo repozitára.

## Otvorené body

1. Python 3.14 gate treba zopakovať na hostiteľovi, ktorý má `py -3.14`.
2. T1/T2 live spot check je voliteľný a závisí od explicitného bezpečného endpointu; v tomto
   sandboxe sa nesmie hádať Google URL ani čítať lokálne credentials.
3. Diagnostika je zámerne bounded/in-memory; nie je to dlhodobý tracing backend.
4. `LOG_BODIES_ENABLED` zostáva opt-in a limitovaný; nové metriky neobchádzajú privacy hranice.

## Zámerné non-fixy

- Nebol pridaný regex rescue parser ani domýšľanie truncovaných argumentov.
- Nebol zmenený verejný Argon protokol, 81-element thinking payload, pro-only routing, kapacita 2
  ani akceptované timeout/fallback pravidlá.
- Fallback implementácia nebola duplikovaná mimo `app/core/argon_fallback.py`.
- Model sa nemení po tom, ako už stream poslal obsah; serving model ostáva označený.
- Neboli menené runtime dáta, cookies, `.env` ani testy oslabovaním/deletovaním.

## Reprodukčné príkazy

```text
API_KEY=sk-test-key .venv/bin/python -m ruff check .
API_KEY=sk-test-key .venv/bin/pytest -q
API_KEY=sk-test-key .venv/bin/pytest evals -q -p no:cacheprovider
API_KEY=sk-test-key .venv/bin/python -m evals.tune --tier t0 --state evals/state.json
```

Pri odovzdaní sa tabuľka Baseline/Finálny stav aktualizuje presným výstupom posledných troch
brán; secret-bearing súbory a runtime state sa do Git diffu nepridávajú.
