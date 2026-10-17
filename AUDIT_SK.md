# Slovenský audit a reprodukčná správa

Dátum auditu: **2026-10-16 UTC** (revízia 2026-10-17 UTC)
Repozitár: `badmarsh/gemini2api-argon-proxy`
Pracovná vetva: `arena/b7864136-gemini2api-argon-proxy`

> Poznámka k názvu vetvy: v zadaní auditu bola uvedená vetva `arena/7d0e69e9-gemini2api-argon-proxy`.
> Takáto vetva v lokálnom repozitári neexistuje; práce prebiehali na jedinej existujúcej vetve
> `arena/b7864136-gemini2api-argon-proxy` a push prebiehal iba do nej (bez vytvárania ďalších vetiev).

## Rozsah a bezpečnostné hranice

Audit pokryl `app/` (tool-call JSON, streamovanie, Argon → Flash fallback, autentizáciu, účty,
limity, perzistenciu a bezpečnostné hranice), statickú konzolu a offline evaluačný harness. Neboli
čítané ani menené `.env`, `data/`, cookies, `accounts.json`, `api-keys.json` ani
`docker-compose.override.yml`. Do logov a reportov sa neukladajú prompty, hlavičky s povereniami
ani identifikátory účtov; diagnostika používa nanajvýš krátky jednosmerný hash účtu.

## Baseline a finálne brány

| Brána | Baseline | Finálny stav |
|---|---:|---:|
| Unit/integration pytest | 991 passed, 1 warning | **1104 passed, 1 warning** (`python 3.11` sandbox) |
| Ruff | čistý | čistý (`python -m ruff check .`) |
| Eval suite | 240 passed | **240 passed** (`pytest evals -q -p no:cacheprovider`) |
| T0 | existujúce deterministické fake-upstream baseline súbory | **52 records, bez abortu**; report `evals/reports/20261016T225213Z-30667a.json` (resume: `{"run_id": "20261016T225213Z-30667a", "tier": "t0", "records": 52, "aborted": false}`) |
| Python 3.14 brána | v sandboxe chýba `py` launcher | **zelená v CI** (`test-python-3-14`: ruff + pytest na 3.14, beh `39040306228`); lokálne overené len na Python 3.11 |

Pôvodný `py -3.14 -m pytest -q` nebolo možné v tomto sandboxe vykonať, pretože launcher `py`
neexistuje. Toto nie je vydávané za úspešný lokálny Python 3.14 test. CI jobina `test-python-3-14`
preto vznikla ako advisory (`continue-on-error: true`) a po prvom zelenom behu bola povýšená na
tvrdú bránu. Jediná odchýlka oproti produkčným pinom: `pydantic==2.11.3` ťahne
`pydantic-core 2.33.2`, ktorý nemá cp314 wheel (build zo zdroja by potreboval Rust toolchain), preto
jobina inštaluje `pydantic>=2.12`. `requirements.txt` zostáva nezmenený a testy sú zelené aj s
novším pydantic (1104 passed).

## Zistenia a dôkaz RED → GREEN

### 1. Jednotný budget regenerácie za turn

**RED:** guard slučky a parser retry mali samostatné rozhodovanie, takže jeden logický turn mohol
spotrebovať dve drahé regenerácie.
**GREEN:** `RegenerationBudget` je zdieľaný všetkými štyrmi router rodinami a buffered aj
streaming cestami. Maximálne jeden token sa spotrebuje pred guardom alebo parser retry. Povolené
na zablokované pokusy a posledný dôvod sú v `/admin/status.regenerations`. Dôkaz: `tests/unit/test_regeneration_budget.py`
na router/eval testy.

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

### 7. Prometheus-compatible metriky

**RED:** existovala len JSON diagnostika pre konzolu; vonkajšie scrapeovanie (Prometheus,
OpenTelemetry exporter) chýbalo a každý si musel písať vlastný klient.
**GREEN:** `GET /admin/metrics` (admin kľúč) renderuje štandardný text exposition formát
(`app/core/prometheus_metrics.py`): počet requestov, latency a TTFT histogramy, fallback,
Argon breaker, account pool (kapacita, saturácia, slot leak, cooling), parser chyby a
regenerácie. Hodnoty labelov sú ohraničené (max. 12 hodnôt na label, zvyšok sa zrúši do
`other`); neexistujú labely s request id, account hashom, promptom ani client modelom.
JSON endpoint zostáva zdrojom pre konzolu. Dôkaz: `tests/unit/test_prometheus_metrics.py` (11 testov).

