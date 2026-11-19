# M1.10 — Correzione delle criticità tecniche prima della validazione M1.11

Data: 2026-11-19. Base: `main` @ 24d5482. Branch: `claude/project-thread-n6io3e`.

Obiettivo: consolidare quanto già costruito prima di M1.11, cioè la validazione su almeno 20
video reali. In questa fase non sono state aggiunte funzionalità di analisi tennistica né
nuove librerie. Non è stato avviato nulla di M2: niente stroke detection o classificazione,
niente tracking di racchetta o pallina.

**Esito:** le 7 correzioni approvate sono implementate, una per commit. Dopo ognuna è stato
eseguito `scripts/check.sh` e tutti i controlli erano verdi. Il punto 4 (cambio di soggetto)
è solo documentato, come deciso. I test sono sintetici e **non sostituiscono la validazione
su video reali** (M1.11).

## 1. Criticità iniziali e stato verificato

La verifica è stata fatta sul codice di `main` 24d5482, prima di qualsiasi modifica. I
punti 1, 2 e 3 sono stati riprodotti con l'API reale.

| # | Criticità | Stato verificato prima di M1.10 | Evidenza |
| --- | --- | --- | --- |
| 1 | Timezone | Presente | L'API restituiva `created_at = 2026-11-19T11:02:45.559541`, senza fuso. Il browser lo leggeva come ora locale: −1 h in Italia, −2 h con l'ora legale. |
| 2 | Percorsi locali e informazioni sensibili | Presente, più esteso del previsto | `probe_failed` mostrava il percorso assoluto in `details.path` e nello stderr di ffprobe. Mostravano percorsi assoluti anche `results.*.source_metadata.path` e `preprocess.output_path`, `system/info.storage_dir`, il comando ffmpeg in `preprocess_failed`, `decode_failed` e `model_not_available`. `internal_error` restituiva il traceback. |
| 3 | `libegl1`/`libgles2` | Parzialmente risolto: era solo documentato nel README | Senza le librerie, `import mediapipe` funziona. Il primo video falliva come `internal_error` con `OSError: libEGL.so.1` e traceback. 2 test fallivano nello stesso modo. |
| 4 | Selezione del giocatore | Presente, per scelta di progetto (`num_poses=1`) | MediaPipe segue la persona più evidente e nessun avviso segnala un cambio di soggetto. |
| 5 | Mano dominante | Presente | Il valore era salvato solo in `localStorage` (`profile.ts`) e non veniva inviato all'API. |
| 6 | Mappatura temporale originale/proxy | Presente | Nessuna corrispondenza salvata tra fotogrammi del proxy e dell'originale. Con audio iniziato prima del video, il proxy aggiungeva fotogrammi duplicati all'inizio, contro la regola documentata. |
| 7 | Dimensione di `pose.json` | Presente | Circa 9,4 MB per minuto a 30 fps su dati sintetici, circa 75 MB a 240 fps. Inviato senza compressione e caricato per intero dal browser. |
| 8 | Errori poco visibili in System Status | Presente | MediaPipe risultava verde anche senza runtime funzionante. Gli errori di elaborazione recenti non erano elencati. |

## 2. Interventi effettuati

