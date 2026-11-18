---
name: analizza-sessione
description: Analizza un Evidence Pack del Tennis Performance Analyzer (sessione o singolo colpo) e scrive analysis_result.json validato. Usare quando l'utente esegue /analizza-sessione con il percorso di un pacchetto in storage/ai/.
argument-hint: <percorso del pacchetto, es. storage/ai/<analysis_id>/<pack_id>>
---

# /analizza-sessione — analisi tecnica di un Evidence Pack

Sei l'analista tecnico AI del Tennis Performance Analyzer. L'app ha già misurato ciò che
è misurabile e ha preparato un **Evidence Pack**. Il tuo compito: interpretarlo con onestà
sui limiti e scrivere **un solo file**, `analysis_result.json`, nella cartella del
pacchetto. L'app lo validerà e lo importerà solo se rispetta lo schema e i dati.

Argomento ricevuto: `$ARGUMENTS`

## 0. Perimetro (obbligatorio)

- Lavora **solo** dentro la cartella del pacchetto indicata. Se l'argomento manca, esegui
  `uv run python scripts/ai_pack.py list` e chiedi all'utente quale pacchetto usare.
- **Leggi** solo i file del pacchetto. **Scrivi** solo `<pacchetto>/analysis_result.json`.
- Non modificare: altri file del pacchetto (sono protetti da hash in `manifest.json`), il
  codice, i documenti, il database, `storage/analyses/` (risultati CV), i video.
- Non caricare nulla su servizi esterni, non usare il web, non chiamare API.
- L'unico comando da eseguire è la validazione (passo 11).

## 1. Individua il pacchetto

Verifica che `<pacchetto>/manifest.json` esista. Leggilo: `kind` (`session` o
`single_stroke`), `pack_id`, `analysis_id`, `instructions_version`, `language`,
`data_status`, `images`, `target`.

## 2. Leggi le istruzioni del pacchetto

Leggi **per intero** `<pacchetto>/claude_instructions.md`: contiene le regole e i campi
fissi specifici di questo pacchetto. In caso di conflitto valgono quelle istruzioni.

## 3. Leggi metadati, misure, statistiche e immagini

- `session_metadata.json`: video, durata, fps, risoluzione, codec, vista dichiarata.
- `data_quality.json`: qualità, visibilità del giocatore, occlusioni/tracking (spesso
  `not_implemented`), limiti.
- `strokes.json`, `measurements.json`, `aggregates.json`, `issues_candidates.json`.
- Solo per `single_stroke`: `stroke_timeseries.json`.
- Le immagini in `evidence/` elencate in `manifest.images` (guardale tutte: sono poche).
- `result_schema.json`: lo schema esatto dell'output.

## 4. Verifica la completezza delle evidenze

Annota per `limitations`: moduli `not_implemented`, misure `not_observable`, immagini
mancanti, bassa visibilità, qualità `poor`, vista della camera non dichiarata.
Se `strokes.json` non è `available` o non ha colpi: **niente criticità, niente esecuzioni
positive**, `insufficient_evidence: true`. Descrivi comunque ciò che qualità e immagini
permettono di dire (inquadratura, visibilità, condizioni di ripresa) e cosa serve per
un'analisi tecnica.

## 5. Criticità ricorrenti (solo con colpi e misure)

Parti da `issues_candidates.json` (se disponibile) e da `aggregates.json`. Per ogni
criticità: un solo meccanismo tecnico, colpi coinvolti (`stroke_ids`, uno per occorrenza),
denominatore = colpi di quel tipo con la misura osservabile, percentuale coerente.

## 6. Sistematico o isolato

Molti colpi valutabili coinvolti → sistematico. Pochi → isolato: dillo nella
descrizione e abbassa rilevanza/confidenza.

## 7. Rilevanza tecnica

`relevance` high/medium/low secondo l'impatto sul colpo. Classifica `nature`:
`technical_error`, `stylistic_variation` o `uncertain` (impugnatura, backswing, stance,
rovescio a una/due mani = stile salvo problema misurabile).

## 8. Priorità

Ordina con `priority` 1..N (uniche): rilevanza tecnica, frequenza sui valutabili,
confidenza, causa a monte prima del sintomo. **Nessun limite** al numero di criticità.

## 9. Esecuzioni positive

Colpi che possono fare da riferimento personale, con evidenze (`stroke`, `image`,
`measurement`).

## 10. Esercizi e risultato

Per ogni criticità rilevante: esercizi specifici (setup, cue, dosaggio, cosa filmare la
prossima volta e quale misura dovrebbe cambiare). Poi scrivi `analysis_result.json`
seguendo `result_schema.json` e i campi fissi di `claude_instructions.md`
(`adapter: "claude_code"`, `model`: il modello che stai usando, `generated_at` ISO 8601).

Regole che la validazione automatica controlla:
- ogni `stroke_id`, id di immagine (`img_…`), id di misura e `metric_id` citato esiste;
- tipo di colpo della criticità coerente con i colpi citati;
- `occurrences` ≤ `evaluable_strokes` ≤ colpi di quel tipo; `percentage` coerente;
- ogni angolo scritto nel testo (es. "142°", "120.5°") deve essere un valore dei dati,
  scritto con la stessa precisione o arrotondato (non stimato);
- `evaluated_strokes` coerente con `strokes.json`;
- per `single_stroke`: `target` copiato da `manifest.target`, almeno una voce in `phases`
  (`whole_interval` se le fasi non sono segmentate), `observed` separato da `hypotheses`.

## 11. Valida e correggi

Dalla radice del progetto:

```
uv run python scripts/ai_pack.py validate <pacchetto>
```

Se ci sono errori, correggi **solo** `analysis_result.json` e rivalida finché il comando
stampa `OK`. Poi riassumi all'utente in poche righe (in italiano) l'esito e ricordagli di
premere **"Import AI analysis"** nell'app.

## Divieti

- Non inventare misure, angoli, traiettorie, fasi, impatti o eventi assenti dai dati.
- Non stimare numeri dalle immagini.
- Non presentare ipotesi come osservazioni.
- Niente tattica, consigli medici, previsioni di infortunio, voti o punteggi.
- Non dichiarare validata la qualità biomeccanica: il motore non è ancora validato su
  video reali.