### 8. Bezpečnostné hlavičky a CSRF plocha

**RED:** odpovede bez `Content-Security-Policy`, `nosniff`, `X-Frame-Options` a sprievodných
hlavičiek; konzola nevedela vysvetliť, prečo nie je potrebný CSRF token.
**GREEN:** `app/core/security_headers.py` pridáva CSP (obmedzené na dva CDN, ktoré konzola naozaj
používa), `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy` a
`Cross-Origin-Opener-Policy`; externé odkazy majú `rel="noopener noreferrer"`. CSRF plocha je
zdokumentovaná: konzola overuje bearer token v hlavičke `Authorization` a nepoužíva cookies, takže
neexistuje ambient credential, ktorý by mohol zneužiť cross-site request; invariant je
pinned regresným testom. Dôkaz: `tests/unit/test_security_headers.py` (9 testov).

### 9. Prevádzkový prehľad (SLO panel)

**RED:** dashboard ukazoval stav bez kontextu — nebolo vidieť, či je chyba 2 % alebo 20 %, či je
pomalý TTFT, či je preplnený pool ani či bol vyčerpaný regeneračný budget; „neznáme" sa zobrazovalo
ako „zdravé".
**GREEN:** `app/core/slo.py` (`build_slo()`) počíta stav `ok` / `degraded` / `unknown` s dôvodmi
(`breaker_open`, `error_rate`, `fallback_rate`, `ttft_p95`, `inter_chunk_gap`, `pool_saturated`,
`pool_slot_leak`, `regeneration_budget_exhausted`) a okná 1h/24h/7d. Dashboard panel zobrazuje
objem, chybovosť, TTFT p50/p95, max inter-chunk gap, breaker, fallback rate, budget a pool.
Pri nulovom počte requestov je stav `unknown`, nie `ok`. Dôkaz: `tests/unit/test_slo_overview.py`
(15 testov).

### 10. Request explorer

**RED:** jednotlivé requesty nebolo možné korelovať; incident sa musel rekonštruovať z viacerých
zdrojov a bolo riziko, že sa v diagnostike vypíše telo promptu.
**GREEN:** `GET /admin/debug-metrics/{request_id}` vracia časovú os jedného requestu
(`app/core/request_timeline.py`): ingress/egress časy, TTFT, celková doba, inter-chunk gapy,
požadovaný vs. skutočný model, fallback dôvod, parser failure class, počet regenerácií, stream
markery a finish reason. Tela requestu/odpovede nie sú súčasťou payloadu ani časovej osi;
`Authorization`, cookies, prompty ani account ID sa nikdy neexportujú. Dôkaz:
`tests/unit/test_request_explorer.py` (16 testov).

### 11. Configuration readiness

**RED:** konzola zobrazovala len pár polí, takže sa nedalo zistiť, prečo panel nastavenie
neplatí — panel hodnota vs. env vs. default, ktoré nastavenia potrebujú reštart, či override
odpadol od schémy.
**GREEN:** `GET /admin/config` (`app/core/config_diagnostics.py`) vracia efektívne hodnoty so
zdrojom (`env` / `override` / `default`), zoznam reštartom vyžadovaných polí, validačné problémy
a drift (`unknown_override`, `invalid_override`, `override_not_applied`, `env_overridden`).
Secrets sú vykázané iba ako „konfigurované" (pri `argon_system_prompt` navyše dĺžka), nikdy
ako hodnota. Dôkaz: `tests/unit/test_config_readiness.py` (14 testov vrátane node render testu).

### 12. Prístupnosť dashboardu

**RED:** ovládanie bolo mysliteľné len myšou, histogram nemal textový náhradu, stav sa menil
potichu, animácie nerešpektovali `prefers-reduced-motion` a filtrovanie sa po reloadi stratilo.
**GREEN:** fokus sa presúva na nadpis sekcie pri zmene sekcie, drill-down v exploreri dostane
fokus, histogramové stĺpky majú `aria-label` (samotný stĺpec je `aria-hidden`), stavový chip je
`role="status" aria-live="polite"`, existuje tlačidlo pause/resume auto-refreshu s `aria-pressed`
(stav prežije reload), filter explorera je perzistovaný a CSS obsahuje `prefers-reduced-motion`
blok. Všetky nové texty sú v piatich jazykoch. Dôkaz: `tests/unit/test_dashboard_accessibility.py`
(9 node testov).