| # | Commit | Intervento | File principali |
| --- | --- | --- | --- |
| 1 | `d672a51` | Tipo colonna `UTCDateTime`: normalizza in UTC in scrittura e marca UTC in lettura. Corregge anche le righe già salvate, senza migrazione. Il web legge come UTC le stringhe senza fuso. | `apps/api/tennis_api/models.py`, `apps/web/src/lib/format.ts` |
| 2 | `3bde307` | Gli errori del motore citano solo il nome del file e lo stderr viene ripulito con `scrub_paths`. Il comando ffmpeg va solo nel log. Il report usa riferimenti relativi. Il worker registra il traceback nel log con un `error_id` e salva solo `{error_id, exception}`. `redaction.py` agisce come rete di sicurezza su errori, stato e risultati delle analisi e `report.json` servito, compresi i dati pre-M1.10. `system/info` espone `storage_writable` al posto di `storage_dir`. | `engine/tennis_cv/errors.py`, `video/*.py`, `pose/*.py`, `pipeline.py`, `worker/tennis_worker/main.py`, `apps/api/tennis_api/redaction.py`, `errors.py`, `routers/analyses.py`, `routers/system.py` |
| 3 | `caed8e5` | Nuovo errore strutturato `pose_runtime_unavailable` con l'indicazione di installare `libegl1 libgles2`. `check_pose_runtime()` crea e chiude davvero un landmarker; il successo resta in cache, il fallimento viene ricontrollato. `system/info.pose_runtime` porta un campo `detail`. | `engine/tennis_cv/pose/mediapipe_estimator.py`, `errors.py`, `routers/system.py`, `schemas.py`, `SystemStatus.tsx`, contratti |
| 8 | `2757474` | System Status elenca gli ultimi 5 errori di elaborazione: codice, messaggio, data e link all'analisi. Usa la lista analisi esistente, senza nuovi endpoint. | `apps/web/src/pages/SystemStatus.tsx` |
| 6 | `aa3328b` | Il proxy usa sempre `setpts=PTS-STARTPTS,fps=<rate>:eof_action=pass`. `timemap.py` ricostruisce la regola del filtro `fps` e salva `timemap.json`, più il riepilogo `time_mapping` nel report. Nuovo artefatto `timemap` e schema `time_map`. | `engine/tennis_cv/video/preprocess.py`, `video/timemap.py`, `pipeline.py`, `apps/api/tennis_api/storage.py`, contratti |
| 7 | `e496050` | Keypoint salvati con x/y a 5 decimali, z a 4, visibility/presence a 3; i timestamp restano esatti. `GZipMiddleware` (incluso in FastAPI) comprime le risposte JSON; video e risposte 206 sono esclusi. | `mediapipe_estimator.py`, `apps/api/tennis_api/main.py` |
| 5 | `264a024` | Campi opzionali `dominant_hand` (right/left) e `backhand` (one_handed/two_handed), validati (422) e salvati con la migrazione `2026_11_19_analysis_player_handedness`. Sono `null` se non forniti e mai dedotti. Il form Nuova analisi li precompila dal profilo. Non entrano nell'Evidence Pack. | `models.py`, `migrations.py`, `schemas.py`, `routers/analyses.py`, `NewAnalysis.tsx`, `AnalysisDetail.tsx`, contratti |
| 4 | — | Nessun codice, come deciso: il limite è documentato e il protocollo di annotazione manuale è in `ROADMAP.md`, M1.11. | `docs/VIDEO_PIPELINE.md`, `docs/ROADMAP.md` |
| doc | (commit documentazione) | Questo documento, `VIDEO_PIPELINE`, `ARCHITECTURE`, `DATA_MODEL`, `ROADMAP`, `DECISIONS` (ADR-015, ADR-016), `README`. | `docs/`, `README.md` |

Nessuna nuova dipendenza: `TypeDecorator` (SQLAlchemy), `GZipMiddleware` (FastAPI/Starlette)
e ffprobe erano già presenti.

### Dettaglio: mappatura temporale (`timemap.json`)

La corrispondenza è **esatta sul fotogramma ma approssimata nel tempo**. Non è una
sincronizzazione temporale esatta: ogni mappa riporta `exact_time_sync: false`.

- **Algoritmo.** ffprobe legge i PTS dei pacchetti video dell'originale, senza decodifica e
  scartando i pacchetti marcati *discard* dall'edit list. I PTS vengono ordinati e riportati
  al primo fotogramma. Il fotogramma sorgente `i` cade nello slot `floor(t_i × fps + 0,5)`.
  Il fotogramma proxy `k` mostra l'ultimo fotogramma sorgente con slot ≤ `k`. È la regola
  del filtro `fps` di FFmpeg (`round=near`).
- **Tornare all'originale.** Il tempo nell'originale del fotogramma mostrato al tempo proxy
  `T` è `source_start_pts_s + source_times_s[proxy_to_source[floor(T × fps)]]`.
- **`start_time` non nullo.** Il proxy parte dal primo fotogramma video (`setpts=PTS-STARTPTS`).
  L'istante corrispondente nell'originale è salvato in `source_start_pts_s`.
- **Fotogrammi duplicati e scartati.** `duplicated_frames` conta i fotogrammi proxy che
  ripetono il precedente. `dropped_frames` conta i fotogrammi sorgente che non compaiono nel
  proxy.
- **Precisione attesa.** L'identità del fotogramma è esatta se FFmpeg applica la regola come
  ricostruita. Nei test di verità a terra, con l'indice codificato nei pixel e letto dal
  proxy reale, gli errori sono 0 su: CFR 30 fps, VFR 30→120 fps, `start_time` 2,5 s, audio
  0,5 s prima del video, 240 fps CFR e 120 fps con jitter e fotogrammi mancanti. Per l'errore
  temporale |tempo proxy − tempo sorgente| ogni mappa riporta `max_abs_error_ms` e
  `mean_abs_error_ms`: vale al massimo mezzo intervallo proxy dove la sorgente è densa, di
  più attraverso i buchi.
- **Limiti con VFR e alto frame rate.** La verifica su smartphone reali non è ancora stata
  fatta (M1.11). I metadati slow-motion, per esempio le rampe dello slo-mo iPhone, vengono
  ignorati. Una raffica a 240 fps dentro un clip VFR viene decimata alla frequenza media. Gli
  slot esattamente a metà dipendono dall'arrotondamento in virgola mobile. Se ffprobe
  fallisce, la mappa manca (`time_mapping: null`) ma l'analisi si completa.

## 3. Test eseguiti e risultati

Tutti i comandi sono stati eseguiti nel container di sviluppo (Linux, Python 3.12, FFmpeg
6.1.1), con il modello MediaPipe scaricato e `libegl1 libgles2` installati.

| Momento | Comando | Risultato |
| --- | --- | --- |
| Baseline, senza `libegl1` | `uv run pytest` | 119 passati, 2 falliti (`OSError: libEGL.so.1`) |
| Baseline, con `libegl1` | `./scripts/check.sh` | verde: 121 pytest, 22 Vitest |
| Dopo #1 | `./scripts/check.sh` | verde: 124 pytest, 23 Vitest |
| Dopo #2 | `./scripts/check.sh` | verde: 131 pytest, 23 Vitest |
| Dopo #3 | `./scripts/check.sh` | verde: 136 pytest, 24 Vitest |
| Dopo #8 | `./scripts/check.sh` | verde: 136 pytest, 26 Vitest |
| Dopo #6 | `./scripts/check.sh` | verde: 143 pytest, 26 Vitest |
| Dopo #7 | `./scripts/check.sh` | verde: 147 pytest, 26 Vitest |
| Dopo #5 | `./scripts/check.sh` | verde: 151 pytest, 29 Vitest |
| Finale (con documentazione) | `./scripts/check.sh` | verde: 151 pytest, 29 Vitest (ruff, mypy strict, contratti, ESLint, Prettier, tsc) |

Verifiche aggiuntive:

- **Punto 2.** Lo script di riproduzione iniziale è stato rieseguito: `probe_failed` ora
  restituisce `{"file": "original.mp4", "stderr": "...original.mp4: Invalid data..."}` e i
  risultati dell'analisi non contengono percorsi assoluti.
- **Punto 3.** Rimuovendo davvero `libegl1` dal container, `check_pose_runtime()` restituisce
  il motivo strutturato. Reinstallando la libreria torna disponibile.

Nuovi test (nessun test esistente rimosso):

| File | Test | Copre |
| --- | --- | --- |
| `tests/integration/test_timestamps.py` | 3 | Offset UTC su tutte le date, righe naive pre-M1.10, normalizzazione |
| `tests/integration/test_no_path_leaks.py` | 7 | `probe_failed`, risultati e report, righe e report pre-M1.10, `internal_error` (traceback solo nel log), `system/info`, errori del motore, redazione |
| `tests/integration/test_pose_runtime.py` | 5 | Errore strutturato, worker, `system/info` con runtime rotto e funzionante, modello mancante |
| `tests/unit/test_timemap.py` | 7 | Verità a terra su 6 tipi di video e regola di selezione |
| `tests/unit/test_pose_precision.py` | 3 | Arrotondamento, angoli invariati (< 0,05°), dimensione |
| `tests/integration/test_compression.py` | 1 | JSON gzip; video, range 206 e originale non compressi |
| `tests/integration/test_player_handedness.py` | 4 | Persistenza, null, validazione, migrazione |
| `apps/web/src/lib/format.test.ts` | +1 | Date API lette come UTC |
| `apps/web/src/pages/pages.test.tsx` | +3 | Runtime MediaPipe ed errori recenti in System Status |
| `apps/web/src/pages/NewAnalysis.test.tsx` | 3 | Mano dominante precompilata, inviata, null se non specificata |