### 13. Prevádzka účtového poolu

**RED:** dashboard ukazoval stav účtu, ale nie to, koľko slotov je obsadených, koľko ostáva
cooldownu ani či je session zdravá; operátor nemal bezpečnú akciu nad konkrétnym účtom.
**GREEN:** `AccountPool.set_account_enabled()` prepína účet za behu (rotácia ho preskočí, cookies
a počítadlá zostanú), `POST /admin/accounts/{id}/status` je chránený admin kľúčom a na neznámy
id vráti 404 JSON. Karta účtu zobrazuje obsadené sloty vs. kapacitu, zostávajúci cooldown a
zdravie session; akcie „Check" a „Enable/Disable" aj reset Argon breakeru sú chránené
potvrdením. Stav je iba za behu (nezapisuje sa do `accounts.json`) a account id sa nikdy
nezobrazuje v texte, iba v `data-` atribúte. Dôkaz:
`tests/unit/test_account_pool_operations.py` (13 testov).

## Otvorené body

1. Python 3.14 brána je zelená v CI (`test-python-3-14`, beh `39040306228`), ale lokálne ju
   stále nebolo možné spustiť (v sandboxe chýba `py` launcher). Zostáva otvorené: priamo na
   hostiteľovi s `py -3.14` overiť aj produkčné piny (`pydantic==2.11.3` nemá cp314 wheel) a
   rozhodnúť, či sa má dependency pin zdvihnúť aj pre produkciu.
2. T1/T2 live spot check je voliteľný a závisí od explicitného bezpečného endpointu; v tomto
   sandboxe sa nesmie hádať Google URL ani čítať lokálne credentials.
3. Diagnostika je zámerne bounded/in-memory; nie je to dlhodobý tracing backend.
4. `LOG_BODIES_ENABLED` zostáva opt-in a limitovaný; nové metriky neobchádzajú privacy hranice.
5. **Nerealizované kandidáty z auditu** (zostávajú otvorené, rozsah sa nemenil): pridané boli
   Prometheus exporter, operačný prehľad, request explorer, konfiguračná pripravenosť,
   prístupnosť dashboardu, prevádzka účtového poolu, bezpečnostné hlavičky, CI gate pre `evals`
   a Python 3.14 job. Otvorené zostávajú: per-key/per-tenant kvóty a rozpočty, konfigurovateľné
   routing profily, redacted replay/fixture knižnica, alerty a administratívny audit trail s
   webhookom, request-size/upload limity, SSRF/file-serving hranice, perzistencia secret
   redaction, parser/stream fuzz séria a reliability/perf hardening.
6. Exporter je Prometheus text; OTel SDK/exporter (s push odosielaním) nebol pridaný, aby
   nevznikol nový závislý package a aby zostala zachovaná bezpečná hranica konzoly.

## Zámerné non-fixy

- Nebol pridaný regex rescue parser ani domýšľanie truncovaných argumentov.
- Nebol zmenený verejný Argon protokol, 81-element thinking payload, pro-only routing, kapacita 2
  ani akceptované timeout/fallback pravidlá.
- Fallback implementácia nebola duplikovaná mimo `app/core/argon_fallback.py`.
- Model sa nemení po tom, ako už stream poslal obsah; serving model ostáva označený.
- Neboli menené runtime dáta, cookies, `.env` ani testy oslabovaním/deletovaním. Existujúci test
  `tests/unit/test_account_card_rendering.py` bol rozšírený iba o dve nové pomocné funkcie, ktoré
  karta účtu volá; jeho tvrdenia zostali nezmenené.
- Nezmenili sa globálne limity, kapacita poolu ani správanie request timeoutov.
- Bezpečnostné hlavičky sa nepridávali tak, aby vypnuli existujúce funkcie konzoly (CSP ponecháva
  `'unsafe-inline'` pre inline bootstrap skripty konzoly).

## Reprodukčné príkazy

```text
API_KEY=sk-test-key .venv/bin/python -m ruff check .
API_KEY=sk-test-key .venv/bin/pytest -q
API_KEY=sk-test-key .venv/bin/pytest evals -q -p no:cacheprovider
API_KEY=sk-test-key .venv/bin/python -m evals.tune --tier t0 --resume
```

Pri odovzdaní sa tabuľka Baseline/Finálny stav aktualizuje presným výstupom posledných troch
brán; secret-bearing súbory a runtime state sa do Git diffu nepridávajú.