Test esistenti adattati, senza rimuoverli né indebolirli:

- `test_command_caps_long_side_for_portrait`: per un input CFR ora si aspetta
  `fps=30/1:eof_action=pass` (stessa frequenza, nessun ricampionamento) invece di nessun
  filtro `fps`.
- `test_analysis_lifecycle`: si aspetta il nuovo artefatto `timemap` e ne verifica il
  contenuto.
- `test_migration_keeps_results_imported_before_the_history`: dopo aver svuotato
  `schema_migrations` si aspetta che girino entrambe le migrazioni.
- Fixture web tipizzate come `Analysis`: aggiunti i due campi `null`.

## 4. Problemi rimasti aperti

Priorità e milestone sono riportate anche in `docs/ROADMAP.md`, sezione "Open technical items".

| Problema | Priorità | Milestone / condizione |
| --- | --- | --- |
| Selezione esplicita del giocatore (tocco, multi-persona) | Alta | Da progettare con le evidenze di M1.11, prima di fidarsi dei risultati M2 |
| Retention, cancellazione e conservazione dei dati | Alta | **Prima di rendere l'app accessibile online** |
| Allineamento tra `AI_GUIDELINES.md` e comportamento effettivo | Alta | **Prima dell'integrazione dell'analisi AI** per feedback tecnico reale (al più tardi M7) |
| Heartbeat del worker / indicatore "worker attivo" | Media | Prima del deploy online; utile ma non necessario per M1.11 |
| Protezione filesystem su Windows (`O_NOFOLLOW`/`dir_fd` assenti) | Media | Prima di supportare installazioni Windows |
| Concorrenza degli import AI e duplicati non identici | Media | Prima di uso multiutente o online; al più tardi M7 |
| Caricamento a finestre di `pose.json` nel browser | Bassa | M3, o prima se M1.11 mostra problemi di caricamento |

## 5. Limitazioni conosciute

- **Solo test sintetici.** Nessuna correzione è stata verificata su video di tennis reali.
  Mappatura temporale, dimensione di `pose.json` e runtime vanno ricontrollati in M1.11.
- **Cambio di soggetto non rilevato.** Con `num_poses=1` lo scheletro può passare a un'altra
  persona senza avviso. In M1.11 il giocatore si annota a mano e si registrano i cambi.
- **Redazione basata su radici note.** Copre solo i percorsi assoluti di storage, modelli,
  repository e home. Un percorso esterno a queste radici in un messaggio di terze parti,
  per esempio `/var/tmp/x/original.mp4`, resta visibile (verificato da un test). Gli errori
  del motore non ne producono più. Il campo `platform` di `system/info` resta e
  mostra il sistema operativo e il kernel.
- **Eccezione voluta.** Il comando di hand-off AI mostra il percorso del pacchetto da
  digitare in Claude Code. È assoluto solo se lo storage è fuori dal repository.
- **Profilo giocatore.** Resta nel browser. Solo mano dominante e rovescio arrivano al
  backend, per singola analisi. Le analisi create prima hanno valori `null`.
- **Proxy.** Con il filtro `fps` sempre attivo, cambia solo per i video il cui stream video
  non inizia all'inizio del contenitore. Le analisi già elaborate non cambiano finché non
  vengono rielaborate.
- **Misure di dimensione.** Le cifre su `pose.json` vengono da keypoint casuali sintetici:
  i dati reali si comprimono meglio.

## 6. Pronto per M1.11?

Le condizioni concordate sono soddisfatte:

- le criticità bloccanti (1, 2, 3+8, 6) sono risolte, e con loro le migliorative approvate (7, 5);
- i test automatici sono verdi: 151 pytest e 29 Vitest, più lint, tipi e contratti;
- le limitazioni residue sono documentate qui e in `ROADMAP.md`;
- il repository è in uno stato stabile: un commit per intervento su un branch dedicato, in
  attesa di revisione e merge su `main`.

M1.11 richiede i clip reali di Leonardo e il protocollo di annotazione descritto in
`ROADMAP.md` (giocatore da seguire, cambi di soggetto, mano dominante, `time_mapping`,
dimensione di `pose.json`).
